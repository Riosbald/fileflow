"""Property-based cross-engine fuzz test (ADR-001 moat).

Generates random file trees and random option combinations, then runs the FULL
pipeline through BOTH engines and asserts byte-identical output:

  Python: iter_documents → sort → apply_token_budget → render_documents
  JS:     collectFiles → sort → applyTokenBudget → buildPrompt   (fuzz_harness.js)

The two engines can only agree if their gitignore semantics, filtering, token
estimate, budget truncation and all three formatters are identical. Any drift
fails this test. Requires `node` on PATH; skipped otherwise.
"""

import json
import os
import random
import subprocess
import sys

import pytest

from fileflow.cli import (
    apply_token_budget,
    collect,
    render_documents,
)
from fileflow.provenance import read_provenance

HERE = os.path.dirname(__file__)
HARNESS = os.path.join(HERE, "..", "fileflow", "web", "js", "fuzz_harness.js")

FORMATS = ["default", "xml", "json"]
NAMES = ["a.py", "b.txt", "c.md", "data.csv", "util.js", "readme", "index.html", "conf.yaml"]
DIRS = ["src", "lib", "tests", "docs", "assets", "bin"]

# Front matter variants prepended to some files, so label PARSING is fuzzed across both engines too
# (CRLF, quoting, invalid labels, markup-ish URLs, astral characters).
FRONT_MATTERS = [
    "---\nprovenance: observed\nsource_url: https://e.com/a\n---\n",
    '---\nprovenance: observed\nsource_url: "https://e.com/?a=1&b=<2>\\"q\\""\n---\n',
    "---\r\nprovenance: generated\r\n---\r\n",
    "---\nprovenance: USER\n---\n",
    "---\nprovenance: nonsense\n---\n",
    "---\nprovenance: observed\nsource_url: https://e.com/\U0001f600\u00e9\n---\n",
    "---\nprovenance: observed\n",
    "---\n__proto__: x\nprovenance: observed\n---\n",
]
INCLUDE_POOL = ["*.py", "*.txt", "src/*", "*/*.md", "a?.py", "readme", "*", "docs", "*.csv", "?????.*", "lib/*.js", "*\u00e9*", "*/*", "b.txt"]


def _tree_node(rng, depth):
    """Generate a single tree node (file or dir) as the JS-internal shape."""
    if depth >= 3 or rng.random() < 0.6:
        name = rng.choice(NAMES)
        if rng.random() < 0.2:
            name = "." + name
        content = "".join(rng.choice(["a","b","c","d","e","f","g","0","1","2","3"," ","\n","\u00e9","\u65e5","\U0001f600","\r","\r\n","\x0c","\u2028","\x85","\x1c"]) for _ in range(rng.randint(0, 60)))
        if rng.random() < 0.25:
            content = rng.choice(FRONT_MATTERS) + content
        return {"name": name, "content": content}
    name = rng.choice(DIRS)
    if rng.random() < 0.2:
        name = "." + name
    children = _unique_siblings(rng, rng.randint(1, 4), depth + 1)
    return {"name": name, "children": children}


def _unique_siblings(rng, count, depth=0):
    """Generate sibling nodes with unique names (a real FS disallows dupes)."""
    nodes = []
    used = set()
    attempts = 0
    while len(nodes) < count and attempts < 100:
        node = _tree_node(rng, depth)
        if node["name"] in used:
            attempts += 1
            continue
        used.add(node["name"])
        nodes.append(node)
        attempts += 1
    return nodes


def _random_gitignore(rng):
    """Random .gitignore content exercising negation, anchoring, dir-only."""
    rules = []
    for _ in range(rng.randint(0, 4)):
        kind = rng.random()
        if kind < 0.3:
            rules.append("*." + rng.choice(["pyc", "log", "tmp", "bak"]))
        elif kind < 0.5:
            rules.append(rng.choice(["src", "build", "node_modules"]) + "/")
        elif kind < 0.7:
            rules.append("/" + rng.choice(["secret", "private.txt", "tmp/"]))
        elif kind < 0.85:
            rules.append(rng.choice(["keep.py", "!keep.py", "!*.log"]))
        else:
            rules.append("# comment")
    return "\n".join(rules) + "\n"


