# Pilot kit: learn from five real people before building anything else

*Everything fileflow knows about real use so far came from one author running it. This kit is how that changes. It builds nothing; it finds out what is worth building.*

**One decision per step.** Do them in order.

## Step 0 · Run the stranger walkthrough yourself (30 min, today)

```bash
python scripts/make_stranger_project.py /tmp/stranger-app
cd /tmp/stranger-app
fileflow .          # type nothing else. Read only what it prints.
```
Write down, in one line each: *what did it tell me? what did I need that it did not tell me? what would I do next?* Then try five mistakes (wrong path, `--format yaml`, a typo'd config key, an empty folder, `fetch example.com`). `tests/test_stranger_walkthrough.py` already keeps the answers from the **last** stranger from being forgotten; this step finds the **next** ones. Repeat it before every release, on a repo you did not make.

## Step 1 · Fix the one real blocker: participants cannot install it yet

**Verified here:** `pip wheel . --no-deps` builds `fileflow-0.2.0-py3-none-any.whl` (237 KB, includes the web client and fonts). In a fresh venv it installed in **≈ 0.7 s** (≈ 4 s with venv creation; `[server]` extra +5 s) and the first run took **92 ms** (one sandbox, fast network; yours will differ).
**Done / not done:** the branch is pushed to GitHub (`arena/01a02791-fileflow`; a fresh clone installs and passes), but **nothing is on PyPI** and the CI workflow is not installed yet (`ci/README.md`), so a participant still has no download link. Pick one:
- *Fastest:* hand them the wheel file: `python -m venv v && v/bin/pip install ./fileflow-0.2.0-py3-none-any.whl` (needs PyPI for `click`).
- *Also available once CI is installed (`ci/README.md`):* every push builds the wheel in a clean job and uploads it as a downloadable artifact.
- *Better:* publish to TestPyPI via trusted publishing (roadmap P0). Needs your decision and your accounts.

## Step 2 · Who (5 people, 2–3 teams)

Engineers who **already paste code into an AI chat at work**, ideally at a fintech, agency or vendor (the beachhead in `docs/production-architecture.md`, still unconfirmed by you). At least one who is *not* the person who would buy it, and at least one who would be blamed for a leak. Skip friends who will be polite.

## Step 3 · Ground rules (read aloud, 1 minute)

- "We are testing the tool, not you. If it confuses you, that is the finding."
- "Use **your own** repo on **your own** machine. Nothing is uploaded: fileflow has no telemetry and `prompt` never touches the network."
- "I will not look at your code or secrets. If your screen shows something private, tell me and I will look away. I take notes without code in them."
- No recording unless they say yes in writing. Note-taking only is the default. (Nigeria's NDPA covers personal data; names and employers stay out of the log: use `s1…s5`. *This is not legal advice.*)
- **Facilitator never helps.** If they are stuck, ask "what are you trying to do, and what do you expect to happen?" and write the answer down. Help only after the task is over.

## Step 4 · The session (25 minutes)

| Min | Do | Watch for (record in `observation-log.csv`) |
|---|---|---|
| 0–3 | Ground rules. Ask: "How do you give code to an AI today? Show me." | their usual method; how long it takes (seconds) |
| 3–5 | Install from the wheel. | where they hesitate; **seconds to first prompt** |
| 5–15 | **Task card** (below), their repo. Say nothing. | do they use fileflow or go back to pasting; do they read stderr; do they open `--help` |
| 15–19 | Ask: "Without scrolling up: what was left out of that prompt?" | `could_name_a_file_left_out` (yes/no, which one) |
| 19–22 | Ask: "Was there anything the tool warned you about? What did you do?" | `saw_heads_up`, `acted_on_heads_up` |
| 22–25 | Ask: "Who in your team would decide what this ignores? Who reviews a change to it?" and "What could get you blamed here?" | `asked_who_owns_config`, the blame answer, one quote |

### Task card (give them this, nothing else)

> You want an AI to explain **one module** of your own project so a new teammate could understand it. Prepare what you would send. **Do not send anything real today** — stop when the prompt is ready to paste. Say out loud anything you would not want to include.

## Step 5 · Decide the success criteria *before* the first session

Write these down and sign them with a date. Changing them afterwards is allowed; hiding that you did is not.

| Question | We call it a success if… | If it fails, we… |
|---|---|---|
| Faster than paste? | ≥ 4 of 5 get a ready prompt in **less time than their usual method** | the positioning is wrong; stop building features and look at the first 5 minutes |
| Does the "left out" line work? | ≥ 4 of 5 name a left-out file unprompted | make the line louder or move it into the prompt's header; re-test |
| Is the heads-up credible? | ≥ 3 of the participants it fires for act on it; ≤ 1 dismisses it as noise | raise the threshold, or turn it into a default exclusion (decision B) |
| Are secrets stopped? | **0 of 5 would send a secret** the tool could have caught; and any secret kind the scanner missed is recorded | add the shape to the scanner (with its placeholders as controls) before anything else |
| Does a lockfile default matter? | ≥ 2 of 5 say "just exclude lockfiles" and none say "I need them" | do decision B for lockfiles only |
| Who owns the config? | ≥ 3 of 5 name an owner **or** ask for one | build a `CODEOWNERS` check; otherwise leave it |

## Step 6 · After each session (10 minutes, same day)

1. Fill the CSV row. 2. Copy one confusing moment **verbatim**. 3. Write the single change you would make if only this person mattered. 4. Do not fix anything until all five are done (you will over-fit to person one).

## Step 7 · After five

Count against Step 5. For every row that failed, open the decision in `docs/systems-gap-analysis.md` §6 and write what you will do. Add a field note (`docs/field-notes.md`) with what surprised you. **Anything you cannot trace to a quote or a number does not go on the roadmap.**

## What this kit cannot tell you

Whether anyone *pays*; whether a team (as opposed to five individuals) adopts it; long-term behaviour (five 25-minute sessions measure first impressions); whether the Lagos network/power assumptions hold for a hosted plane (ask where they work from, but test that separately); accessibility for people who use assistive technology (recruit for that on purpose; the automated audit is not a substitute).
