"""/api/prompt tells the page what stayed out of the prompt and why (FN-014).

Before: the page showed "11 files" and nothing about the 6 left out. A missing file is the
most dangerous failure of a context tool because nobody notices an absence.
"""
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from fileflow import receipt as R  # noqa: E402
from fileflow import walker  # noqa: E402
from fileflow.server import app as appmod  # noqa: E402
from fileflow.server.app import build_app  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    (tmp_path / "README.md").write_text("# Pay\n")
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / "logo.png").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "b.js").write_text("x\n")
    (tmp_path / ".gitignore").write_text("dist/\n")
    return tmp_path


def meta(root, **params):
    with TestClient(build_app(project_root=str(root))) as c:
        r = c.get("/api/prompt", params=params)
        assert r.status_code == 200, r.text
        return r.json()["meta"]


def test_left_out_lists_each_exclusion_with_a_code_and_plain_words(proj):
    m = meta(proj)
    by_path = {e["path"]: e for e in m["left_out"]}
    assert by_path[".env"]["reason"] == "HIDDEN"
    assert by_path["dist/"]["reason"] == "GITIGNORED"
    assert by_path["logo.png"]["reason"] == "BINARY"
    for e in m["left_out"]:
        assert e["label"] == R.REASON_WORDS[e["reason"]] and e["reason"] in walker.REASONS
    assert m["left_out_total"] == len(m["left_out"])
    assert m["left_out_counts"]["HIDDEN"] == sum(1 for e in m["left_out"] if e["reason"] == "HIDDEN")


def test_no_absolute_path_leaks_into_the_listing(proj):
    m = meta(proj)
    assert all(not e["path"].startswith("/") and str(proj) not in e["path"] for e in m["left_out"])


def test_secret_held_back_is_listed(proj):
    (proj / "keys.txt").write_text("aws_access_key_id = " + "AKIA" + "IOSFODNN7EXAMPLE\n")
    m = meta(proj, secrets="exclude")
    assert any(e["path"] == "keys.txt" and e["reason"] == "SECRET_BLOCKED" for e in m["left_out"])
    assert m["blocked"] == 1


def test_token_budget_drops_and_truncations_are_listed_not_silent(proj):
    for n in "abc":
        (proj / (n + ".txt")).write_text(("word " * 400) + "\n")   # ~500 tokens each
    m = meta(proj, budget=700)
    budget = [e for e in m["left_out"] if e["reason"] == "TOKEN_BUDGET"]
    assert any(e.get("partial") for e in budget), "the file cut at the budget is reported"
    assert any(not e.get("partial") for e in budget), "files dropped after it are reported"
    assert m["left_out_counts"]["TOKEN_BUDGET"] == len(budget)


def test_the_listing_is_bounded(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n")
    for i in range(appmod.LEFT_OUT_LIMIT + 25):
        (tmp_path / (".h%03d" % i)).write_text("h\n")
    m = meta(tmp_path)
    assert len(m["left_out"]) == appmod.LEFT_OUT_LIMIT
    assert m["left_out_total"] >= appmod.LEFT_OUT_LIMIT + 25          # the truth is still reported
    assert m["left_out_counts"]["HIDDEN"] >= appmod.LEFT_OUT_LIMIT + 25


def test_dominant_file_is_reported_with_the_pattern_that_removes_it(proj):
    (proj / "package-lock.json").write_text('{"x":"' + "x" * 80_000 + '"}\n')
    d = meta(proj)["dominant"]
    assert d["path"] == "package-lock.json" and d["pattern"] == "package-lock.json"
    assert d["percent"] >= 99 and d["tokens"] >= R.DOMINANT_MIN_TOKENS and d["shared"] == 0


def test_dominant_file_pattern_is_glob_escaped_and_flags_shared_names(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "[id].tsx").write_text("const a = 1;\n" * 6000)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "[id].tsx").write_text("x\n")
    d = meta(tmp_path)["dominant"]
    assert d["pattern"] == "[[]id].tsx" and d["shared"] == 1


def test_no_dominant_file_when_the_prompt_is_small_or_balanced(proj):
    assert meta(proj)["dominant"] is None
    for n in "abc":
        (proj / (n + ".txt")).write_text("word " * 10000)
    assert meta(proj)["dominant"] is None


def test_applying_the_reported_pattern_removes_the_file(proj):
    (proj / "package-lock.json").write_text('{"x":"' + "x" * 80_000 + '"}\n')
    d = meta(proj)["dominant"]
    after = meta(proj, ignore_patterns=d["pattern"])
    assert after["dominant"] is None and after["tokens"] < 100
    assert any(e["path"] == "package-lock.json" and e["reason"] == "PATTERN" for e in after["left_out"])
