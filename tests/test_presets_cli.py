"""Presets, instructions, variables, secret guard, receipt and --copy through the real CLI."""
import json
import os

import pytest
from click.testing import CliRunner

import fileflow.cli as cli_module
from fileflow.cli import cli
from fileflow import walker

KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # assembled so this file is not itself a finding


def run(args, **kw):
    try:
        runner = CliRunner(mix_stderr=False)  # click < 8.2
    except TypeError:
        runner = CliRunner()  # click >= 8.2 always captures stdout and stderr separately
    return runner.invoke(cli, [str(a) for a in args], **kw)


CONFIG = '''version = 1

[output]
format = "default"
line_numbers = false
max_tokens = 0
separators = true

[presets.hero]
description = "Hero section only"
paths = ["components/hero", "notes/project-notes.txt"]
max_tokens = 0
format = "xml"
instruction = "Improve the hero section only."

[presets.ask]
description = "Needs variables"
paths = ["notes"]
[presets.ask.prompt]
role = "Act as a mentor."
context = "I am a {{role}} in {{domain}} for {{years}} years."
task = "Find the gap."
[presets.ask.vars]
years = "3"
'''


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "components" / "hero").mkdir(parents=True)
    (tmp_path / "components" / "hero" / "Hero.jsx").write_text("export const Hero = () => 1;\n")
    (tmp_path / "components" / "footer.jsx").write_text("export const Footer = () => 2;\n")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "project-notes.txt").write_text("purpose: demo\n")
    (tmp_path / ".files-to-prompt").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def conf(proj, text):
    (proj / ".files-to-prompt").write_text(text)


# ---- presets ----------------------------------------------------------------
def test_preset_selects_its_paths_with_no_other_flags(proj):
    r = run(["--preset", "hero"])
    assert r.exit_code == 0, r.stderr
    assert "Hero.jsx" in r.stdout and "project-notes.txt" in r.stdout
    assert "footer.jsx" not in r.stdout


def test_preset_equals_spelling_it_out_by_hand(proj):
    by_preset = run(["-p", "hero"]).stdout
    by_hand = run(["components/hero", "notes/project-notes.txt", "--format", "xml",
                   "--instruction", "Improve the hero section only."]).stdout
    assert by_preset == by_hand and by_preset


def test_unknown_preset_lists_choices_and_suggests(proj):
    r = run(["--preset", "her"])
    assert r.exit_code == 2
    assert "E_PRESET_UNKNOWN" in r.stderr and "hero" in r.stderr and "did you mean 'hero'" in r.stderr
    assert r.stdout == ""


def test_cli_paths_override_the_preset_paths(proj):
    out = run(["--preset", "hero", "components/footer.jsx"]).stdout
    assert "footer.jsx" in out and "Hero.jsx" not in out


@pytest.mark.parametrize("row", ["defaults", "root", "preset", "cli"])
def test_precedence_cli_over_preset_over_root_over_defaults(proj, row):
    """One row per layer: format is set at several levels and the highest one wins."""
    base = 'version = 1\n'
    root_fmt = '[output]\nformat = "json"\n' if row != "defaults" else ""
    preset_fmt = 'format = "xml"\n' if row in ("preset", "cli") else ""
    conf(proj, base + root_fmt + '[presets.p]\npaths = ["notes"]\n' + preset_fmt)
    args = ["--preset", "p"] + (["--format", "default"] if row == "cli" else [])
    out = run(args).stdout
    expect = {"defaults": "notes/project-notes.txt\n---", "root": '"path":', "preset": "<documents>", "cli": "notes/project-notes.txt\n---"}[row]
    assert expect in out, (row, out[:120])
    if row == "cli":
        assert "<documents>" not in out and '"path":' not in out


def test_precedence_for_numeric_and_boolean_options(proj):
    conf(proj, 'version = 1\n[output]\nline_numbers = true\nmax_tokens = 5\n[presets.p]\npaths = ["notes"]\nline_numbers = false\nmax_tokens = 0\n')
    assert "1  purpose" not in run(["--preset", "p"]).stdout          # preset beats root
    assert "1  purpose" in run(["--preset", "p", "--line-numbers"]).stdout  # CLI beats preset
    assert "truncated" not in run(["--preset", "p"]).stdout           # preset budget 0 beats root 5
    assert "truncated" in run(["--preset", "p", "--max-tokens", "1"]).stdout


