"""End-to-end integration test: run ``fileflow serve`` as a REAL
subprocess and exercise it over HTTP — including the config
live-refresh SSE stream when .files-to-prompt changes on disk."""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from fileflow.config import write_config

SERVER_STARTUP_TIMEOUT = 30
SSE_TIMEOUT = 15


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# readme\n")
    return tmp_path


@pytest.fixture()
def server(project):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "fileflow", "serve",
         "--host", "127.0.0.1", "--port", str(port), "--root", str(project)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + SERVER_STARTUP_TIMEOUT
        last_err = None
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"server exited early:\n{out}")
            try:
                httpx.get(base + "/api/config", timeout=2)
                break
            except httpx.HTTPError as exc:
                last_err = exc
                time.sleep(0.2)
        else:
            raise RuntimeError(f"server never came up: {last_err}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_end_to_end(server, project):
    # 1. Fresh server: no config, default options.
    data = httpx.get(server + "/api/config").json()
    assert data["exists"] is False
    assert data["options"]["format"] == "default"

    # 2. Render with defaults.
    render = httpx.post(server + "/api/render", json={}).json()
    assert render["format"] == "default"
    assert "README.md" in render["output"]

    # 3. Save config over HTTP -> file on disk.
    res = httpx.put(server + "/api/config",
                    json={"format": "json", "exclude": {"patterns": ["*.md"]}})
    assert res.status_code == 200
    assert (project / ".files-to-prompt").is_file()

    # 4. Render now honors the saved config (json + exclusion).
    render = httpx.post(server + "/api/render", json={}).json()
    assert render["format"] == "json"
    parsed = json.loads(render["output"])
    assert [f["path"] for f in parsed["files"]] == ["src/app.py"]

    # 5. Per-request override still beats the config.
    render = httpx.post(server + "/api/render",
                        json={"overrides": {"format": "xml"}}).json()
    assert render["output"].startswith("<documents>")

    # 6. Web client is served.
    assert "EventSource" in httpx.get(server + "/").text


def test_live_refresh_on_disk_edit(server, project):
    """Editing .files-to-prompt on disk (outside the server) triggers a
    config-changed SSE event, and the next render picks up the change."""
    with httpx.stream("GET", server + "/api/events", timeout=SSE_TIMEOUT) as stream:
        lines = stream.iter_lines()
        hello = _next_data(lines)
        assert hello["event"] == "hello"

        # Simulate a hand edit / CLI edit of the config file.
        write_config(project, {"format": "markdown", "line_numbers": True})

        changed = _next_data(lines)
        assert changed["event"] == "config-changed"
        assert changed["version"] > hello["version"]

    render = httpx.post(server + "/api/render", json={}).json()
    assert render["format"] == "markdown"
    assert "```" in render["output"]
    assert "1  # readme" in render["output"]


def _next_data(lines):
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("SSE stream ended without a data event")
