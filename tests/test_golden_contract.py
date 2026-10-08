"""Cross-engine golden-output contract (Python side).

Loads the shared testdata/golden-corpus.json and asserts the Python engine's
renderer produces byte-identical output to the committed expected strings.
The Node suite (fileflow/web/js/golden.test.js) runs the same corpus against the JS
engine — so if the two engines ever drift, one of the two suites fails.
"""

import json
import os

import pytest

from fileflow.cli import path_is_ignored, render_documents
from fileflow.provenance import read_provenance
from fileflow.walker import include_match

HERE = os.path.dirname(__file__)
CORPUS = os.path.join(HERE, "..", "testdata", "golden-corpus.json")


@pytest.fixture(scope="module")
def corpus():
    with open(CORPUS, encoding="utf-8") as f:
        return json.load(f)


def _documents(corpus):
    return [(d["path"], d["content"]) for d in corpus["documents"]]


@pytest.mark.parametrize(
    "scenario,opts",
    [
        ("default", {"format": "default", "line_numbers": False, "separators": True}),
        ("no-separators", {"format": "default", "line_numbers": False, "separators": False}),
        ("xml", {"format": "xml", "line_numbers": False, "separators": True}),
        ("json", {"format": "json", "line_numbers": False, "separators": True}),
        ("line-numbers", {"format": "default", "line_numbers": True, "separators": True}),
        ("xml-line-numbers", {"format": "xml", "line_numbers": True, "separators": True}),
        ("json-line-numbers", {"format": "json", "line_numbers": True, "separators": True}),
        ("line-numbers-no-separators", {"format": "default", "line_numbers": True, "separators": False}),
    ],
)
def test_render_matches_golden(corpus, scenario, opts):
    actual = render_documents(
        _documents(corpus),
        format=opts["format"],
        line_numbers=opts["line_numbers"],
        separators=opts["separators"],
    )
    expected = corpus["scenarios"][scenario]
    assert actual == expected, f"Python engine diverged from golden in '{scenario}'"


def test_gitignore_matches_golden(corpus):
    """The Python gitignore matcher must agree with the committed reference."""
    for case in corpus["gitignore_cases"]:
        scopes = [(s["dir"], s["patterns"]) for s in case["scopes"]]
        for entry in case["paths"]:
            actual = path_is_ignored(entry["path"], entry["isDir"], scopes)
            assert (
                actual == entry["expected"]
            ), f"{case['name']}: path_is_ignored({entry['path']}) != {entry['expected']}"


def test_instruction_scenarios_match_the_golden_corpus(corpus):
    """Task-instruction rendering is pinned for every format (JS asserts the same strings)."""
    scenarios = corpus["instruction_scenarios"]
    assert len(scenarios) >= 8
    for case in scenarios:
        o = case["opts"]
        actual = render_documents(
            _documents(corpus),
            format=o["format"],
            line_numbers=o["lineNumbers"],
            separators=o["separators"],
            instruction=o["instruction"],
        )
        assert actual == case["expected"], "Python diverged from golden in '%s'" % case["name"]


def test_provenance_scenarios_match_the_golden_corpus(corpus):
    """Provenance annotations are pinned for every format (JS asserts the same strings)."""
    scenarios = corpus["provenance_scenarios"]
    assert len(scenarios) >= 7
    for case in scenarios:
        o = case["opts"]
        actual = render_documents(
            _documents(corpus),
            format=o["format"],
            line_numbers=o["lineNumbers"],
            separators=o["separators"],
            instruction=o.get("instruction"),
            provenance=o["provenance"],
        )
        assert actual == case["expected"], "Python diverged from golden in '%s'" % case["name"]


def test_provenance_parse_cases_match_the_golden_corpus(corpus):
    """How a label is READ from front matter is part of the contract too (CRLF, quotes, odd whitespace...)."""
    cases = corpus["provenance_parse_cases"]
    assert len(cases) >= 20
    for case in cases:
        assert read_provenance(case["text"]) == case["expected"], repr(case["text"])


def test_include_glob_cases_match_the_golden_corpus(corpus):
    """`[include]` glob semantics (the `*`/`?` subset, case-sensitive, code points) are part of the contract."""
    cases = corpus["include_cases"]
    assert len(cases) >= 40 and any(c["expected"] for c in cases) and any(not c["expected"] for c in cases)
    for c in cases:
        name = c["rel"].rsplit("/", 1)[-1]
        assert include_match(c["rel"], name, c["patterns"]) is c["expected"], c