def test_exclude_patterns_are_additive(proj):
    conf(proj, 'version = 1\n[exclude]\npatterns = ["footer.jsx"]\n[presets.p]\npaths = ["components"]\nexclude_patterns = ["Hero.jsx"]\n')
    out = run(["--preset", "p"]).stdout
    assert "footer.jsx" not in out and "Hero.jsx" not in out


# ---- confinement (config may come from a repository you just cloned) ----------
@pytest.mark.parametrize("bad", ["../outside", "/etc", "components/../../outside"])
def test_preset_paths_cannot_leave_the_project(proj, bad):
    conf(proj, 'version = 1\n[presets.p]\npaths = ["%s"]\n' % bad)
    r = run(["--preset", "p"])
    assert r.exit_code == 3 and "E_PATH_OUTSIDE_ROOT" in r.stderr and r.stdout == ""


def test_preset_path_through_an_outside_symlink_is_refused(proj, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "id_rsa").write_text("PRIVATE")
    os.symlink(outside, proj / "innocent")
    conf(proj, 'version = 1\n[presets.p]\npaths = ["innocent"]\n')
    r = run(["--preset", "p"])
    assert r.exit_code == 3 and "PRIVATE" not in r.stdout


def test_preset_instruction_file_is_confined_but_the_cli_flag_is_not(proj, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "task.md").write_text("from outside, chosen by the user")
    conf(proj, 'version = 1\n[presets.p]\npaths = ["notes"]\ninstruction_file = "%s"\n' % (outside / "task.md"))
    r = run(["--preset", "p"])
    assert r.exit_code == 3 and "E_PATH_OUTSIDE_ROOT" in r.stderr
    ok = run(["--preset", "p", "--instruction-file", outside / "task.md"])  # the user typed it
    assert ok.exit_code == 0 and "from outside, chosen by the user" in ok.stdout


def test_missing_preset_path_is_a_classified_error(proj):
    conf(proj, 'version = 1\n[presets.p]\npaths = ["nope"]\n')
    r = run(["--preset", "p"])
    assert r.exit_code == 2 and "E_PRESET_PATH_MISSING" in r.stderr and "nope" in r.stderr


# ---- instructions + variables -------------------------------------------------
def test_instruction_flag_renders_outside_the_files(proj):
    out = run(["notes", "--instruction", "Summarise.", "--format", "xml"]).stdout
    assert out.startswith("<task_instructions>\nSummarise.\n</task_instructions>\n<documents>")


def test_instruction_file_and_flag_are_mutually_exclusive(proj):
    (proj / "t.md").write_text("x")
    r = run(["notes", "--instruction", "a", "--instruction-file", "t.md"])
    assert r.exit_code == 2 and "E_USAGE" in r.stderr


def test_preset_instruction_file_inside_the_project(proj):
    (proj / "notes" / "task.md").write_text("Do the thing for {{who}}.\n")
    conf(proj, 'version = 1\n[presets.p]\npaths = ["notes/project-notes.txt"]\ninstruction_file = "notes/task.md"\n')
    out = run(["--preset", "p", "--var", "who=me"]).stdout
    assert out.startswith("# Task\n\nDo the thing for me.")


def test_structured_prompt_renders_labelled_lines(proj):
    out = run(["--preset", "ask", "--var", "role=developer", "--var", "domain=backend"]).stdout
    assert out.startswith(
        "# Task\n\nROLE: Act as a mentor.\nCONTEXT: I am a developer in backend for 3 years.\nTASK: Find the gap."
    )


def test_unresolved_placeholders_fail_loudly_and_say_how_to_fix(proj):
    r = run(["--preset", "ask", "--var", "role=developer"])
    assert r.exit_code == 2 and "E_PLACEHOLDER_UNRESOLVED" in r.stderr
    assert "{{domain}}" in r.stderr and "--var domain=VALUE" in r.stderr and "{{role}}" not in r.stderr
    assert r.stdout == ""  # nothing half-filled ever reaches the clipboard/stdout


