"""Provenance labels: parsing, rendering (hand-written oracle), collection, receipt, server."""
import json
import os

import pytest
from click.testing import CliRunner

from fileflow.cli import cli, collect, render_documents
from fileflow.provenance import LABELS, attr_value, read_provenance

OBS = {"provenance": "observed", "source_url": "https://example.com/a"}
DOCS = [("a.md", "alpha"), ("b.txt", "beta")]


# ---- parsing ---------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("---\nprovenance: observed\nsource_url: https://e.com/x\n---\nbody", {"provenance": "observed", "source_url": "https://e.com/x"}),
    ('---\nprovenance: observed\nsource_url: "https://e.com/x?a=1&b=2"\n---\n', {"provenance": "observed", "source_url": "https://e.com/x?a=1&b=2"}),
    ("---\nprovenance: generated\n---\n", {"provenance": "generated"}),
    ("---\nprovenance:   USER  \n---\n", {"provenance": "user"}),
    ("---\nprovenance: 'observed'\n---\n", {"provenance": "observed"}),
    ('---\nprovenance: "observed"\n---\n', {"provenance": "observed"}),
    ("---\r\nprovenance: observed\r\n---\r\n", {"provenance": "observed"}),
    ("---\nprovenance: made-up\n---\n", None),
    ("---\nprovenance:\n---\n", None),
    ("---\ntitle: x\n---\n", None),
    ("no front matter", None),
    ("---\nprovenance: observed\n", None),                      # never closed
    ("\n---\nprovenance: observed\n---\n", None),               # must be the very first line
    ("# heading\n---\nprovenance: observed\n---\n", None),
    ('---\nprovenance: observed\nsource_url: "unterminated\n---\n', {"provenance": "observed", "source_url": '"unterminated'}),
])
def test_read_provenance(text, expected):
    assert read_provenance(text) == expected


def test_only_the_three_labels_exist():
    assert LABELS == ("observed", "generated", "user")


def test_attribute_values_cannot_break_out_of_xml():
    assert attr_value('x" onload="evil()') == "x onload=evil()"
    assert attr_value("<script>&") == "script"
    assert attr_value("a\nb\tc\x00d") == "abcd"
    assert len(attr_value("a" * 1000)) == 300


# ---- rendering: hand-written oracle ------------------------------------------------------------------------
def test_default_format_annotates_the_path_line():
    out = render_documents([("a.md", "alpha")], provenance={"a.md": OBS})
    assert out == "a.md\n[provenance: observed; source: https://example.com/a]\n---\nalpha\n---"


def test_default_without_separators():
    out = render_documents([("a.md", "alpha")], separators=False, provenance={"a.md": {"provenance": "generated"}})
    assert out == "a.md\n[provenance: generated]\nalpha"


def test_xml_gets_attributes_only_where_a_label_exists():
    out = render_documents(DOCS, format="xml", provenance={"a.md": OBS})
    assert '<document index="1" provenance="observed" source_url="https://example.com/a">' in out
    assert '<document index="2">' in out


def test_json_adds_keys_only_where_a_label_exists():
    data = json.loads(render_documents(DOCS, format="json", provenance={"a.md": OBS}))
    assert data[0] == {"path": "a.md", "content": "alpha", "provenance": "observed", "source_url": "https://example.com/a"}
    assert data[1] == {"path": "b.txt", "content": "beta"}


@pytest.mark.parametrize("fmt", ["default", "xml", "json"])
@pytest.mark.parametrize("prov", [None, {}, {"other.md": OBS}])
def test_no_applicable_label_means_output_is_byte_identical_to_before(fmt, prov):
    assert render_documents(DOCS, format=fmt, provenance=prov) == render_documents(DOCS, format=fmt)


