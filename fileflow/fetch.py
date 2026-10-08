"""`fileflow fetch`: download a page as readable text, safely.

This is the **only** module that opens a network connection to a user-chosen address, and it
is CLI-only: the server never imports it (a test enforces that), because a server that fetches
arbitrary URLs for remote clients is an SSRF endpoint.

Threat model: the URL is untrusted (it may come from a README, a shared preset, or a page that
redirects). We must not let it reach the machine's own services, the local network, or cloud
metadata endpoints (``169.254.169.254``), and we must not be slowed or exhausted by a hostile
server. Defences, each with a test:

1.  **Scheme / syntax**: ``http`` and ``https`` only; no ``user:pass@``; no control characters;
    hostnames are IDNA-encoded; invalid ports rejected.
2.  **Address policy**: the hostname is resolved *here*, and **every** address it resolves to
    must be globally routable. Numeric tricks (``2130706433``, ``0x7f.1``, ``[::ffff:127.0.0.1]``)
    are caught because the *resolved address*, not the spelling, is checked. IPv6 forms that
    embed an IPv4 address (IPv4-mapped, NAT64, 6to4) and Teredo are refused outright.
3.  **DNS-rebinding / TOCTOU**: the socket connects to the **address we validated**, not to the
    name again (HTTPS still verifies the certificate against the original hostname via SNI).
4.  **Redirects**: followed by hand (at most 3), and each hop goes through 1-3 again. An
    ``https`` -> ``http`` downgrade is refused.
5.  **Resource caps**: bytes, wall-clock deadline (also against slow-drip servers), content type
    allow-list, ``Accept-Encoding: identity`` (no decompression bombs).
6.  **No ambient authority**: no cookies, no credentials, no proxy variables, no ``Referer``.

``allow_private=True`` (CLI ``--allow-private``) lifts only rule 2, for fetching docs from your own
intranet or localhost deliberately.
"""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections import namedtuple
from urllib.parse import urljoin, urlsplit

from . import __version__
from .errors import FileflowError
from .htmltext import html_to_text, strip_invisible
from .provenance import build_frontmatter

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
MAX_REDIRECTS = 3
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "text/x-markdown")
USER_AGENT = "fileflow/%s (+https://github.com/Riosbald/fileflow; single user-initiated fetch)" % __version__

Fetched = namedtuple("Fetched", ["url", "status", "content_type", "charset", "body", "redirects"])

# IPv6 prefixes that embed or tunnel another address; refusing them avoids reasoning about the
# embedded IPv4 (NAT64 64:ff9b::/96, 6to4 2002::/16, Teredo 2001::/32).
_TUNNEL_NETS = [ipaddress.ip_network(n) for n in ("64:ff9b::/96", "2002::/16", "2001::/32")]


def _blocked(code, message, hint=None):
    return FileflowError(code, message, hint)


def is_public_ip(ip):
    """True only for a globally routable unicast address (``ip`` is an ``ipaddress`` object)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return False  # ::ffff:a.b.c.d  -- judge by refusing, not by unmapping
        if any(ip in net for net in _TUNNEL_NETS):
            return False
        if ip.is_site_local:
            return False
    return bool(ip.is_global) and not (
        ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_unspecified
    )


def resolve(host, port):
    """All addresses ``host`` resolves to, as ``ipaddress`` objects (de-duplicated, order kept)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FileflowError("E_FETCH_NETWORK", "cannot resolve '%s': %s" % (host, exc.strerror or exc), "check the address and your connection")
    except UnicodeError:
        raise _blocked("E_FETCH_BLOCKED", "hostname '%s' is not valid" % host)
    out = []
    for info in infos:
        raw = info[4][0].split("%", 1)[0]  # drop an IPv6 zone id (fe80::1%eth0)
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise _blocked("E_FETCH_BLOCKED", "'%s' resolved to something that is not an IP address" % host)
        if ip not in out:
            out.append(ip)
    if not out:
        raise FileflowError("E_FETCH_NETWORK", "'%s' resolved to no addresses" % host)
    return out