def _random_opts(rng):
    return {
        "format": rng.choice(FORMATS),
        "lineNumbers": rng.random() < 0.5,
        "separators": rng.random() < 0.8,
        "includeHidden": rng.random() < 0.4,
        "ignoreGitignore": rng.random() < 0.3,
        "ignorePatterns": rng.sample(["*.log", "*.tmp", "docs", "__pycache__"], rng.randint(0, 2)),
        "maxTokens": rng.choice([0, 0, 0, 5, 12, 30, 80]),
        "includePatterns": [] if rng.random() < 0.55 else rng.sample(INCLUDE_POOL, rng.randint(1, 2)),
    }


def _materialize(node, root, base):
    """Write a virtual tree node to disk at root."""
    rel = base + "/" + node["name"] if base else node["name"]
    if "children" in node:
        d = os.path.join(root, rel)
        os.makedirs(d, exist_ok=True)
        for child in node["children"]:
            _materialize(child, root, rel)
    else:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(node.get("content", ""))


def _python_render(root, opts):
    col = collect(
        [root],
        include_hidden=opts["includeHidden"],
        ignore_gitignore=opts["ignoreGitignore"],
        ignore_patterns=opts["ignorePatterns"],
        include_patterns=opts["includePatterns"],
    )
    docs, prov = [], {}
    for p, c in col.documents:
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        docs.append((rel, c))
        label = read_provenance(c)  # from the content BEFORE any budget truncation, as the JS harness does
        if label:
            prov[rel] = label
    docs.sort(key=lambda t: t[0])
    docs = apply_token_budget(docs, opts["maxTokens"], tokenizer="heuristic")
    return render_documents(
        docs,
        format=opts["format"],
        line_numbers=opts["lineNumbers"],
        separators=opts["separators"],
        provenance=prov,
    )


def _node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_cross_engine_fuzz(tmpdir):
    rng = random.Random(1337)  # fixed seed → reproducible
    count = int(os.environ.get("FILEFLOW_FUZZ_CASES", "40"))

    cases = []
    root_dir = str(tmpdir)
    for i in range(count):
        tree = _unique_siblings(rng, rng.randint(1, 4))
        # Ensure a root .gitignore to exercise gitignore scoping.
        tree.append({"name": ".gitignore", "content": _random_gitignore(rng)})
        opts = _random_opts(rng)
        cases.append({"tree": tree, "opts": opts})

    # Materialize every case under its own subdir, compute Python output.
    python_outputs = []
    input_cases = []
    for i, case in enumerate(cases):
        sub = os.path.join(root_dir, "case%d" % i)
        os.makedirs(sub, exist_ok=True)
        for node in case["tree"]:
            _materialize(node, sub, "")
        python_outputs.append(_python_render(sub, case["opts"]))
        input_cases.append({"tree": case["tree"], "opts": case["opts"]})

    # Run the JS harness once over all cases.
    input_path = os.path.join(root_dir, "fuzz-input.json")
    with open(input_path, "w", encoding="utf-8") as f:
        json.dump({"cases": input_cases}, f)
    proc = subprocess.run(
        ["node", HARNESS, input_path], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, "node harness failed:\n" + proc.stderr
    js_outputs = json.loads(proc.stdout)

    assert len(js_outputs) == len(python_outputs)
    mismatches = 0
    for i, (py, js) in enumerate(zip(python_outputs, js_outputs)):
        if py != js["text"]:
            mismatches += 1
            print(f"\n--- mismatch case {i} ---")
            print("opts:", json.dumps(cases[i]["opts"]))
            print("PYTHON:", repr(py[:200]))
            print("JS    :", repr(js["text"][:200]))
    assert mismatches == 0, f"{mismatches}/{count} cross-engine cases diverged"
