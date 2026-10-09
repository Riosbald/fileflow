"""Tests for `fileflow serve` — the optional local FastAPI backend.

Uses FastAPI's TestClient (needs the `server` extra + httpx). Verifies the
/api routes (health, project, tree, prompt, config) and the localhost binding
guard without starting a real server.
"""

import os
import re
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from fileflow.server import security  # noqa: E402
from fileflow.server import config as configmod  # noqa: E402
from fileflow.server import watcher  # noqa: E402
from fileflow.server.app import build_app  # noqa: E402


@pytest.fixture
def client(tmpdir):
    """A TestClient pointed at a small real project."""
    (tmpdir / "README.md").write("# hi\n")
    (tmpdir / "src").mkdir()
    (tmpdir / "src" / "app.py").write("def main():\n    return 1\n")
    (tmpdir / ".gitignore").write("secret.txt\n")
    (tmpdir / "secret.txt").write("ignored\n")
    app = build_app(project_root=str(tmpdir))
    with TestClient(app) as c:
        c.project_root = str(tmpdir)
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_project(client):
    r = client.get("/api/project", params={"root": client.project_root})
    assert r.status_code == 200
    data = r.json()
    assert data["fileCount"] == 2  # README.md + src/app.py (secret.txt gitignored)


def test_tree(client):
    r = client.get("/api/tree", params={"root": client.project_root})
    assert r.status_code == 200
    names = [n["name"] for n in r.json()]
    assert "README.md" in names
    assert "secret.txt" not in names  # gitignored


def test_prompt_default(client):
    r = client.get("/api/prompt", params={"root": client.project_root})
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["files"] == 2
    assert "README.md" in data["text"]
    assert "def main()" in data["text"]


def test_prompt_invalid_format(client):
    r = client.get(
        "/api/prompt", params={"root": client.project_root, "format": "bogus"}
    )
    assert r.status_code == 400


def test_file_read(client):
    r = client.get(
        "/api/file",
        params={"root": client.project_root, "path": "src/app.py"},
    )
    assert r.status_code == 200
    assert "def main()" in r.json()["content"]


def test_file_missing(client):
    r = client.get(
        "/api/file", params={"root": client.project_root, "path": "nope.txt"}
    )
    assert r.status_code == 404


def test_file_traversal_blocked(client):
    r = client.get(
        "/api/file", params={"root": client.project_root, "path": "../outside"}
    )
    assert r.status_code == 404


def test_prompt_xml(client):
    r = client.get(
        "/api/prompt", params={"root": client.project_root, "format": "xml"}
    )
    assert r.status_code == 200
    assert "<documents>" in r.json()["text"]


def test_prompt_budget_truncates(client):
    """A small budget keeps README, then truncates the crossing file."""
    r = client.get(
        "/api/prompt", params={"root": client.project_root, "budget": 3}
    )
    assert r.status_code == 200
    text = r.json()["text"]
    assert "truncated to fit" in text
    assert r.json()["meta"]["files"] == 2  # README + truncated src/app.py
    # Server trimming reuses the engine's marker, so CLI and server agree.
    assert "this file was ~" in text


def test_prompt_meta_reports_tokenizer(client):
    r = client.get("/api/prompt", params={"root": client.project_root})
    assert r.status_code == 200
    assert r.json()["meta"]["tokenizer"] == "heuristic"
    assert r.json()["meta"]["tokens"] > 0


def test_prompt_bad_tokenizer_returns_400(client):
    # A budget forces the tokenizer to run; an unloadable one is a 400.
    r = client.get(
        "/api/prompt",
        params={"root": client.project_root, "tokenizer": "no-such-enc", "budget": 100},
    )
    assert r.status_code == 400
    assert "tokenizer" in r.json()["detail"]


def test_config_naive_parser(tmpdir):
    """The <3.11 fallback TOML parser reads a simple .files-to-prompt."""
    (tmpdir / ".files-to-prompt").write(
        "[output]\nformat = \"json\"\nmax_tokens = 4000\n\n[ignore]\ngitignore = false\n"
    )
    cfg = configmod.read_config(str(tmpdir))
    assert cfg["output"]["format"] == "json"
    assert cfg["output"]["max_tokens"] == 4000
    assert cfg["ignore"]["gitignore"] is False


