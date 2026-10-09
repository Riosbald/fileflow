"""Tests for fileflow.

Uses pytest and click.testing.CliRunner. The ``sample_project`` fixture
creates a realistic project structure (files, directories, a .gitignore and
hidden files) so the tests exercise the real filesystem behaviour.
"""

import json
import os

import click
import pytest
from click.testing import CliRunner

import fileflow.cli as cli_module
from fileflow.cli import (
    add_line_numbers,
    apply_token_budget,
    cli,
    estimate_tokens,
    generate_prompt,
    read_gitignore,
    read_project_config,
    should_ignore,
)


@pytest.fixture
def sample_project(tmpdir):
    """Build a realistic project tree and return its root path."""
    root = tmpdir.mkdir("project")

    (root / "README.md").write("# Hello\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write("def main():\n    return 1\n")
    (root / "src" / "utils.py").write("VALUE = 42\n")
    (root / "src" / "nested").mkdir()
    (root / "src" / "nested" / "deep.txt").write("deep content\n")

    # Hidden files and directories (excluded unless --include-hidden)
    (root / ".hidden_file").write("secret\n")
    (root / ".hidden_dir").mkdir()
    (root / ".hidden_dir" / "inner.txt").write("inner secret\n")

    # A .gitignore that excludes build artifacts
    (root / ".gitignore").write("build/\n*.pyc\n")
    (root / "build").mkdir()
    (root / "build" / "output.txt").write("ignored\n")
    (root / "src" / "cached.pyc").write("ignored bytecode\n")

    return root


def run_cli(*args, **kwargs):
    return CliRunner().invoke(cli, list(args), **kwargs)


# --------------------------------------------------------------------------- #
# F1: Directory-to-prompt conversion
# --------------------------------------------------------------------------- #
def test_directory_traversal_is_recursive(sample_project):
    result = run_cli(str(sample_project))
    assert result.exit_code == 0
    assert "src/nested/deep.txt" in result.output
    assert "src/app.py" in result.output
    assert "deep content" in result.output


def test_default_output_format(sample_project):
    result = run_cli(str(sample_project / "src" / "app.py"))
    assert result.exit_code == 0
    # app.py is written with a trailing newline, so there is a blank line
    # between the content and the closing --- separator.
    expected = (
        f"{sample_project / 'src' / 'app.py'}\n"
        "---\n"
        "def main():\n    return 1\n"
        "\n---"
    )
    assert result.output.strip() == expected


def test_hidden_files_excluded_by_default(sample_project):
    result = run_cli(str(sample_project))
    assert ".hidden_file" not in result.output
    assert ".hidden_dir" not in result.output
    assert "inner secret" not in result.output


# --------------------------------------------------------------------------- #
# F3: Hidden file control
# --------------------------------------------------------------------------- #
def test_include_hidden(sample_project):
    result = run_cli(str(sample_project), "--include-hidden")
    assert result.exit_code == 0
    assert ".hidden_file" in result.output
    assert "secret" in result.output
    assert ".hidden_dir/inner.txt" in result.output


# --------------------------------------------------------------------------- #
# F4: Gitignore integration
# --------------------------------------------------------------------------- #
def test_gitignore_respected_by_default(sample_project):
    result = run_cli(str(sample_project))
    assert result.exit_code == 0
    assert "build/output.txt" not in result.output
    assert "cached.pyc" not in result.output


def test_ignore_gitignore(sample_project):
    result = run_cli(str(sample_project), "--ignore-gitignore")
    assert result.exit_code == 0
    assert "build/output.txt" in result.output
    assert "cached.pyc" in result.output


def test_gitignore_negation_reincludes(tmpdir):
    (tmpdir / ".gitignore").write("*.pyc\n!keep.pyc\n")
    (tmpdir / "drop.pyc").write("x")
    (tmpdir / "keep.pyc").write("x")
    (tmpdir / "app.py").write("print(1)")
    result = run_cli(str(tmpdir))
    assert result.exit_code == 0
    assert "drop.pyc" not in result.output
    assert "keep.pyc" in result.output
    assert "app.py" in result.output


def test_gitignore_anchored_to_root(tmpdir):
    (tmpdir / ".gitignore").write("/root-only.txt\n")
    (tmpdir / "root-only.txt").write("a")
    (tmpdir / "sub").mkdir()
    (tmpdir / "sub" / "root-only.txt").write("b")
    result = run_cli(str(tmpdir))
    assert result.exit_code == 0
    # The root copy is ignored (anchored pattern); the nested copy is kept.
    lines = result.output.splitlines()
    nested = [l for l in lines if l.endswith("root-only.txt")]
    assert len(nested) == 1 and "sub" in nested[0]


def test_gitignore_directory_only_rule(tmpdir):
    (tmpdir / ".gitignore").write("cache/\n")
    (tmpdir / "cache").mkdir()
    (tmpdir / "cache" / "x.txt").write("x")
    (tmpdir / "cache.txt").write("not a dir")
    result = run_cli(str(tmpdir))
    assert result.exit_code == 0
    assert "cache/x.txt" not in result.output
    assert "cache.txt" in result.output  # a file is not matched by "cache/"


def test_gitignore_skips_comments_and_blank_lines(tmpdir):
    gitignore = tmpdir.join(".gitignore")
    gitignore.write("# a comment\n\nbuild/\n*.pyc\n\n# trailing\n")
    rules = read_gitignore(str(tmpdir))
    assert rules == ["build/", "*.pyc"]


def test_should_ignore_basename_and_directory_rules(tmpdir):
    tmpdir.join("node_modules").mkdir()
    tmpdir.join("readme.md").write("x")
    rules = read_gitignore(str(tmpdir))
    assert rules == []
    assert should_ignore(os.path.join(str(tmpdir), "whatever"), ["*.log"]) is False
    assert should_ignore(os.path.join(str(tmpdir), "app.log"), ["*.log"]) is True
    assert (
        should_ignore(os.path.join(str(tmpdir), "node_modules"), ["node_modules/"])
        is True
    )
    # A file matching a directory-only rule is not ignored
    assert (
        should_ignore(os.path.join(str(tmpdir), "readme.md"), ["readme/"]) is False
    )


# --------------------------------------------------------------------------- #
# F5: Ignore patterns
# --------------------------------------------------------------------------- #
def test_ignore_patterns(sample_project):
    result = run_cli(str(sample_project), "--ignore-patterns", "*.md")
    assert result.exit_code == 0
    assert "README.md" not in result.output
    assert "src/app.py" in result.output


def test_multiple_ignore_patterns(sample_project):
    result = run_cli(
        str(sample_project), "--ignore-patterns", "*.md", "--ignore-patterns", "*.txt"
    )
    assert result.exit_code == 0
    assert "README.md" not in result.output
    assert "deep.txt" not in result.output
    assert "src/app.py" in result.output


def test_ignore_patterns_prunes_directories(tmpdir):
    (tmpdir / "data").mkdir()
    (tmpdir / "data" / "x.csv").write("1,2\n")
    (tmpdir / "data" / "y.csv").write("3,4\n")
    result = run_cli(str(tmpdir), "--ignore-patterns", "data")
    assert result.exit_code == 0
    assert "data/x.csv" not in result.output
    assert "x.csv" not in result.output


# --------------------------------------------------------------------------- #
# F2: Multi-path support
# --------------------------------------------------------------------------- #
def test_multiple_files_and_directories(sample_project):
    result = run_cli(
        str(sample_project / "README.md"),
        str(sample_project / "src" / "utils.py"),
    )
    assert result.exit_code == 0
    assert "README.md" in result.output
    assert "utils.py" in result.output
    assert "VALUE = 42" in result.output


def test_single_file(sample_project):
    result = run_cli(str(sample_project / "README.md"))
    assert result.exit_code == 0
    assert "# Hello" in result.output


def test_invalid_path_raises_bad_parameter(sample_project):
    missing = str(sample_project / "does-not-exist.py")
    result = run_cli(missing)
    assert result.exit_code == 2  # UsageError exit code
    assert "does-not-exist.py" in result.output
    # With standalone mode disabled the underlying click.BadParameter is raised.
    with pytest.raises(click.BadParameter):
        CliRunner().invoke(cli, [missing], standalone_mode=False, catch_exceptions=False)


def test_empty_directory_produces_no_prompt_and_says_so(tmpdir):
    # FN-015: it used to print a blank line and nothing else; a wrong folder gave a silent empty prompt.
    empty = tmpdir.mkdir("empty")
    result = run_cli(str(empty))
    assert result.exit_code == 0
    text = result.output.strip()          # (CliRunner may interleave stderr here)
    assert text.startswith("Warning: nothing to send: no files were found here")
    assert "\n" not in text, "no prompt text at all: the warning is the only output"


# --------------------------------------------------------------------------- #
# Output control
# --------------------------------------------------------------------------- #
def test_output_file(sample_project, tmpdir):
    out = tmpdir / "prompt.txt"
    result = run_cli(str(sample_project / "src"), "--output-file", str(out))
    assert result.exit_code == 0
    assert result.output == ""
    assert out.exists()
    assert "app.py" in out.read()
    assert "VALUE = 42" in out.read()


def test_xml_format(sample_project):
    result = run_cli(str(sample_project / "src" / "app.py"), "--format", "xml")
    assert result.exit_code == 0
    assert "<documents>" in result.output
    assert "<document index=\"1\">" in result.output
    assert "<source>" in result.output
    assert "<document_content>" in result.output
    assert "</documents>" in result.output


def test_json_format(sample_project):
    result = run_cli(str(sample_project / "src" / "app.py"), "--format", "json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["path"].endswith("app.py")
    assert "def main()" in data[0]["content"]


def test_no_separators(sample_project):
    result = run_cli(str(sample_project / "src" / "app.py"), "--no-separators")
    assert result.exit_code == 0
    assert "---" not in result.output
    assert "def main()" in result.output


def test_line_numbers(sample_project):
    result = run_cli(str(sample_project / "src" / "app.py"), "--line-numbers")
    assert result.exit_code == 0
    assert "1  def main():" in result.output
    assert "2      return 1" in result.output


def test_add_line_numbers_alignment():
    out = add_line_numbers("a\nb\nc\nd\ne\nf\ng\nh\ni\nj")
    lines = out.splitlines()
    assert lines[0] == " 1  a"
    assert lines[9] == "10  j"


# --------------------------------------------------------------------------- #
# Edge cases: binary files
# --------------------------------------------------------------------------- #
def test_binary_file_is_skipped(tmpdir):
    binary = tmpdir / "image.png"
    binary.write_binary(b"\x89PNG\r\n\x1a\n\x00\x00\x00binarydata\xff\xfe")
    result = run_cli(str(tmpdir))
    assert result.exit_code == 0
    # The binary file is not rendered as a document...
    assert "binarydata" not in result.output
    # ...but a warning is emitted to stderr so it is handled gracefully.
    # (FN-014: our own vocabulary, not a Python exception name.)
    assert "not UTF-8 text (BINARY)" in result.output and "UnicodeDecodeError" not in result.output


# --------------------------------------------------------------------------- #
# Core algorithm (generate_prompt)
# --------------------------------------------------------------------------- #
def test_generate_prompt_returns_text(sample_project):
    text = generate_prompt([str(sample_project / "src" / "app.py")])
    assert "def main()" in text
    assert "---" in text


def test_generate_prompt_json_round_trip(sample_project):
    text = generate_prompt(
        [str(sample_project / "src")], format="json", line_numbers=True
    )
    data = json.loads(text)
    assert all("path" in d and "content" in d for d in data)
    assert any("1  " in d["content"] for d in data)


# --------------------------------------------------------------------------- #
# Size cap
# --------------------------------------------------------------------------- #
def test_size_cap_skips_large_file(tmpdir, monkeypatch):
    (tmpdir / "big.txt").write("x" * 1000)
    (tmpdir / "small.txt").write("y")
    monkeypatch.setattr(cli_module, "_MAX_FILE_BYTES", 100)
    result = run_cli(str(tmpdir))
    assert result.exit_code == 0
    # The big file's content is excluded; only the warning mentions it.
    assert "xxx" not in result.output
    assert "small.txt" in result.output
    assert "size limit (TOO_LARGE)" in result.output
    monkeypatch.undo()


# (The default limit, its parsing and its messages: tests/test_size_limit.py.)


# --------------------------------------------------------------------------- #
# .files-to-prompt config file (CLI consumes it; CLI flags override)
# --------------------------------------------------------------------------- #
def test_config_file_sets_format(tmpdir, monkeypatch):
    (tmpdir / ".files-to-prompt").write('[output]\nformat = "json"\n')
    (tmpdir / "app.py").write("print(1)\n")
    monkeypatch.chdir(tmpdir)
    result = run_cli("app.py")
    assert result.exit_code == 0
    data = json.loads(result.output)  # JSON means the config was honored
    assert data[0]["path"].endswith("app.py")


def test_config_flag_overrides(tmpdir, monkeypatch):
    (tmpdir / ".files-to-prompt").write('[output]\nformat = "json"\n')
    (tmpdir / "app.py").write("print(1)\n")
    monkeypatch.chdir(tmpdir)
    result = run_cli("app.py", "--format", "xml")
    assert result.exit_code == 0
    assert result.output.startswith("<documents>")


def test_config_hidden_and_exclude(tmpdir, monkeypatch):
    (tmpdir / ".files-to-prompt").write('[ignore]\nhidden = true\n\n[exclude]\npatterns = ["*.md"]\n')
    (tmpdir / "app.py").write("print(1)\n")
    (tmpdir / ".env").write("secret\n")
    (tmpdir / "notes.md").write("# doc\n")
    monkeypatch.chdir(tmpdir)
    result = run_cli(".")
    assert result.exit_code == 0
    assert ".env" in result.output        # hidden included per config
    assert "notes.md" not in result.output  # excluded per config
    assert "app.py" in result.output


def test_config_cli_patterns_merge(tmpdir, monkeypatch):
    (tmpdir / ".files-to-prompt").write('[exclude]\npatterns = ["*.md"]\n')
    (tmpdir / "app.py").write("print(1)\n")
    (tmpdir / "notes.md").write("# doc\n")
    (tmpdir / "data.csv").write("a,b\n")
    monkeypatch.chdir(tmpdir)
    result = run_cli(".", "--ignore-patterns", "*.csv")
    assert result.exit_code == 0
    assert "app.py" in result.output
    assert "notes.md" not in result.output  # from config
    assert "data.csv" not in result.output  # from CLI flag


def test_no_config_returns_empty(tmpdir, monkeypatch):
    monkeypatch.chdir(tmpdir)  # empty dir, no config
    assert read_project_config() == {}


# --------------------------------------------------------------------------- #
# Token budget (--max-tokens)
# --------------------------------------------------------------------------- #
def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("aaaaaaaa") == 2  # ~4 chars / token


def test_apply_token_budget_disabled_by_zero():
    docs = [("a", "x" * 100), ("b", "y")]
    assert apply_token_budget(docs, 0) == docs
    assert apply_token_budget(docs, -5) == docs


def test_apply_token_budget_fits():
    docs = [("a", "x" * 40), ("b", "y")]
    out = apply_token_budget(docs, 100)
    assert [p for p, _ in out] == ["a", "b"]


def test_apply_token_budget_truncates_and_drops():
    big = "x" * 1000  # ~250 tokens
    docs = [("small", "a" * 40), ("big", big), ("after", "b" * 40)]
    out = apply_token_budget(docs, 20)
    assert [p for p, _ in out] == ["small", "big"]
    assert "truncated" in out[1][1]


def test_max_tokens_cli_flag(sample_project):
    result = run_cli(str(sample_project / "src"), "--max-tokens", "1")
    assert result.exit_code == 0
    # A 1-token budget truncates/drops almost everything.
    assert "truncated to fit" in result.output


def test_heuristic_tokenizer_flag(sample_project):
    result = run_cli(
        str(sample_project / "src"), "--max-tokens", "5", "--tokenizer", "heuristic"
    )
    assert result.exit_code == 0
    assert "truncated to fit" in result.output


def test_tokenizer_requires_tiktoken(sample_project):
    """A non-heuristic tokenizer without tiktoken raises a UsageError."""
    result = run_cli(
        str(sample_project / "src"), "--max-tokens", "5", "--tokenizer", "cl100k_base"
    )
    assert result.exit_code == 2
    assert "requires tiktoken" in result.output


# ---- line endings and separators: normalised on read, never otherwise rewritten ---------------------
def test_line_numbers_split_on_newline_only_and_never_rewrite_other_separators():
    """Python's str.splitlines() also breaks on \\f \\x1c-\\x1e \\x85 \\u2028 \\u2029 and turned them into newlines."""
    from fileflow.cli import add_line_numbers
    odd = "one\x0ctwo\u2028three\x85four\x1cfive"
    assert add_line_numbers(odd) == "1  " + odd           # one line: nothing split, nothing altered
    assert add_line_numbers("a\n\nb") == "1  a\n2  \n3  b"
    assert add_line_numbers("\n") == "1  " and add_line_numbers("") == "" and add_line_numbers("a\n") == "1  a"


def test_crlf_and_lone_cr_files_are_read_with_lf_endings(tmp_path):
    from fileflow.cli import collect
    (tmp_path / "win.txt").write_bytes(b"a\r\nb\r\n")
    (tmp_path / "oldmac.txt").write_bytes(b"x\ry\r")
    got = {os.path.basename(p): c for p, c in collect([str(tmp_path)]).documents}
    assert got == {"win.txt": "a\nb\n", "oldmac.txt": "x\ny\n"}
