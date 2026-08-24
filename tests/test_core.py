from pathlib import Path

import pytest

from fileflow.core import (
    DEFAULT_OPTIONS,
    FileEntry,
    RenderOptions,
    add_line_numbers,
    build_prompt,
    collect_files,
    estimate_tokens,
    render,
)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# readme\n")
    (tmp_path / ".secret").write_text("hidden\n")
    (tmp_path / "notes.log").write_text("log line\n")
    (tmp_path / "image.bin").write_bytes(b"\x00\xff\x00\xff")
    return tmp_path


def opts(**kw) -> RenderOptions:
    return RenderOptions(**DEFAULT_OPTIONS).merged(kw)


def paths_of(entries):
    return [e.path for e in entries]


class TestCollect:
    def test_basic_collection_sorted(self, tree):
        entries = collect_files([tree], opts())
        names = [Path(p).name for p in paths_of(entries)]
        assert names == ["README.md", "notes.log", "app.py"]

    def test_binary_skipped(self, tree):
        entries = collect_files([tree], opts())
        assert not any(p.endswith("image.bin") for p in paths_of(entries))

    def test_hidden_excluded_by_default(self, tree):
        entries = collect_files([tree], opts())
        assert not any(".secret" in p for p in paths_of(entries))

    def test_include_hidden(self, tree):
        entries = collect_files([tree], opts(include_hidden=True))
        assert any(".secret" in p for p in paths_of(entries))

    def test_exclude_patterns(self, tree):
        entries = collect_files([tree], opts(exclude=("*.log",)))
        assert not any(p.endswith(".log") for p in paths_of(entries))

    def test_exclude_directory_pattern(self, tree):
        entries = collect_files([tree], opts(exclude=("src",)))
        assert not any("/src/" in p for p in paths_of(entries))

    def test_gitignore_honored(self, tree):
        (tree / ".gitignore").write_text("*.log\n")
        entries = collect_files([tree], opts())
        assert not any(p.endswith(".log") for p in paths_of(entries))

    def test_gitignore_ignored_when_asked(self, tree):
        (tree / ".gitignore").write_text("*.log\n")
        entries = collect_files([tree], opts(ignore_gitignore=True))
        assert any(p.endswith(".log") for p in paths_of(entries))

    def test_nested_gitignore(self, tree):
        (tree / "src" / ".gitignore").write_text("app.py\n")
        entries = collect_files([tree], opts())
        assert not any(p.endswith("app.py") for p in paths_of(entries))

    def test_single_file_path(self, tree):
        entries = collect_files([tree / "README.md"], opts())
        assert len(entries) == 1
        assert entries[0].content == "# readme\n"

    def test_git_dir_always_skipped(self, tree):
        (tree / ".git").mkdir()
        (tree / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        entries = collect_files([tree], opts(include_hidden=True))
        assert not any(".git/" in p for p in paths_of(entries))


class TestRender:
    ENTRIES = [
        FileEntry("a.py", "one\ntwo\n"),
        FileEntry("b.md", "# hello\n"),
    ]

    def test_default_format(self):
        out = render(self.ENTRIES, opts())
        assert "a.py\n---\none\ntwo\n\n---" in out
        assert "b.md" in out

    def test_separators(self):
        out = render(self.ENTRIES, opts(separators=True))
        assert "FILE: a.py" in out
        assert "=" * 48 in out

    def test_markdown_format(self):
        out = render(self.ENTRIES, opts(format="markdown"))
        assert "```python" in out
        assert "```markdown" in out

    def test_markdown_fence_escalation(self):
        entries = [FileEntry("x.md", "```\ncode\n```\n")]
        out = render(entries, opts(format="markdown"))
        assert "````" in out

    def test_xml_format(self):
        out = render(self.ENTRIES, opts(format="xml"))
        assert out.startswith("<documents>")
        assert '<document index="1">' in out
        assert "<source>a.py</source>" in out
        assert out.rstrip().endswith("</documents>")

    def test_json_format(self):
        import json

        out = render(self.ENTRIES, opts(format="json"))
        data = json.loads(out)
        assert [f["path"] for f in data["files"]] == ["a.py", "b.md"]
        assert data["truncated"] is False

    def test_line_numbers(self):
        out = render(self.ENTRIES, opts(line_numbers=True))
        assert "1  one" in out
        assert "2  two" in out

    def test_max_tokens_truncates(self):
        big = [FileEntry(f"f{i}.txt", "x" * 400) for i in range(10)]
        out = render(big, opts(max_tokens=250))
        assert "truncated" in out
        assert out.count(".txt") < 10

    def test_max_tokens_json_meta(self):
        import json

        big = [FileEntry(f"f{i}.txt", "x" * 400) for i in range(10)]
        data = json.loads(render(big, opts(format="json", max_tokens=250)))
        assert data["truncated"] is True
        assert len(data["files"]) < 10

    def test_no_truncation_without_budget(self):
        big = [FileEntry(f"f{i}.txt", "x" * 400) for i in range(10)]
        out = render(big, opts())
        assert "truncated" not in out

    def test_unknown_format_rejected(self):
        with pytest.raises(ValueError):
            opts(format="yaml")

    def test_empty_render(self):
        assert render([], opts()) == ""
        assert "<documents>" in render([], opts(format="xml"))


class TestHelpers:
    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("ab") == 1
        assert estimate_tokens("x" * 400) == 100

    def test_add_line_numbers_preserves_trailing_newline(self):
        assert add_line_numbers("a\nb\n").endswith("\n")
        assert not add_line_numbers("a\nb").endswith("\n")

    def test_merged_exclude_is_union(self):
        o = opts(exclude=("*.log",)).merged({"exclude": ("*.tmp", "*.log")})
        assert o.exclude == ("*.log", "*.tmp")

    def test_merged_ignores_none(self):
        o = opts(format="xml").merged({"format": None, "line_numbers": None})
        assert o.format == "xml"

    def test_build_prompt(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        out = build_prompt([tmp_path], opts())
        assert "hello" in out
