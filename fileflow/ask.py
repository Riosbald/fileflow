"""`fileflow ask`: send one prompt to a model and get one answer back. Opt-in, single-shot.

Scope (ADR-010): prompt in, answer out. **No tools, no file writes, no shell, no memory, no loop.**
The core of fileflow stays deterministic and key-free; this module is only imported by the ``ask``
command, never by ``prompt``, ``serve``, ``check`` or ``fetch``.

Providers: ``openai`` (any OpenAI-compatible ``/chat/completions`` endpoint: OpenAI, hosted
look-alikes, and local servers such as Ollama / llama.cpp / vLLM) and ``anthropic`` (``/v1/messages``).

Security rules, each with a test:

* **The destination and the key come from you, never from a repository.** ``base_url`` is read from the
  CLI flag or your environment (``OPENAI_BASE_URL`` / ``ANTHROPIC_BASE_URL``), not from
  ``.files-to-prompt``: a cloned repo must not be able to point your API key at a server it controls.
* **HTTPS only**, except for loopback (local models). No ``user:pass@`` in URLs.
* **Redirects are refused.** Python's HTTP client forwards request headers (including your API key)
  to a redirect target; an API endpoint has no business redirecting.
* **The key is never printed**, and is scrubbed from every error message.
* Responses are size-capped. The response *body* is read under an overall deadline; the connect and
  header phase is bounded by the socket timeout only (a provider that drips headers could hold the call
  open: acceptable for an endpoint you chose and trust with your key, unlike `fetch`, whose URL is untrusted).
* Sending project content to a non-local host needs consent (see ``cli.py``); that is policy, not
  transport, so it lives with the command.

Nothing here has been run against a live provider API: both adapters are exercised against local mock
servers shaped from the providers' documented request/response formats. Treat the first real call as a test.
"""

import hashlib
import ipaddress
import json
import time
import urllib.error
import urllib.request
from collections import namedtuple
from urllib.parse import urlsplit

from .errors import FileflowError
from .presets import ASK_PROVIDERS

PROVIDERS = ASK_PROVIDERS
DEFAULT_BASE = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
DEFAULT_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
BASE_URL_ENV = {"openai": "OPENAI_BASE_URL", "anthropic": "ANTHROPIC_BASE_URL"}
ANTHROPIC_VERSION = "2023-06-01"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_OUTPUT = 4096

ModelResult = namedtuple("ModelResult", ["text", "input_tokens", "output_tokens", "stop_reason", "model", "host", "provider"])


# ---- destination policy -------------------------------------------------------------------------------
def host_of(url):
    return (urlsplit(url).hostname or "").lower()


def is_loopback_host(host):
    """``localhost`` or a loopback IP literal. (Other names are treated as remote, even if they resolve locally.)"""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def validate_base_url(url):
    """Return the normalised base URL or raise ``E_MODEL_CONFIG``. https required unless loopback."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FileflowError("E_MODEL_CONFIG", "base URL '%s' is not a valid http(s) URL" % url, "e.g. https://api.openai.com/v1 or http://localhost:11434/v1")
    if "@" in parts.netloc:
        raise FileflowError("E_MODEL_CONFIG", "base URLs with embedded credentials are refused", "pass the key through the environment")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise FileflowError("E_MODEL_CONFIG", "base URL contains spaces or control characters")
    if parts.scheme == "http" and not is_loopback_host(parts.hostname.lower()):
        raise FileflowError("E_MODEL_CONFIG", "refusing http:// to %s: your API key and project would travel unencrypted" % parts.hostname,
                            "use https://, or a local model on localhost")
    return url.rstrip("/")


def resolve_base_url(provider, cli_value, environ):
    """CLI flag, else the provider's standard environment variable, else the provider default. Never a config file."""
    if cli_value is not None and not cli_value.strip():
        # An explicitly empty --base-url is almost always an unset shell variable. Falling back to the
        # default host would quietly decide where your files go, so refuse instead.
        raise FileflowError("E_MODEL_CONFIG", "--base-url was given but is empty", "check the variable you expanded into it")
    return validate_base_url(cli_value or environ.get(BASE_URL_ENV[provider]) or DEFAULT_BASE[provider])


def resolve_key(provider, base_url, key_env, environ):
    """The API key from the environment (name chosen on the CLI), or ``None`` for a local server that needs none."""
    name = key_env or DEFAULT_KEY_ENV[provider]
    key = environ.get(name, "")
    if key and (key != key.strip() or any(ord(c) < 0x21 or ord(c) == 0x7F for c in key)):
        raise FileflowError("E_MODEL_NOKEY", "the key in $%s contains whitespace or control characters" % name,
                            "re-export it without trailing newlines or spaces")
    if key:
        return key
    if is_loopback_host(host_of(base_url)) and provider == "openai":
        return None  # local OpenAI-compatible servers (Ollama etc.) usually need no key
    raise FileflowError("E_MODEL_NOKEY", "no API key: $%s is not set" % name,
                        "export %s=...  (or choose another variable with --api-key-env)" % name)