def test_write_config_arrays_roundtrip(tmpdir):
    """write_config emits valid TOML arrays; multiple patterns survive."""
    cfg = {
        "output": {"format": "json", "max_tokens": 5000},
        "exclude": {"patterns": ["*.md", "__pycache__"]},
        "ignore": {"gitignore": True, "hidden": False},
    }
    written = configmod.write_config(str(tmpdir), cfg)
    assert "patterns = [\"*.md\", \"__pycache__\"]" in written
    # Reading it back must preserve both patterns (valid TOML now).
    roundtrip = configmod.read_config(str(tmpdir))
    assert roundtrip["exclude"]["patterns"] == ["*.md", "__pycache__"]
    assert roundtrip["output"]["max_tokens"] == 5000


def test_config_defaults(client):
    r = client.get("/api/config", params={"root": client.project_root})
    assert r.status_code == 200
    assert r.json()["output"]["format"] == "default"
    assert r.json()["ignore"]["gitignore"] is True


def test_config_put_reads_back(client):
    r = client.put(
        "/api/config",
        params={"root": client.project_root},
        json={"output": {"max_tokens": 16000}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Written to .files-to-prompt; verify it round-trips.
    r2 = client.get("/api/config", params={"root": client.project_root})
    assert r2.json()["output"]["max_tokens"] == 16000


def test_path_escape_blocked(tmpdir):
    """safe_join refuses a sub-path that leaves the root."""
    root = str(tmpdir)
    outside = str(tmpdir) + ".sibling"
    with pytest.raises(security.PathOutsideRootError):
        security.safe_join(root, "../" + outside)


def test_project_root_defaults_to_serve_root(tmpdir):
    """The server's --root becomes the default root for /api endpoints."""
    (tmpdir / "a.txt").write("hi\n")
    app = build_app(project_root=str(tmpdir))
    with TestClient(app) as c:
        r = c.get("/api/tree")  # no root param
        assert r.status_code == 200
        names = [n["name"] for n in r.json()]
        assert "a.txt" in names


def test_watch_survives_client_disconnect(tmpdir):
    """Watch handler exits cleanly when the client disconnects (no traceback)."""
    app = build_app(project_root=str(tmpdir))
    with TestClient(app) as c:
        with c.websocket_connect(f"/api/watch?poll=0.15") as ws:
            pass  # connect and immediately close
    # Reaching here without an unhandled exception is the assertion.


def test_host_guard():
    # localhost always allowed
    assert security.is_allowed_host("127.0.0.1", allow_remote=False) is True
    assert security.is_allowed_host("::1", allow_remote=False) is True
    # non-localhost requires the explicit flag
    assert security.is_allowed_host("0.0.0.0", allow_remote=False) is False
    assert security.is_allowed_host("0.0.0.0", allow_remote=True) is True


def test_watch_notifies_on_change(client):
    """A file change under the root produces a change notification."""
    with client.websocket_connect(
        f"/api/watch?root={client.project_root}&poll=0.15"
    ) as ws:
        ready = ws.receive_json()  # baseline taken; safe to change files now
        assert ready["ready"] is True and ready["changed"] is False
        # Create a new file under the root; the next poll should detect it.
        with open(os.path.join(client.project_root, "added.txt"), "w") as fh:
            fh.write("hello\n")
        data = ws.receive_json()
        assert data["changed"] is True
        assert data["root"] == client.project_root
        assert data["hash"] != ready["hash"]



# ---------------------------------------------------------------------------
# Root confinement.
#
# Regression tests for a real vulnerability: every endpoint used to accept an
# arbitrary ``?root=`` query parameter, so ``--root`` was only a *default* and a
# client could read any directory the server process could (``?root=/etc``).
# The served root is now a boundary: ``root`` may narrow it, never widen it.
# ---------------------------------------------------------------------------


@pytest.fixture
def jailed(tmp_path):
    """A served project, plus neighbours holding secrets it must never reveal."""
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "ok.txt").write_text("fine\n")
    (proj / "sub" / "inner.txt").write_text("inner\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET\n")
    sibling = tmp_path / "proj-evil"  # shares the jail's name as a string prefix
    sibling.mkdir()
    (sibling / "secret.txt").write_text("PREFIX-SECRET\n")
    with TestClient(build_app(project_root=str(proj))) as c:
        c.proj, c.outside, c.sibling = proj, outside, sibling
        yield c


@pytest.mark.parametrize("endpoint", ["/api/project", "/api/tree", "/api/prompt", "/api/config"])
def test_root_override_outside_project_is_forbidden(jailed, endpoint):
    escapes = [
        str(jailed.outside),  # absolute, outside
        str(jailed.sibling),  # string-prefix sibling: /x/proj-evil vs /x/proj
        "..",  # relative traversal (relative to the project, not the CWD)
        "sub/../..",  # traversal hidden behind a real directory
        "/",
        "/etc",
    ]
    for bad in escapes:
        r = jailed.get(endpoint, params={"root": bad})
        assert r.status_code == 403, (endpoint, bad, r.status_code, r.text)
        assert "SECRET" not in r.text


def test_file_endpoint_cannot_reach_outside_via_root(jailed):
    for bad in (str(jailed.outside), str(jailed.sibling)):
        r = jailed.get("/api/file", params={"root": bad, "path": "secret.txt"})
        assert r.status_code == 403
        assert "SECRET" not in r.text


def test_config_put_cannot_write_outside_via_root(jailed):
    r = jailed.put(
        "/api/config", params={"root": str(jailed.outside)}, json={"output": {"max_tokens": 1}}
    )
    assert r.status_code == 403
    assert not (jailed.outside / ".files-to-prompt").exists()


def test_root_override_can_narrow_to_a_subdirectory(jailed):
    # Absolute, relative-to-the-project, and the project root itself all work.
    for value, expected in (
        (str(jailed.proj / "sub"), ["inner.txt"]),
        ("sub", ["inner.txt"]),
        (str(jailed.proj), ["ok.txt", "sub"]),
    ):
        r = jailed.get("/api/tree", params={"root": value})
        assert r.status_code == 200, (value, r.text)
        assert sorted(n["name"] for n in r.json()) == expected


def test_root_override_must_be_a_directory(jailed):
    assert jailed.get("/api/tree", params={"root": "ok.txt"}).status_code == 400
    assert jailed.get("/api/tree", params={"root": "no-such-dir"}).status_code == 400


def test_outside_root_gives_same_answer_whether_or_not_it_exists(jailed):
    """No oracle for probing the rest of the filesystem."""
    real = jailed.get("/api/tree", params={"root": str(jailed.outside)})
    ghost = jailed.get("/api/tree", params={"root": str(jailed.outside / "nope")})
    assert real.status_code == ghost.status_code == 403


def test_root_with_nul_byte_is_a_client_error(jailed):
    r = jailed.get("/api/tree", params={"root": "a\x00b"})
    assert r.status_code in (400, 403)  # never a 500


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_symlink_in_project_cannot_be_used_to_escape(jailed):
    (jailed.proj / "portal").symlink_to(jailed.outside, target_is_directory=True)
    assert jailed.get("/api/tree", params={"root": "portal"}).status_code == 403
    r = jailed.get("/api/file", params={"path": "portal/secret.txt"})
    assert r.status_code == 404
    assert "SECRET" not in r.text


def test_file_endpoint_never_serves_git_internals(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[remote]\n\turl = https://tok3n@example.com/x.git\n")
    (tmp_path / "ok.txt").write_text("fine\n")
    with TestClient(build_app(project_root=str(tmp_path))) as c:
        r = c.get("/api/file", params={"path": ".git/config"})
        assert r.status_code == 404
        assert "tok3n" not in r.text
        assert c.get("/api/file", params={"path": "ok.txt"}).status_code == 200


def test_watch_rejects_root_outside_project(jailed):
    from starlette.websockets import WebSocketDisconnect

    url = "/api/watch?poll=0.15&root=" + quote(str(jailed.outside), safe="")
    with pytest.raises(WebSocketDisconnect) as exc:
        with jailed.websocket_connect(url):
            pass
    assert exc.value.code == 1008


def test_is_within_respects_component_boundaries():
    assert security.is_within("/srv/proj", "/srv/proj")
    assert security.is_within("/srv/proj", "/srv/proj/a/b")
    assert not security.is_within("/srv/proj", "/srv/proj-evil")
    assert not security.is_within("/srv/proj", "/srv")
    assert not security.is_within("/srv/proj", "/etc/passwd")
    # A root of "/" used to reject *everything* (it compared against "//").
    assert security.is_within("/", "/etc")


# ---------------------------------------------------------------------------
# .files-to-prompt: strict reading, safe writing, and the <3.11 fallback parser
# ---------------------------------------------------------------------------

AWKWARD_PATTERNS = [
    "back\\slash",
    'quo"te',
    "tab\tchar",
    "new\nline",
    "uni\u2713code",
    "C:\\temp\\*.log",
    "ctl\x01char",
    "#hash",
    "a, b",
    "]tricky[",
]


def test_write_config_roundtrips_awkward_patterns(tmpdir):
    """Whatever the UI saves must be readable back, on whichever parser runs."""
    configmod.write_config(str(tmpdir), {"exclude": {"patterns": AWKWARD_PATTERNS}})
    cfg = configmod.read_config(str(tmpdir), strict=True)
    assert cfg["exclude"]["patterns"] == AWKWARD_PATTERNS


def test_naive_parser_handles_arrays_comments_and_multiline(tmpdir):
    """The pre-3.11 fallback used to turn arrays into a string. Exercised on
    every Python version by calling it directly."""
    path = tmpdir / ".files-to-prompt"
    path.write(
        "# project config\n"
        "[exclude]\n"
        "patterns = [\n"
        '  "*.min.js",   # minified\n'
        "  'literal\\path',\n"
        '  "__pycache__",\n'
        "]\n"
        "[output]\n"
        'format = "xml"  # inline comment\n'
        "max_tokens = 4000\n"
        "separators = false\n"
        "[include]\n"
        "patterns = []\n"
    )
    assert configmod._naive_toml(str(path)) == {
        "exclude": {"patterns": ["*.min.js", "literal\\path", "__pycache__"]},
        "output": {"format": "xml", "max_tokens": 4000, "separators": False},
        "include": {"patterns": []},
    }


def test_naive_parser_agrees_with_tomllib(tmpdir):
    tomllib = pytest.importorskip("tomllib")  # 3.11+: differential check
    configmod.write_config(
        str(tmpdir),
        {
            "output": {"format": "json", "max_tokens": 12345, "line_numbers": True},
            "exclude": {"patterns": AWKWARD_PATTERNS},
            "ignore": {"gitignore": False, "hidden": True},
        },
    )
    path = str(tmpdir / ".files-to-prompt")
    with open(path, "rb") as fh:
        expected = tomllib.load(fh)
    assert configmod._naive_toml(path) == expected


@pytest.mark.parametrize(
    "text",
    [
        "[output\nformat = \n",  # unterminated table + missing value
        "not toml at all [[[\n",
        "[exclude]\npatterns = [\"a\", \n",  # unterminated array
        "[output]\nformat = unquoted\n",
    ],
)
def test_invalid_config_is_422_over_http_and_defaults_when_lenient(tmpdir, text):
    (tmpdir / ".files-to-prompt").write(text)
    with TestClient(build_app(project_root=str(tmpdir))) as c:
        r = c.get("/api/config")
        assert r.status_code == 422
        assert ".files-to-prompt" in r.json()["detail"]
    # The CLI-facing lenient read still degrades to defaults instead of failing.
    assert configmod.read_config(str(tmpdir)) == configmod.DEFAULTS
    with pytest.raises(configmod.ConfigError):
        configmod.read_config(str(tmpdir), strict=True)


def test_wrongly_shaped_values_are_rejected_strictly_and_ignored_leniently(tmpdir):
    (tmpdir / ".files-to-prompt").write('[exclude]\npatterns = "*.md"\n')
    with pytest.raises(configmod.ConfigError):
        configmod.read_config(str(tmpdir), strict=True)
    assert configmod.read_config(str(tmpdir))["exclude"]["patterns"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"output": {"format": "pdf"}},
        {"output": {"max_tokens": "lots"}},
        {"output": {"max_tokens": -5}},
        {"output": {"separators": "yes"}},
        {"exclude": {"patterns": "*.md"}},
        {"exclude": {"patterns": [1, 2]}},
    ],
)
def test_config_put_rejects_a_payload_that_would_write_a_broken_file(client, payload):
    r = client.put("/api/config", json=payload)
    assert r.status_code == 400
    assert not os.path.exists(os.path.join(client.project_root, ".files-to-prompt"))


