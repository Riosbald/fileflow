"""The web UI saves only the settings it shows; presets and other keys must survive."""
import pytest

from fileflow.server import config as C

PRESET_FILE = '''version = 1

[output]
format = "xml"

[secrets]
mode = "block"

[presets.hero]
description = "Hero only"
paths = ["components/hero", "notes/project-notes.txt"]
max_tokens = 12000
instruction = "Improve the hero section. Keep \\"spacing\\" tokens."

[presets.hero.vars]
domain = "web"
years = 3

[presets.learn.prompt]
role = "Act as a mentor."
task = "Find the gap in {{domain}}."
'''


def write(root, text):
    (root / ".files-to-prompt").write_text(text)


def test_presets_parse_as_nested_tables(tmp_path):
    write(tmp_path, PRESET_FILE)
    cfg = C.read_config(str(tmp_path), strict=True)
    assert cfg["version"] == 1
    assert cfg["presets"]["hero"]["paths"] == ["components/hero", "notes/project-notes.txt"]
    assert cfg["presets"]["hero"]["vars"] == {"domain": "web", "years": 3}
    assert cfg["presets"]["learn"]["prompt"]["task"] == "Find the gap in {{domain}}."
    assert cfg["secrets"]["mode"] == "block"


def test_ui_save_keeps_presets_version_secrets(tmp_path):
    write(tmp_path, PRESET_FILE)
    before = C.read_config(str(tmp_path), strict=True)
    # exactly what the web client sends: only the sections it knows about
    C.write_config(str(tmp_path), {
        "output": {"format": "json", "separators": True, "line_numbers": True, "max_tokens": 500},
        "include": {"patterns": []}, "exclude": {"patterns": ["dist"]},
        "ignore": {"gitignore": True, "hidden": False},
    })
    after = C.read_config(str(tmp_path), strict=True)
    assert after["output"]["format"] == "json" and after["exclude"]["patterns"] == ["dist"]
    for key in ("presets", "version", "secrets"):
        assert after[key] == before[key], key + " was lost by a UI save"


def test_payload_can_explicitly_replace_an_extra_section(tmp_path):
    write(tmp_path, PRESET_FILE)
    C.write_config(str(tmp_path), {"secrets": {"mode": "warn"}})
    assert C.read_config(str(tmp_path), strict=True)["secrets"]["mode"] == "warn"


def test_written_file_is_valid_toml_with_scalars_before_tables(tmp_path):
    write(tmp_path, PRESET_FILE)
    text = C.write_config(str(tmp_path), {"output": {"format": "xml"}})
    assert text.index("version = 1") < text.index("[")
    import tomllib  # py3.11 in CI image; the fallback parser is covered below
    tomllib.loads(text)


def test_fallback_parser_handles_nested_tables(tmp_path):
    write(tmp_path, PRESET_FILE.replace("years = 3", "years = 3"))
    data = C._naive_toml(str(tmp_path / ".files-to-prompt"))
    assert data["presets"]["hero"]["vars"]["domain"] == "web"
    assert data["presets"]["learn"]["prompt"]["role"] == "Act as a mentor."


@pytest.mark.parametrize("snippet", ['x = """multi\nline"""', 'x = { a = 1 }'])
def test_fallback_parser_refuses_what_it_cannot_read(tmp_path, snippet):
    write(tmp_path, snippet + "\n")
    with pytest.raises(ValueError):
        C._naive_toml(str(tmp_path / ".files-to-prompt"))
