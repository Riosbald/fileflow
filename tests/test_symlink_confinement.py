"""Symlinks must not smuggle files from outside the walked directory into a prompt."""
import os

import pytest
from click.testing import CliRunner

from fileflow.cli import cli

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks"
)

SECRET = "PRIVATE KEY MATERIAL"


@pytest.fixture
def tree(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "id_rsa").write_text(SECRET)
    (outside / "dir").mkdir()
    (outside / "dir" / "inner.txt").write_text(SECRET + " inner")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "real.py").write_text("print('ok')\n")
    (proj / "target.txt").write_text("inside target\n")
    os.symlink(outside / "id_rsa", proj / "notes.txt")        # file -> outside
    os.symlink(outside / "dir", proj / "linkdir")             # dir  -> outside
    os.symlink(proj / "target.txt", proj / "alias.txt")       # file -> inside
    return proj, outside


def run(*args):
    return CliRunner().invoke(cli, [str(a) for a in args])


def test_outside_file_symlink_is_not_included(tree):
    proj, _ = tree
    r = run(proj)
    assert r.exit_code == 0
    assert SECRET not in r.output
    assert "real.py" in r.output


def test_outside_dir_symlink_is_not_walked(tree):
    proj, _ = tree
    assert "inner.txt" not in run(proj).output


def test_skip_is_reported_not_silent(tree):
    proj, _ = tree
    r = run(proj)
    assert "notes.txt" in r.output and "outside" in r.output.lower()


def test_inside_symlink_to_file_still_included(tree):
    proj, _ = tree
    assert "inside target" in run(proj).output


def test_explicit_file_argument_is_the_users_choice(tree):
    proj, outside = tree
    assert SECRET in run(outside / "id_rsa").output


def test_server_prompt_does_not_leak(tree):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fileflow.server.app import build_app

    proj, _ = tree
    body = TestClient(build_app(project_root=str(proj))).get("/api/prompt").text
    assert SECRET not in body and "real.py" in body
