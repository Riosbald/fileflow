"""End-to-end tests: run the real ``fileflow serve`` command as a subprocess.

``test_server.py`` drives the ASGI app in-process with Starlette's TestClient.
That is fast, but it cannot catch problems in the parts that only exist when the
actual command runs: the CLI wiring (``--root``, ``--host``/``--allow-remote``),
uvicorn startup, static-file serving of the bundled web client, real sockets,
and process shutdown. These tests launch ``python -m fileflow.cli serve`` on a
free port from an *unrelated* working directory, then talk to it over real HTTP
and a real WebSocket.

Requires the ``server`` extra (fastapi + uvicorn[standard]).
"""

import asyncio
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STARTUP_TIMEOUT = 25  # seconds; generous for slow CI runners
SHUTDOWN_TIMEOUT = 8  # SIGTERM -> exit. Graceful shutdown is near-instant; a hang is a bug.

# No proxies: these requests must go straight to the loopback server.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(url, timeout=5):
    """Return ``(status, body)``; HTTP errors are returned, not raised."""
    try:
        with _opener.open(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def http_request(method, url, payload=None, timeout=5):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def raw_get(port, path):
    """GET with the path sent verbatim (no client-side ``..`` normalisation)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def replace_atomically(path, text):
    """Swap a file's content in one step so the watcher never sees a half-write."""
    tmp = str(path) + ".swap"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, str(path))


def _server_command(root, port, *extra):
    return [sys.executable, "-m", "fileflow.cli", "serve", "--root", str(root), "--port", str(port), *extra]


def _server_env():
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    # Make `fileflow` importable even if it is not pip-installed.
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    return env


class LiveServer:
    def __init__(self, proc, port, root, outside, log_path):
        self.proc, self.port, self.root, self.outside, self._log = proc, port, root, outside, log_path

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def output(self):
        with open(self._log, "r", errors="replace") as fh:
            return fh.read()