def parse_url(url):
    """Validate the URL's *shape*; returns ``(scheme, ascii_host, port, path_and_query)``."""
    if not isinstance(url, str) or not url.strip():
        raise _blocked("E_FETCH_BLOCKED", "empty URL")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url.strip()):
        raise _blocked("E_FETCH_BLOCKED", "URL contains spaces or control characters")
    parts = urlsplit(url.strip())
    if not parts.scheme:
        # A bare "example.com/page" is a typo, not an attack: say what to type.
        raise _blocked("E_FETCH_BLOCKED", "only http:// and https:// URLs are fetched ('%s' is not a full URL)" % url.strip()[:80],
                       "for example: https://%s" % url.strip().lstrip("/")[:80])
    if parts.scheme.lower() not in ("http", "https"):
        raise _blocked("E_FETCH_BLOCKED", "only http:// and https:// URLs are fetched (got '%s')" % parts.scheme,
                       "file://, ftp://, data: and the like are refused on purpose")
    if "@" in parts.netloc:
        raise _blocked("E_FETCH_BLOCKED", "URLs with embedded credentials (user:pass@host) are refused",
                       "'http://trusted.com@evil.com' style URLs are a classic disguise")
    host = parts.hostname
    if not host:
        raise _blocked("E_FETCH_BLOCKED", "URL has no host")
    try:
        port = parts.port
    except ValueError:
        raise _blocked("E_FETCH_BLOCKED", "URL has an invalid port")
    scheme = parts.scheme.lower()
    port = port or (443 if scheme == "https" else 80)
    try:
        ascii_host = host.encode("idna").decode("ascii") if not _is_ip_literal(host) else host
    except UnicodeError:
        raise _blocked("E_FETCH_BLOCKED", "hostname '%s' is not valid" % host)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, ascii_host, port, path