def test_allow_unresolved_keeps_them_verbatim(proj):
    r = run(["--preset", "ask", "--allow-unresolved"])
    assert r.exit_code == 0 and "{{role}}" in r.stdout and "{{domain}}" in r.stdout and "for 3 years" in r.stdout


def test_cli_var_overrides_preset_default(proj):
    out = run(["--preset", "ask", "--var", "role=a", "--var", "domain=b", "--var", "years=9"]).stdout
    assert "for 9 years" in out and "for 3 years" not in out


def test_substitution_is_single_pass(proj):
    out = run(["notes", "--instruction", "{{a}}", "--var", "a={{b}}", "--var", "b=BOOM", "--allow-unresolved"]).stdout
    assert "{{b}}" in out and "BOOM" not in out.split("# Files")[0]


def test_file_contents_are_never_scanned_for_placeholders(proj):
    (proj / "notes" / "tpl.txt").write_text("keep {{this}} as is\n")
    r = run(["notes", "--instruction", "Fine."])
    assert r.exit_code == 0 and "{{this}}" in r.stdout


def test_unused_var_is_warned_about_not_fatal(proj):
    r = run(["notes", "--instruction", "Hello", "--var", "typo=1"])
    assert r.exit_code == 0 and "'typo' is not used" in r.stderr


def test_bad_var_syntax(proj):
    r = run(["notes", "--instruction", "x", "--var", "novalue"])
    assert r.exit_code == 2 and "name=value" in r.stderr


# ---- config errors -------------------------------------------------------------
def test_broken_config_is_an_error_when_a_preset_is_requested(proj):
    conf(proj, 'version = 1\n[presets.p\n')
    r = run(["--preset", "p"])
    assert r.exit_code == 2 and "E_CONFIG_PARSE" in r.stderr


def test_broken_config_is_an_error_even_without_a_preset(proj):
    """FN-015: it used to warn and send anyway, dropping the user's safety settings."""
    conf(proj, '[output\n')
    r = run(["notes"])
    assert r.exit_code == 2 and "E_CONFIG_PARSE" in r.stderr and "nothing was sent" in r.stderr
    assert "project-notes" not in r.stdout and "ignoring config file" not in r.stderr


def test_wrong_preset_types_are_schema_errors(proj):
    conf(proj, 'version = 1\n[presets.p]\npaths = "notes"\n')
    r = run(["--preset", "p"])
    assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr and "paths" in r.stderr


def test_two_instruction_sources_in_one_preset_are_rejected(proj):
    conf(proj, 'version = 1\n[presets.p]\npaths = ["notes"]\ninstruction = "a"\ninstruction_file = "b"\n')
    assert "E_CONFIG_SCHEMA" in run(["--preset", "p"]).stderr


def test_newer_schema_version_is_refused(proj):
    conf(proj, 'version = 99\n')
    r = run(["notes"])
    assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr and "up to 1" in r.stderr


def test_unknown_keys_are_tolerated(proj):
    conf(proj, 'version = 1\nfuture_thing = true\n[presets.p]\npaths = ["notes"]\nsome_new_key = 1\n')
    assert run(["--preset", "p"]).exit_code == 0


# ---- presets command ------------------------------------------------------------
def test_presets_command_lists_name_description_budget(proj):
    r = run(["presets"])
    assert r.exit_code == 0
    assert "hero" in r.stdout and "Hero section only" in r.stdout and "ask" in r.stdout


def test_presets_command_json_and_empty(proj):
    assert set(json.loads(run(["presets", "--json"]).stdout)) == {"hero", "ask"}
    conf(proj, 'version = 1\n')
    assert "No presets defined" in run(["presets"]).stdout


# ---- secret guard -----------------------------------------------------------------
@pytest.fixture
def leaky(proj):
    (proj / "notes" / "creds.txt").write_text("aws = %s\n" % KEY)
    return proj


def test_warn_is_the_default_keeps_file_and_never_shows_the_value_in_the_warning(leaky):
    r = run(["notes"])
    assert r.exit_code == 0 and KEY in r.stdout           # the user asked for these files
    assert "possible secret" in r.stderr and "aws-access-key-id" in r.stderr and KEY not in r.stderr


