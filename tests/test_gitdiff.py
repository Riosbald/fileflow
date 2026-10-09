"""--diff / --staged / --since / --patch: correctness, and git run defensively inside a possibly hostile repo."""
import json
import os
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from fileflow import gitdiff
from fileflow.cli import cli
from fileflow.errors import FileflowError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # assembled so this file is not itself a finding


def git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(root)] + list(args), env=env, check=True, capture_output=True, text=True).stdout


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    write(tmp_path, ".gitignore", "ignored.log\n")
    for rel in ("a.py", "b.py", "src/c.py", "src/d.py"):
        write(tmp_path, rel, rel + " v1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    return tmp_path


def rels(root, paths):
    return sorted(os.path.relpath(p, str(root.resolve())).replace(os.sep, "/") for p in paths)


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


def docs(result):
    return sorted(d["path"] for d in json.loads(result.stdout))


# ---- what counts as changed -------------------------------------------------------------------------------
def test_head_mode_is_modified_plus_untracked_but_not_ignored_or_deleted(repo):
    write(repo, "a.py", "a.py v2\n")                       # modified
    write(repo, "new.py", "n\n")                           # untracked
    write(repo, "ignored.log", "x\n")                      # untracked but ignored
    os.remove(repo / "b.py")                               # deleted: nothing to read
    assert rels(repo, gitdiff.changed_files(str(repo), "head")) == ["a.py", "new.py"]


def test_staged_mode_sees_only_the_index(repo):
    write(repo, "a.py", "a.py v2\n")
    git(repo, "add", "a.py")
    write(repo, "b.py", "b.py v2\n")                       # modified, NOT staged
    write(repo, "new.py", "n\n")                           # untracked
    assert rels(repo, gitdiff.changed_files(str(repo), "staged")) == ["a.py"]


def test_since_a_commit(repo):
    write(repo, "a.py", "a.py v2\n")
    git(repo, "commit", "-qam", "two")
    write(repo, "src/c.py", "c v2\n")
    first = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    assert rels(repo, gitdiff.changed_files(str(repo), "since", first)) == ["a.py", "src/c.py"]
    assert rels(repo, gitdiff.changed_files(str(repo), "since", "HEAD")) == ["src/c.py"]
    assert rels(repo, gitdiff.changed_files(str(repo), "since", "HEAD~1")) == ["a.py", "src/c.py"]


def test_renames_and_filenames_with_spaces_and_unicode(repo):
    git(repo, "mv", "a.py", "renamed file \u00e9.py")
    write(repo, "sp ace/\u65e5\u672c.py", "x\n")
    assert rels(repo, gitdiff.changed_files(str(repo), "head")) == ["renamed file \u00e9.py", "sp ace/\u65e5\u672c.py"]


def test_a_repository_with_no_commits_yet(tmp_path):
    git(tmp_path, "init", "-q")
    write(tmp_path, "a.py", "a\n")
    write(tmp_path, "b.py", "b\n")
    git(tmp_path, "add", "a.py")
    assert rels(tmp_path, gitdiff.changed_files(str(tmp_path), "head")) == ["a.py", "b.py"]
    assert rels(tmp_path, gitdiff.changed_files(str(tmp_path), "staged")) == ["a.py"]
    assert "+++ b/a.py" in gitdiff.patch_text(str(tmp_path), "staged")   # a patch with no HEAD to compare against


# ---- CLI ------------------------------------------------------------------------------------------------------
def test_cli_diff_defaults_to_the_current_directory(repo):
    write(repo, "a.py", "a.py v2\n")
    write(repo, "new.py", "n\n")
    assert docs(run(["--diff", "--format", "json"], repo)) == ["./a.py", "./new.py"]


def test_cli_diff_inside_a_subdirectory_of_the_repo(repo):
    write(repo, "a.py", "a.py v2\n")
    write(repo, "src/c.py", "c v2\n")
    assert docs(run(["--diff", "--format", "json"], repo / "src")) == ["./c.py"]


def test_cli_staged_and_since(repo):
    write(repo, "a.py", "a.py v2\n")
    git(repo, "add", "a.py")
    write(repo, "b.py", "b.py v2\n")
    assert docs(run(["--staged", "--format", "json"], repo)) == ["./a.py"]
    assert docs(run(["--since", "HEAD", "--format", "json"], repo)) == ["./a.py", "./b.py"]


def test_cli_patch_is_one_extra_document_with_the_hunks(repo):
    write(repo, "a.py", "a.py v2\n")
    out = run(["--patch", "--format", "json"], repo)
    by = {d["path"]: d["content"] for d in json.loads(out.stdout)}
    assert set(by) == {"./a.py", "git-diff.patch"}
    assert "-a.py v1" in by["git-diff.patch"] and "+a.py v2" in by["git-diff.patch"] and by["git-diff.patch"].startswith("diff --git")


def test_cli_explicit_paths_are_filtered_too(repo):
    write(repo, "a.py", "a.py v2\n")
    assert docs(run(["a.py", "b.py", "--diff", "--format", "json"], repo)) == ["a.py"]


def test_receipt_explains_the_selection(repo):
    write(repo, "a.py", "a.py v2\n")
    run(["--diff", "--receipt-file", "r.json"], repo)
    rec = json.loads((repo / "r.json").read_text())
    assert rec["changes"] == {"mode": "head", "base": None}
    assert {"path": "b.py", "reason": "NOT_CHANGED"} in rec["excluded"]
    assert any("limited to changed files" in v for v in rec["ledger"]["verified"])
    assert "NOT_CHANGED" in __import__("fileflow.walker", fromlist=["REASONS"]).REASONS


def test_nothing_changed_warns_instead_of_failing(repo):
    r = run(["--diff"], repo)
    assert r.exit_code == 0 and "no changed files" in r.stderr.lower()


def test_staged_and_since_together_is_a_usage_error(repo):
    r = run(["--staged", "--since", "HEAD"], repo)
    assert r.exit_code == 2 and "E_USAGE" in r.stderr


def test_preset_can_ask_for_the_diff(repo):
    (repo / ".files-to-prompt").write_text('version = 1\n[output]\nformat = "json"\n[presets.review]\npaths = ["."]\ndiff = true\npatch = true\n')
    write(repo, "a.py", "a.py v2\n")
    names = docs(run(["--preset", "review"], repo))
    assert "./a.py" in names and "git-diff.patch" in names and "./b.py" not in names


def test_bad_diff_flag_type_in_a_preset(repo):
    (repo / ".files-to-prompt").write_text('version = 1\n[presets.p]\npaths = ["."]\ndiff = "yes"\n')
    r = run(["--preset", "p"], repo)
    assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr


# ---- errors ------------------------------------------------------------------------------------------------------
def test_not_a_repository_is_a_clear_error(tmp_path):
    write(tmp_path, "a.py", "x\n")
    r = run(["--diff"], tmp_path)
    assert r.exit_code == 1 and "E_IO" in r.stderr and "not a git repository" in r.stderr


def test_git_missing_is_a_clear_error(repo, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    r = run(["--diff"], repo)
    assert r.exit_code == 1 and "git is not installed" in r.stderr


def test_unknown_ref(repo):
    r = run(["--since", "no-such-branch"], repo)
    assert r.exit_code == 2 and "does not know a commit" in r.stderr


# ---- option injection through --since ---------------------------------------------------------------------------------
@pytest.mark.parametrize("ref", ["--output=/tmp/fileflow-pwned", "-p", "--exec=touch", "HEAD..HEAD~1", "a b", "$(touch /tmp/x)", "HEAD;rm", "", "-", "main.lock"])
def test_since_refs_that_look_like_options_or_syntax_are_refused(repo, ref, tmp_path):
    with pytest.raises(FileflowError) as e:
        gitdiff.resolve_ref(str(repo), ref)
    assert e.value.code == "E_USAGE"
    assert not os.path.exists("/tmp/fileflow-pwned")


def test_since_never_hands_the_raw_ref_to_git_diff(repo, monkeypatch):
    seen = []
    real = gitdiff._git
    monkeypatch.setattr(gitdiff, "_git", lambda root, *a, **k: (seen.append(a), real(root, *a, **k))[1])
    gitdiff.changed_files(str(repo), "since", "HEAD")
    diff_calls = [a for a in seen if a and a[0] == "diff"]
    assert diff_calls and all("HEAD" not in a and any(len(x) == 40 for x in a) for a in diff_calls)


# ---- a hostile repository must not get code executed by the commands we run ------------------------------------------------
def _marker(tmp_path):
    return tmp_path.parent / (tmp_path.name + "-pwned")


def test_core_fsmonitor_in_repo_config_is_not_executed(repo):
    marker = _marker(repo)
    hook = repo.parent / (repo.name + "-hook.sh")
    hook.write_text("#!/bin/sh\ntouch %s\n" % marker)
    hook.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(hook))
    write(repo, "a.py", "a.py v2\n")
    assert docs(run(["--diff", "--patch", "--format", "json"], repo))      # works...
    assert not marker.exists(), "core.fsmonitor from .git/config was executed"


def test_external_diff_driver_is_not_executed(repo):
    marker = _marker(repo)
    tool = repo.parent / (repo.name + "-ext.sh")
    tool.write_text("#!/bin/sh\ntouch %s\n" % marker)
    tool.chmod(0o755)
    git(repo, "config", "diff.external", str(tool))
    write(repo, "a.py", "a.py v2\n")
    out = run(["--patch", "--format", "json"], repo)
    assert "git-diff.patch" in docs(out) and not marker.exists(), "diff.external was executed"


def test_textconv_filter_is_not_executed(repo):
    marker = _marker(repo)
    tool = repo.parent / (repo.name + "-tc.sh")
    tool.write_text("#!/bin/sh\ntouch %s\ncat \"$1\"\n" % marker)
    tool.chmod(0o755)
    write(repo, ".gitattributes", "*.py diff=pwn\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-qm", "attrs")
    git(repo, "config", "diff.pwn.textconv", str(tool))
    write(repo, "a.py", "a.py v2\n")
    out = run(["--patch", "--format", "json"], repo)
    assert "git-diff.patch" in docs(out) and not marker.exists(), "diff.<driver>.textconv was executed"


# ---- secrets in the patch -----------------------------------------------------------------------------------------------
def test_a_secret_that_only_appears_in_a_removed_line_is_still_caught(repo):
    write(repo, "cfg.py", 'AWS = "%s"\n' % KEY)
    git(repo, "add", "cfg.py")
    git(repo, "commit", "-qm", "oops")
    write(repo, "cfg.py", 'AWS = os.environ["AWS"]\n')                         # fixed in the working tree...
    r = run(["--patch", "--secrets", "block", "--format", "json"], repo)         # ...but the patch still shows the key
    assert r.exit_code == 3 and "E_SECRET_FOUND" in r.stderr and "patch" in r.stderr and KEY not in r.stderr and r.stdout == ""
    ex = run(["--patch", "--secrets", "exclude", "--format", "json"], repo)
    assert ex.exit_code == 0 and "git-diff.patch" not in docs(ex) and KEY not in ex.stdout and "Left out the patch" in ex.stderr
    warn = run(["--patch", "--format", "json"], repo)
    assert warn.exit_code == 0 and "git-diff.patch" in docs(warn) and "patch contains a possible secret" in warn.stderr and KEY not in warn.stderr


def test_patch_is_size_capped(repo):
    write(repo, "a.py", "x\n" * 5000)
    text = gitdiff.patch_text(str(repo), "head", limit=500)
    assert "patch truncated at 500 bytes" in text and len(text) < 800