def _is_ip_literal(host):
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to a pre-validated address; the URL's hostname is only used for the Host header."""

    def __init__(self, host, port, pinned_ip, timeout):
        http.client.HTTPConnection.__init__(self, host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection((str(self._pinned_ip), self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Same, over TLS: the certificate is verified against the original hostname (SNI)."""

    def __init__(self, host, port, pinned_ip, timeout, context):
        http.client.HTTPSConnection.__init__(self, host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection((str(self._pinned_ip), self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _charset(content_type):
    for part in (content_type or "").split(";")[1:]:
        k, _, v = part.strip().partition("=")
        if k.lower() == "charset":
            return v.strip().strip('"\'') or None
    return None


def fetch_url(url, max_bytes=DEFAULT_MAX_BYTES, timeout=DEFAULT_TIMEOUT, allow_private=False,
              resolver=resolve, ssl_context=None, max_redirects=MAX_REDIRECTS):
    """Fetch ``url`` and return :class:`Fetched`. Raises :class:`FileflowError` (``E_FETCH_*``)."""
    deadline = time.monotonic() + timeout
    context = ssl_context or ssl.create_default_context()
    current = url
    redirects = []
    for _hop in range(max_redirects + 1):
        scheme, host, port, path = parse_url(current)
        addrs = resolver(host, port)
        if not allow_private:
            bad = [str(a) for a in addrs if not is_public_ip(a)]
            if bad:
                raise _blocked(
                    "E_FETCH_BLOCKED",
                    "'%s' resolves to a non-public address (%s); refusing to connect" % (host, ", ".join(bad)),
                    "this protects local services and cloud metadata. To fetch from your own network on purpose: --allow-private")
        # Connect to the first validated address. (One address, chosen now: no second DNS lookup.)
        ip = addrs[0]
        conn = (_PinnedHTTPS(host, port, ip, timeout, context) if scheme == "https"
                else _PinnedHTTP(host, port, ip, timeout))
        default_port = 443 if scheme == "https" else 80
        shown = "[%s]" % host if ":" in host else host
        host_header = shown if port == default_port else "%s:%d" % (shown, port)
        timed_out = []
        finished = []
        timer = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FileflowError("E_FETCH_TIMEOUT", "gave up after %.0f seconds" % timeout, "the server was too slow; try again or raise --timeout")
            conn.timeout = remaining
            conn.connect()

            sock = conn.sock

            def _expire():  # hard stop for EVERY phase: a server that drips bytes can't reset a per-recv timeout
                if finished:
                    return
                timed_out.append(True)
                for action in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
                    try:
                        action()
                    except (OSError, AttributeError):
                        pass
            timer = threading.Timer(max(0.01, deadline - time.monotonic()), _expire)
            timer.daemon = True
            timer.start()
            conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host_header)
            conn.putheader("User-Agent", USER_AGENT)
            conn.putheader("Accept", "text/html,text/plain,text/markdown;q=0.9,*/*;q=0.1")
            conn.putheader("Accept-Encoding", "identity")
            conn.putheader("Connection", "close")
            conn.endheaders()
            resp = conn.getresponse()
            status = resp.status
            if status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                resp.close()
                if not location:
                    raise _blocked("E_FETCH_BLOCKED", "redirect (%d) without a Location header" % status)
                nxt = urljoin(current, location)
                if scheme == "https" and nxt.lower().startswith("http://"):
                    raise _blocked("E_FETCH_BLOCKED", "refusing an https -> http downgrade redirect to %s" % nxt)
                redirects.append(nxt)
                current = nxt
                continue
            if status >= 400:
                raise FileflowError("E_FETCH_NETWORK", "server answered HTTP %d for %s" % (status, current))
            if status != 200:
                raise FileflowError("E_FETCH_NETWORK", "unexpected HTTP status %d" % status)
            encoding = (resp.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise FileflowError("E_FETCH_EXTRACT", "server sent '%s' content although identity was requested" % encoding)
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and ctype not in TEXT_TYPES:
                raise FileflowError("E_FETCH_EXTRACT", "content type '%s' is not text" % ctype,
                                    "fileflow fetches HTML, plain text and Markdown only")
            declared = resp.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise _blocked("E_FETCH_BLOCKED", "response is %s bytes; the limit is %s" % (format(int(declared), ","), format(max_bytes, ",")),
                               "raise it with --max-bytes if you are sure")
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FileflowError("E_FETCH_TIMEOUT", "gave up after %.0f seconds" % timeout, "the server was too slow; try again or raise --timeout")
                chunk = resp.read1(8192)  # returns after ONE underlying read (read(n) would wait to fill n bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise _blocked("E_FETCH_BLOCKED", "response exceeded %s bytes; stopped reading" % format(max_bytes, ","),
                                   "raise it with --max-bytes if you are sure")
                chunks.append(chunk)
            if timed_out:  # the watchdog fired while we were finishing: do not trust a possibly truncated body
                raise FileflowError("E_FETCH_TIMEOUT", "gave up after %.0f seconds" % timeout, "the server was too slow; try again or raise --timeout")
            return Fetched(current, status, ctype or "text/html", _charset(resp.getheader("Content-Type")),
                           b"".join(chunks), redirects)
        except FileflowError:
            raise
        except socket.timeout:
            raise FileflowError("E_FETCH_TIMEOUT", "timed out talking to %s" % host, "try again or raise --timeout")
        except ssl.SSLError as exc:
            raise FileflowError("E_FETCH_NETWORK", "TLS failed for %s: %s" % (host, getattr(exc, "reason", None) or exc),
                                "the certificate could not be verified; fileflow never skips verification")
        except (OSError, http.client.HTTPException) as exc:
            if timed_out:
                raise FileflowError("E_FETCH_TIMEOUT", "gave up after %.0f seconds" % timeout, "the server was too slow; try again or raise --timeout")
            raise FileflowError("E_FETCH_NETWORK", "could not fetch %s: %s" % (current, exc))
        finally:
            finished.append(True)
            if timer is not None:
                timer.cancel()
            conn.close()
    raise _blocked("E_FETCH_BLOCKED", "too many redirects (more than %d)" % max_redirects)


MIN_TEXT_CHARS = 200   # an HTML page with less than this is almost always JavaScript-rendered or a wall
SHORT_TEXT_CHARS = 600  # below this the CLI says "have a look at the file"
CHALLENGE_MAX_CHARS = 1500  # only SHORT pages can be an interstitial; a long article may merely mention captchas
_CHALLENGE_TITLES = ("just a moment", "client challenge", "attention required", "access denied", "verify you are human",
                     "are you a robot", "captcha", "security check", "checking your browser", "ddos protection")
_CHALLENGE_PHRASES = ("enable javascript", "enable cookies", "checking your browser", "a required part of this site",
                      "verify you are human", "security check to access", "do not have access to this site",
                      "unusual traffic", "automated requests", "are you a robot")


def looks_like_challenge(title, body):
    """An anti-bot / JavaScript-wall interstitial is not the page that was asked for (pypi.org served one)."""
    if len(body) >= CHALLENGE_MAX_CHARS:
        return False
    t, b = title.lower(), body.lower()
    return any(x in t for x in _CHALLENGE_TITLES) or any(x in b for x in _CHALLENGE_PHRASES)


def _decode(body, charset):
    for enc in ([charset] if charset else []) + ["utf-8"]:
        try:
            return body.decode(enc, errors="replace" if enc == "utf-8" else "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def slug_for(url):
    """A safe, readable file stem from a URL: ``example.com/blog/post`` -> ``example-com-blog-post``."""
    parts = urlsplit(url)
    raw = (parts.hostname or "page") + parts.path
    stem = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:80].strip("-")
    return stem or "page"


def page_to_document(fetched, requested_url, now):
    """Turn a :class:`Fetched` into ``(file_text, info)``: front matter + extracted Markdown.

    ``info`` = ``{title, chars, invisible_removed, hidden_elements}``. Raises ``E_FETCH_EXTRACT``
    when a page yields too little text to be worth saving.
    """
    text = _decode(fetched.body, fetched.charset)
    stats = {"invisible_removed": 0, "hidden_elements": 0}
    if fetched.content_type in ("text/html", "application/xhtml+xml"):
        title, body, stats = html_to_text(text, fetched.url)
        if looks_like_challenge(title, body):
            raise FileflowError(
                "E_FETCH_EXTRACT",
                "%s served an anti-bot / challenge page instead of the content (%s)" % (urlsplit(fetched.url).hostname, title or "no title"),
                "this site needs a real browser; save the text yourself (with a front matter label) or try a different source",
            )
        if len(body) < MIN_TEXT_CHARS:
            raise FileflowError(
                "E_FETCH_EXTRACT",
                "only %d characters of text found at %s" % (len(body), fetched.url),
                "the page is probably rendered by JavaScript, or is a login/paywall; save the text yourself and add a front matter label",
            )
    else:  # text/plain, text/markdown: keep as written, minus invisible characters
        body, removed = strip_invisible(text)
        stats["invisible_removed"] = removed
        title = ""
        if not body.strip():
            raise FileflowError("E_FETCH_EXTRACT", "the response was empty")
    body = body.strip("\n") + "\n"
    fields = [
        ("source_url", fetched.url),
        ("fetched_at", now),
        ("sha256_12", hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]),
        ("provenance", "observed"),
    ]
    if fetched.redirects:
        fields.insert(1, ("requested_url", requested_url))
    if title:
        fields.append(("title", title))
    if stats["invisible_removed"]:
        fields.append(("invisible_chars_removed", stats["invisible_removed"]))
    if stats["hidden_elements"]:
        fields.append(("hidden_elements_removed", stats["hidden_elements"]))
    fields.append(("license_note", "check permissions before republishing; observed = fetched from source_url, not verified as correct"))
    info = {"title": title, "chars": len(body), "invisible_removed": stats["invisible_removed"],
            "hidden_elements": stats["hidden_elements"], "short": len(body) < SHORT_TEXT_CHARS}
    return build_frontmatter(fields) + "\n" + body, info
