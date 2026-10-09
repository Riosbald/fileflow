"""`fileflow init` templates and `fileflow check` -- planted problems with known findings."""
import json
import os
import re

import pytest
from click.testing import CliRunner

from fileflow.cli import cli

KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # assembled so this file is not itself a finding


def run(args, cwd=None):
    try:
        runner = CliRunner(mix_stderr=False)  # click < 8.2
    except TypeError:
        runner = CliRunner()
    if cwd:
        old = os.getcwd()
        os.chdir(str(cwd))
    try:
        return runner.invoke(cli, [str(a) for a in args])
    finally:
        if cwd:
            os.chdir(old)


def put(root, rel, text=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def check(root, *extra):
    return run(["check", "--root", root, "--json", *extra])


def findings(root, *extra):
    r = check(root, *extra)
    return r, json.loads(r.stdout)["findings"]


def codes(fs, level=None):
    return sorted(f["code"] for f in fs if level is None or f["level"] == level)


@pytest.fixture
def harness(tmp_path):
    assert run(["init", "--template", "harness", "--dir", tmp_path]).exit_code == 0
    return tmp_path


def fill_all(root):
    """Replace every <<FILL: ...>> blank, as a user finishing the template would."""
    for p in root.rglob("*.md"):
        t = p.read_text()
        if "<<FILL:" in t:
            p.write_text(re.sub(r"<<FILL: [^>]*>>", "done", t))


# ---- init ---------------------------------------------------------------------
@pytest.mark.parametrize("template", ["minimal", "web-sprint", "harness"])
def test_every_template_passes_check_with_no_errors(tmp_path, template):
    assert run(["init", "--template", template, "--dir", tmp_path]).exit_code == 0
    r, fs = findings(tmp_path)
    assert r.exit_code == 0 and codes(fs, "error") == [], fs


def test_init_is_idempotent_and_never_overwrites_user_edits(harness):
    (harness / "AGENTS.md").write_text("# mine\n")
    r = run(["init", "--template", "harness", "--dir", harness])
    assert "Nothing to do" in r.stdout and "skipped      AGENTS.md" in r.stdout
    assert (harness / "AGENTS.md").read_text() == "# mine\n"


def test_force_overwrites(harness):
    (harness / "AGENTS.md").write_text("# mine\n")
    r = run(["init", "--template", "harness", "--dir", harness, "--force"])
    assert "overwritten  AGENTS.md" in r.stdout and (harness / "AGENTS.md").read_text().startswith("# AGENTS.md")


def test_no_claude_skips_claude_files(tmp_path):
    run(["init", "--template", "harness", "--dir", tmp_path, "--no-claude"])
    assert (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists() and not (tmp_path / ".claude").exists()


def test_init_prints_exactly_one_next_step(tmp_path):
    out = run(["init", "--template", "minimal", "--dir", tmp_path]).stdout
    assert out.count("Next:") == 1 and "fileflow --preset analyze" in out


def test_list_describes_templates():
    out = run(["init", "--list"]).stdout
    assert all(t in out for t in ("minimal", "web-sprint", "harness"))


def test_the_template_obeys_its_own_size_guidance(harness):
    assert len((harness / "AGENTS.md").read_text().splitlines()) <= 100


def test_generated_presets_actually_run(harness):
    assert "onboard" in run(["presets"], cwd=harness).stdout
    r = run(["--preset", "onboard"], cwd=harness)
    assert r.exit_code == 0 and "# AGENTS.md" in r.stdout and "# Task" in r.stdout
    assert run(["--preset", "harness-review"], cwd=harness).exit_code == 0


def test_a_fresh_harness_warns_about_blanks_and_strict_fails_it(harness):
    r, fs = findings(harness)
    assert r.exit_code == 0 and {"H_UNFILLED", "H_SPEC_CRITICAL_UNFILLED", "H_SPEC_UNFILLED"} <= set(codes(fs, "warn"))
    assert check(harness, "--strict").exit_code == 2


def test_a_filled_in_harness_is_clean_even_under_strict(harness):
    fill_all(harness)
    r, fs = findings(harness, "--strict")
    assert r.exit_code == 0 and fs == [], fs


def test_blank_in_critical_section_is_named(harness):
    fs = findings(harness)[1]
    crit = next(f for f in fs if f["code"] == "H_SPEC_CRITICAL_UNFILLED")
    assert "Permissions" in crit["message"] and "Checks" in crit["message"] and "The loop" in crit["message"]


# ---- size -----------------------------------------------------------------------
def test_length_guidance_warns_in_two_steps(tmp_path):
    put(tmp_path, "AGENTS.md", "x\n" * 150)
    assert codes(findings(tmp_path)[1], "warn") == ["H_LONG"]
    put(tmp_path, "AGENTS.md", "x\n" * 250)
    assert codes(findings(tmp_path)[1], "warn") == ["H_VERY_LONG"]


def test_length_threshold_is_configurable(tmp_path):
    put(tmp_path, "AGENTS.md", "x\n" * 150)
    put(tmp_path, ".files-to-prompt", "version = 1\n[harness]\nmax_lines = 180\n")
    assert "H_LONG" not in codes(findings(tmp_path)[1])  # 150 lines is fine under max_lines = 180
    put(tmp_path, ".files-to-prompt", "version = 1\n[harness]\nmax_lines = 120\n")
    assert "H_LONG" in codes(findings(tmp_path)[1])


def test_bad_harness_config_is_an_error(tmp_path):
    put(tmp_path, "AGENTS.md", "x\n")
    put(tmp_path, ".files-to-prompt", "version = 1\n[harness]\nmax_lines = \"lots\"\n")
    r, fs = findings(tmp_path)
    assert r.exit_code == 2 and "C_CONFIG_SCHEMA" in codes(fs, "error")


def test_codex_chain_over_32k_is_an_error(tmp_path):
    put(tmp_path, "AGENTS.md", "a" * 20000 + "\n")
    put(tmp_path, "services/payments/AGENTS.md", "b" * 20000 + "\n")
    r, fs = findings(tmp_path)
    assert r.exit_code == 2
    err = next(f for f in fs if f["code"] == "H_CODEX_BYTES")
    assert err["path"] == "services/payments/AGENTS.md" and "silently dropped" in err["message"]


def test_codex_reads_one_file_per_directory_override_wins(tmp_path):
    put(tmp_path, "AGENTS.md", "a" * 20000 + "\n")
    put(tmp_path, "svc/AGENTS.md", "b" * 20000 + "\n")        # ignored by Codex: an override exists
    put(tmp_path, "svc/AGENTS.override.md", "c" * 100 + "\n")
    assert "H_CODEX_BYTES" not in codes(findings(tmp_path)[1])


def test_two_siblings_do_not_add_up(tmp_path):
    put(tmp_path, "AGENTS.md", "a" * 100 + "\n")
    put(tmp_path, "one/AGENTS.md", "b" * 20000 + "\n")
    put(tmp_path, "two/AGENTS.md", "c" * 20000 + "\n")
    assert "H_CODEX_BYTES" not in codes(findings(tmp_path)[1])  # separate chains


# ---- links / imports -------------------------------------------------------------
def test_dangling_pointer_in_the_map_is_an_error(tmp_path):
    put(tmp_path, "AGENTS.md", "See [the plan](docs/plan.md) and [arch](ARCHITECTURE.md).\n")
    put(tmp_path, "ARCHITECTURE.md", "ok\n")
    r, fs = findings(tmp_path)
    assert r.exit_code == 2
    e = next(f for f in fs if f["code"] == "H_LINK_DANGLING")
    assert e["level"] == "error" and e["path"] == "AGENTS.md:1" and "docs/plan.md" in e["message"]


def test_dangling_link_in_a_doc_is_only_a_warning(tmp_path):
    put(tmp_path, "AGENTS.md", "ok\n")
    put(tmp_path, "docs/a.md", "[x](missing.md)\n")
    r, fs = findings(tmp_path)
    assert r.exit_code == 0 and codes(fs, "warn") == ["H_LINK_DANGLING"]


def test_links_in_code_urls_and_anchors_are_not_checked(tmp_path):
    put(tmp_path, "AGENTS.md", "\n".join([
        "```", "[fenced](nope.md)", "```", "`[span](nope2.md)`",
        "[web](https://example.com/x.md)", "[anchor](#section)", "[mail](mailto:a@b.co)",
        "[with anchor](real.md#part) and [query](real.md?x=1)"]) + "\n")
    put(tmp_path, "real.md", "x\n")
    assert [f for f in findings(tmp_path)[1] if f["level"] != "info"] == []


def test_link_leaving_the_repo_is_a_warning(tmp_path):
    root = tmp_path / "repo"
    put(root, "AGENTS.md", "[out](../elsewhere.md)\n")
    put(tmp_path, "elsewhere.md", "x\n")
    assert "H_LINK_OUTSIDE" in codes(findings(root)[1], "warn")


def test_imports(tmp_path):
    put(tmp_path, "AGENTS.md", "x\n")
    put(tmp_path, "CLAUDE.md", "@AGENTS.md\n@nope.md\nmention `@README.md` safely\nmail me@example.com\nping @alice\n")
    fs = findings(tmp_path)[1]
    assert [f["message"] for f in fs if f["code"] == "H_IMPORT_MISSING"] == ["@nope.md does not exist"]


def test_import_cycle_and_depth(tmp_path):
    put(tmp_path, "CLAUDE.md", "@a.md\n")
    put(tmp_path, "a.md", "@b.md\n")
    put(tmp_path, "b.md", "@a.md\n")
    assert "H_IMPORT_CYCLE" in codes(findings(tmp_path)[1])
    deep = tmp_path / "deep"
    put(deep, "CLAUDE.md", "@l1.md\n")
    for i in range(1, 7):
        put(deep, "l%d.md" % i, "@l%d.md\n" % (i + 1))
    put(deep, "l7.md", "end\n")
    assert "H_IMPORT_DEPTH" in codes(findings(deep)[1])


def test_drift_between_agents_and_claude(tmp_path):
    put(tmp_path, "AGENTS.md", "# rules\n- be careful\n")
    put(tmp_path, "CLAUDE.md", "# rules\n- be careful\n")
    assert "H_DRIFT" in codes(findings(tmp_path)[1])
    put(tmp_path, "CLAUDE.md", "@AGENTS.md\n")
    assert "H_DRIFT" not in codes(findings(tmp_path)[1])
    put(tmp_path, "CLAUDE.md", "something different entirely\n")
    assert "H_DRIFT" in codes(findings(tmp_path)[1])


# ---- spec -------------------------------------------------------------------------
def test_spec_missing_sections_are_named(tmp_path):
    put(tmp_path, "docs/HARNESS.md", "## 1. Instructions\nx\n## 5. Permissions\nx\n")
    f = next(f for f in findings(tmp_path)[1] if f["code"] == "H_SPEC_MISSING_SECTION")
    assert "2. Context" in f["message"] and "8. The loop" in f["message"] and "5. Permissions" not in f["message"]


# ---- permissions: advisory vs enforced ------------------------------------------------
SPEC = "".join("## %d. %s\ndone\n" % (n, t) for n, t in
               [(1, "Instructions"), (2, "Context"), (3, "Skills"), (4, "Memory"), (5, "Permissions"), (6, "Tools"), (7, "Checks"), (8, "The loop")])


def test_permissions_only_in_prose_is_flagged(tmp_path):
    put(tmp_path, "docs/HARNESS.md", SPEC)
    f = next(f for f in findings(tmp_path)[1] if f["code"] == "H_PERMS_PROSE_ONLY")
    assert "advisory" in f["hint"] and f["level"] == "warn"


def test_deny_rules_or_a_container_silence_the_prose_only_warning(tmp_path):
    put(tmp_path, "docs/HARNESS.md", SPEC)
    put(tmp_path, ".claude/settings.json", json.dumps({"permissions": {"deny": ["Bash(rm -rf *)"]}}))
    assert "H_PERMS_PROSE_ONLY" not in codes(findings(tmp_path)[1])
    (tmp_path / ".claude" / "settings.json").unlink()
    put(tmp_path, ".devcontainer/devcontainer.json", "{}")
    assert "H_PERMS_PROSE_ONLY" not in codes(findings(tmp_path)[1])


@pytest.mark.parametrize("body,code", [
    ("{not json", "H_SETTINGS_INVALID"),
    (json.dumps({"permissions": {"deny": "Bash(rm *)"}}), "H_SETTINGS_INVALID"),
    (json.dumps({"permissions": ["x"]}), "H_SETTINGS_INVALID"),
    (json.dumps({"permissions": {"deny": [1]}}), "H_SETTINGS_INVALID"),
])
def test_malformed_settings_are_errors(tmp_path, body, code):
    put(tmp_path, ".claude/settings.json", body)
    r, fs = findings(tmp_path)
    assert r.exit_code == 2 and code in codes(fs, "error")


def test_documented_ignored_rule_forms_are_flagged(tmp_path):
    put(tmp_path, ".claude/settings.json", json.dumps({"permissions": {"deny": ["Bash(command:rm *)", "Read(file_path:.env)", "Bash(rm *)"]}}))
    bad = [f for f in findings(tmp_path)[1] if f["code"] == "H_SETTINGS_RULE_IGNORED"]
    assert len(bad) == 2 and all("ignored" in f["message"] for f in bad)


def test_bypass_permissions_default_is_flagged(tmp_path):
    put(tmp_path, ".claude/settings.json", json.dumps({"permissions": {"defaultMode": "bypassPermissions", "deny": ["Bash(rm *)"]}}))
    assert "H_BYPASS_DEFAULT" in codes(findings(tmp_path)[1], "warn")


# ---- skills ------------------------------------------------------------------------------
def skill(root, name, text):
    put(root, ".claude/skills/%s/SKILL.md" % name, text)


def test_valid_skill_passes_including_folded_description(tmp_path):
    skill(tmp_path, "deploy", "---\nname: deploy\ndescription: >\n  Deploy the app.\n  Use when asked to ship.\n---\nSteps...\n")
    assert [f for f in findings(tmp_path)[1] if f["code"].startswith("H_SKILL")] == []


@pytest.mark.parametrize("text,why", [
    ("no frontmatter at all\n", "frontmatter"),
    ("---\nname: Deploy_It\ndescription: x\n---\n", "lowercase"),
    ("---\nname: my-claude-helper\ndescription: x\n---\n", "claude"),
    ("---\nname: ok\ndescription:\n---\n", "empty"),
    ("---\nname: ok\ndescription: " + "d" * 1025 + "\n---\n", "1024"),
    ("---\nname: ok\ndescription: use <b>this</b>\n---\n", "XML"),
])
def test_invalid_skills_are_errors(tmp_path, text, why):
    skill(tmp_path, "s", text)
    r, fs = findings(tmp_path)
    assert r.exit_code == 2
    assert any(f["code"] == "H_SKILL_INVALID" and why in f["message"] for f in fs), fs


def test_long_skill_is_a_warning(tmp_path):
    skill(tmp_path, "big", "---\nname: big\ndescription: d\n---\n" + "line\n" * 600)
    r, fs = findings(tmp_path)
    assert r.exit_code == 0 and "H_SKILL_LONG" in codes(fs, "warn")


# ---- secrets ---------------------------------------------------------------------------------
def test_secret_in_an_instruction_file_is_an_error_and_never_echoed(tmp_path):
    put(tmp_path, "AGENTS.md", "key: %s\n" % KEY)
    r = check(tmp_path)
    assert r.exit_code == 2 and "H_SECRET_IN_INSTRUCTIONS" in r.stdout and KEY not in r.stdout
    text = run(["check", "--root", tmp_path]).stdout
    assert KEY not in text and "H_SECRET_IN_INSTRUCTIONS" in text


def test_secret_elsewhere_is_a_warning(tmp_path):
    put(tmp_path, "src/app.py", 'KEY = "%s"\n' % KEY)
    r, fs = findings(tmp_path)
    assert r.exit_code == 0 and "C_SECRET" in codes(fs, "warn")


# ---- .files-to-prompt ------------------------------------------------------------------------
def test_config_errors(tmp_path):
    put(tmp_path, ".files-to-prompt", "[output\n")
    r, fs = findings(tmp_path)
    assert r.exit_code == 2 and "C_CONFIG_PARSE" in codes(fs, "error")
    put(tmp_path, ".files-to-prompt", 'version = 1\n[presets.p]\npaths = "x"\n')
    assert "C_CONFIG_SCHEMA" in codes(findings(tmp_path)[1], "error")


def test_preset_path_problems(tmp_path):
    put(tmp_path, ".files-to-prompt", 'version = 1\n[presets.a]\npaths = ["nope"]\n[presets.b]\npaths = ["../out"]\n[presets.c]\npaths = ["."]\ninstruction_file = "missing.md"\n')
    fs = findings(tmp_path)[1]
    assert sorted(f["path"] for f in fs if f["code"] == "C_PRESET_PATH") == ["presets.a", "presets.b"]
    assert "C_PRESET_INSTRUCTION" in codes(fs, "error")


def test_budget_truncation_and_placeholders(tmp_path):
    put(tmp_path, "big.txt", "word " * 4000)
    put(tmp_path, ".files-to-prompt", 'version = 1\n[presets.p]\npaths = ["big.txt"]\nmax_tokens = 100\ninstruction = "Hello {{who}} {{what}}"\n[presets.p.vars]\nwho = "x"\n')
    fs = findings(tmp_path)[1]
    assert "C_BUDGET_TRUNCATES" in codes(fs, "warn")
    ph = next(f for f in fs if f["code"] == "C_PLACEHOLDERS")
    assert ph["level"] == "info" and "what" in ph["message"] and "who" not in ph["message"]


# ---- general behaviour --------------------------------------------------------------------------
def test_no_harness_files_means_harness_checks_are_skipped(tmp_path):
    put(tmp_path, "a.txt", "x\n")
    r = run(["check", "--root", tmp_path])
    assert r.exit_code == 0 and "harness checks skipped" in r.stdout


def test_report_always_states_what_it_cannot_know(harness):
    out = run(["check", "--root", harness]).stdout
    assert "assumed" in out and "cannot tell whether the instructions are good" in out
    assert "unknown" in out and "what your agent actually loads" in out


def test_json_report_shape(harness):
    d = json.loads(check(harness).stdout)
    assert set(d) >= {"ok", "errors", "warnings", "findings", "ledger", "stats"}
    assert set(d["ledger"]) == {"verified", "assumed", "unknown"}
    assert all(set(f) == {"level", "code", "path", "message", "hint"} for f in d["findings"])


def test_check_ignores_vendored_directories(tmp_path):
    put(tmp_path, "AGENTS.md", "ok\n")
    put(tmp_path, "node_modules/pkg/AGENTS.md", "x\n" * 500)
    put(tmp_path, ".venv/lib/CLAUDE.md", "@nope.md\n")
    assert [f for f in findings(tmp_path)[1] if f["level"] != "info"] == []


# ---- dogfooding: the repository's own harness must stay clean ----------------------------------
def test_this_repositorys_own_harness_passes_strict():
    """fileflow's own AGENTS.md / docs / settings are held to the rules it ships (and CI runs this)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isfile(os.path.join(root, "docs", "HARNESS.md")):
        pytest.skip("not running from a source checkout")
    r = run(["check", "--root", root, "--strict", "--json"])
    report = json.loads(r.stdout)
    bad = [(f["level"], f["code"], f["path"], f["message"]) for f in report["findings"] if f["level"] != "info"]
    assert r.exit_code == 0 and bad == [], bad


# ---- [ask] section ---------------------------------------------------------------------------------------------------
def test_ask_section_ignored_keys_warn_and_a_stored_key_is_an_error(tmp_path):
    put(tmp_path, ".files-to-prompt", 'version = 1\n[ask]\nprovider = "openai"\nmodel = "m"\nbase_url = "https://x.example/v1"\napi_key_env = "FOO"\n')
    r, fs = findings(tmp_path)
    ignored = [f for f in fs if f["code"] == "C_ASK_IGNORED_KEY"]
    assert r.exit_code == 0 and len(ignored) == 2 and all(f["level"] == "warn" for f in ignored)
    put(tmp_path, ".files-to-prompt", 'version = 1\n[ask]\nprovider = "openai"\napi_key = "sk-abc123def456ghi789"\n')
    r, fs = findings(tmp_path)
    assert r.exit_code == 2 and "C_ASK_KEY_IN_CONFIG" in codes(fs, "error") and "sk-abc123def456ghi789" not in r.stdout


def test_ask_section_type_errors_are_schema_errors(tmp_path):
    put(tmp_path, ".files-to-prompt", 'version = 1\n[ask]\nprovider = "gemini"\n')
    r, fs = findings(tmp_path)
    assert r.exit_code == 2 and "C_CONFIG_SCHEMA" in codes(fs, "error")


def test_check_never_loads_the_model_client(tmp_path):
    import subprocess as sp
    import sys as _s
    put(tmp_path, ".files-to-prompt", 'version = 1\n[ask]\nprovider = "openai"\nmodel = "m"\n')
    code = "import sys; from fileflow.check import run_checks; run_checks(%r); assert 'fileflow.ask' not in sys.modules; print('ok')" % str(tmp_path)
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = sp.run([_s.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr


# ---- a shared config that git ignores is shared with nobody (found in this repo: FN-012) -----------------------------
def test_a_gitignored_config_is_flagged_because_it_is_never_shared_or_versioned(tmp_path):
    put(tmp_path, ".gitignore", "dist/\n.files-to-prompt\n")
    put(tmp_path, ".files-to-prompt", 'version = 1\n[presets.p]\npaths = ["."]\n')
    r, fs = findings(tmp_path)
    f = next(f for f in fs if f["code"] == "C_CONFIG_IGNORED")
    assert r.exit_code == 0 and f["level"] == "warn" and "never shared" in f["message"] and ".gitignore" in f["hint"]
    assert check(tmp_path, "--strict").exit_code == 2


def test_a_tracked_config_is_not_flagged(tmp_path):
    put(tmp_path, ".gitignore", "dist/\n")
    put(tmp_path, ".files-to-prompt", 'version = 1\n[presets.p]\npaths = ["."]\n')
    assert "C_CONFIG_IGNORED" not in codes(findings(tmp_path)[1])


def test_this_repository_tracks_its_own_config():
    """The repo's presets and [harness] limits live in .files-to-prompt: it must exist and not be ignored."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(root, ".git")):
        pytest.skip("not a git checkout")
    assert os.path.isfile(os.path.join(root, ".files-to-prompt")), "the repo's own .files-to-prompt is missing"
    import subprocess as sp
    ignored = sp.run(["git", "-C", root, "check-ignore", "-q", ".files-to-prompt"]).returncode == 0
    assert not ignored, ".files-to-prompt is git-ignored: it would never be committed or shared"
