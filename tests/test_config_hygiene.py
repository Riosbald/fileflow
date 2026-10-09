"""Novice-mistake hygiene found by the stranger walkthrough (FN-015).

* a typo'd setting (`formt = "xml"`) silently did nothing, and `fileflow check` said "ok";
* an empty result (wrong folder, everything ignored) produced a blank prompt and no message;
* `fileflow fetch notaurl` said "got 'none'".
Rule for all three: stay quiet when nothing is wrong, speak specifically when something almost
certainly is, and never invent a false alarm (the real configs below must stay clean).
"""
import json
import os

import pytest
from click.testing import CliRunner

from fileflow import presets, receipt as R, scaffold
from fileflow.cli import cli
from fileflow.server import config as webconfig

try:
    import tomllib as toml
except ImportError:  # Python < 3.11
    import tomli as toml


def _runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def run(args, cwd):
    old = os.getcwd()
    os.chdir(str(cwd))
    try:
        return _runner().invoke(cli, [str(a) for a in args])
    finally:
        os.chdir(old)


def write_cfg(root, text):
    (root / ".files-to-prompt").write_text(text, encoding="utf-8")


# ---- unknown keys ------------------------------------------------------------------------------
def test_a_near_miss_is_reported_with_the_real_name():
    cfg = toml.loads('version = 1\n[output]\nformt = "xml"\nmax_token = 5\n[presets.p]\nmax_tokenz = 1\n')
    got = {(u["where"], u["key"]): u["suggestion"] for u in presets.unknown_keys(cfg)}
    assert got == {("[output]", "formt"): "format", ("[output]", "max_token"): "max_tokens",
                   ("[presets.p]", "max_tokenz"): "max_tokens"}


def test_a_top_level_table_typo_is_caught():
    got = presets.unknown_keys(toml.loads("version = 1\n[outptu]\nformat = 'xml'\n"))
    assert got == [{"where": "top level", "key": "outptu", "suggestion": "output"}]


def test_a_genuinely_new_key_is_unknown_but_has_no_suggestion():
    got = presets.unknown_keys(toml.loads("version = 1\n[output]\nholographic = true\n"))
    assert got == [{"where": "[output]", "key": "holographic", "suggestion": None}]


def test_every_real_config_in_this_project_is_clean():
    """A false alarm teaches people to ignore the real one."""
    texts = [open(os.path.join(os.path.dirname(__file__), "..", ".files-to-prompt"), encoding="utf-8").read()]
    texts += [getattr(scaffold, n) for n in dir(scaffold) if n.endswith("_CONFIG") and isinstance(getattr(scaffold, n), str)]
    assert len(texts) >= 4
    for text in texts:
        assert presets.unknown_keys(toml.loads(text)) == [], text[:60]


def test_what_the_web_ui_writes_is_clean(tmp_path):
    webconfig.write_config(str(tmp_path), {
        "output": {"format": "xml", "separators": True, "line_numbers": False, "max_tokens": 8000},
        "include": {"patterns": ["*.py"]}, "exclude": {"patterns": ["tests"]},
        "ignore": {"gitignore": True, "hidden": False},
    })
    cfg = toml.loads((tmp_path / ".files-to-prompt").read_text(encoding="utf-8"))
    assert presets.unknown_keys(cfg) == []


def test_check_lists_every_unknown_key_and_strict_fails_on_it(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, 'version = 1\n[output]\nformt = "xml"\nholographic = true\n')
    r = run(["check", "--json"], tmp_path)
    findings = json.loads(r.stdout)["findings"]
    codes = [(f["code"], f["message"]) for f in findings if f["code"] == "C_CONFIG_UNKNOWN_KEY"]
    assert len(codes) == 2 and any("did you mean 'format'" in m for _, m in codes)
    assert run(["check", "--strict"], tmp_path).exit_code == 2
    assert run(["check"], tmp_path).exit_code == 0          # a warning, not an error


def test_a_normal_run_warns_only_for_near_misses_and_still_runs(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, 'version = 1\n[output]\nformt = "xml"\nholographic = true\n')
    r = run(["."], tmp_path)
    assert r.exit_code == 0 and "a.py" in r.stdout
    assert "'formt' is not a setting fileflow reads (did you mean 'format'?)" in r.stderr
    assert "holographic" not in r.stderr          # a new key from a newer fileflow stays quiet


