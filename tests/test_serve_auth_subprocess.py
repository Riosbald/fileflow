"""The real `fileflow serve` command: Host allowlist and token wiring (end to end)."""
import http.client
import os
import re
import signal
import subprocess
import sys
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from test_serve_subprocess import _run_serve, _server_command, _server_env, free_port  # noqa: E402


class Running:
    def __init__(self, tmp_path, *extra):
        self.root = tmp_path / "proj"
        self.root.mkdir()
        (self.root / "a.txt").write_text("hello\n")
        self.port = free_port()
        self.log = tmp_path / "server.log"
        self._fh = open(self.log, "w")
        self.proc = subprocess.Popen(
            _server_command(self.root, self.port, *extra),
            cwd=str(tmp_path), env=_server_env(), stdout=self._fh, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("server exited early:\n" + self.output())
            if self.get("/api/health", host="127.0.0.1")[0] in (200, 400):  # 400 = up, Host pinned
                return
            time.sleep(0.1)
        raise AssertionError("server did not start:\n" + self.output())

    def output(self):
        self._fh.flush()
        return open(self.log, errors="replace").read()

    def get(self, path, host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("GET", path, skip_host=True)
            conn.putheader("Host", host or "127.0.0.1:%d" % self.port)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            conn.endheaders()
            r = conn.getresponse()
            return r.status, r.read().decode("utf-8", "replace")
        except OSError:
            return 0, ""
        finally:
            conn.close()

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._fh.close()


@pytest.fixture
def run(tmp_path):
    started = []

    def start(*extra):
        r = Running(tmp_path, *extra)
        started.append(r)
        return r

    yield start
    for r in started:
        r.stop()


def test_default_local_server_refuses_a_rebinding_host(run):
    s = run()
    assert s.get("/api/project")[0] == 200
    assert s.get("/api/project", host="evil.example:%d" % s.port)[0] == 400
    assert s.get("/api/file?path=a.txt", host="attacker.test")[0] == 400


def test_allow_remote_generates_a_token_and_requires_it(run):
    s = run("--host", "127.0.0.1", "--allow-remote")
    m = re.search(r"Open: http://127\.0\.0\.1:\d+/\?token=(\S+)", s.output())
    assert m, s.output()
    token = m.group(1)
    assert len(token) >= 16
    assert s.get("/api/project")[0] == 401
    assert s.get("/api/file?path=a.txt")[0] == 401
    status, body = s.get("/api/file?path=a.txt", headers={"Authorization": "Bearer " + token})
    assert status == 200 and "hello" in body
    assert s.get("/api/health")[0] == 200  # liveness stays open


def test_explicit_token_is_used_and_printed(run):
    s = run("--host", "127.0.0.1", "--allow-remote", "--token", "my-fixed-token")
    assert s.get("/api/project?token=my-fixed-token")[0] == 200
    assert s.get("/api/project?token=wrong")[0] == 401


def test_no_token_is_loud(run):
    s = run("--host", "127.0.0.1", "--allow-remote", "--no-token")
    assert s.get("/api/project")[0] == 200
    assert "WITHOUT authentication" in s.output()


def test_token_and_no_token_conflict(tmp_path):
    r = _run_serve(tmp_path, "--allow-remote", "--token", "x", "--no-token", "--port", str(free_port()))
    assert r.returncode == 2 and "mutually exclusive" in r.stdout


def test_allowed_host_pins_a_remote_server(run):
    s = run("--host", "127.0.0.1", "--allow-remote", "--no-token", "--allowed-host", "files.example")
    assert s.get("/api/project", host="files.example")[0] == 200
    assert s.get("/api/project", host="other.example")[0] == 400


def _put(s, origin):
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    try:
        body = b'{"output": {}}'
        conn.request("PUT", "/api/config", body=body, headers={
            "Host": "127.0.0.1:%d" % s.port, "Content-Type": "application/json",
            "Content-Length": str(len(body)), **({"Origin": origin} if origin else {})})
        return conn.getresponse().status
    finally:
        conn.close()


@pytest.mark.parametrize("extra", [(), ("--host", "127.0.0.1", "--allow-remote", "--no-token")])
def test_cross_site_writes_are_refused_in_every_serve_mode(run, extra):
    """Local bind and `--allow-remote --no-token` alike: a foreign Origin cannot write."""
    s = run(*extra)
    assert _put(s, "http://evil.example") == 403
    assert not (s.root / ".files-to-prompt").exists()
    assert _put(s, "http://127.0.0.1:%d" % s.port) == 200      # the real web client still can
    assert _put(s, None) == 200                                 # and so can curl / scripts
