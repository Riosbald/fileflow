#!/usr/bin/env python3
"""Build the messy project used by the "stranger walkthrough" (docs/pilot-kit.md).

    python scripts/make_stranger_project.py /tmp/stranger-app

A believable small billing service: git repo, `node_modules`, a `.env` holding a database URL and a
Stripe-shaped key, a large lockfile, a PNG, a log. Every credential-shaped string is ASSEMBLED here so
this file is not itself a finding when `fileflow check --strict` scans the repo. Nothing is real.
"""
import os
import subprocess
import sys
from pathlib import Path


def build(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=False)
    files = {
        "main.py": "from src.payments import pay\n\nprint(pay(100))\n",
        "src/payments.py": "def pay(amount):\n    return amount * 1.075  # VAT\n",
        "src/__init__.py": "",
        "README.md": "# Pay\n\nInternal billing service.\n",
        "docs/notes.txt": "TODO: rotate keys, ask finance about VAT\n",
        "node_modules/x/index.js": "console.log(1)\n",
        "dist/bundle.js": "built\n",
        "debug.log": "debug\n" * 50,
        ".gitignore": "node_modules/\ndist/\n*.log\n",
        # credential-shaped values, assembled
        ".env": "DATABASE_URL=" + "postgres://admin:" + "S3cr3t" + "P4ssw0rd@db.internal:5432/prod\n"
                + "STRIPE_SECRET=" + "sk_live_" + "a" * 24 + "\n",
        "package-lock.json": '{"lockfile":"' + "x" * 400_000 + '"}\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "logo.png").write_bytes(bytes(range(256)) * 12)
    return root


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "/tmp/stranger-app"
    print("stranger project written to", build(target))
    print("now, as someone who has never seen fileflow:  cd %s && fileflow ." % target)