@pytest.fixture
def live_server(tmp_path):
    """``fileflow serve --root <project>`` running for the duration of one test."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "dist").mkdir()
    (root / "README.md").write_text("# demo\n")
    (root / "src" / "main.py").write_text("def main():\n    return 'hello'\n")
    (root / ".gitignore").write_text("dist/\n*.log\n")
    (root / "dist" / "bundle.js").write_text("ignored build output\n")
    (root / "debug.log").write_text("ignored log\n")
    (root / ".env.example").write_text("HIDDEN=1\n")

    outside = tmp_path / "outside"  # a neighbour the server must never reveal
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET-OUTSIDE\n")

    elsewhere = tmp_path / "elsewhere"  # CWD differs from --root on purpose
    elsewhere.mkdir()

    port = free_port()
    log_path = tmp_path / "server.log"
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            _server_command(root, port),
            cwd=str(elsewhere),
            env=_server_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    server = LiveServer(proc, port, root, outside, log_path)
    try:
        deadline = time.time() + STARTUP_TIMEOUT
        while True:
            if proc.poll() is not None:
                pytest.fail("fileflow serve exited early (code %s):\n%s" % (proc.returncode, server.output()))
            try:
                if http_get(server.base + "/api/health", timeout=1)[0] == 200:
                    break
            except OSError:
                pass  # not listening yet
            if time.time() > deadline:
                pytest.fail("fileflow serve did not become ready:\n%s" % server.output())
            time.sleep(0.1)
        yield server
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            # Not just cleanup: a server that cannot stop on SIGTERM (Ctrl+C) is a
            # regression, e.g. watcher loops that outlive their WebSocket client.
            pytest.fail(
                "fileflow serve ignored SIGTERM for %ss:\n%s" % (SHUTDOWN_TIMEOUT, server.output())
            )


# ---------------------------------------------------------------------------
# What the command serves
# ---------------------------------------------------------------------------


def test_serves_the_bundled_web_client_and_the_api(live_server):
    status, html = http_get(live_server.base + "/")
    assert status == 200 and "fileflow" in html.lower()
    for asset in ("/js/app.js", "/js/core.js", "/css/styles.css"):
        assert http_get(live_server.base + asset)[0] == 200, asset
    status, body = http_get(live_server.base + "/api/health")
    assert status == 200 and json.loads(body)["status"] == "ok"


def test_root_flag_decides_what_is_served_not_the_cwd(live_server):
    base = live_server.base
    project = json.loads(http_get(base + "/api/project")[1])
    assert project["root"] == os.path.realpath(str(live_server.root))

    tree = json.loads(http_get(base + "/api/tree")[1])
    names = [n["name"] for n in tree]
    assert "README.md" in names and "src" in names
    assert "dist" not in names and "debug.log" not in names  # .gitignore applied
    assert ".env.example" not in names  # hidden files excluded by default

    status, body = http_get(base + "/api/prompt")
    text = json.loads(body)["text"]
    assert status == 200 and "def main()" in text
    assert "ignored build output" not in text and "HIDDEN=1" not in text


def test_config_round_trips_through_the_real_server(live_server):
    base = live_server.base
    cfg = {
        "output": {"format": "xml", "separators": False, "line_numbers": True, "max_tokens": 4000},
        "ignore": {"gitignore": True, "hidden": False},
        "exclude": {"patterns": ["*.md", "weird\\pattern"]},
    }
    assert http_request("PUT", base + "/api/config", cfg)[0] == 200
    assert (live_server.root / ".files-to-prompt").is_file()
    got = json.loads(http_get(base + "/api/config")[1])
    assert got["output"] == cfg["output"]
    assert got["exclude"]["patterns"] == cfg["exclude"]["patterns"]
    # A payload that would write a broken file is refused and changes nothing.
    assert http_request("PUT", base + "/api/config", {"output": {"format": "pdf"}})[0] == 400
    assert json.loads(http_get(base + "/api/config")[1])["output"]["format"] == "xml"


def test_a_hand_broken_config_is_reported_not_silently_defaulted(live_server):
    (live_server.root / ".files-to-prompt").write_text("[output\nformat = \n")
    status, body = http_get(live_server.base + "/api/config")
    assert status == 422 and ".files-to-prompt" in json.loads(body)["detail"]


# ---------------------------------------------------------------------------
# Containment, attacked over real sockets
# ---------------------------------------------------------------------------


def test_cannot_escape_the_served_root_over_http(live_server):
    base = live_server.base
    q = urllib.parse.quote
    escapes = [
        "/api/file?root=%s&path=secret.txt" % q(str(live_server.outside), safe=""),
        "/api/tree?root=%s" % q(str(live_server.outside), safe=""),
        "/api/project?root=%s" % q(os.path.dirname(str(live_server.root)), safe=""),
        "/api/prompt?root=/",
        "/api/tree?root=/etc",
        "/api/tree?root=..",
        "/api/tree?root=%2e%2e%2f",
    ]
    for url in escapes:
        status, body = http_get(base + url)
        assert status == 403, (url, status, body)
        assert "TOP-SECRET" not in body
    # Traversal through `path` is refused too, and even with the right root.
    status, body = http_get(base + "/api/file?path=../outside/secret.txt")
    assert status == 404 and "TOP-SECRET" not in body
    # Nothing was written outside the root by a refused config write.
    status, _ = http_request(
        "PUT", base + "/api/config?root=" + q(str(live_server.outside), safe=""), {"output": {}}
    )
    assert status == 403
    assert not (live_server.outside / ".files-to-prompt").exists()


def test_static_mount_cannot_be_traversed_to_the_python_source(live_server):
    """The web client is served from ``fileflow/web/``, inside the Python package."""
    for path in (
        "/../fileflow/cli.py",
        "/%2e%2e/fileflow/cli.py",
        "/..%2ffileflow/cli.py",
        "/css/../../fileflow/cli.py",
    ):
        status, body = raw_get(live_server.port, path)
        assert "def serve(" not in body, path
        assert status in (400, 404), (path, status)


# ---------------------------------------------------------------------------
# Live reload over a real WebSocket
# ---------------------------------------------------------------------------


def test_watch_over_a_real_websocket_flags_config_edits(live_server):
    websockets = pytest.importorskip("websockets")
    url = "ws://127.0.0.1:%d/api/watch?poll=0.2" % live_server.port

    async def scenario():
        async with websockets.connect(url) as ws:
            ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert ready["ready"] is True and ready["changed"] is False

            replace_atomically(live_server.root / ".files-to-prompt", '[output]\nformat = "xml"\n')
            cfg_frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert cfg_frame["changed"] is True and cfg_frame["config_changed"] is True

            replace_atomically(live_server.root / "src" / "main.py", "def main():\n    return 'a much longer body'\n")
            src_frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert src_frame["changed"] is True and src_frame["config_changed"] is False
            assert len({ready["hash"], cfg_frame["hash"], src_frame["hash"]}) == 3

    asyncio.run(scenario())


def test_watch_over_a_real_websocket_refuses_a_root_outside_the_project(live_server):
    websockets = pytest.importorskip("websockets")
    url = "ws://127.0.0.1:%d/api/watch?poll=0.2&root=%s" % (
        live_server.port,
        urllib.parse.quote(str(live_server.outside), safe=""),
    )

    async def scenario():
        with pytest.raises(websockets.exceptions.InvalidHandshake) as exc:
            async with websockets.connect(url):
                pass
        response = getattr(exc.value, "response", None)
        status = getattr(response, "status_code", None) or getattr(exc.value, "status_code", None)
        assert status == 403

    asyncio.run(scenario())


def test_abrupt_websocket_disconnect_leaves_no_traceback_in_the_server_log(live_server):
    websockets = pytest.importorskip("websockets")
    url = "ws://127.0.0.1:%d/api/watch?poll=0.2" % live_server.port

    async def scenario():
        ws = await websockets.connect(url)
        await asyncio.wait_for(ws.recv(), 10)
        ws.transport.abort()  # drop the TCP connection with no closing handshake
        await asyncio.sleep(1.0)  # several poll cycles: the handler must notice and exit

    asyncio.run(scenario())
    log = live_server.output()
    assert "Traceback" not in log and "Exception in ASGI application" not in log, log
    # ...and the server is still healthy afterwards.
    assert http_get(live_server.base + "/api/health")[0] == 200


def test_sigterm_with_a_browser_tab_still_connected_exits_promptly(live_server):
    """Ctrl+C while the web client is open used to hang until a second Ctrl+C."""
    websockets = pytest.importorskip("websockets")
    url = "ws://127.0.0.1:%d/api/watch?poll=0.2" % live_server.port

    async def scenario():
        async with websockets.connect(url) as ws:
            await asyncio.wait_for(ws.recv(), 10)  # connected and watching
            started = time.time()
            live_server.proc.terminate()  # the user presses Ctrl+C
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: live_server.proc.wait(timeout=SHUTDOWN_TIMEOUT))
            return time.time() - started

    assert asyncio.run(scenario()) < SHUTDOWN_TIMEOUT


# ---------------------------------------------------------------------------
# CLI guard rails (these exit immediately, so no server fixture)
# ---------------------------------------------------------------------------


def _run_serve(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "fileflow.cli", "serve", *args],
        cwd=str(tmp_path),
        env=_server_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        timeout=30,
    )


def test_refuses_a_non_local_bind_without_allow_remote(tmp_path):
    result = _run_serve(tmp_path, "--host", "0.0.0.0", "--port", str(free_port()))
    assert result.returncode != 0
    assert "Refusing to bind" in result.stdout and "--allow-remote" in result.stdout


def test_a_missing_root_is_a_clean_usage_error(tmp_path):
    result = _run_serve(tmp_path, "--root", str(tmp_path / "no-such-dir"), "--port", str(free_port()))
    assert result.returncode == 2
    assert "Traceback" not in result.stdout
    assert "does not exist" in result.stdout