def test_a_clean_config_is_silent(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, 'version = 1\n[output]\nformat = "xml"\n')
    assert "not a setting" not in run(["."], tmp_path).stderr


# ---- empty results -----------------------------------------------------------------------------
def test_an_empty_folder_says_so(tmp_path):
    r = run(["."], tmp_path)
    assert r.exit_code == 0 and r.stdout.strip() == ""
    assert "nothing to send: no files were found here. Are you in the right folder?" in r.stderr


def test_everything_hidden_names_the_flag_that_would_help(tmp_path):
    (tmp_path / ".env.example").write_text("A=1\n")
    err = run(["."], tmp_path).stderr
    assert "every file was left out (1 hidden)" in err and "--include-hidden" in err


def test_everything_gitignored_and_hidden_names_both_flags(tmp_path):
    (tmp_path / ".gitignore").write_text("*.py\n")
    (tmp_path / "a.py").write_text("x = 1\n")
    err = run(["."], tmp_path).stderr
    assert "1 ignored by .gitignore" in err and "1 hidden" in err
    assert "--include-hidden" in err and "--ignore-gitignore" in err
    # the advice works: lifting the ignore rule brings a.py back
    assert "a.py" in run([".", "--ignore-gitignore"], tmp_path).stdout


def test_the_advice_for_an_empty_result_works(tmp_path):
    (tmp_path / ".env.example").write_text("A=1\n")
    assert ".env.example" in run([".", "--include-hidden"], tmp_path).stdout


def test_no_notice_when_something_is_sent(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert "nothing to send" not in run(["."], tmp_path).stderr


def test_notice_wording_for_diff_mode():
    rec = {"totals": {"files_sent": 0}, "excluded": []}
    assert "no changed files" in R.empty_notice(rec, change_mode="diff")
    assert R.empty_notice({"totals": {"files_sent": 2}, "excluded": []}) is None


# ---- fetch wording -----------------------------------------------------------------------------
def test_fetch_of_a_bare_name_says_what_to_type():
    r = _runner().invoke(cli, ["fetch", "example.com/page"])
    assert r.exit_code == 4
    text = r.stderr
    assert "only http:// and https:// URLs are fetched" in text and "not a full URL" in text
    assert "for example: https://example.com/page" in text and "got 'none'" not in text


def test_fetch_of_file_urls_is_still_refused_plainly():
    r = _runner().invoke(cli, ["fetch", "file:///etc/passwd"])
    assert r.exit_code == 4 and "got 'file'" in r.stderr


# ---- a broken config must not silently drop safety settings (FN-015, decision D) ---------------
def test_a_broken_config_holding_secrets_block_cannot_leak_a_secret(tmp_path):
    secret = "aws_access_key_id = " + "AKIA" + "IOSFODNN7EXAMPLE\n"
    (tmp_path / "keys.txt").write_text(secret)
    write_cfg(tmp_path, 'version = 1\n[secrets]\nmode = "block"\n[output\n')      # typo breaks the file
    r = run(["."], tmp_path)
    assert r.exit_code == 2
    assert "AKIA" not in r.stdout + r.stderr          # nothing emitted, and the value is never echoed


def test_schema_errors_stop_the_run_too(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, 'version = 1\n[output]\nformat = 7\n[presets.p]\nmax_tokens = "lots"\n')
    r = run(["."], tmp_path)
    assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr and r.stdout.strip() == ""


def test_the_web_server_stays_tolerant_so_the_ui_can_repair_the_file(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fileflow.server.app import build_app

    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, "[output\n")
    with TestClient(build_app(project_root=str(tmp_path))) as c:
        assert c.get("/api/prompt").status_code == 200       # still serves prompts
        assert c.get("/api/config").status_code == 422        # and reports the file as broken


# ---- wrong-typed settings are errors, not silent defaults ---------------------------------------
BAD_SETTINGS = {
    "[output] format = 7": ('[output]\nformat = 7\n', "[output] format must be one of"),
    "[output] format = 'yaml'": ('[output]\nformat = "yaml"\n', "[output] format must be one of"),
    "[output] separators = 'yes'": ('[output]\nseparators = "yes"\n', "[output] separators must be true or false"),
    "[output] line_numbers = 1": ('[output]\nline_numbers = 1\n', "[output] line_numbers must be true or false"),
    "[output] max_tokens = 'lots'": ('[output]\nmax_tokens = "lots"\n', "[output] max_tokens must be a whole number"),
    "[output] max_tokens = -5": ('[output]\nmax_tokens = -5\n', "[output] max_tokens must be a whole number"),
    "[output] max_tokens = true": ('[output]\nmax_tokens = true\n', "[output] max_tokens must be a whole number"),
    "[exclude] patterns = 'tests'": ('[exclude]\npatterns = "tests"\n', "[exclude] patterns must be an array of strings"),  # reader
    "[include] patterns = [1]": ('[include]\npatterns = [1]\n', "[include] patterns must be an array of strings"),      # reader
    "[ignore] hidden = 'no'": ('[ignore]\nhidden = "no"\n', "[ignore] hidden must be true or false"),
    "[ignore] gitignore = 0": ('[ignore]\ngitignore = 0\n', "[ignore] gitignore must be true or false"),
    "[output] is not a table": ('output = 3\n', "[output] must be a table"),
}


@pytest.mark.parametrize("name", sorted(BAD_SETTINGS))
def test_a_wrong_typed_setting_stops_the_run_and_names_it(name, tmp_path):
    body, expected = BAD_SETTINGS[name]
    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, "version = 1\n" + body)
    r = run(["."], tmp_path)
    assert r.exit_code == 2 and "E_CONFIG_SCHEMA" in r.stderr and expected in r.stderr, r.stderr
    assert r.stdout.strip() == ""
    chk = run(["check", "--json"], tmp_path)
    assert chk.exit_code == 2
    assert any(f["code"] == "C_CONFIG_SCHEMA" and expected in f["message"] for f in json.loads(chk.stdout)["findings"])


