"""First-run feedback (FN-014): a person must be able to see what went into the prompt, what
stayed out and why, and be warned when one file is most of it.

Found by running `fileflow .` on a realistic messy project: a 400 KB lockfile was 99.9 % of a
~100,000-token prompt, six files were left out, and the only thing printed was a Python
exception name about a PNG.
"""
import shlex
import socket

import pytest
from click.testing import CliRunner

import fileflow.cli as cm
from fileflow import receipt as R
from fileflow import walker
from fileflow.cli import cli


def _runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def run(args, env=None):
    return _runner().invoke(cli, [str(a) for a in args], env=env or {})


@pytest.fixture
def messy(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("print(1)\n")
    (tmp_path / "README.md").write_text("# Pay\n")
    (tmp_path / "package-lock.json").write_text('{"x":"' + "x" * 200_000 + '"}\n')   # ~50k tokens
    (tmp_path / "logo.png").write_bytes(bytes(range(256)) * 4)
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "b.js").write_text("x\n")
    (tmp_path / ".gitignore").write_text("dist/\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---- vocabulary: one set of words everywhere ----------------------------------------------------
def test_every_reason_code_has_plain_words():
    assert set(R.REASON_WORDS) == set(walker.REASONS), "a new reason code needs wording (CLI line + web panel)"
    assert all(w and w == w.strip() for w in R.REASON_WORDS.values())


def test_binary_warning_uses_our_vocabulary_not_a_python_exception_name(messy):
    r = run([".", "--ignore-patterns", "package-lock.json"])
    assert "UnicodeDecodeError" not in r.stderr
    assert "logo.png: not UTF-8 text (BINARY)" in r.stderr


# ---- the dominant-file advisory -----------------------------------------------------------------
def test_dominant_file_thresholds():
    big = R.DOMINANT_MIN_TOKENS
    assert R.dominant_file([("a", big), ("b", 10)])[0] == "a"
    assert R.dominant_file([("a", big - 1), ("b", 0)]) is None                # too small to matter
    assert R.dominant_file([("a", big), ("b", big)])[0] == "b"                # exactly 50%: flagged (ties -> later name)
    assert R.dominant_file([("a", big), ("b", big + 1), ("c", big)]) is None  # largest is a third: not dominant
    assert R.dominant_file([]) is None
    assert R.dominant_file([("a", 100), ("b", 100)]) is None                  # small prompt, never nag


def test_the_advisory_fires_in_a_pipe_and_names_the_file_and_the_cost(messy):
    r = run(["."])
    assert r.exit_code == 0
    line = next(l for l in r.stderr.splitlines() if l.startswith("Heads-up:"))
    assert "package-lock.json is " in line and "% of this prompt" in line and "(estimate)" in line
    assert "package-lock.json" in r.stdout          # we warn; we do not change what is sent


def test_the_advice_actually_works_when_run(messy):
    """Parse the suggested flag out of the message and execute it: advice that doesn't work is worse than none."""
    line = next(l for l in run(["."]).stderr.splitlines() if l.startswith("Heads-up:"))
    flag_text = line.split("--ignore-patterns", 1)[1]
    after = run([".", "--ignore-patterns"] + shlex.split(flag_text))
    assert after.exit_code == 0
    assert "package-lock.json" not in after.stdout and "main.py" in after.stdout
    assert not any(l.startswith("Heads-up:") for l in after.stderr.splitlines())


def test_the_advice_survives_spaces_and_quotes_in_the_file_name(tmp_path, monkeypatch):
    (tmp_path / "data dir").mkdir()
    (tmp_path / "data dir" / "big file's.csv").write_text("v\n" + "1234567890\n" * 5000)
    (tmp_path / "small.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    line = next(l for l in run(["."]).stderr.splitlines() if l.startswith("Heads-up:"))
    args = shlex.split(line.split("--ignore-patterns", 1)[1])
    assert args == ["big file's.csv"]          # NAME, not path: --ignore-patterns never matches a "/" path
    assert "big file" not in run([".", "--ignore-patterns"] + args).stdout


def test_glob_characters_in_the_name_are_escaped(tmp_path, monkeypatch):
    """Next.js style `[id].tsx`: unescaped, the pattern is a character class and matches nothing."""
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "[id].tsx").write_text("const a = 1;\n" * 6000)
    (tmp_path / "small.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    line = next(l for l in run(["."]).stderr.splitlines() if l.startswith("Heads-up:"))
    args = shlex.split(line.split("--ignore-patterns", 1)[1])
    assert args == ["[[]id].tsx"]
    assert "[id].tsx" not in run([".", "--ignore-patterns"] + args).stdout


def test_a_shared_name_is_called_out_because_the_pattern_would_remove_the_others(tmp_path, monkeypatch):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "index.js").write_text("v\n" + "1234567890\n" * (5000 if d == "a" else 1))
    monkeypatch.chdir(tmp_path)
    line = next(l for l in run(["."]).stderr.splitlines() if l.startswith("Heads-up:"))
    assert "1 other file called index.js would also be left out" in line


def test_a_pattern_with_a_slash_can_never_match_and_we_say_so(tmp_path, monkeypatch):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("hi\n")
    monkeypatch.chdir(tmp_path)
    for pat, advice in (("sub/a.txt", "'a.txt'"), ("sub/", "'sub'"), ("node_modules/", "'node_modules'")):
        r = run([".", "--ignore-patterns", pat])
        assert "matches nothing" in r.stderr and advice in r.stderr and "NAMES" in r.stderr, pat
    quiet = run([".", "--ignore-patterns", "a.txt"])
    assert "matches nothing" not in quiet.stderr and "a.txt" not in quiet.stdout


def test_no_advisory_when_nothing_dominates(tmp_path, monkeypatch):
    for n in "abc":
        (tmp_path / (n + ".txt")).write_text("word " * 10000)
    monkeypatch.chdir(tmp_path)
    assert "Heads-up" not in run(["."]).stderr


def test_ask_shows_the_advisory_before_the_consent_decision(messy, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")))
    r = run(["ask", "--provider", "openai", "--model", "m", "--base-url", "https://api.example.invalid/v1", ".",
             "--instruction", "x"], env={"OPENAI_API_KEY": "sk-TESTKEY-do-not-leak-0123456789"})
    assert r.exit_code == 2 and "E_MODEL_CONSENT" in r.stderr
    assert r.stderr.index("Heads-up: package-lock.json") < r.stderr.index("E_MODEL_CONSENT")


# ---- the terminal line --------------------------------------------------------------------------
def test_piped_runs_get_no_summary_line_and_stdout_is_untouched(messy):
    piped = run([".", "--ignore-patterns", "package-lock.json"])
    assert "fileflow:" not in piped.stderr
    assert piped.stdout.count("main.py") == 1


def test_a_terminal_gets_one_line_after_the_output(messy, monkeypatch):
    monkeypatch.setattr(cm, "_stderr_is_tty", lambda: True)
    r = run([".", "--ignore-patterns", "package-lock.json"])
    lines = [l for l in r.stderr.splitlines() if l.startswith("fileflow:")]
    assert len(lines) == 1
    text = lines[0]
    assert "2 files" in text and "left out 5" in text and "Details: --receipt" in text
    for words in ("hidden", "ignored by .gitignore", "not text", "matched an ignore pattern"):
        assert words in text, words
    assert "HIDDEN" not in text      # words for people; codes live in the receipt


def test_with_receipt_the_terminal_line_is_not_repeated(messy, monkeypatch):
    monkeypatch.setattr(cm, "_stderr_is_tty", lambda: True)
    r = run([".", "--receipt"])
    assert "Context receipt" in r.stderr and "fileflow:" not in r.stderr


def test_nothing_left_out_means_no_left_out_clause(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cm, "_stderr_is_tty", lambda: True)
    t = next(l for l in run(["."]).stderr.splitlines() if l.startswith("fileflow:"))
    assert t.startswith("fileflow: 1 file,") and "left out" not in t


def test_on_a_real_terminal_the_summary_is_the_last_thing_printed(messy):
    """The point of the line is that the eye lands on it. Needs a pty, so POSIX only."""
    pty = pytest.importorskip("pty")
    import os
    import select
    import subprocess
    import sys

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; from fileflow.cli import cli; cli(sys.argv[1:])", ".", "--ignore-patterns", "package-lock.json"],
            stdout=slave, stderr=slave, stdin=subprocess.DEVNULL, cwd=str(messy),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"},
        )
        os.close(slave)
        chunks = b""
        while True:
            ready, _, _ = select.select([master], [], [], 15)
            if not ready:
                break
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            chunks += data
        proc.wait(timeout=15)
    finally:
        os.close(master)
    text = chunks.decode("utf-8", "replace").replace("\r", "")
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines[-1].startswith("fileflow:") and "Details: --receipt" in lines[-1], lines[-3:]
    assert any("main.py" in l for l in lines[:-1]), "the prompt itself comes first"