# ---- request / response shapes -----------------------------------------------------------------------------
def build_request(provider, base_url, model, user_text, system=None, max_output_tokens=DEFAULT_MAX_OUTPUT):
    """``(url, headers_without_key, json_body_bytes)`` for one non-streaming call."""
    if provider == "openai":
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user_text}]
        body = {"model": model, "messages": messages}
        # OpenAI's own endpoint wants max_completion_tokens; compatible servers all understand max_tokens.
        body["max_completion_tokens" if host_of(base_url) == "api.openai.com" else "max_tokens"] = max_output_tokens
        url = base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
    elif provider == "anthropic":
        body = {"model": model, "max_tokens": max_output_tokens, "messages": [{"role": "user", "content": user_text}]}
        if system:
            body["system"] = system
        url = base_url + "/v1/messages"
        headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
    else:
        raise FileflowError("E_MODEL_CONFIG", "unknown provider '%s'" % provider, "choose one of: " + ", ".join(PROVIDERS))
    return url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8")


def add_key(provider, headers, key):
    headers = dict(headers)
    if key:
        if provider == "anthropic":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = "Bearer " + key
    return headers


def _text_parts(content):
    """A message's text: a string, or a list of ``{"type": "text", "text": ...}`` parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") == "text")
    return None


def parse_response(provider, data, host):
    """Parse a decoded JSON response into :class:`ModelResult`, or raise ``E_MODEL_RESPONSE``."""
    def bad(why):
        return FileflowError("E_MODEL_RESPONSE", "the response from %s was not in the expected shape (%s)" % (host, why),
                             "is --provider right for this endpoint?")
    if not isinstance(data, dict):
        raise bad("not a JSON object")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if provider == "openai":
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise bad("no choices")
        message = choices[0].get("message")
        text = _text_parts(message.get("content")) if isinstance(message, dict) else None
        if text is None:
            raise bad("no message content")
        return ModelResult(text, usage.get("prompt_tokens"), usage.get("completion_tokens"), choices[0].get("finish_reason"),
                           data.get("model"), host, provider)
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise bad("no content blocks")
    text = _text_parts(blocks)
    if text is None or (not text and not blocks):
        raise bad("no text content")
    return ModelResult(text, usage.get("input_tokens"), usage.get("output_tokens"), data.get("stop_reason"), data.get("model"), host, provider)


# ---- transport --------------------------------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would re-send our headers, API key included, to wherever a 3xx points. Refuse instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _scrub(text, key):
    text = str(text)
    if key:
        text = text.replace(key, "[redacted]")
    return text


def _provider_message(body, key):
    """A short, key-free description from a provider's error body (``{"error": {"message": ...}}`` and friends)."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        err = data.get("error", data) if isinstance(data, dict) else {}
        msg = err.get("message") if isinstance(err, dict) else None
        kind = err.get("type") if isinstance(err, dict) else None
        text = " ".join(x for x in (kind, msg) if isinstance(x, str))
    except (ValueError, AttributeError):
        text = body.decode("utf-8", "replace")
    return _scrub(text.strip().replace("\n", " "), key)[:300]


def call_model(provider, base_url, model, user_text, key, system=None, max_output_tokens=DEFAULT_MAX_OUTPUT,
               timeout=DEFAULT_TIMEOUT, opener=None):
    """Make the one call and return :class:`ModelResult`. All failures are classified ``E_MODEL_*``."""
    host = host_of(base_url)
    url, headers, body = build_request(provider, base_url, model, user_text, system, max_output_tokens)
    headers = add_key(provider, headers, key)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    # An explicit opener: no redirects (see _NoRedirect). Proxies from the environment are still honoured.
    opener = opener or urllib.request.build_opener(_NoRedirect)
    deadline = time.monotonic() + timeout
    try:
        with opener.open(req, timeout=timeout) as resp:
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FileflowError("E_MODEL_TIMEOUT", "no complete answer from %s within %.0f seconds" % (host, timeout), "raise --timeout")
                chunk = resp.read1(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise FileflowError("E_MODEL_RESPONSE", "response from %s exceeded %d bytes; stopped reading" % (host, MAX_RESPONSE_BYTES))
                chunks.append(chunk)
    except FileflowError:
        raise
    except urllib.error.HTTPError as exc:
        detail = _provider_message(exc.read(2000) if hasattr(exc, "read") else b"", key)
        if exc.code in (301, 302, 303, 307, 308):
            raise FileflowError("E_MODEL_NETWORK", "%s answered with a redirect; refused (following it would forward your API key)" % host,
                                "check --base-url")
        if exc.code in (401, 403):
            raise FileflowError("E_MODEL_AUTH", "%s rejected the API key (HTTP %d)%s" % (host, exc.code, ": " + detail if detail else ""),
                                "check the key and that it may use this model")
        if exc.code == 429:
            raise FileflowError("E_MODEL_RATE", "%s says slow down or out of quota (HTTP 429)%s" % (host, ": " + detail if detail else ""),
                                "wait and retry; fileflow does not retry automatically (a retry costs money)")
        raise FileflowError("E_MODEL_NETWORK", "%s answered HTTP %d%s" % (host, exc.code, ": " + detail if detail else ""))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise FileflowError("E_MODEL_TIMEOUT", "timed out talking to %s" % host, "raise --timeout")
        raise FileflowError("E_MODEL_NETWORK", "could not reach %s: %s" % (host, _scrub(reason, key)))
    except (TimeoutError, OSError) as exc:
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            raise FileflowError("E_MODEL_TIMEOUT", "timed out talking to %s" % host, "raise --timeout")
        raise FileflowError("E_MODEL_NETWORK", "connection to %s failed: %s" % (host, _scrub(exc, key)))
    try:
        data = json.loads(b"".join(chunks).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FileflowError("E_MODEL_RESPONSE", "%s did not return JSON" % host, "is --base-url a model API endpoint?")
    return parse_response(provider, data, host)


def sha12(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