def test_valid_settings_are_accepted(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    write_cfg(tmp_path, 'version = 1\n[output]\nformat = "json"\nseparators = false\nline_numbers = true\nmax_tokens = 0\n'
                        '[exclude]\npatterns = ["tests"]\n[include]\npatterns = []\n[ignore]\nhidden = false\ngitignore = true\n')
    r = run(["."], tmp_path)
    assert r.exit_code == 0 and "a.py" in r.stdout


def test_check_reports_every_schema_problem_not_just_the_first(tmp_path):
    write_cfg(tmp_path, 'version = 1\n[output]\nformat = 7\n[presets.p]\nmax_tokens = "lots"\n')
    findings = json.loads(run(["check", "--json"], tmp_path).stdout)["findings"]
    msgs = [f["message"] for f in findings if f["code"] == "C_CONFIG_SCHEMA"]
    assert any("[output] format" in m for m in msgs) and any("presets.p.max_tokens" in m for m in msgs)


def test_what_the_web_ui_writes_passes_validation(tmp_path):
    webconfig.write_config(str(tmp_path), {
        "output": {"format": "xml", "separators": True, "line_numbers": False, "max_tokens": 8000},
        "include": {"patterns": ["*.py"]}, "exclude": {"patterns": ["tests"]},
        "ignore": {"gitignore": True, "hidden": False},
    })
    presets.validate_settings(toml.loads((tmp_path / ".files-to-prompt").read_text(encoding="utf-8")))
    (tmp_path / "a.py").write_text("x = 1\n")
    assert run(["."], tmp_path).exit_code == 0


def test_check_reports_a_bad_secrets_mode_and_a_bad_setting_together(tmp_path):
    write_cfg(tmp_path, 'version = 1\n[secrets]\nmode = "maybe"\n[output]\nformat = 7\n')
    msgs = [f["message"] for f in json.loads(run(["check", "--json"], tmp_path).stdout)["findings"] if f["code"] == "C_CONFIG_SCHEMA"]
    assert any("[secrets] mode" in m for m in msgs) and any("[output] format" in m for m in msgs), msgs
