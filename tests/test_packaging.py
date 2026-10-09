"""The built wheel must contain the web client, or `fileflow serve` is API-only."""
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

REPO = os.path.join(os.path.dirname(__file__), "..")


def _clean_copy(dest):
    """Copy the sources only, so stale build/ or egg-info/ can't change the result."""
    ignore = shutil.ignore_patterns(
        ".git", ".venv", "build", "dist", "*.egg-info", "__pycache__", "node_modules"
    )
    shutil.copytree(os.path.abspath(REPO), dest, ignore=ignore)


def test_wheel_contains_the_web_client(tmp_path):
    src = str(tmp_path / "src")
    out = tmp_path / "out"
    _clean_copy(src)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", src, "--no-deps",
         "-q", "-w", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        if os.environ.get("CI"):  # CI has network: a failed build is a real failure
            pytest.fail("wheel build failed: " + proc.stderr[-500:])
        pytest.skip("cannot build a wheel in this environment: " + proc.stderr[-300:])
    wheel = next(p for p in os.listdir(out) if p.endswith(".whl"))
    names = zipfile.ZipFile(os.path.join(out, wheel)).namelist()
    for needed in ("fileflow/web/index.html", "fileflow/web/css/styles.css",
                   "fileflow/web/js/app.js", "fileflow/web/js/core.js",
                   "fileflow/web/fonts/inter-latin-wght-normal.woff2",
                   "fileflow/web/fonts/jetbrains-mono-latin-wght-normal.woff2",
                   "fileflow/web/fonts/OFL-Inter.txt"):
        assert needed in names, needed + " missing from wheel"
    assert not any(n.endswith(".test.js") for n in names), "test files shipped in wheel"