def test_exclude_leaves_the_file_out(leaky):
    r = run(["notes", "--secrets", "exclude"])
    assert r.exit_code == 0 and KEY not in r.stdout and "project-notes" in r.stdout
    assert KEY not in r.stderr


def test_block_fails_closed_with_exit_3_and_emits_nothing(leaky):
    r = run(["notes", "--secrets", "block"])
    assert r.exit_code == 3 and "E_SECRET_FOUND" in r.stderr
    assert "creds.txt" in r.stderr and KEY not in r.stderr and r.stdout == ""


def test_block_does_not_fire_without_findings(proj):
    assert run(["notes", "--secrets", "block"]).exit_code == 0


def test_off_is_silent(leaky):
    r = run(["notes", "--secrets", "off"])
    assert r.exit_code == 0 and "possible secret" not in r.stderr


def test_secret_mode_from_root_config_and_preset_and_cli(leaky):
    conf(leaky, 'version = 1\n[secrets]\nmode = "block"\n[presets.p]\npaths = ["notes"]\n[presets.q]\npaths = ["notes"]\nsecrets = "warn"\n')
    assert run(["notes"]).exit_code == 3                       # root config
    assert run(["--preset", "p"]).exit_code == 3               # inherited
    assert run(["--preset", "q"]).exit_code == 0               # preset beats root
    assert run(["--preset", "p", "--secrets", "off"]).exit_code == 0  # CLI beats all


@pytest.mark.parametrize("text,hit", [
    ("-----BEGIN RSA PRIVATE KEY-----\n" + "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n", True),
    ("-----BEGIN RSA PRIVATE KEY-----\n", False),  # header alone is a mention, not a key
    ("token = ghp_" + "a" * 36 + "\n", True),
    ("export DB_PASSWORD=hunter2hunter2\n", True),
    ("DB_PASSWORD=changeme\n", False),
    ("API_KEY=your_api_key_here\n", False),
    ("password: ${DB_PASSWORD}\n", False),
    ("const sk = 'skeleton-key-for-the-css-class-name-here'\n", False),
    ("# AKIA is a prefix, not a key\n", False),
])
def test_scanner_precision(text, hit):
    from fileflow.secretscan import scan
    assert bool(scan(text)) is hit, text


# ---- receipt ------------------------------------------------------------------------
def make_messy(proj):
    (proj / ".gitignore").write_text("dist/\n*.log\n")
    (proj / "dist").mkdir()
    (proj / "dist" / "bundle.js").write_text("x")
    (proj / "debug.log").write_text("x")
    (proj / ".env.example").write_text("A=1\n")
    (proj / "logo.bin").write_bytes(b"\xff\xfe\x00\x80")


def test_receipt_goes_to_stderr_and_stdout_stays_a_clean_prompt(proj):
    plain = run(["notes"]).stdout
    r = run(["notes", "--receipt"])
    assert r.stdout == plain
    assert "Context receipt" in r.stderr and "unknown" in r.stderr


def test_receipt_file_is_complete_every_file_exactly_once(proj, tmp_path_factory):
    make_messy(proj)
    out = proj.parent / "receipt.json"
    assert run([".", "--receipt-file", out, "--max-tokens", "12"]).exit_code == 0
    rec = json.loads(out.read_text())
    seen = [i["path"] for i in rec["included"] + rec["truncated"] + rec["excluded"]]
    assert len(seen) == len(set(seen)), "a file appears twice in the receipt"
    # ground truth: everything the walker met
    met = set()
    for e in walker.walk_entries(["."], True, True, []):  # include everything -> superset check below
        met.add(os.path.relpath(e.path, ".").replace(os.sep, "/") + ("/" if e.is_dir else ""))
    assert set(seen) <= met
    must = {"components/footer.jsx", "components/hero/Hero.jsx", "notes/project-notes.txt", "logo.bin", "dist/", "debug.log", ".env.example"}
    assert must <= set(seen), must - set(seen)


