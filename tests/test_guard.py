"""Host allowlist, Origin check and token (DNS rebinding / CSWSH / open --allow-remote)."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from fileflow.server.app import build_app  # noqa: E402
from fileflow.server import guard  # noqa: E402

LOCAL = sorted(guard.LOCAL_HOSTS)
# Starlette's WebSocket test client ignores base_url and sends "Host: testserver";
# send the Host explicitly so the *Origin* check is what these tests exercise.
WS = {"Host": "localhost"}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    return tmp_path


def app_for(root, **kw):
    return build_app(project_root=str(root), **kw)


# --- unit: header parsing ------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [("localhost:8090", "localhost"), ("LOCALHOST", "localhost"), ("[::1]:8090", "::1"),
     ("127.0.0.1", "127.0.0.1"), ("evil.example:80", "evil.example"), ("", "")],
)
def test_hostname_of(raw, expected):
    assert guard.hostname_of(raw) == expected


def test_origin_host():
    assert guard.origin_host("http://localhost:8090") == "localhost"
    assert guard.origin_host("null") is None and guard.origin_host(None) is None


# --- DNS rebinding -------------------------------------------------------
def test_foreign_host_header_is_refused_on_localhost(root):
    c = TestClient(app_for(root, allowed_hosts=LOCAL), base_url="http://localhost")
    assert c.get("/api/project").status_code == 200
    r = c.get("/api/project", headers={"Host": "evil.example:8090"})
    assert r.status_code == 400
    assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 400  # even health


def test_extra_allowed_host_is_accepted(root):
    c = TestClient(app_for(root, allowed_hosts=LOCAL + ["dev.local"]), base_url="http://localhost")
    assert c.get("/api/project", headers={"Host": "dev.local:8090"}).status_code == 200


def test_no_allowlist_means_any_host(root):
    c = TestClient(app_for(root), base_url="http://anything.example")
    assert c.get("/api/project").status_code == 200


# --- cross-site WebSocket / unsafe methods -------------------------------
def test_cross_origin_websocket_is_refused(root):
    c = TestClient(app_for(root, allowed_hosts=LOCAL), base_url="http://localhost")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/watch", headers={**WS, "Origin": "http://evil.example"}):
            pass


def test_same_origin_websocket_is_accepted(root):
    c = TestClient(app_for(root, allowed_hosts=LOCAL), base_url="http://localhost")
    with c.websocket_connect("/api/watch", headers={**WS, "Origin": "http://localhost"}) as ws:
        assert ws.receive_json()["ready"] is True


def test_cross_origin_write_is_refused_even_with_correct_host(root):
    c = TestClient(app_for(root, allowed_hosts=LOCAL), base_url="http://localhost")
    r = c.put("/api/config", json={"output": {}}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert not (root / ".files-to-prompt").exists()


# --- token ----------------------------------------------------------------
def test_token_required_for_api_and_pages(root):
    c = TestClient(app_for(root, token="s3cret"), base_url="http://localhost")
    assert c.get("/api/project").status_code == 401
    assert c.get("/api/prompt").status_code == 401
    assert c.get("/api/file", params={"path": "a.txt"}).status_code == 401
    assert "token" in c.get("/").text.lower()


def test_health_needs_no_token(root):
    c = TestClient(app_for(root, token="s3cret"), base_url="http://localhost")
    assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize("how", ["bearer", "query"])
def test_valid_token_is_accepted(root, how):
    c = TestClient(app_for(root, token="s3cret"), base_url="http://localhost")
    if how == "bearer":
        r = c.get("/api/project", headers={"Authorization": "Bearer s3cret"})
    else:
        r = c.get("/api/project", params={"token": "s3cret"})
    assert r.status_code == 200


@pytest.mark.parametrize("bad", ["", "s3cre", "s3cret2", "S3CRET"])
def test_wrong_token_is_refused(root, bad):
    c = TestClient(app_for(root, token="s3cret"), base_url="http://localhost")
    assert c.get("/api/project", headers={"Authorization": "Bearer " + bad}).status_code == 401
    assert c.get("/api/project", params={"token": bad}).status_code == 401


def test_page_visit_with_token_sets_cookie_and_hides_it_from_the_url(root, tmp_path):
    (root / "index.html").write_text("<h1>hi</h1>")
    app = build_app(static_dir=str(root), project_root=str(root), token="s3cret")
    c = TestClient(app, base_url="http://localhost", follow_redirects=False)
    r = c.get("/", params={"token": "s3cret", "x": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/?x=1"
    cookie = r.headers["set-cookie"]
    assert "fileflow_token=s3cret" in cookie and "HttpOnly" in cookie
    # the cookie alone now authorises API calls
    assert c.get("/api/project", headers={"Cookie": "fileflow_token=s3cret"}).status_code == 200


def test_cookie_is_secure_and_cross_site_capable_behind_https_proxy(root):
    (root / "index.html").write_text("x")
    app = build_app(static_dir=str(root), project_root=str(root), token="t")
    c = TestClient(app, base_url="http://localhost", follow_redirects=False)
    cookie = c.get("/", params={"token": "t"}, headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"]
    assert "Secure" in cookie and "SameSite=None" in cookie


def test_websocket_needs_token_too(root):
    c = TestClient(app_for(root, token="s3cret"), base_url="http://localhost")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/watch"):
            pass
    with c.websocket_connect("/api/watch?token=s3cret") as ws:
        assert ws.receive_json()["ready"] is True


# --- the Origin check must not depend on a token/host list being configured ---
def test_enforce_origin_without_hosts_or_token(root):
    c = TestClient(app_for(root, enforce_origin=True), base_url="http://anything.example")
    assert c.get("/api/project").status_code == 200            # no host list, no token: open as asked
    r = c.put("/api/config", json={"output": {}}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and not (root / ".files-to-prompt").exists()
    ok = c.put("/api/config", json={"output": {}}, headers={"Origin": "http://anything.example"})
    assert ok.status_code == 200                                # same-origin writes still work


def test_library_default_stays_unguarded(root):
    c = TestClient(app_for(root), base_url="http://localhost")
    assert c.put("/api/config", json={"output": {}}, headers={"Origin": "http://evil.example"}).status_code == 200