def test_cli_refuses_to_run_with_a_broken_config(tmpdir, monkeypatch):
    from click.testing import CliRunner

    from fileflow.cli import cli

    (tmpdir / "a.txt").write("hello\n")
    (tmpdir / ".files-to-prompt").write("[output\nformat = \n")
    monkeypatch.chdir(tmpdir)
    result = CliRunner().invoke(cli, [str(tmpdir / "a.txt")])
    assert result.exit_code == 2
    assert "hello" not in result.output           # nothing is emitted
    assert "E_CONFIG_PARSE" in result.output and "nothing was sent" in result.output


# ---------------------------------------------------------------------------
# Watcher: what counts as a change
# ---------------------------------------------------------------------------


def _write(path, text, mtime_ns=None):
    path.write_text(text)
    if mtime_ns is not None:
        os.utime(str(path), ns=(mtime_ns, mtime_ns))


def test_snapshot_is_stable_when_nothing_changes(tmp_path):
    _write(tmp_path / "a.py", "x")
    assert watcher.snapshot(str(tmp_path)) == watcher.snapshot(str(tmp_path))


def test_snapshot_sees_same_size_rewrite_within_one_second(tmp_path):
    """mtime used to be truncated to whole seconds, hiding fast same-size edits."""
    t = 1_700_000_000_000_000_000
    f = tmp_path / "a.py"
    _write(f, "a", t)
    before = watcher.snapshot(str(tmp_path))
    _write(f, "b", t + 5_000_000)  # +5 ms, same size, same second
    assert watcher.snapshot(str(tmp_path)).files != before.files