def test_receipt_reasons_are_the_closed_vocabulary(proj):
    make_messy(proj)
    out = proj.parent / "r.json"
    run([".", "--receipt-file", out, "--max-tokens", "8"])
    rec = json.loads(out.read_text())
    reasons = {e["reason"] for e in rec["excluded"]} | {t["reason"] for t in rec["truncated"]}
    assert reasons <= set(walker.REASONS)
    assert {"HIDDEN", "GITIGNORED", "BINARY", "TOKEN_BUDGET"} <= reasons


def test_budget_truncation_and_drops_are_never_silent(proj):
    out = proj.parent / "r.json"
    run(["components", "notes", "--receipt-file", out, "--max-tokens", "9"])
    rec = json.loads(out.read_text())
    assert rec["totals"]["budget"] == 9
    cut = rec["truncated"] + [e for e in rec["excluded"] if e["reason"] == "TOKEN_BUDGET"]
    assert cut, "a 9-token budget must leave something out, and say so"


def test_receipt_is_deterministic_and_has_no_absolute_paths(proj):
    a, b = proj.parent / "a.json", proj.parent / "b.json"
    run([".", "--receipt-file", a]); run([".", "--receipt-file", b])
    assert a.read_text() == b.read_text()
    assert str(proj) not in a.read_text() and str(proj.parent) not in a.read_text()


def test_receipt_ledger_separates_verified_assumed_unknown(proj):
    out = proj.parent / "r.json"
    run(["--preset", "hero", "--receipt-file", out])
    led = json.loads(out.read_text())["ledger"]
    assert any("parsed and validated" in v for v in led["verified"])
    assert any("preset paths exist" in v for v in led["verified"])
    assert any("no unresolved placeholders" in v for v in led["verified"])
    assert any("heuristic" in a for a in led["assumed"])
    assert any("context window" in u for u in led["unknown"]) and any("follow the instruction" in u for u in led["unknown"])
    assert led["failed_checks"] == []


def test_receipt_flags_secrets_and_unresolved_as_failed_checks(leaky, proj):
    out = proj.parent / "r.json"
    run(["--preset", "ask", "--allow-unresolved", "--receipt-file", out])
    failed = json.loads(out.read_text())["ledger"]["failed_checks"]
    assert any("unresolved placeholders" in f and "domain" in f for f in failed)
    assert any("possible secret in notes/creds.txt" in f for f in failed)
    assert KEY not in out.read_text()


def test_receipt_excludes_secret_blocked_files_with_a_reason(leaky, proj):
    out = proj.parent / "r.json"
    run(["notes", "--secrets", "exclude", "--receipt-file", out])
    rec = json.loads(out.read_text())
    assert {"path": "notes/creds.txt", "reason": "SECRET_BLOCKED"} in rec["excluded"]


def test_receipt_estimate_wording(proj):
    assert "(estimate)" in run(["notes", "--receipt"]).stderr


# ---- clipboard ----------------------------------------------------------------------
def test_copy_puts_the_prompt_on_the_clipboard_and_keeps_stdout_quiet(proj, monkeypatch):
    got = {}
    monkeypatch.setattr(cli_module.clipboard, "copy", lambda text: got.setdefault("t", text) and "fake-clip")
    r = run(["notes", "--copy"])
    assert r.exit_code == 0 and r.stdout == ""
    assert "project-notes.txt" in got["t"] and "Copied to clipboard via fake-clip" in r.stderr and "(estimate)" in r.stderr


def test_copy_falls_back_to_stdout_when_there_is_no_clipboard(proj, monkeypatch):
    monkeypatch.setattr(cli_module.clipboard, "copy", lambda text: None)
    r = run(["notes", "--copy"])
    assert r.exit_code == 0 and "project-notes.txt" in r.stdout and "no clipboard tool" in r.stderr


# ---- taxonomy ---------------------------------------------------------------------
def test_every_error_code_has_a_documented_exit_code():
    from fileflow.errors import EXIT_CODES, FileflowError
    assert set(EXIT_CODES.values()) <= {1, 2, 3, 4}
    with pytest.raises(ValueError):
        FileflowError("E_MADE_UP", "x")
    assert FileflowError("E_PATH_OUTSIDE_ROOT", "x").exit_code == 3
