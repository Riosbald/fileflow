"""The prompt, the tree and the project count must agree -- they share one walker."""
import os

import pytest

from fileflow.cli import collect
from fileflow import walker
from fileflow.server import workspace


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    (p / "sub").mkdir(parents=True)
    (p / "sub" / ".gitignore").write_text("*.tmp\n")
    (p / "sub" / "real.py").write_text("x = 1\n")
    (p / "sub" / "scratch.tmp").write_text("junk\n")
    (p / "top.py").write_text("y = 2\n")
    (p / ".hidden").write_text("h\n")
    (p / "empty").mkdir()
    return p


def tree_files(nodes, prefix=""):
    out = []
    for n in nodes:
        path = prefix + n["name"]
        if n["type"] == "dir":
            out += tree_files(n["children"], path + "/")
        else:
            out.append(path)
    return out


def test_tree_project_and_prompt_agree_on_nested_gitignore(proj):
    prompt_files = sorted(os.path.relpath(p, proj) for p, _ in collect([str(proj)]).documents)
    shown = sorted(tree_files(workspace.build_tree(str(proj))))
    info = workspace.project_info(str(proj))
    assert prompt_files == shown == ["sub/real.py", "top.py"]
    assert info["fileCount"] == 2


def test_tree_keeps_empty_directories(proj):
    names = [n["name"] for n in workspace.build_tree(str(proj))]
    assert "empty" in names and "sub" in names


def test_tree_and_count_follow_include_hidden(proj):
    shown = tree_files(workspace.build_tree(str(proj), include_hidden=True))
    assert ".hidden" in shown
    assert workspace.project_info(str(proj), include_hidden=True)["fileCount"] == 4  # .hidden, sub/.gitignore, sub/real.py, top.py


def test_every_exclusion_has_a_known_reason(proj, tmp_path):
    (tmp_path / "secret").write_text("s")
    os.symlink(tmp_path / "secret", proj / "link.txt")
    (proj / "bin.dat").write_bytes(b"\xff\xfe\x00\x80")
    col = collect([str(proj)], ignore_patterns=["nothing"])
    reasons = {e.reason for e in col.excluded}
    assert reasons <= set(walker.REASONS)
    assert {"HIDDEN", "GITIGNORED", "SYMLINK_OUTSIDE_ROOT", "BINARY"} <= reasons


def test_tree_hides_outside_symlinks(proj, tmp_path):
    (tmp_path / "secret").write_text("s")
    os.symlink(tmp_path / "secret", proj / "link.txt")
    assert "link.txt" not in tree_files(workspace.build_tree(str(proj)))
