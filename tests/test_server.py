import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fileflow.config import config_path, write_config
from fileflow.server import ConfigWatcher, create_app


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# readme\n")
    return tmp_path


@pytest.fixture()
def client(project):
    with TestClient(create_app(project)) as c:
        yield c


def test_index_serves_web_client(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "fileflow" in res.text
    assert "EventSource" in res.text


def test_tree(client):
    data = client.get("/api/tree").json()
    assert [f["path"] for f in data["files"]] == ["README.md", "src/app.py"]


def test_get_config_no_file(client):
    data = client.get("/api/config").json()
    assert data["exists"] is False
    assert data["config"] == {}
    assert data["options"]["format"] == "default"


def test_put_then_get_config(client, project):
    res = client.put("/api/config", json={"format": "xml", "exclude": {"patterns": ["*.md", "*.log"]}})
    assert res.status_code == 200
    assert config_path(project).is_file()
    data = client.get("/api/config").json()
    assert data["config"]["format"] == "xml"
    assert data["config"]["exclude"]["patterns"] == ["*.md", "*.log"]
    assert data["options"]["exclude"] == ["*.md", "*.log"]


def test_put_config_rejects_bad_format(client):
    res = client.put("/api/config", json={"format": "yaml"})
    assert res.status_code == 400


def test_render_uses_config_from_disk(client, project):
    write_config(project, {"format": "json"})
    data = client.post("/api/render", json={}).json()
    assert data["format"] == "json"
    parsed = json.loads(data["output"])
    assert [f["path"] for f in parsed["files"]] == ["README.md", "src/app.py"]


def test_render_overrides_beat_config(client, project):
    write_config(project, {"format": "json"})
    data = client.post("/api/render", json={"overrides": {"format": "xml"}}).json()
    assert data["format"] == "xml"
    assert data["output"].startswith("<documents>")


def test_render_bad_override(client):
    res = client.post("/api/render", json={"overrides": {"format": "nope"}})
    assert res.status_code == 400


def test_config_excludes_apply_to_tree(client, project):
    write_config(project, {"exclude": {"patterns": ["*.md"]}})
    data = client.get("/api/tree").json()
    assert [f["path"] for f in data["files"]] == ["src/app.py"]


class TestWatcher:
    def test_detects_create_change_delete(self, project):
        w = ConfigWatcher(project)
        assert w.poll_once() is False

        write_config(project, {"format": "xml"})
        assert w.poll_once() is True
        v1 = w.version

        assert w.poll_once() is False  # no change, no bump

        time.sleep(0.01)
        write_config(project, {"format": "json"})
        assert w.poll_once() is True
        assert w.version == v1 + 1

        config_path(project).unlink()
        assert w.poll_once() is True

    def test_thread_start_stop(self, project):
        w = ConfigWatcher(project)
        w.start()
        write_config(project, {"format": "xml"})
        deadline = time.time() + 3
        while w.version == 0 and time.time() < deadline:
            time.sleep(0.05)
        w.stop()
        assert w.version >= 1


def test_events_stream_reports_config_change(project):
    """Drive the SSE generator directly (this starlette TestClient build
    buffers streaming responses, so it can't consume SSE in-process; the
    real-HTTP path is covered by test_integration.py)."""
    import asyncio

    from starlette.requests import Request

    app = create_app(project)
    watcher = app.state.watcher  # thread not started: poll manually
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/events")

    async def never_receive():
        await asyncio.sleep(3600)
        return {"type": "http.request"}

    async def main():
        scope = {"type": "http", "method": "GET", "path": "/api/events",
                 "headers": [], "query_string": b"once=1"}
        response = await route.endpoint(Request(scope, never_receive), once=True)
        gen = response.body_iterator
        hello = await gen.__anext__()
        write_config(project, {"format": "markdown"})
        assert watcher.poll_once() is True
        changed = await gen.__anext__()
        with pytest.raises(StopAsyncIteration):  # once=1 closes the stream
            await asyncio.wait_for(gen.__anext__(), timeout=5)
        return hello, changed

    hello, changed = asyncio.run(main())
    hello_data = json.loads(hello.removeprefix("data: "))
    changed_data = json.loads(changed.removeprefix("data: "))
    assert hello_data["event"] == "hello"
    assert changed_data["event"] == "config-changed"
    assert changed_data["version"] > hello_data["version"]
