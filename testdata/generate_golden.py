#!/usr/bin/env python3
"""Generate testdata/golden-corpus.json.

The golden corpus is a shared, committed fixture that pins the *rendered output*
of the fileflow engine for a fixed set of (path, content) documents across every
format/option combination. Both the Python test suite (pytest) and the Node test
suite (fileflow/web/js/golden.test.js) load this file and assert their own engine produces
byte-identical output. This is the cross-engine contract that prevents silent
drift between the CLI and the web client.

This script computes the expected strings from the Python engine (the reference
implementation) and writes them next to the documents.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fileflow.cli import path_is_ignored, render_documents  # noqa: E402
from fileflow.provenance import read_provenance  # noqa: E402
from fileflow.walker import include_match  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "golden-corpus.json")

DOCUMENTS = [
    {"path": "README.md", "content": "# fileflow\n"},
    {"path": "src/app.py", "content": "def main():\n    return 1\n"},
    {"path": "src/utils.py", "content": "VALUE = 42\n"},
    {"path": "src/nested/deep.txt", "content": "deep content\n"},
    {"path": "notes.txt", "content": "no trailing newline"},
    # Characters Python's splitlines() would treat as line breaks (form feed, NEL, U+2028, FS): they must pass through untouched.
    {"path": "src/odd-separators.txt", "content": "one\x0ctwo\u2028three\x85four\x1cfive\nsecond line\n"},
    # Non-ASCII + astral characters: JSON must stay readable (not \\u-escaped) in both engines.
    {"path": "docs/caf\u00e9.md", "content": "na\u00efve \u2014 \u65e5\u672c\u8a9e \U0001f600\n"},
]

# (scenario_name, opts) — opts mirror the web client's options() shape.
SCENARIOS = [
    ("default", {"format": "default", "line_numbers": False, "separators": True}),
    ("no-separators", {"format": "default", "line_numbers": False, "separators": False}),
    ("xml", {"format": "xml", "line_numbers": False, "separators": True}),
    ("json", {"format": "json", "line_numbers": False, "separators": True}),
    ("line-numbers", {"format": "default", "line_numbers": True, "separators": True}),
    ("xml-line-numbers", {"format": "xml", "line_numbers": True, "separators": True}),
    ("json-line-numbers", {"format": "json", "line_numbers": True, "separators": True}),
    ("line-numbers-no-separators", {"format": "default", "line_numbers": True, "separators": False}),
]


# Scenarios with a task instruction (opts use the web client's camelCase shape and
# are stored IN the corpus, so neither test suite re-declares them).
INSTRUCTION = "Explain the entry point.\n\nKeep it short."
INSTRUCTION_SCENARIOS = [
    ("task-default", {"format": "default", "lineNumbers": False, "separators": True, "instruction": INSTRUCTION}),
    ("task-no-separators", {"format": "default", "lineNumbers": False, "separators": False, "instruction": INSTRUCTION}),
    ("task-xml", {"format": "xml", "lineNumbers": False, "separators": True, "instruction": INSTRUCTION}),
    ("task-json", {"format": "json", "lineNumbers": False, "separators": True, "instruction": INSTRUCTION}),
    ("task-line-numbers", {"format": "default", "lineNumbers": True, "separators": True, "instruction": INSTRUCTION}),
    ("task-xml-line-numbers", {"format": "xml", "lineNumbers": True, "separators": True, "instruction": "\n  padded with ASCII whitespace  \r\n"}),
    ("task-blank-is-ignored", {"format": "default", "lineNumbers": False, "separators": True, "instruction": " \n\t "}),
    ("task-unicode", {"format": "json", "lineNumbers": False, "separators": True, "instruction": "R\u00e9sum\u00e9 \u2014 \u00a0keep\u00a0 \U0001f600"}),
]


# Provenance: the same render scenarios with labels on some documents. The label map is explicit here;
# PROVENANCE_PARSE_CASES pins how a label is READ from a file's front matter (both engines must agree).
PROV = {
    "README.md": {"provenance": "observed", "source_url": "https://example.com/readme?a=1&b=2"},
    "src/app.py": {"provenance": "generated"},
    "notes.txt": {"provenance": "user", "source_url": 'https://e.com/"><x y="\nline2\u0007bell'},
    "docs/caf\u00e9.md": {"provenance": "observed", "source_url": "https://e.com/" + "\U0001f600" * 200},
}
PROVENANCE_SCENARIOS = [
    ("prov-default", {"format": "default", "lineNumbers": False, "separators": True}),
    ("prov-no-separators", {"format": "default", "lineNumbers": False, "separators": False}),
    ("prov-xml", {"format": "xml", "lineNumbers": False, "separators": True}),
    ("prov-json", {"format": "json", "lineNumbers": False, "separators": True}),
    ("prov-xml-line-numbers", {"format": "xml", "lineNumbers": True, "separators": True}),
    ("prov-xml-instruction", {"format": "xml", "lineNumbers": False, "separators": True, "instruction": "Review."}),
    ("prov-json-instruction", {"format": "json", "lineNumbers": True, "separators": True, "instruction": "Review."}),
]
PROVENANCE_PARSE_CASES = [
    "---\nprovenance: observed\nsource_url: https://e.com/x\n---\nbody",
    '---\nprovenance: observed\nsource_url: "https://e.com/x?a=1&b=2"\n---\n',
    "---\nprovenance: generated\n---\n",
    "---\nprovenance:   USER  \n---\n",
    "---\nprovenance: 'observed'\n---\n",
    '---\nprovenance: "observed"\n---\n',
    "---\r\nprovenance: observed\r\nsource_url: https://e.com/crlf\r\n---\r\n",
    "---\nprovenance: made-up\n---\n",
    "---\nprovenance:\n---\n",
    "---\ntitle: x\n---\n",
    "no front matter",
    "---\nprovenance: observed\n",
    "\n---\nprovenance: observed\n---\n",
    '---\nprovenance: observed\nsource_url: "unterminated\n---\n',
    '---\nprovenance: observed\nsource_url: "caf\\u00e9 \\"q\\""\n---\n',
    "---\nprovenance: observed\nsource_url: >\n  https://e.com/folded\n  more\n---\n",
    "---\nprovenance: observed\nprovenance: user\n---\n",
    "---\n__proto__: x\nprovenance: observed\nconstructor: y\n---\n",
    "---\nprovenance:\u00a0observed\n---\n",
    "---\u00a0\nprovenance: observed\n---\n",
    "--- \nprovenance: observed\n--- \n",
    "---\n  provenance: observed\n---\n",
    "---\nprovenance : observed\n---\n",
    "---\nProvenance: observed\n---\n",
]


# [include] glob semantics: (patterns, relative path). Expected values come from the Python engine.
INCLUDE_CASES = [
    (["*.py"], "a.py"), (["*.py"], "src/main.py"), (["*.py"], "A.PY"), (["*.py"], "a.pyc"), (["*.py"], ".py"),
    (["a?.py"], "ab.py"), (["a?.py"], "a.py"), (["a?.py"], "abc.py"),
    (["src/*.py"], "src/main.py"), (["src/*.py"], "src/deep/inner.py"), (["src/*.py"], "other/src/main.py"), (["src/*.py"], "main.py"),
    (["docs/guide.md"], "docs/guide.md"), (["docs/guide.md"], "docs/guide.mdx"),
    (["[ab].py"], "a.py"), (["[ab].py"], "[ab].py"), (["a+b.py"], "a+b.py"), (["a+b.py"], "aab.py"),
    (["a.b"], "a.b"), (["a.b"], "axb"), (["(x)"], "(x)"), (["^x$"], "^x$"), (["x|y"], "x|y"), (["x|y"], "x"), (["{a}"], "{a}"), (["a\\b"], "a\\b"),
    (["*"], "anything.at.all"), (["?"], "x"), (["?"], ""), (["??"], "\U0001f600"), (["?"], "\U0001f600"), (["*\u00e9*"], "caf\u00e9.md"),
    (["*.txt", "*.md"], "notes.md"), (["*.txt", "*.md"], "notes.py"), (["caf\u00e9.*"], "caf\u00e9.md"), (["a?c"], "a\nc"), (["*"], "line\nbreak"),
    (["src/?"], "src/x"), (["src/?"], "src/xy"), (["*/*"], "a/b"), (["*/*"], "ab"), (["**"], "x/y/z"),
]


def main():
    documents = [(d["path"], d["content"]) for d in DOCUMENTS]
    scenarios = {}
    for name, opts in SCENARIOS:
        scenarios[name] = render_documents(
            documents,
            format=opts["format"],
            line_numbers=opts["line_numbers"],
            separators=opts["separators"],
        )

    instruction_scenarios = []
    for name, opts in INSTRUCTION_SCENARIOS:
        instruction_scenarios.append(
            {
                "name": name,
                "opts": opts,
                "expected": render_documents(
                    documents,
                    format=opts["format"],
                    line_numbers=opts["lineNumbers"],
                    separators=opts["separators"],
                    instruction=opts["instruction"],
                ),
            }
        )

    provenance_scenarios = []
    for name, opts in PROVENANCE_SCENARIOS:
        provenance_scenarios.append(
            {
                "name": name,
                "opts": dict(opts, provenance=PROV),
                "expected": render_documents(
                    documents,
                    format=opts["format"],
                    line_numbers=opts["lineNumbers"],
                    separators=opts["separators"],
                    instruction=opts.get("instruction"),
                    provenance=PROV,
                ),
            }
        )
    include_cases = [
        {"patterns": pats, "rel": rel, "expected": include_match(rel, rel.rsplit("/", 1)[-1], pats)}
        for pats, rel in INCLUDE_CASES
    ]
    provenance_parse = [{"text": t, "expected": read_provenance(t)} for t in PROVENANCE_PARSE_CASES]

    # Gitignore filtering cases. Each path is evaluated with isDir, and
    # `expected` is the reference result from the Python engine.
    gitignore_cases = [
        {
            "name": "root",
            "scopes": [
                {
                    "dir": "",
                    "patterns": ["build/", "*.pyc", "!keep.pyc", "/root-only.txt", "logs/*.log"],
                }
            ],
            "paths": [
                {"path": "build", "isDir": True, "expected": True},
                {"path": "src/app.pyc", "isDir": False, "expected": True},
                {"path": "keep.pyc", "isDir": False, "expected": False},
                {"path": "root-only.txt", "isDir": False, "expected": True},
                {"path": "sub/root-only.txt", "isDir": False, "expected": False},
                {"path": "logs/app.log", "isDir": False, "expected": True},
                {"path": "app.log", "isDir": False, "expected": False},
                {"path": "src/app.py", "isDir": False, "expected": False},
            ],
        },
        {
            "name": "nested",
            "scopes": [
                {"dir": "", "patterns": ["*.tmp"]},
                {"dir": "src", "patterns": ["/secret.txt", "cache/"]},
            ],
            "paths": [
                {"path": "src/secret.txt", "isDir": False, "expected": True},
                {"path": "secret.txt", "isDir": False, "expected": False},
                {"path": "notes.tmp", "isDir": False, "expected": True},
                {"path": "src/cache", "isDir": True, "expected": True},
            ],
        },
    ]

    # Compute the reference `expected` values from the Python engine so the
    # corpus stays authoritative and self-consistent.
    for case in gitignore_cases:
        scopes = [(s["dir"], s["patterns"]) for s in case["scopes"]]
        for entry in case["paths"]:
            entry["expected"] = path_is_ignored(entry["path"], entry["isDir"], scopes)

    data = {
        "documents": DOCUMENTS,
        "scenarios": scenarios,
        "instruction_scenarios": instruction_scenarios,
        "provenance_scenarios": provenance_scenarios,
        "include_cases": include_cases,
        "provenance_parse_cases": provenance_parse,
        "gitignore_cases": gitignore_cases,
        "_comment": (
            "Shared golden-output corpus. Expected strings are computed from the "
            "Python engine (reference). Both pytest and Node assert byte-identical "
            "output. Regenerate with: python3 testdata/generate_golden.py"
        ),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print("Wrote", OUT, "with", len(SCENARIOS), "scenarios")


if __name__ == "__main__":
    main()
