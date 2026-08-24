"""fileflow server: JSON API + web client + config live-refresh.

Endpoints:

* ``GET  /``            – web client
* ``GET  /api/tree``    – file listing under the served root
* ``GET  /api/config``  – parsed config + effective options
* ``PUT  /api/config``  – persist config to .files-to-prompt
* ``POST /api/render``  – render the root (config merged with overrides)
* ``GET  /api/events``  – SSE stream; emits ``config-changed`` whenever
  ``.files-to-prompt`` changes on disk (edited by hand, by the CLI, or
  by another client), enabling live re-apply + re-render.

Every render re-reads the config from disk, so the config file is the
live source of truth in server mode.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import config_path, config_to_options, read_config, write_config
from .core import DEFAULT_OPTIONS, FORMATS, RenderOptions, collect_files, estimate_tokens, render

WATCH_INTERVAL = 0.25  # seconds between config mtime checks
SSE_POLL = 0.25        # seconds between SSE version checks
SSE_KEEPALIVE = 15.0   # seconds between keepalive comments


class ConfigWatcher:
    """Polls the config file's (mtime, size) signature and bumps a
    version counter whenever it changes (including create/delete)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.version = 0
        self._signature = self._stat()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _stat(self):
        try:
            st = config_path(self.root).stat()
            return (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return None

    def poll_once(self) -> bool:
        sig = self._stat()
        if sig != self._signature:
            self._signature = sig
            self.version += 1
            return True
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(WATCH_INTERVAL)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="fileflow-config-watcher")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def effective_options(root: Path, overrides: dict | None = None) -> RenderOptions:
    """defaults <- config file <- request overrides."""
    options = RenderOptions(**DEFAULT_OPTIONS)
    options = options.merged(config_to_options(read_config(root)))
    if overrides:
        clean = {k: v for k, v in overrides.items() if k in DEFAULT_OPTIONS}
        if "exclude" in clean and clean["exclude"] is not None:
            clean["exclude"] = tuple(clean["exclude"])
        options = options.merged(clean)
    return options


def _options_dict(options: RenderOptions) -> dict:
    return {
        "format": options.format,
        "separators": options.separators,
        "line_numbers": options.line_numbers,
        "max_tokens": options.max_tokens,
        "include_hidden": options.include_hidden,
        "ignore_gitignore": options.ignore_gitignore,
        "exclude": list(options.exclude),
    }


def _relativize(entries, root: Path):
    from .core import FileEntry

    prefix = root.as_posix().rstrip("/") + "/"
    out = []
    for e in entries:
        rel = e.path[len(prefix):] if e.path.startswith(prefix) else e.path
        out.append(FileEntry(rel, e.content))
    return out


def _web_client() -> str:
    return (resources.files("fileflow") / "web" / "index.html").read_text(encoding="utf-8")


def create_app(root: Path) -> FastAPI:
    root = Path(root).resolve()
    app = FastAPI(title="fileflow", version="0.3.0")
    watcher = ConfigWatcher(root)
    app.state.root = root
    app.state.watcher = watcher

    @app.on_event("startup")
    async def _start_watcher() -> None:
        watcher.start()

    @app.on_event("shutdown")
    async def _stop_watcher() -> None:
        watcher.stop()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _web_client()

    @app.get("/api/tree")
    async def tree():
        options = effective_options(root)
        entries = _relativize(collect_files([root], options), root)
        files = [{"path": e.path, "tokens": estimate_tokens(e.content)} for e in entries]
        return {"root": str(root), "files": files}

    @app.get("/api/config")
    async def get_config():
        raw = read_config(root)
        return {
            "root": str(root),
            "exists": config_path(root).is_file(),
            "config": raw,
            "options": _options_dict(effective_options(root)),
            "version": watcher.version,
        }

    @app.put("/api/config")
    async def put_config(request: Request):
        data = await request.json()
        if not isinstance(data, dict):
            return JSONResponse({"error": "config body must be an object"}, status_code=400)
        fmt = data.get("format")
        if fmt is not None and fmt not in FORMATS:
            return JSONResponse(
                {"error": f"unknown format {fmt!r}; expected one of {list(FORMATS)}"},
                status_code=400,
            )
        write_config(root, data)
        return {"ok": True, "config": read_config(root)}

    @app.post("/api/render")
    async def do_render(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        overrides = body.get("overrides") if isinstance(body, dict) else None
        try:
            options = effective_options(root, overrides)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        entries = _relativize(collect_files([root], options), root)
        output = render(entries, options)
        return {
            "output": output,
            "format": options.format,
            "files": len(entries),
            "tokens": estimate_tokens(output),
            "options": _options_dict(options),
            "config_version": watcher.version,
        }

    @app.get("/api/events")
    async def events(request: Request, once: bool = False) -> StreamingResponse:
        async def stream():
            last_seen = watcher.version
            yield f"data: {json.dumps({'event': 'hello', 'version': last_seen})}\n\n"
            last_beat = time.monotonic()
            while True:
                if await request.is_disconnected():
                    return
                if watcher.version != last_seen:
                    last_seen = watcher.version
                    payload = {"event": "config-changed", "version": last_seen}
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_beat = time.monotonic()
                    if once:
                        return
                elif time.monotonic() - last_beat > SSE_KEEPALIVE:
                    yield ": keepalive\n\n"
                    last_beat = time.monotonic()
                await asyncio.sleep(SSE_POLL)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
