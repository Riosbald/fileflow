"""Request guard for ``fileflow serve``: Host allowlist, Origin check, token.

Why this exists
---------------
* **DNS rebinding.** A web page can point ``evil.example`` at ``127.0.0.1`` and
  then read ``/api/*`` as if it were same-origin. The browser still sends
  ``Host: evil.example``, so refusing hosts we did not expect closes that door.
* **Cross-site WebSockets.** WebSockets are not covered by the same-origin
  policy, so a page on another site can open ``ws://localhost:8090/api/watch``.
  We require ``Origin`` (when present) to match the ``Host`` or an allowed host.
* **``--allow-remote`` had no authentication.** With a token configured, every
  request except the health probe must present it (``Authorization: Bearer``,
  an ``fileflow_token`` cookie, or ``?token=``).

Implemented as a plain ASGI middleware so HTTP *and* WebSocket connections
pass through exactly the same checks.
"""

import hmac
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
COOKIE = "fileflow_token"
# Liveness probe: reveals only a version string, needs no token (still Host-checked).
OPEN_PATHS = frozenset({"/api/health"})


def hostname_of(value):
    """Lower-cased host part of a ``Host`` header / Origin authority, port removed."""
    value = (value or "").strip().lower()
    if value.startswith("["):  # [::1]:8090
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value  # bare IPv6 without brackets, or no port


def origin_host(origin):
    """Host part of an ``Origin`` header (``scheme://host[:port]``); ``None`` if unusable."""
    if not origin or origin == "null":
        return None
    rest = origin.split("://", 1)[-1]
    return hostname_of(rest.split("/", 1)[0])


class Guard:
    """ASGI middleware enforcing the Host allowlist, Origin check and token."""

    def __init__(self, app, allowed_hosts=None, token=None):
        self.app = app
        self.allowed_hosts = (
            None if allowed_hosts is None else frozenset(h.lower() for h in allowed_hosts)
        )
        self.token = token

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _headers(scope):
        return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

    def _host_ok(self, headers):
        if self.allowed_hosts is None:
            return True
        return hostname_of(headers.get("host")) in self.allowed_hosts

    def _origin_ok(self, headers):
        origin = headers.get("origin")
        if origin is None:
            return True  # not a browser cross-site request (curl, CLI, same-origin GET)
        oh = origin_host(origin)
        if oh is None:
            return False
        if oh == hostname_of(headers.get("host")):
            return True
        return self.allowed_hosts is not None and oh in self.allowed_hosts

    def _token_from(self, scope, headers):
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            yield auth[7:].strip(), "header"
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            pass
        if COOKIE in cookie:
            yield cookie[COOKIE].value, "cookie"
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        for value in query.get("token", []):
            yield value, "query"

    def _authorised(self, scope, headers):
        """Return the credential channel that matched, or ``None``."""
        for candidate, channel in self._token_from(scope, headers):
            if hmac.compare_digest(candidate.encode(), self.token.encode()):
                return channel
        return None

    # -- ASGI ------------------------------------------------------------
    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = self._headers(scope)
        method = scope.get("method", "GET")

        if not self._host_ok(headers):
            await self._deny(kind, send, 400, "Invalid Host header")
            return
        unsafe = kind == "websocket" or method not in ("GET", "HEAD", "OPTIONS")
        if unsafe and not self._origin_ok(headers):
            await self._deny(kind, send, 403, "Cross-origin request refused")
            return

        if self.token is not None and scope.get("path") not in OPEN_PATHS:
            channel = self._authorised(scope, headers)
            if channel is None:
                await self._deny(kind, send, 401, "A token is required (?token=…)", html=True)
                return
            if channel == "query" and kind == "http" and not scope["path"].startswith("/api/"):
                await self._set_cookie_and_redirect(scope, headers, send)
                return
        await self.app(scope, receive, send)

    async def _deny(self, kind, send, status, message, html=False):
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if html:
            body = (
                "<!doctype html><meta charset=utf-8><title>fileflow</title>"
                "<body style='font:16px system-ui;margin:3rem'>"
                "<h1>fileflow</h1><p>This server needs an access token.</p>"
                "<p>Open the URL printed when the server started "
                "(it ends in <code>?token=…</code>).</p></body>"
            ).encode()
            ctype = b"text/html; charset=utf-8"
        else:
            body = ('{"detail":"%s"}' % message).encode()
            ctype = b"application/json"
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _set_cookie_and_redirect(self, scope, headers, send):
        """Swap ``?token=`` for an HttpOnly cookie so the secret leaves the URL bar."""
        secure = headers.get("x-forwarded-proto", scope.get("scheme", "http")) == "https"
        attrs = "Path=/; HttpOnly; " + ("SameSite=None; Secure" if secure else "SameSite=Lax")
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        query.pop("token", None)
        rest = "&".join("%s=%s" % (k, v) for k, vs in query.items() for v in vs)
        location = scope["path"] + ("?" + rest if rest else "")
        await send(
            {
                "type": "http.response.start",
                "status": 303,
                "headers": [
                    (b"location", location.encode("latin-1")),
                    (b"set-cookie", ("%s=%s; %s" % (COOKIE, self.token, attrs)).encode()),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})