def test_snapshot_sees_hidden_files_and_the_config(tmp_path):
    _write(tmp_path / "a.py", "x")
    base = watcher.snapshot(str(tmp_path))

    _write(tmp_path / ".env.example", "A=1\n")  # hidden files count
    after_hidden = watcher.snapshot(str(tmp_path))
    assert after_hidden.files != base.files
    assert after_hidden.config == base.config == "absent"

    _write(tmp_path / ".files-to-prompt", '[output]\nformat = "xml"\n')
    after_cfg = watcher.snapshot(str(tmp_path))
    assert after_cfg.config != base.config
    assert after_cfg.files != after_hidden.files

    (tmp_path / ".files-to-prompt").unlink()
    assert watcher.snapshot(str(tmp_path)).config == "absent"


def test_config_is_tracked_even_when_the_project_gitignores_it(tmp_path):
    """This repo's own .gitignore ignores .files-to-prompt; edits must still land."""
    _write(tmp_path / ".gitignore", ".files-to-prompt\n")
    _write(tmp_path / "a.py", "x")
    before = watcher.snapshot(str(tmp_path))
    _write(tmp_path / ".files-to-prompt", '[output]\nformat = "json"\n')
    after = watcher.snapshot(str(tmp_path))
    assert after.files == before.files  # the walk skipped it...
    assert after.config != before.config  # ...the dedicated digest did not


