"""FastAPI application factory for `fileflow serve`.

Exposes a thin, read-mostly /api used by the web client: project metadata,
the nested file tree, the rendered prompt, config read/write, and a WebSocket
for watch-mode change notifications.

Security model: the server is pinned to the directory it was started with
(``fileflow serve --root X``). Endpoints accept an optional ``root`` query
parameter, but it may only *narrow* the scope to a sub-directory of X; any value
that resolves outside X is refused with HTTP 403. See ``security.confine_root``.
"""

import asyncio
import os
from typing import Optional

import click
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .. import cli as cli_mod
from .. import receipt as receipt_mod
from .. import secretscan
from ..cli import apply_token_budget_detailed, collect, count_tokens, render_documents
from . import security
from . import config as configmod
from . import guard
from . import watcher
from . import workspace

# Bound the response: a project with 100k ignored files must not produce a 5 MB reply.
LEFT_OUT_LIMIT = 300


def _rel(path, root):
    """``path`` relative to ``root`` with forward slashes (stable across OSes)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def build_app(static_dir=None, project_root=None, allowed_hosts=None, token=None, enforce_origin=False):
    """Create the FastAPI app.

    ``static_dir`` mounts the web client if given. ``project_root`` is the
    directory the server is pinned to: it is the default for every endpoint and
    the outer boundary for the optional ``root`` query parameter. If omitted it
    defaults to the process working directory.

    ``allowed_hosts`` (iterable of host names, or ``None`` for "any") and
    ``token`` (or ``None`` for "no auth") configure :class:`guard.Guard`; the
    ``serve`` command always sets them, library callers get an open app.
    ``enforce_origin`` installs the guard even with no host list and no token,
    so cross-site WebSocket/writes are refused; ``serve`` always sets it.
    """
    jail = security.resolve_project_root(project_root)
    app = FastAPI(title="fileflow", version=__version__)

    def served_root(
        root: Optional[str] = Query(
            None, description="Optional sub-directory of the served project"
        )
    ):
        """Resolve the request's root, refusing anything outside the jail."""
        try:
            return security.confine_root(jail, root)
        except security.PathOutsideRootError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # NOTE: /api routes are registered BEFORE the "/" static mount. Starlette
    # matches routes in registration order, so mounting static at "/" first
    # would shadow every /api/* endpoint with a 404.

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/project")
    def project(r: str = Depends(served_root), include_patterns: str = ""):
        return workspace.project_info(r, include_patterns=[p for p in include_patterns.split(",") if p])

    @app.get("/api/tree")
    def tree(
        r: str = Depends(served_root),
        include_hidden: bool = False,
        ignore_gitignore: bool = False,
        ignore_patterns: str = "",
        include_patterns: str = "",
    ):
        pats = [p for p in ignore_patterns.split(",") if p]
        incl = [p for p in include_patterns.split(",") if p]
        return workspace.build_tree(
            r, include_hidden=include_hidden, ignore_gitignore=ignore_gitignore, ignore_patterns=pats,
            include_patterns=incl,
        )

    @app.get("/api/file")
    def file(r: str = Depends(served_root), path: str = Query("", description="Path within root")):
        try:
            full = security.safe_join(r, path)
        except security.PathOutsideRootError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        # VCS internals are never served: .git routinely holds credentials
        # (remote URLs with tokens, config) and is never prompt material.
        if ".git" in os.path.relpath(full, r).split(os.sep):
            raise HTTPException(status_code=404, detail="Not a file: %s" % path)
        if not os.path.isfile(full):
            raise HTTPException(status_code=404, detail="Not a file: %s" % path)
        # Same cap as the prompt: never read an unbounded file into memory because someone clicked it.
        cap = cli_mod._MAX_FILE_BYTES
        if cap > 0 and os.path.getsize(full) > cap:
            raise HTTPException(
                status_code=413,
                detail="%s is %s, over the %s size limit, so it is not shown or sent (FILEFLOW_MAX_FILE_BYTES=0 lifts the limit)"
                % (path, cli_mod.human_size(os.path.getsize(full)), cli_mod.human_size(cap)),
            )
        try:
            with open(full, "r", encoding="utf-8") as fh:
                content = fh.read()
        except (UnicodeDecodeError, OSError):
            raise HTTPException(status_code=422, detail="Unreadable or binary file")
        return {"path": path, "content": content}

    @app.get("/api/prompt")
    def prompt(
        r: str = Depends(served_root),
        format: str = "default",
        budget: int = 0,
        tokenizer: str = "heuristic",
        include_hidden: bool = False,
        ignore_gitignore: bool = False,
        line_numbers: bool = False,
        separators: bool = True,
        ignore_patterns: str = "",
        include_patterns: str = "",
        secrets: str = "warn",
    ):
        if format not in ("default", "xml", "json"):
            raise HTTPException(status_code=400, detail="format must be default|xml|json")
        if secrets not in secretscan.MODES:
            raise HTTPException(status_code=400, detail="secrets must be warn|exclude|block|off")
        pats = [p for p in ignore_patterns.split(",") if p]
        col = collect(
            [r],
            include_hidden=include_hidden,
            ignore_gitignore=ignore_gitignore,
            ignore_patterns=pats,
            include_patterns=[p for p in include_patterns.split(",") if p],
            secrets=secrets,
            quiet=True,
        )
        if secrets == "block" and col.secret_findings:
            # Fail closed, like the CLI's `--secrets block`: name the files and
            # rules (never the values) so the client can show what to fix.
            detail = "; ".join(
                "%s (%s)" % (_rel(path, r), ", ".join(sorted({f.rule for f in found})))
                for path, found in sorted(col.secret_findings.items())
            )
            raise HTTPException(status_code=422, detail="possible secrets, nothing emitted: " + detail)
        # Paths in the prompt are relative to the served root: an absolute
        # local path (/home/me/...) says nothing useful to a model and leaks
        # the machine's layout into whatever the user pastes it into.
        documents = [(_rel(p, r), c) for p, c in col.documents]
        budget_truncated, budget_dropped = [], []
        if budget and budget > 0:
            # Reuse the engine's budget logic so server and CLI trimming agree
            # (the meta token count below uses the same tokenizer).
            try:
                trimmed = apply_token_budget_detailed(documents, budget, tokenizer=tokenizer)
            except click.UsageError as exc:
                raise HTTPException(status_code=400, detail=str(exc.message))
            documents = trimmed.documents
            budget_truncated, budget_dropped = trimmed.truncated, trimmed.dropped
        text = render_documents(
            documents,
            format=format,
            line_numbers=line_numbers,
            separators=separators,
            provenance={_rel(p, r): info for p, info in col.provenance.items()},
        )
        try:
            per_file = [(p, count_tokens(c, tokenizer)) for p, c in documents]
        except click.UsageError:
            # A tokenizer that can't be loaded falls back to the heuristic for
            # the informational meta count rather than failing the request.
            per_file = [(p, count_tokens(c, "heuristic")) for p, c in documents]
        tokens = sum(t for _, t in per_file)
        # What stayed out, and why: the same evidence the CLI receipt holds, so the page can
        # answer "why isn't my file in the prompt?" without anyone opening a terminal.
        left = [{"path": _rel(e.path, r) + ("/" if e.is_dir else ""), "reason": e.reason} for e in col.excluded]
        left += [{"path": p, "reason": "TOKEN_BUDGET"} for p in budget_dropped]
        left += [{"path": p, "reason": "TOKEN_BUDGET", "partial": True} for p, _, _ in budget_truncated]
        left.sort(key=lambda e: (e["reason"], e["path"]))
        left_counts = {}
        for e in left:
            e["label"] = receipt_mod.REASON_WORDS[e["reason"]]
            left_counts[e["reason"]] = left_counts.get(e["reason"], 0) + 1
        return {
            "root": r,
            "format": format,
            "text": text,
            "meta": {
                "files": len(documents),
                "chars": len(text),
                "tokens": tokens,
                "lines": text.count("\n") + 1 if text else 0,
                "tokenizer": tokenizer,
                "secrets": [
                    {
                        "path": _rel(path, r),
                        "rules": sorted({f.rule for f in found}),
                        "lines": sorted({f.line for f in found}),
                    }
                    for path, found in sorted(col.secret_findings.items())
                ],
                "blocked": sum(1 for e in col.excluded if e.reason == "SECRET_BLOCKED"),
                "left_out": left[:LEFT_OUT_LIMIT],
                "left_out_total": len(left),
                "left_out_counts": left_counts,
                "dominant": receipt_mod.dominant_info(per_file),
            },
        }

    @app.get("/api/config")
    def get_config(r: str = Depends(served_root)):
        # Strict: a half-edited, invalid .files-to-prompt is reported (422)
        # rather than silently read as defaults, so the web client can keep
        # the user's current settings while the file is being fixed.
        try:
            return configmod.read_config(r, strict=True)
        except configmod.ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.put("/api/config")
    def put_config(r: str = Depends(served_root), payload: dict = None):
        if payload is None:
            raise HTTPException(status_code=400, detail="empty payload")
        try:
            written = configmod.write_config(r, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"ok": True, "path": configmod.config_path(r), "written": written}

    @app.websocket("/api/watch")
    async def watch(websocket: WebSocket, root: Optional[str] = None, poll: float = 2.0):
        """Push ``{root, hash, changed, config_changed}`` when the project changes.

        Protocol: after the handshake the server takes a baseline snapshot and
        sends one ``ready`` frame (``changed`` false) carrying the baseline
        ``hash``. A client that reconnects can compare that hash with the last
        one it saw to learn whether anything changed while it was away. After
        that, a frame is sent whenever the snapshot differs from the previous
        one; ``config_changed`` is true when the content of ``.files-to-prompt``
        differs. The root is confined exactly like the HTTP endpoints; a bad
        root is rejected before the handshake completes.
        """
        try:
            r = security.confine_root(jail, root)
        except ValueError:  # outside the served project, or not a directory
            await websocket.close(code=1008)
            return
        await websocket.accept()
        loop = asyncio.get_running_loop()

        def scan():
            # The directory walk is blocking I/O: keep it off the event loop so
            # a large project can't stall every other request.
            return loop.run_in_executor(None, watcher.snapshot, r)

        async def send(frame):
            try:
                await websocket.send_json(frame)
                return True
            except (WebSocketDisconnect, RuntimeError):  # client is gone
                return False

        async def client_gone():
            """Finish as soon as the client disconnects; anything it sends is ignored."""
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
            except (WebSocketDisconnect, RuntimeError):
                return

        # A dedicated receiver notices a disconnect immediately. (Draining the
        # socket with short receive() timeouts inside the poll loop and then
        # swallowing the disconnect meant the loop never ended: every closed tab
        # left a poller running forever and blocked a clean server shutdown.)
        gone = asyncio.ensure_future(client_gone())
        try:
            last = await scan()
            ready = {
                "root": r,
                "hash": watcher.digest_of(last),
                "changed": False,
                "config_changed": False,
                "ready": True,
            }
            if not await send(ready):
                return
            while True:
                done, _ = await asyncio.wait({gone}, timeout=max(0.1, poll))
                if done:  # the client disconnected while we were waiting
                    break
                current = await scan()
                if current != last:
                    frame = {
                        "root": r,
                        "hash": watcher.digest_of(current),
                        "changed": True,
                        "config_changed": current.config != last.config,
                    }
                    last = current
                    if not await send(frame):
                        break
        finally:
            gone.cancel()

    if allowed_hosts is not None or token is not None or enforce_origin:
        app.add_middleware(guard.Guard, allowed_hosts=allowed_hosts, token=token)

    # Mount the static web client last so /api routes win.
    if static_dir and os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")

    return app
