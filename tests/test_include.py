"""`[include]` patterns: narrow a walk to matching files. Exclusions still win; explicit files bypass it."""
import json
import os

import pytest
from click.testing import CliRunner

from fileflow import walker
from fileflow.cli import cli, collect
from fileflow.server import config as C
from fileflow.server import workspace


@pytest.fixture
def proj(tmp_path):
    for rel, text in {
        "a.py": "a", "b.txt": "b", "README.md": "r", ".hidden.py": "h", "notes.PY": "upper",
        "src/main.py": "m", "src/util.js": "u", "src/deep/inner.py": "i", "docs/guide.md": "g",
        "dist/out.py": "built", ".gitignore": "dist/\n",
    }.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def names(col, root):
    return sorted(os.path.relpath(p, root).replace(os.sep, "/") for p, _ in col.documents)


# ---- matching semantics ------------------------------------------------------------------------
@pytest.mark.parametrize("pattern,rel,expected", [
    ("*.py", "a.py", True), ("*.py", "src/main.py", True), ("*.py", "notes.PY", False),        # case-sensitive, name match
    ("*.py", "b.txt", False), ("a?.py", "ab.py", True), ("a?.py", "a.py", False), ("a?.py", "abc.py", False),
    ("src/*.py", "src/main.py", True), ("src/*.py", "src/deep/inner.py", True),                 # `*` crosses `/`, like fnmatch
    ("src/*.py", "a.py", False), ("src/*.py", "other/src/main.py", False),                      # path patterns are anchored
    ("docs/guide.md", "docs/guide.md", True), ("docs/guide.md", "docs/guide.mdx", False),
    ("[ab].py", "a.py", False), ("[ab].py", "[ab].py", True),                                   # no character classes: `[` is literal
    ("a.py", "a.py", True), ("a+b.py", "a+b.py", True), ("a.py", "aXpy", False),                # regex metacharacters are literal
    ("*", "anything.at.all", True), ("?", "x", True), ("?", "", False),
])
def test_glob_semantics(pattern, rel, expected):
    name = rel.rsplit("/", 1)[-1]
    assert walker.include_match(rel, name, [pattern]) is expected


def test_any_pattern_may_match():
    assert walker.include_match("b.txt", "b.txt", ["*.py", "*.txt"]) and not walker.include_match("c.md", "c.md", ["*.py", "*.txt"])


def test_empty_include_means_no_filter(proj):
    assert names(collect([str(proj)], include_patterns=[]), proj) == names(collect([str(proj)]), proj)
    assert names(collect([str(proj)], include_patterns=None), proj) == names(collect([str(proj)]), proj)


# ---- walking ----------------------------------------------------------------------------------------
def test_include_narrows_to_matching_files(proj):
    assert names(collect([str(proj)], include_patterns=["*.py"]), proj) == ["a.py", "src/deep/inner.py", "src/main.py"]
    assert names(collect([str(proj)], include_patterns=["src/*"]), proj) == ["src/deep/inner.py", "src/main.py", "src/util.js"]


def test_exclusions_still_win_over_include(proj):
    got = names(collect([str(proj)], include_patterns=["*.py"]), proj)
    assert "dist/out.py" not in got         # gitignored
    assert ".hidden.py" not in got          # hidden
    got = names(collect([str(proj)], include_patterns=["*.py"], ignore_patterns=["inner.py"]), proj)
    assert "src/deep/inner.py" not in got   # --ignore-patterns
    assert ".hidden.py" in names(collect([str(proj)], include_patterns=["*.py"], include_hidden=True), proj)


def test_directories_are_always_walked_so_nested_matches_are_found(proj):
    assert "src/deep/inner.py" in names(collect([str(proj)], include_patterns=["inner.py"]), proj)


def test_explicit_file_arguments_bypass_include(proj):
    col = collect([str(proj / "b.txt"), str(proj / "src")], include_patterns=["*.py"])
    assert names(col, proj) == ["b.txt", "src/deep/inner.py", "src/main.py"]


def test_non_matching_files_are_reported_with_their_own_reason(proj):
    col = collect([str(proj)], include_patterns=["*.py"])
    reasons = {os.path.relpath(e.path, proj): e.reason for e in col.excluded}
    assert reasons["b.txt"] == "NOT_INCLUDED" and reasons["README.md"] == "NOT_INCLUDED" and reasons["src/util.js"] == "NOT_INCLUDED"
    assert "NOT_INCLUDED" in walker.REASONS


# ---- server views -------------------------------------------------------------------------------------
def tree_files(nodes, prefix=""):
    out = []
    for n in nodes:
        path = prefix + n["name"]
        out += tree_files(n["children"], path + "/") if n["type"] == "dir" else [path]
    return sorted(out)