def test_snapshot_ignores_gitignored_and_heavy_directories(tmp_path):
    _write(tmp_path / ".gitignore", "dist/\n*.log\n")
    _write(tmp_path / "a.py", "x")
    for rel in ("dist/bundle.js", "app.log", "node_modules/pkg/index.js", ".git/HEAD", "__pycache__/a.pyc"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        _write(tmp_path / rel, "1")
    base = watcher.snapshot(str(tmp_path))
    for rel in ("dist/bundle.js", "app.log", "node_modules/pkg/index.js", ".git/HEAD", "__pycache__/a.pyc"):
        _write(tmp_path / rel, "changed, and longer")
    assert watcher.snapshot(str(tmp_path)) == base  # none of that affects the prompt
    _write(tmp_path / "a.py", "changed, and longer")
    assert watcher.snapshot(str(tmp_path)).files != base.files


def test_snapshot_honours_nested_gitignore(tmp_path):
    (tmp_path / "sub").mkdir()
    _write(tmp_path / "sub" / ".gitignore", "*.tmp\n")
    _write(tmp_path / "sub" / "scratch.tmp", "1")
    _write(tmp_path / "sub" / "real.py", "1")
    base = watcher.snapshot(str(tmp_path))
    _write(tmp_path / "sub" / "scratch.tmp", "changed, and longer")
    assert watcher.snapshot(str(tmp_path)) == base
    _write(tmp_path / "sub" / "real.py", "changed, and longer")
    assert watcher.snapshot(str(tmp_path)).files != base.files


def test_snapshot_survives_an_unreadable_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_bytes(b"\xff\xfe\x00bad")
    _write(tmp_path / "a.py", "x")
    watcher.snapshot(str(tmp_path))  # must not raise


def test_watch_frames_distinguish_config_edits_from_source_edits(client):
    def replace_atomically(rel, text):
        full = os.path.join(client.project_root, rel)
        tmp = full + ".swap"
        with open(tmp, "w") as fh:
            fh.write(text)
        os.replace(tmp, full)

    with client.websocket_connect("/api/watch?poll=0.15") as ws:
        ready = ws.receive_json()
        assert ready["ready"] is True
        assert re.fullmatch(r"[0-9a-f]{20}", ready["hash"]), "hash is a digest, not a file listing"

        replace_atomically(".files-to-prompt", '[output]\nformat = "xml"\n')
        cfg_frame = ws.receive_json()
        assert cfg_frame["changed"] is True and cfg_frame["config_changed"] is True

        replace_atomically("src/app.py", "def main():\n    return 'a much longer body'\n")
        src_frame = ws.receive_json()
        assert src_frame["changed"] is True and src_frame["config_changed"] is False
        assert len({ready["hash"], cfg_frame["hash"], src_frame["hash"]}) == 3
