"""The default per-file size limit (FN-014, decision A).

Before: no cap at all. Any file of any size was read whole into memory and sent, which is how a
400 KB lockfile became 99.9 % of a ~100,000-token prompt. Now: 1 MiB by default, never silent
(warning + `TOO_LARGE` in the receipt and the web list), and one documented way to lift it.
"""
import json
import os
import subprocess
import sys

import pytest

import fileflow.cli as cm

MIB = 1024 * 1024


def run(args, cwd, env_extra=None, unset=True):
    """A clean process, so the REAL import-time default is what gets tested."""
    env = {k: v for k, v in os.environ.items() if not (unset and k == "FILEFLOW_MAX_FILE_BYTES")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})})
    code = "import sys; from fileflow.cli import cli; cli(sys.argv[1:])"
    return subprocess.run([sys.executable, "-c", code] + list(args), cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "small.py").write_text("print('small')\n")
    (tmp_path / "exact.txt").write_text("e" * MIB)             # exactly at the limit: kept
    (tmp_path / "over.log").write_text("o" * (MIB + 1))        # one byte over: skipped
    return tmp_path


# ---- the default -------------------------------------------------------------------------------
def test_default_is_one_mebibyte_exclusive_of_the_boundary(proj):
    assert cm.DEFAULT_MAX_FILE_BYTES == MIB
    r = run(["."], proj)
    assert r.returncode == 0
    assert "small.py" in r.stdout and "exact.txt" in r.stdout
    assert "o" * 100 not in r.stdout and "over.log" not in r.stdout


def test_a_skipped_file_is_never_silent_and_says_how_to_include_it(proj):
    r = run(["."], proj)
    line = next(l for l in r.stderr.splitlines() if "over.log" in l)
    assert "1.0 MB" in line and "size limit (TOO_LARGE)" in line
    assert "FILEFLOW_MAX_FILE_BYTES=0" in line


def test_the_remedy_in_the_message_actually_works(proj):
    r = run(["."], proj, env_extra={"FILEFLOW_MAX_FILE_BYTES": "0"})
    assert "over.log" in r.stdout and not any("TOO_LARGE" in l for l in r.stderr.splitlines())
    bigger = run(["."], proj, env_extra={"FILEFLOW_MAX_FILE_BYTES": str(2 * MIB)})
    assert "over.log" in bigger.stdout


def test_the_receipt_records_it_as_too_large(proj):
    r = run([".", "--receipt-file", "r.json"], proj)
    rec = json.loads((proj / "r.json").read_text())
    assert {"path": "over.log", "reason": "TOO_LARGE"} in rec["excluded"]
    assert all(i["path"] != "over.log" for i in rec["included"])


def test_a_file_named_on_the_command_line_is_capped_too(proj):
    r = run(["over.log"], proj)
    assert "o" * 100 not in r.stdout          # the content never went out
    assert "over.log" in r.stderr and "TOO_LARGE" in r.stderr


# ---- the environment variable -------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    (None, MIB), ("", MIB), ("   ", MIB), ("0", 0), ("123", 123), (" 42 ", 42), ("5242880", 5242880),
])
def test_valid_values(raw, expected):
    assert cm.parse_max_file_bytes(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["abc", "-5", "1.5", "10MB", "1e6", "0x10"])
def test_garbage_falls_back_to_the_default_and_says_so(raw):
    value, problem = cm.parse_max_file_bytes(raw)
    assert value == MIB and raw in problem and "use 0 for no limit" in problem


def test_garbage_in_the_environment_neither_crashes_nor_disables_the_guard(proj):
    """It used to be a bare int(): a typo meant a traceback on every command."""
    r = run(["."], proj, env_extra={"FILEFLOW_MAX_FILE_BYTES": "10MB"})
    assert r.returncode == 0 and "Traceback" not in r.stderr
    assert "'10MB' is not a whole number of bytes" in r.stderr
    assert "over.log" not in r.stdout          # the default guard is still on


def test_human_sizes():
    assert cm.human_size(0) == "0 bytes" and cm.human_size(1023) == "1023 bytes"
    assert cm.human_size(1024) == "1.0 KB" and cm.human_size(MIB) == "1.0 MB"
    assert cm.human_size(3 * MIB + MIB // 2) == "3.5 MB"


# ---- the web server ----------------------------------------------------------------------------
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fileflow.server.app import build_app  # noqa: E402


def test_server_lists_it_as_left_out_and_refuses_to_serve_it(proj, monkeypatch):
    monkeypatch.setattr(cm, "_MAX_FILE_BYTES", MIB)
    with TestClient(build_app(project_root=str(proj))) as c:
        meta = c.get("/api/prompt").json()["meta"]
        assert {"path": "over.log", "reason": "TOO_LARGE", "label": "over the file size limit"} in meta["left_out"]
        r = c.get("/api/file", params={"path": "over.log"})
        assert r.status_code == 413
        d = r.json()["detail"]
        assert "over.log" in d and "1.0 MB" in d and "FILEFLOW_MAX_FILE_BYTES=0" in d
        assert c.get("/api/file", params={"path": "small.py"}).status_code == 200
        assert c.get("/api/file", params={"path": "exact.txt"}).status_code == 200


def test_server_serves_big_files_when_the_limit_is_lifted(proj, monkeypatch):
    monkeypatch.setattr(cm, "_MAX_FILE_BYTES", 0)
    with TestClient(build_app(project_root=str(proj))) as c:
        assert c.get("/api/file", params={"path": "over.log"}).status_code == 200


def test_the_message_reports_the_files_real_size_not_just_the_limit(tmp_path):
    (tmp_path / "dump.sql").write_text("d" * (3 * MIB + MIB // 2))
    (tmp_path / "ok.py").write_text("x = 1\n")
    line = next(l for l in run(["."], tmp_path).stderr.splitlines() if "dump.sql" in l)
    assert "3.5 MB, over the 1.0 MB size limit" in line