def test_tree_and_count_follow_include_and_drop_empty_folders(proj):
    tree = workspace.build_tree(str(proj), include_patterns=["*.py"])
    assert tree_files(tree) == ["a.py", "src/deep/inner.py", "src/main.py"]
    assert "docs" not in [n["name"] for n in tree]            # no matching file inside: not shown
    assert workspace.project_info(str(proj), include_patterns=["*.py"])["fileCount"] == 3


def test_server_prompt_and_tree_accept_include_patterns(proj):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fileflow.server.app import build_app
    c = TestClient(build_app(project_root=str(proj)))
    docs = json.loads(c.get("/api/prompt", params={"format": "json", "include_patterns": "*.py"}).json()["text"])
    assert sorted(d["path"] for d in docs) == ["a.py", "src/deep/inner.py", "src/main.py"]
    assert tree_files(c.get("/api/tree", params={"include_patterns": "*.md"}).json()) == ["README.md", "docs/guide.md"]
    assert c.get("/api/project", params={"include_patterns": "*.py"}).json()["fileCount"] == 3


# ---- CLI + config layers ----------------------------------------------------------------------------------
def run(args, cwd):
    try:
        runner = CliRunner(mix_stderr=False)
    except TypeError:
        runner = CliRunner()
    old = os.getcwd()
    os.chdir(str(cwd))
    try:
        return runner.invoke(cli, [str(a) for a in args])
    finally:
        os.chdir(old)


def paths_in(out):
    return sorted(l for l in out.splitlines() if l and not l.startswith(("---", "[")) and "." in l and " " not in l and len(l) < 40)


def test_cli_include_flag(proj):
    r = run([".", "--include", "*.py", "--include", "*.md", "--format", "json"], proj)
    got = sorted(d["path"] for d in json.loads(r.stdout))
    assert got == ["./README.md", "./a.py", "./docs/guide.md", "./src/deep/inner.py", "./src/main.py"]


def test_cli_warns_when_nothing_matches(proj):
    r = run([".", "--include", "*.nope"], proj)
    assert r.exit_code == 0 and "no files matched" in r.stderr.lower()


def write_cfg(proj, text):
    (proj / ".files-to-prompt").write_text(text)


def files(r):
    return sorted(d["path"] for d in json.loads(r.stdout))


def test_include_precedence_replaces_rather_than_adds(proj):
    write_cfg(proj, 'version = 1\n[output]\nformat = "json"\n[include]\npatterns = ["*.md"]\n'
                    '[presets.py]\npaths = ["."]\ninclude_patterns = ["*.py"]\n[presets.plain]\npaths = ["."]\n')
    assert files(run(["."], proj)) == ["./README.md", "./docs/guide.md"]                       # root config
    assert files(run(["--preset", "plain"], proj)) == ["./README.md", "./docs/guide.md"]       # inherited
    assert files(run(["--preset", "py"], proj)) == ["./a.py", "./src/deep/inner.py", "./src/main.py"]   # preset replaces root
    assert files(run(["--preset", "py", "--include", "*.txt"], proj)) == ["./b.txt"]           # CLI replaces preset


def test_bad_include_types_are_schema_errors(proj):
    write_cfg(proj, 'version = 1\n[presets.p]\npaths = ["."]\ninclude_patterns = "*.py"\n')
    r = run(["--preset", "p"], proj)
    assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr and "include_patterns" in r.stderr


def test_receipt_accounts_for_every_file_with_include(proj):
    run([".", "--include", "*.py", "--receipt-file", "r.json"], proj)
    rec = json.loads((proj / "r.json").read_text())
    seen = [i["path"] for i in rec["included"] + rec["truncated"] + rec["excluded"]]
    assert len(seen) == len(set(seen)) and {"b.txt", "a.py"} <= set(seen)
    assert {e["reason"] for e in rec["excluded"]} <= set(walker.REASONS)


# ---- saving from the web UI must not erase an include list ---------------------------------------------------
def test_ui_save_without_an_include_section_preserves_the_include_list(tmp_path):
    (tmp_path / ".files-to-prompt").write_text('version = 1\n[include]\npatterns = ["*.py", "docs/*"]\n')
    C.write_config(str(tmp_path), {"output": {"format": "xml"}, "exclude": {"patterns": ["dist"]},
                                   "ignore": {"gitignore": True, "hidden": False}})
    cfg = C.read_config(str(tmp_path), strict=True)
    assert cfg["include"]["patterns"] == ["*.py", "docs/*"] and cfg["output"]["format"] == "xml"


def test_an_explicit_include_in_the_payload_wins(tmp_path):
    (tmp_path / ".files-to-prompt").write_text('[include]\npatterns = ["*.py"]\n')
    C.write_config(str(tmp_path), {"include": {"patterns": ["*.md"]}})
    assert C.read_config(str(tmp_path), strict=True)["include"]["patterns"] == ["*.md"]
    C.write_config(str(tmp_path), {"include": {"patterns": []}})            # an explicit empty list clears it
    assert C.read_config(str(tmp_path), strict=True)["include"]["patterns"] == []
