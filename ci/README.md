# CI workflow (install step needed)

`ci.yml` here is the project's GitHub Actions workflow. It lives in `ci/` instead of
`.github/workflows/` **only because the automation that pushed this branch is not allowed to create
workflow files** (GitHub rejects any push that touches `.github/workflows/` from an app without the
`workflows` permission). It has **never run on GitHub**: only its commands were run locally.

Install it once, with your own credentials:

```bash
mkdir -p .github/workflows
cp ci/ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml && git commit -m "Add CI" && git push
```

What it runs: Python 3.8 / 3.11 / 3.12 (CLI, golden contract, `fileflow check --strict`), the Node web
engine tests, the cross-engine fuzz, a **wheel build + clean-venv stranger walkthrough** (uploads the
wheel as an artifact: the file pilot participants install), plus two **advisory** jobs that may fail the
first time: the real-browser accessibility audit and the Windows run.

Until it is installed, run the same checks locally: see `docs/engineering-contract.md`, "Release checklist".
