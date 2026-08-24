import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from fileflow.cli import cli, read_project_config
from fileflow.config import write_config


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# readme\n")
    (tmp_path / "notes.log").write_text("log\n")
    (tmp_path / ".hidden.txt").write_text("shh\n")
    return tmp_path


def run(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_default_output(project):
    out = run("prompt", str(project))
    assert "README.md" in out and "---" in out


def test_config_sets_format(project):
    write_config(project, {"format": "json"})
    data = json.loads(run("prompt", str(project)))
    assert any(f["path"].endswith("app.py") for f in data["files"])


def test_cli_flag_overrides_config(project):
    write_config(project, {"format": "json"})
    out = run("prompt", str(project), "--format", "xml")
    assert out.startswith("<documents>")


def test_config_hidden_and_exclude(project):
    write_config(project, {"include_hidden": True, "exclude": {"patterns": ["*.log"]}})
    out = run("prompt", str(project))
    assert ".hidden.txt" in out
    assert "notes.log" not in out


def test_cli_and_config_patterns_merge(project):
    write_config(project, {"exclude": {"patterns": ["*.log"]}})
    out = run("prompt", str(project), "-e", "*.md")
    assert "notes.log" not in out
    assert "README.md" not in out
    assert "app.py" in out


def test_no_config_returns_empty(project):
    assert read_project_config(project) == {}


def test_config_line_numbers_and_flag_off(project):
    write_config(project, {"line_numbers": True})
    assert "1  # readme" in run("prompt", str(project))
    assert "1  # readme" not in run("prompt", str(project), "--no-line-numbers")


def test_config_max_tokens(project):
    (project / "big.txt").write_text("x" * 4000)
    write_config(project, {"max_tokens": 50})
    out = run("prompt", str(project))
    assert "truncated" in out


def test_config_root_from_file_argument(project):
    write_config(project, {"format": "xml"})
    out = run("prompt", str(project / "README.md"))
    assert out.startswith("<documents>")


def test_output_flag_writes_file(project, tmp_path):
    target = tmp_path / "out.txt"
    run("prompt", str(project), "-o", str(target))
    assert "README.md" in target.read_text()


def test_exclude_flag_repeatable(project):
    out = run("prompt", str(project), "-e", "*.log", "-e", "*.md")
    assert "notes.log" not in out and "README.md" not in out


def test_cli_imports_without_fastapi():
    """The CLI must load with zero server dependencies."""
    code = (
        "import sys; import fileflow.cli; "
        "assert 'fastapi' not in sys.modules, 'fastapi leaked into CLI import'; "
        "assert 'uvicorn' not in sys.modules, 'uvicorn leaked into CLI import'; "
        "print('clean')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "clean"
