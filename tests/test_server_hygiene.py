"""Served prompts must not leak absolute local paths or secrets."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fileflow.server.app import build_app  # noqa: E402

KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # assembled so this file is not itself a finding


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "src" / "creds.py").write_text('AWS = "%s"\n' % KEY)
    return tmp_path


def client(proj):
    return TestClient(build_app(project_root=str(proj)))


def test_prompt_paths_are_relative_to_the_served_root(proj):
    text = client(proj).get("/api/prompt").json()["text"]
    assert str(proj) not in text
    assert "src/app.py" in text


@pytest.mark.parametrize("fmt", ["default", "xml", "json"])
def test_no_absolute_path_in_any_format(proj, fmt):
    assert str(proj) not in client(proj).get("/api/prompt", params={"format": fmt}).json()["text"]


def test_narrowed_root_is_relative_to_the_narrowed_root(proj):
    text = client(proj).get("/api/prompt", params={"root": "src"}).json()["text"]
    assert "app.py" in text and str(proj) not in text and "src/app.py" not in text


def test_secret_reported_without_value_by_default(proj):
    body = client(proj).get("/api/prompt").json()
    assert body["meta"]["secrets"] == [
        {"path": "src/creds.py", "rules": ["aws-access-key-id"], "lines": [1]}
    ]
    assert KEY in body["text"]  # warn mode keeps the file...
    assert KEY not in str(body["meta"])  # ...but the report never repeats the value


def test_secret_exclude_mode_leaves_file_out(proj):
    body = client(proj).get("/api/prompt", params={"secrets": "exclude"}).json()
    assert KEY not in body["text"] and "src/app.py" in body["text"]
    assert body["meta"]["blocked"] == 1


def test_secret_block_mode_fails_closed_without_echoing_the_value(proj):
    r = client(proj).get("/api/prompt", params={"secrets": "block"})
    assert r.status_code == 422
    assert "src/creds.py" in r.json()["detail"] and KEY not in r.text


def test_bad_secrets_mode_is_a_400(proj):
    assert client(proj).get("/api/prompt", params={"secrets": "nope"}).status_code == 400


def test_finding_lines_are_unique_and_sorted(proj):
    (proj / "src" / "both.py").write_text('API_KEY = "AKIA%s"\n' % "ABCDEFGHIJKL1234")  # two rules, one line
    meta = client(proj).get("/api/prompt").json()["meta"]
    entry = next(e for e in meta["secrets"] if e["path"] == "src/both.py")
    assert entry["lines"] == [1] and entry["rules"] == ["aws-access-key-id", "sensitive-assignment"]