def test_hostile_source_url_cannot_inject_markup():
    bad = {"provenance": "observed", "source_url": 'https://e.com/"><evil x="'}
    xml = render_documents([("a.md", "x")], format="xml", provenance={"a.md": bad})
    tag = next(l for l in xml.splitlines() if l.startswith("<document "))
    assert "<evil" not in xml and tag.count('"') == 6 and tag.endswith(">")   # index, provenance, source_url: nothing extra
    dflt = render_documents([("a.md", "x")], provenance={"a.md": {"provenance": "observed", "source_url": "u\nINJECTED LINE"}})
    assert "\nINJECTED LINE" not in dflt


def test_instruction_and_provenance_compose():
    out = render_documents([("a.md", "x")], format="xml", instruction="Do it.", provenance={"a.md": {"provenance": "generated"}})
    assert out.startswith("<task_instructions>\nDo it.\n</task_instructions>\n<documents>") and 'provenance="generated"' in out


# ---- collection --------------------------------------------------------------------------------------------------
def test_collect_reads_labels_from_front_matter(tmp_path):
    (tmp_path / "web.md").write_text('---\nprovenance: observed\nsource_url: "https://e.com/p"\n---\nbody\n')
    (tmp_path / "ai.md").write_text("---\nprovenance: generated\nmodel: x\n---\nanswer\n")
    (tmp_path / "mine.md").write_text("# plain\n")
    (tmp_path / "junk.md").write_text("---\nprovenance: nonsense\n---\n")
    col = collect([str(tmp_path)])
    got = {os.path.basename(p): v for p, v in col.provenance.items()}
    assert got == {"web.md": {"provenance": "observed", "source_url": "https://e.com/p"}, "ai.md": {"provenance": "generated"}}


# ---- CLI + receipt -------------------------------------------------------------------------------------------------
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


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "essay.md").write_text('---\nprovenance: observed\nsource_url: "https://example.com/essay"\n---\nthe text\n')
    (tmp_path / "content" / "analysis.md").write_text("---\nprovenance: generated\n---\nmodel-written notes\n")
    (tmp_path / "notes.md").write_text("mine\n")
    return tmp_path


def test_cli_prompt_labels_documents(proj):
    out = run(["content", "notes.md", "--format", "xml"], proj).stdout
    assert 'provenance="observed" source_url="https://example.com/essay"' in out and 'provenance="generated"' in out
    assert out.count("provenance=") == 2  # notes.md carries no label and gets none


def test_receipt_records_provenance_per_file_and_in_totals(proj):
    run(["content", "notes.md", "--receipt-file", "r.json"], proj)
    rec = json.loads((proj / "r.json").read_text())
    by = {i["path"]: i for i in rec["included"]}
    assert by["content/essay.md"]["provenance"] == "observed" and by["content/essay.md"]["source_url"] == "https://example.com/essay"
    assert by["content/analysis.md"]["provenance"] == "generated" and "provenance" not in by["notes.md"]
    assert rec["totals"]["provenance"] == {"observed": 1, "generated": 1, "user": 0, "unlabelled": 1}
    assert any("origin, not truth" in a for a in rec["ledger"]["assumed"])


def test_receipt_without_any_label_stays_quiet_about_provenance(tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    run(["a.txt", "--receipt-file", "r.json"], tmp_path)
    rec = json.loads((tmp_path / "r.json").read_text())
    assert not any("origin" in a for a in rec["ledger"]["assumed"])


def test_receipt_summary_mentions_labels(proj):
    err = run(["content", "--receipt"], proj).stderr
    assert "observed 1" in err and "generated 1" in err


def test_server_prompt_carries_labels_with_relative_paths(proj):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fileflow.server.app import build_app
    body = TestClient(build_app(project_root=str(proj))).get("/api/prompt", params={"format": "json"}).json()
    docs = json.loads(body["text"])
    labelled = {d["path"]: d.get("provenance") for d in docs}
    assert labelled["content/essay.md"] == "observed" and labelled["content/analysis.md"] == "generated" and labelled["notes.md"] is None
    assert str(proj) not in body["text"]
