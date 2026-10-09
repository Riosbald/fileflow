"""Instruction rendering: hand-written expectations (independent of the implementation).

The cross-engine golden corpus (test_golden_contract.py / golden.test.js) pins
the same behaviour for Python *and* JS; these literals are the oracle that
corpus is generated from, so a bug can't hide by being copied into both.
"""
import json

import pytest

from fileflow.cli import render_documents

DOCS = [("a.py", "x = 1\n"), ("b.txt", "hi")]


def test_default_format_puts_the_task_before_the_files():
    out = render_documents(DOCS, instruction="Explain this.")
    assert out == "# Task\n\nExplain this.\n\n# Files\n\na.py\n---\nx = 1\n\n---\n\nb.txt\n---\nhi\n---"


def test_default_without_separators():
    out = render_documents(DOCS, separators=False, instruction="Go.")
    assert out == "# Task\n\nGo.\n\n# Files\n\na.py\nx = 1\n\n\nb.txt\nhi"


def test_xml_wraps_the_task_outside_documents():
    out = render_documents([("a.py", "x")], format="xml", instruction="Do it.")
    assert out == (
        "<task_instructions>\nDo it.\n</task_instructions>\n"
        "<documents>\n"
        '<document index="1">\n<source>a.py</source>\n<document_content>\nx\n</document_content>\n</document>\n'
        "</documents>"
    )


def test_json_becomes_an_object_only_when_there_is_an_instruction():
    plain = json.loads(render_documents([("a.py", "x")], format="json"))
    assert plain == [{"path": "a.py", "content": "x"}]  # unchanged shape
    with_task = json.loads(render_documents([("a.py", "x")], format="json", instruction="Do it."))
    assert with_task == {"instructions": "Do it.", "documents": [{"path": "a.py", "content": "x"}]}


@pytest.mark.parametrize("fmt", ["default", "xml", "json"])
@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t \r\n"])
def test_blank_instruction_changes_nothing(fmt, blank):
    assert render_documents(DOCS, format=fmt, instruction=blank) == render_documents(DOCS, format=fmt)


def test_only_ascii_whitespace_is_trimmed():
    out = render_documents([("a", "b")], format="xml", instruction="\n\n  Do it.  \r\n")
    assert "<task_instructions>\nDo it.\n</task_instructions>" in out
    nb = "\u00a0keep\u00a0"  # no-break spaces are content, not padding
    assert nb in render_documents([("a", "b")], format="xml", instruction=nb)


def test_instruction_with_no_documents_is_still_emitted():
    assert render_documents([], instruction="Only a task.") == "# Task\n\nOnly a task."
    assert render_documents([], format="xml", instruction="T") == "<task_instructions>\nT\n</task_instructions>\n<documents>\n</documents>"
    assert json.loads(render_documents([], format="json", instruction="T")) == {"instructions": "T", "documents": []}


def test_line_numbers_apply_to_files_not_to_the_instruction():
    out = render_documents([("a", "one\ntwo")], line_numbers=True, instruction="1\n2")
    assert "# Task\n\n1\n2\n\n# Files" in out and "1  one" in out
