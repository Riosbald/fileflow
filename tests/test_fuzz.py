"""Property/fuzz testing: render() must never crash and must preserve
every included file's path, across 200 randomized cases."""

import json
import random
import string

from fileflow.core import DEFAULT_OPTIONS, FileEntry, RenderOptions, render

FORMATS = ("default", "markdown", "xml", "json")
CASES = 200


def random_text(rng: random.Random) -> str:
    alphabet = string.ascii_letters + string.digits + " \n\t\"'`<>&{}[]-_=#/\\"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 300)))


def random_path(rng: random.Random, i: int) -> str:
    parts = ["".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(1, 8)))
             for _ in range(rng.randint(1, 3))]
    ext = rng.choice([".py", ".md", ".txt", ".js", ""])
    return "/".join(parts) + f"_{i}{ext}"


def test_fuzz_render_never_crashes():
    rng = random.Random(20260824)
    for case in range(CASES):
        n_files = rng.randint(0, 8)
        entries = [FileEntry(random_path(rng, i), random_text(rng)) for i in range(n_files)]
        options = RenderOptions(**DEFAULT_OPTIONS).merged({
            "format": rng.choice(FORMATS),
            "separators": rng.random() < 0.5,
            "line_numbers": rng.random() < 0.5,
            "max_tokens": rng.choice([None, None, 1, 10, 100, 10_000]),
        })

        out = render(entries, options)
        assert isinstance(out, str)

        if options.format == "json":
            data = json.loads(out)
            included = {f["path"] for f in data["files"]}
            assert included <= {e.path for e in entries}
            if options.max_tokens is None:
                assert len(data["files"]) == len(entries)
                assert data["truncated"] is False
        elif options.max_tokens is None:
            for e in entries:
                assert e.path in out, f"case {case}: {e.path} missing from output"
