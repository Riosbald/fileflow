#!/usr/bin/env python3
"""Create the throwaway demo project used for the live preview and browser tests.

    python scripts/make_demo.py /tmp/fileflow-demo

Contains no real credentials: notes/aws-example.txt uses AWS's published
example key so the secret warning has something to show.
"""
import sys
from pathlib import Path

FILES = {
    "src/main.py": "from utils import greet\n\n\ndef main():\n    print(greet('world'))\n\n\nif __name__ == '__main__':\n    main()\n",
    "src/utils.py": "def greet(name):\n    return f'hello {name}'\n",
    "tests/test_utils.py": "from src.utils import greet\n\n\ndef test_greet():\n    assert greet('x') == 'hello x'\n",
    "README.md": "# Demo project\n\nA tiny project used to preview fileflow.\n",
    "docs/notes.md": "# Notes\n\nOne section at a time.\n",
    "dist/bundle.js": "console.log('built');\n",
    "node_modules/left-pad/index.js": "module.exports = (s) => s;\n",
    "debug.log": "debug line\n",
    ".env.example": "EXAMPLE_KEY=changeme\n",
    ".gitignore": "dist/\nnode_modules/\n*.log\n",
    "components/hero/Hero.jsx": "export const Hero = () => <h1>Hero</h1>;\n",
    "components/hero/hero.css": ".hero { padding: 2rem; }\n",
    "notes/project-notes.txt": "Ship the hero section first.\n",
    "notes/testing-checklist.md": "- [ ] keyboard only\n- [ ] 320px width\n",
    # AWS's published example key, assembled at runtime so this file itself is not
    # secret-shaped (fileflow check --strict scans the repo, including scripts/).
    "notes/aws-example.txt": "aws_access_key_id = " + "AKIA" + "IOSFODNN7EXAMPLE" + "\n",
    "content/fetched-example.md": '---\nprovenance: observed\nsource_url: "https://example.com/doing-great-work"\n---\n# Doing great work\n\nFetched text.\n',
    "content/model-notes.md": "---\nprovenance: generated\n---\nA model wrote this.\n",
    ".files-to-prompt": '''version = 1

[output]
separators = true
line_numbers = true
max_tokens = 0

[exclude]
patterns = ["tests"]

[secrets]
mode = "warn"

[presets.analyze]
paths = ["."]
max_tokens = 8000
exclude_patterns = ["docs"]
instruction = "Analyse this project and list risks."

[presets.hero]
paths = ["components/hero", "notes/project-notes.txt"]
max_tokens = 12000
format = "xml"

[presets.frontier-audit.prompt]
role = "{{role}}"
context = "{{domain}} with {{years}} years of history"
task = "Audit the files."

[presets.frontier-audit.vars]
years = "3"
''',
}


def lockfile() -> str:
    """A believable npm lockfile (~150 KB, ~38k tokens): the classic file that is most of a prompt
    and none of its value. Deterministic, and contains nothing credential-shaped."""
    import base64
    import hashlib
    import json

    packages = {"": {"name": "demo", "version": "1.0.0"}}
    for i in range(900):
        digest = base64.b64encode(hashlib.sha512(b"pkg-%d" % i).digest()).decode()
        packages["node_modules/pkg-%d" % i] = {
            "version": "1.0.%d" % i,
            "resolved": "https://registry.npmjs.org/pkg-%d/-/pkg-%d-1.0.%d.tgz" % (i, i, i),
            "integrity": "sha512-" + digest,
        }
    return json.dumps({"name": "demo", "lockfileVersion": 3, "packages": packages}, indent=2) + "\n"


# A hidden file whose NAME is markup: shown in the "left out" list, it must render as text.
HOSTILE_NAME = ".<img src=x onerror=window.__xss=1>"


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fileflow-demo")
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "package-lock.json").write_text(lockfile(), encoding="utf-8")
    (root / HOSTILE_NAME).write_text("hidden\n", encoding="utf-8")
    print(f"demo project written to {root} ({len(FILES) + 2} files)")


if __name__ == "__main__":
    main()
