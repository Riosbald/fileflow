"""`fileflow ask`: single-shot model call. Adapters, safety rules, consent, provenance.

Everything runs against local mock servers shaped from the providers' documented request/response formats;
nothing here touches a real provider API. The key is a recognisable sentinel so any leak is caught.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

import fileflow.ask as A
from fileflow.cli import cli
from fileflow.errors import FileflowError
from fileflow.provenance import read_provenance

KEY = "sk-TESTKEY-do-not-leak-0123456789"
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


# ---- mock provider ------------------------------------------------------------------------------------------
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        srv = self.server
        srv.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": raw})
        status, headers, body = srv.responder(self.path, srv.seen[-1])
        try:
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            if callable(body):
                self.send_header("Connection", "close")
                self.end_headers()
                body(self.wfile)
                return
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def openai_ok(text="the answer", finish="stop", usage=(11, 7)):
    return (200, {"Content-Type": "application/json"}, json.dumps({
        "model": "m-1", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)}}).encode())


def anthropic_ok(text="the answer", stop="end_turn", usage=(11, 7)):
    return (200, {"Content-Type": "application/json"}, json.dumps({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "c-1", "content": [{"type": "text", "text": text}],
        "stop_reason": stop, "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}}).encode())


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.seen = []
    srv.responder = lambda path, req: openai_ok()
    srv.base = "http://127.0.0.1:%d" % srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n")
    (tmp_path / "README.md").write_text("# demo\n")
    monkeypatch.chdir(tmp_path)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "FILEFLOW_MODEL", "MY_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def run(args, env=None, input=None):
    try:
        runner = CliRunner(mix_stderr=False)
    except TypeError:
        runner = CliRunner()
    return runner.invoke(cli, [str(a) for a in args], env=env or {}, input=input)


def ask(server, proj, *extra, provider="openai", env=None, **kw):
    base = server.base + ("/v1" if provider == "openai" else "")
    args = ["ask", "--provider", provider, "--model", "test-model", "--base-url", base, "src",
            "--instruction", "Explain it."] + list(extra)
    e = {"OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY": KEY}
    e.update(env or {})
    return run(args, env=e, **kw)


def no_leak(*results):
    for r in results:
        assert KEY not in r.stdout and KEY not in r.stderr, "the API key leaked into output"


# ---- request shapes (pure) -----------------------------------------------------------------------------------
def test_openai_request_shape():
    url, headers, body = A.build_request("openai", "http://localhost:11434/v1", "m", "hello", system="be brief", max_output_tokens=50)
    b = json.loads(body)
    assert url == "http://localhost:11434/v1/chat/completions" and headers["Content-Type"] == "application/json"
    assert b == {"model": "m", "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hello"}], "max_tokens": 50}


def test_openai_own_endpoint_uses_max_completion_tokens():
    _, _, body = A.build_request("openai", "https://api.openai.com/v1", "m", "hi", max_output_tokens=9)
    b = json.loads(body)
    assert b["max_completion_tokens"] == 9 and "max_tokens" not in b and b["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_request_shape():
    url, headers, body = A.build_request("anthropic", "https://api.anthropic.com", "c", "hello", system="sys", max_output_tokens=77)
    b = json.loads(body)
    assert url == "https://api.anthropic.com/v1/messages" and headers["anthropic-version"] == "2023-06-01"
    assert b == {"model": "c", "max_tokens": 77, "messages": [{"role": "user", "content": "hello"}], "system": "sys"}
    assert "system" not in json.loads(A.build_request("anthropic", "https://x.example", "c", "hi")[2])


def test_the_key_goes_in_the_documented_header_only():
    assert A.add_key("openai", {}, "K") == {"Authorization": "Bearer K"}
    assert A.add_key("anthropic", {}, "K") == {"x-api-key": "K"}
    assert A.add_key("openai", {"a": "b"}, None) == {"a": "b"}


# ---- end to end, both providers ---------------------------------------------------------------------------------
def test_openai_compatible_roundtrip(server, proj):
    r = ask(server, proj)
    assert r.exit_code == 0, r.stderr
    assert r.stdout == "the answer\n"
    req = server.seen[0]
    assert req["path"] == "/v1/chat/completions" and req["headers"]["authorization"] == "Bearer " + KEY
    body = json.loads(req["body"])
    user = body["messages"][-1]["content"]
    assert body["model"] == "test-model" and "# Task\n\nExplain it." in user and "print('hello')" in user
    assert "in 11, out 7" in r.stderr and "stop: stop" in r.stderr and "generated text, not verified fact" in r.stderr
    no_leak(r)


def test_anthropic_roundtrip(server, proj):
    server.responder = lambda path, req: anthropic_ok("claude says hi")
    r = ask(server, proj, provider="anthropic")
    assert r.exit_code == 0, r.stderr
    assert r.stdout == "claude says hi\n"
    req = server.seen[0]
    assert req["path"] == "/v1/messages" and req["headers"]["x-api-key"] == KEY and req["headers"]["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req["headers"]
    assert json.loads(req["body"])["max_tokens"] == 4096
    no_leak(r)


def test_content_given_as_a_list_of_text_parts_is_joined(server, proj):
    server.responder = lambda p, q: (200, {"Content-Type": "application/json"}, json.dumps(
        {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]}).encode())
    assert ask(server, proj).stdout == "ab\n"


def test_local_openai_compatible_server_needs_no_key(server, proj):
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", server.base + "/v1", "src", "--instruction", "x"])
    assert r.exit_code == 0 and "authorization" not in server.seen[0]["headers"]


def test_anthropic_still_requires_a_key_even_locally(server, proj):
    r = run(["ask", "--provider", "anthropic", "--model", "m", "--base-url", server.base, "src", "--instruction", "x"])
    assert r.exit_code == 2 and "E_MODEL_NOKEY" in r.stderr and server.seen == []


# ---- the key: sources, shape, secrecy -------------------------------------------------------------------------------
def test_missing_key_is_a_clear_error_before_any_file_is_read(proj):
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x"])
    assert r.exit_code == 2 and "E_MODEL_NOKEY" in r.stderr and "OPENAI_API_KEY" in r.stderr


def test_key_env_name_is_chosen_on_the_command_line(server, proj):
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", server.base + "/v1", "--api-key-env", "MY_KEY", "src", "--instruction", "x"],
            env={"MY_KEY": KEY})
    assert r.exit_code == 0 and server.seen[0]["headers"]["authorization"] == "Bearer " + KEY


@pytest.mark.parametrize("bad", [KEY + "\n", " " + KEY, KEY + " x", "a\tb", "k\x01"])
def test_a_key_with_whitespace_or_control_characters_is_refused(server, proj, bad):
    r = ask(server, proj, env={"OPENAI_API_KEY": bad})
    assert r.exit_code == 2 and "E_MODEL_NOKEY" in r.stderr and server.seen == []


def test_key_is_never_in_any_output_even_when_the_provider_echoes_it(server, proj, tmp_path):
    server.responder = lambda p, q: (401, {"Content-Type": "application/json"}, json.dumps(
        {"error": {"type": "invalid_api_key", "message": "bad key " + KEY + " for you"}}).encode())
    r = ask(server, proj, "--receipt", "--receipt-file", "r.json")
    assert r.exit_code == 4 and "E_MODEL_AUTH" in r.stderr and "[redacted]" in r.stderr
    no_leak(r)
    assert not os.path.exists("r.json") or KEY not in open("r.json").read()


def test_key_never_reaches_the_receipt_or_saved_answer(server, proj):
    r = ask(server, proj, "--receipt-file", "r.json", "--save", "notes/answer.md")
    assert r.exit_code == 0
    for f in ("r.json", "notes/answer.md"):
        assert KEY not in open(f).read()
    no_leak(r)


# ---- destination policy ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://api.example.com/v1", "http://10.0.0.5/v1", "ftp://x/y", "file:///etc/passwd", "https://u:p@api.example.com/",
                                 "http://evil.test@127.0.0.1/", "not a url", "https:///nohost", "http://localhost.evil.com/v1"])
def test_unsafe_base_urls_are_refused_before_any_connection(proj, url, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")))
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", url, "src", "--instruction", "x"], env={"OPENAI_API_KEY": KEY})
    assert r.exit_code == 2 and "E_MODEL_CONFIG" in r.stderr
    no_leak(r)


@pytest.mark.parametrize("url", ["http://localhost:1/v1", "http://127.0.0.1/v1", "http://[::1]:8/v1", "https://api.example.com/v1", "http://foo.localhost/v1"])
def test_acceptable_base_urls(url):
    assert A.validate_base_url(url) == url


def test_base_url_comes_from_cli_or_environment_but_never_from_config(proj):
    (proj / ".files-to-prompt").write_text('version = 1\n[ask]\nprovider = "openai"\nmodel = "m"\nbase_url = "https://evil.example/v1"\napi_key_env = "AWS_SECRET_ACCESS_KEY"\n')
    r = run(["ask", "src", "--instruction", "x"], env={"OPENAI_API_KEY": KEY})          # no --yes, non-interactive
    assert r.exit_code == 2 and "E_MODEL_CONSENT" in r.stderr
    assert "api.openai.com" in r.stderr and "evil.example" not in r.stderr               # the config's base_url was ignored
    assert A.resolve_base_url("openai", None, {"OPENAI_BASE_URL": "https://mine.example/v1"}) == "https://mine.example/v1"
    assert A.resolve_base_url("openai", "https://cli.example", {"OPENAI_BASE_URL": "https://env.example"}) == "https://cli.example"


def test_config_supplies_provider_model_and_output_cap(server, proj):
    (proj / ".files-to-prompt").write_text('version = 1\n[ask]\nprovider = "openai"\nmodel = "from-config"\nmax_output_tokens = 321\n')
    r = run(["ask", "--base-url", server.base + "/v1", "src", "--instruction", "x"])
    assert r.exit_code == 0
    body = json.loads(server.seen[0]["body"])
    assert body["model"] == "from-config" and body["max_tokens"] == 321
    run(["ask", "--base-url", server.base + "/v1", "--model", "cli-wins", "--max-output-tokens", "5", "src", "--instruction", "x"])
    assert json.loads(server.seen[1]["body"])["model"] == "cli-wins" and json.loads(server.seen[1]["body"])["max_tokens"] == 5


def test_model_can_come_from_the_environment_and_there_is_no_default(server, proj):
    r = run(["ask", "--provider", "openai", "--base-url", server.base + "/v1", "src", "--instruction", "x"])
    assert r.exit_code == 2 and "E_MODEL_CONFIG" in r.stderr and "no default" in r.stderr
    r = run(["ask", "--provider", "openai", "--base-url", server.base + "/v1", "src", "--instruction", "x"], env={"FILEFLOW_MODEL": "envmodel"})
    assert r.exit_code == 0 and json.loads(server.seen[0]["body"])["model"] == "envmodel"


def test_provider_is_required(proj):
    r = run(["ask", "--model", "m", "src", "--instruction", "x"])
    assert r.exit_code == 2 and "E_MODEL_CONFIG" in r.stderr and "provider" in r.stderr


def test_bad_ask_section_types(proj):
    for body in ('provider = "gemini"', 'model = 3', 'max_output_tokens = 0', 'max_output_tokens = "lots"'):
        (proj / ".files-to-prompt").write_text("version = 1\n[ask]\n" + body + "\n")
        r = run(["ask", "src", "--instruction", "x", "--provider", "openai", "--model", "m"], env={"OPENAI_API_KEY": KEY})
        assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr, body


# ---- redirects: the key must never follow one ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirects_are_refused_and_the_key_is_never_forwarded(server, proj, code):
    """urllib follows 301/302/303 on a POST (rewriting it to a GET and re-sending the headers, key included);
    307/308 it would not follow. Every one must be refused outright, and the target must never be contacted."""
    seen_methods = []

    class Spy(H):
        def do_GET(self):  # a followed 30x would arrive here as a GET carrying the key
            seen_methods.append(("GET", self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    spy = ThreadingHTTPServer(("127.0.0.1", 0), Spy)
    spy.seen, spy.responder = [], (lambda p, q: openai_ok())
    threading.Thread(target=spy.serve_forever, daemon=True).start()
    try:
        target = "http://127.0.0.1:%d/steal" % spy.server_address[1]
        server.responder = lambda p, q: (code, {"Location": target}, b"")
        r = ask(server, proj)
        assert r.exit_code == 4 and "E_MODEL_NETWORK" in r.stderr and "redirect" in r.stderr
        time.sleep(0.2)
        assert spy.seen == [] and seen_methods == [], "the redirect target was contacted (and would have received the API key)"
        no_leak(r)
    finally:
        spy.shutdown()
        spy.server_close()


# ---- error classification ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,code", [(401, "E_MODEL_AUTH"), (403, "E_MODEL_AUTH"), (429, "E_MODEL_RATE"), (500, "E_MODEL_NETWORK"), (404, "E_MODEL_NETWORK")])
def test_http_errors_are_classified(server, proj, status, code):
    server.responder = lambda p, q: (status, {"Content-Type": "application/json"}, json.dumps({"error": {"message": "nope"}}).encode())
    r = ask(server, proj)
    assert r.exit_code == 4 and code in r.stderr and "nope" in r.stderr and r.stdout == ""


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"choices": []}', b'{"choices": [{"message": {}}]}', b'{"content": "x"}'])
def test_unexpected_response_shapes(server, proj, body):
    server.responder = lambda p, q: (200, {"Content-Type": "application/json"}, body)
    r = ask(server, proj)
    assert r.exit_code == 4 and "E_MODEL_RESPONSE" in r.stderr and r.stdout == ""


def test_timeout(server, proj):
    def slow(path, req):
        time.sleep(2.5)
        return openai_ok()
    server.responder = slow
    t0 = time.time()
    r = ask(server, proj, "--timeout", "0.7")
    assert r.exit_code == 4 and "E_MODEL_TIMEOUT" in r.stderr and time.time() - t0 < 2.4


def test_a_body_that_drips_is_stopped_by_the_overall_deadline(server, proj):
    def drip(w):
        w.write(b'{"choices":')
        w.flush()
        for _ in range(60):
            time.sleep(0.1)
            w.write(b" ")
            w.flush()
    server.responder = lambda p, q: (200, {"Content-Type": "application/json"}, drip)
    t0 = time.time()
    r = ask(server, proj, "--timeout", "1")
    assert r.exit_code == 4 and "E_MODEL_TIMEOUT" in r.stderr and time.time() - t0 < 3.5


def test_oversize_response_is_capped(server, proj, monkeypatch):
    monkeypatch.setattr(A, "MAX_RESPONSE_BYTES", 2000)
    server.responder = lambda p, q: openai_ok("x" * 10_000)
    r = ask(server, proj)
    assert r.exit_code == 4 and "E_MODEL_RESPONSE" in r.stderr and "exceeded" in r.stderr


def test_connection_refused_is_a_network_error(proj):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "http://127.0.0.1:%d/v1" % port, "src", "--instruction", "x"])
    assert r.exit_code == 4 and "E_MODEL_NETWORK" in r.stderr


def test_truncated_answer_is_flagged(server, proj):
    server.responder = lambda p, q: openai_ok("cut", finish="length")
    r = ask(server, proj)
    assert r.exit_code == 0 and "cut off at the output limit" in r.stderr
    server.responder = lambda p, q: anthropic_ok("cut", stop="max_tokens")
    assert "cut off" in ask(server, proj, provider="anthropic").stderr


# ---- consent and egress ---------------------------------------------------------------------------------------------------------
def test_remote_destination_needs_consent_and_nothing_is_sent_without_it(proj, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")))
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x"],
            env={"OPENAI_API_KEY": KEY})
    assert r.exit_code == 2 and "E_MODEL_CONSENT" in r.stderr
    assert "SEND your project content to a third party" in r.stderr and "api.example.invalid" in r.stderr and "nothing left your machine" in r.stderr
    assert "1 file(s)" in r.stderr
    no_leak(r)


def test_yes_goes_ahead_to_a_remote_host(proj):
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x", "--yes"],
            env={"OPENAI_API_KEY": KEY})
    assert "E_MODEL_NETWORK" in r.stderr and "api.example.invalid" in r.stderr     # tried to connect (DNS fails: .invalid)
    no_leak(r)


def test_interactive_consent_can_be_declined(proj, monkeypatch):
    import fileflow.cli as cm
    monkeypatch.setattr(cm, "_interactive", lambda: True)
    monkeypatch.setattr(cm.click, "confirm", lambda *a, **k: False)
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x"],
            env={"OPENAI_API_KEY": KEY})
    assert r.exit_code == 2 and "you declined" in r.stderr


def test_interactive_consent_can_be_accepted(server, proj, monkeypatch):
    import fileflow.cli as cm
    asked = []
    monkeypatch.setattr(cm, "_interactive", lambda: True)
    monkeypatch.setattr(cm.click, "confirm", lambda *a, **k: asked.append(a) or True)
    monkeypatch.setattr(A, "is_loopback_host", lambda h: False)          # treat the mock as a remote host for the consent policy
    monkeypatch.setattr(A, "validate_base_url", lambda u: u)
    r = ask(server, proj)
    assert r.exit_code == 0 and len(asked) == 1 and "third party" in r.stderr


def test_an_empty_base_url_is_an_error_not_a_silent_default(proj, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")))
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "", "src", "--instruction", "x", "--yes"], env={"OPENAI_API_KEY": KEY})
    assert r.exit_code == 2 and "E_MODEL_CONFIG" in r.stderr and "empty" in r.stderr
    # an empty ENVIRONMENT variable is ordinary "unset": the provider default applies
    assert A.resolve_base_url("openai", None, {"OPENAI_BASE_URL": ""}) == "https://api.openai.com/v1"


def test_local_models_need_no_consent(server, proj):
    r = ask(server, proj)
    assert r.exit_code == 0 and "Sending to a local model" in r.stderr and "third party" not in r.stderr


# ---- secrets: fail closed when data leaves the machine ----------------------------------------------------------------------------------
@pytest.fixture
def leaky(proj):
    (proj / "src" / "creds.py").write_text('AWS = "%s"\n' % AWS)
    return proj


def test_remote_destination_defaults_to_blocking_secrets(leaky):
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x", "--yes"],
            env={"OPENAI_API_KEY": KEY})
    assert r.exit_code == 3 and "E_SECRET_FOUND" in r.stderr and "creds.py" in r.stderr and AWS not in r.stderr and "SEND" not in r.stderr


def test_a_repository_config_cannot_weaken_the_remote_default(leaky):
    (leaky / ".files-to-prompt").write_text('version = 1\n[secrets]\nmode = "off"\n[presets.p]\npaths = ["src"]\nsecrets = "off"\ninstruction = "x"\n')
    for extra in ([], ["--preset", "p"]):
        r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x", "--yes"] + extra,
                env={"OPENAI_API_KEY": KEY})
        assert r.exit_code == 3 and "E_SECRET_FOUND" in r.stderr, extra


def test_the_command_line_can_still_choose_to_exclude_or_allow(server, leaky):
    base = ["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", "src", "--instruction", "x", "--yes"]
    r = run(base + ["--secrets", "exclude"], env={"OPENAI_API_KEY": KEY})
    assert "E_MODEL_NETWORK" in r.stderr and "Left out" in r.stderr and "1 file(s)" in r.stderr     # creds.py dropped, app.py would be sent
    assert "secret guard: exclude" in r.stderr


def test_local_destination_uses_normal_layering_so_warn_is_the_default(server, leaky):
    r = ask(server, leaky)
    assert r.exit_code == 0 and "possible secret" in r.stderr and AWS not in r.stderr
    assert AWS in json.loads(server.seen[0]["body"])["messages"][-1]["content"]     # the user asked for a local model, warn mode keeps the file


def test_secrets_in_the_sent_text_never_reach_the_receipt_value(server, leaky):
    ask(server, leaky, "--receipt-file", "r.json")
    assert AWS not in open("r.json").read()


# ---- the bundle is the same as `prompt` ----------------------------------------------------------------------------------------------------
def test_what_is_sent_is_exactly_what_prompt_would_print(server, proj):
    sent = ask(server, proj)
    assert sent.exit_code == 0
    prompt_out = run(["src", "--instruction", "Explain it."]).stdout
    assert json.loads(server.seen[0]["body"])["messages"][-1]["content"] + "\n" == prompt_out


def test_presets_include_and_diff_work_with_ask(server, proj):
    (proj / ".files-to-prompt").write_text('version = 1\n[presets.p]\npaths = ["."]\ninclude_patterns = ["*.md"]\ninstruction = "Summarise {{what}}."\n')
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", server.base + "/v1", "--preset", "p", "--var", "what=this"])
    user = json.loads(server.seen[0]["body"])["messages"][-1]["content"]
    assert r.exit_code == 0 and "Summarise this." in user and "# demo" in user and "print('hello')" not in user


def test_an_instruction_is_required(server, proj):
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", server.base + "/v1", "src"])
    assert r.exit_code == 2 and "E_USAGE" in r.stderr and server.seen == []


def test_system_prompt_is_sent(server, proj):
    ask(server, proj, "--system", "be terse")
    assert json.loads(server.seen[0]["body"])["messages"][0] == {"role": "system", "content": "be terse"}


# ---- provenance: answers are saved as generated ----------------------------------------------------------------------------------------------
def test_save_writes_a_labelled_file_that_prompt_then_reports_as_generated(server, proj):
    server.responder = lambda p, q: openai_ok("# Findings\nall good")
    r = ask(server, proj, "--save", "notes/answer.md")
    assert r.exit_code == 0 and "saved notes/answer.md  (provenance: generated)" in r.stderr
    text = open("notes/answer.md").read()
    assert read_provenance(text) == {"provenance": "generated"} and text.rstrip().endswith("# Findings\nall good")
    head = text.split("\n---\n", 1)[0]
    for field in ("provider: \"openai\"", "model: \"m-1\"", "prompt_sha256_12:", "input_tokens: 11", "output_tokens: 7", "stop_reason: \"stop\"", "generated_at:"):
        assert field in head, field
    again = run(["notes", "--format", "xml"]).stdout
    assert 'provenance="generated"' in again


def test_save_does_not_overwrite_and_stays_inside_the_project(server, proj):
    assert ask(server, proj, "--save", "a.md").exit_code == 0
    r = ask(server, proj, "--save", "a.md")
    assert r.exit_code == 1 and "already exists" in r.stderr and "NOT saved" in r.stderr
    assert ask(server, proj, "--save", "a.md", "--force").exit_code == 0
    for bad in ("../outside.md", "/tmp/fileflow-ask-escape.md"):
        r = ask(server, proj, "--save", bad)
        assert r.exit_code == 3 and "E_PATH_OUTSIDE_ROOT" in r.stderr
    assert not os.path.exists("/tmp/fileflow-ask-escape.md")


# ---- receipt ----------------------------------------------------------------------------------------------------------------------------------
def test_receipt_records_the_model_call_and_what_is_unknown(server, proj):
    ask(server, proj, "--receipt-file", "r.json")
    rec = json.load(open("r.json"))
    mc = rec["model_call"]
    assert mc["provider"] == "openai" and mc["model"] == "m-1" and mc["local"] is True and mc["input_tokens"] == 11 and mc["output_tokens"] == 7
    assert len(mc["prompt_sha256_12"]) == 12 and len(mc["answer_sha256_12"]) == 12
    led = rec["ledger"]
    assert any("well-formed response" in v for v in led["verified"])
    assert any("as reported by the provider" in a for a in led["assumed"])
    assert any("whether the answer is correct" in u for u in led["unknown"])


def test_receipt_for_a_remote_call_names_the_data_handling_unknown(proj, monkeypatch):
    # use a patched transport so a "remote" host can be exercised without a network
    monkeypatch.setattr(A, "call_model", lambda *a, **k: A.ModelResult("ok", 1, 2, "stop", "m", "api.example.com", "openai"))
    monkeypatch.setattr(A, "validate_base_url", lambda u: u)
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.com/v1", "src", "--instruction", "x", "--yes",
             "--receipt-file", "r.json"], env={"OPENAI_API_KEY": KEY})
    assert r.exit_code == 0, r.stderr
    rec = json.load(open("r.json"))
    assert rec["model_call"]["local"] is False and any("retention, training" in u for u in rec["ledger"]["unknown"])


# ---- boundaries -------------------------------------------------------------------------------------------------------------------------------------
def _py(code):
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("mod", ["fileflow.cli", "fileflow.server.app", "fileflow.check", "fileflow.fetch"])
def test_the_model_client_is_not_loaded_by_any_other_part_of_fileflow(mod):
    r = _py("import sys, %s; assert 'fileflow.ask' not in sys.modules, 'model client was imported'; print('ok')" % mod)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


def test_there_is_no_model_endpoint_on_the_server(proj):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fileflow.server.app import build_app
    c = TestClient(build_app(project_root=str(proj)))
    for path in ("/api/ask", "/ask", "/api/chat", "/api/complete"):
        for method in ("get", "post"):
            assert getattr(c, method)(path).status_code in (404, 405)


def test_the_error_codes_exist_with_sane_exit_codes():
    from fileflow.errors import EXIT_CODES
    for code, exit_code in {"E_MODEL_CONFIG": 2, "E_MODEL_NOKEY": 2, "E_MODEL_CONSENT": 2, "E_MODEL_AUTH": 4, "E_MODEL_RATE": 4,
                            "E_MODEL_NETWORK": 4, "E_MODEL_TIMEOUT": 4, "E_MODEL_RESPONSE": 4}.items():
        assert EXIT_CODES[code] == exit_code
