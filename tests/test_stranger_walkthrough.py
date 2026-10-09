"""The stranger walkthrough as a standing test (docs/pilot-kit.md, FN-014/FN-015).

Two questions a first-time user silently asks, answered here for the project built by
scripts/make_stranger_project.py:

  1. "What did it do?"        -> it must tell me what it sent, what it left out, what is risky.
  2. "I typed it wrong."      -> it must say what is wrong and what to do, in human words,
                                 never a traceback or a Python exception name.

This is not a substitute for watching a real person (docs/pilot-kit.md); it keeps the lessons from
the last stranger from being forgotten.
"""
import importlib.util
import json
import os
import re
import shutil

import pytest
from click.testing import CliRunner

import fileflow.cli as cm
from fileflow.cli import cli

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("make_stranger_project", os.path.join(HERE, "..", "scripts", "make_stranger_project.py"))
maker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maker)

PASSWORD = "S3cr3t" + "P4ssw0rd"
STRIPE = "sk_live_" + "a" * 24


def _runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def run(args, cwd, tty=False, monkeypatch=None):
    old = os.getcwd()
    os.chdir(str(cwd))
    try:
        if monkeypatch is not None and tty:
            monkeypatch.setattr(cm, "_stderr_is_tty", lambda: True)
        return _runner().invoke(cli, [str(a) for a in args])
    finally:
        os.chdir(old)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return maker.build(tmp_path_factory.mktemp("stranger") / "app")


# ---- 1. what did it do? -------------------------------------------------------------------------
def test_the_default_run_tells_a_person_what_happened(app, monkeypatch):
    r = run(["."], app, tty=True, monkeypatch=monkeypatch)
    assert r.exit_code == 0
    err = r.stderr
    assert "Heads-up: package-lock.json is" in err                     # the 100k-token noise file
    assert re.search(r"fileflow: \d+ files, about [\d,]+ tokens", err) and "left out" in err   # in / out
    assert "hidden" in err and "ignored by .gitignore" in err          # ...in words
    assert "not UTF-8 text (BINARY)" in err                            # the PNG, in our vocabulary


def test_nothing_secret_leaves_by_default(app):
    r = run(["."], app)
    assert PASSWORD not in r.stdout + r.stderr and STRIPE not in r.stdout + r.stderr
    assert ".env" not in re.findall(r"<source>([^<]+)", run([".", "--format", "xml"], app).stdout)


def test_including_hidden_files_names_every_secret_it_would_send(app):
    r = run([".", "--include-hidden", "--ignore-patterns", "package-lock.json"], app)
    assert "stripe-live-key" in r.stderr and "url-credentials" in r.stderr      # the DB URL used to be missed
    assert PASSWORD not in r.stderr                                             # never echoes the value


def test_block_mode_fails_closed_and_prints_no_values(app):
    r = run([".", "--include-hidden", "--secrets", "block"], app)
    assert r.exit_code == 3
    assert PASSWORD not in r.stdout + r.stderr and STRIPE not in r.stdout + r.stderr


def test_the_receipt_can_answer_why_isnt_my_file_in_there(app):
    run([".", "--receipt-file", "receipt.json"], app)
    rec = json.loads((app / "receipt.json").read_text())
    reasons = {e["path"]: e["reason"] for e in rec["excluded"]}
    assert reasons[".env"] == "HIDDEN" and reasons["assets/logo.png"] == "BINARY"
    assert reasons["node_modules/"] == "GITIGNORED" and reasons["dist/"] == "GITIGNORED"
    os.remove(str(app / "receipt.json"))


def test_following_the_heads_up_advice_removes_the_noise(app):
    line = next(l for l in run(["."], app).stderr.splitlines() if l.startswith("Heads-up:"))
    assert "--ignore-patterns package-lock.json" in line
    after = run([".", "--ignore-patterns", "package-lock.json"], app)
    assert len(after.stdout) < 1500 and "Heads-up" not in after.stderr


# ---- 2. I typed it wrong ------------------------------------------------------------------------
PYTHON_JARGON = re.compile(r"Traceback|\b(UnicodeDecodeError|KeyError|ValueError|TypeError|AttributeError|"
                           r"FileNotFoundError|OSError|IndexError|AssertionError)\b")

MISTAKES = {
    "path that does not exist": ["nope.py"],
    "unknown format": [".", "--format", "yaml"],
    "token budget that is not a number": [".", "--max-tokens", "abc"],
    "preset that does not exist": [".", "--preset", "ghost"],
    "secrets mode typo": [".", "--secrets", "maybe"],
    "init template typo": ["init", "--template", "nope"],
    "ask with no provider": ["ask", ".", "--instruction", "hi"],
    "fetch of a bare name": ["fetch", "example.com/page"],
    "fetch of a local file": ["fetch", "file:///etc/passwd"],
    "ignore pattern with no value": [".", "--ignore-patterns"],
}


@pytest.mark.parametrize("name", sorted(MISTAKES))
def test_a_mistake_gets_a_human_message_and_a_nonzero_exit(name, app):
    r = run(MISTAKES[name], app)
    text = r.stdout + r.stderr
    assert r.exit_code != 0, name
    assert not PYTHON_JARGON.search(text), text
    # either our taxonomy (code + a "Next:" step) or Click's usage line naming what was wrong
    assert ("Next:" in text and "Error [E_" in text) or "Error:" in text, text
    if "Error [E_" in text:
        assert re.search(r"Next: \S", text), text


def test_a_broken_config_stops_the_run_and_says_where(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".files-to-prompt").write_text("version = 1\n[output\nformat=")
    r = run(["."], tmp_path)
    assert r.exit_code == 2 and r.stdout.strip() == ""
    assert "E_CONFIG_PARSE" in r.stderr and "line 2" in r.stderr and "nothing was sent" in r.stderr
    assert "Next: " in r.stderr and "fileflow check" in r.stderr
    assert not PYTHON_JARGON.search(r.stderr)
    assert run(["check"], tmp_path).exit_code == 2           # `check` reports the same file


def test_help_is_short_enough_to_read():
    out = _runner().invoke(cli, ["--help"]).stdout
    assert 0 < len(out.splitlines()) <= 40, "a stranger reads --help once; keep the top level scannable"
