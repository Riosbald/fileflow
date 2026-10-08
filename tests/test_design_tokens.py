"""Static guards for the web client's design system (no browser needed).

The opt-in browser audit (tests/browser/audit.mjs) proves behaviour; these
tests make the cheap, deterministic rules part of every `pytest` run:

* every text/UI colour pair keeps its WCAG contrast ratio in both themes;
* no hardcoded colours or magic z-index values outside the token block;
* the page never references a third-party origin (fonts are self-hosted);
* the structural basics (lang, skip link, main landmark, labelled dialog).
"""
import math
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "fileflow" / "web"
CSS = (WEB / "css" / "styles.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


# ---- colour maths (OKLCH -> linear sRGB -> WCAG relative luminance) --------
def _oklch(value):
    m = re.match(r"oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:\s*/\s*([\d.]+))?\s*\)", value.strip())
    assert m, value
    L, pct, C, h, a = m.groups()
    L = float(L) / (100 if pct else 1)
    C, h, a = float(C), math.radians(float(h)), float(a) if a else 1.0
    A, B = C * math.cos(h), C * math.sin(h)
    l_, m_, s_ = L + 0.3963377774 * A + 0.2158037573 * B, L - 0.1055613458 * A - 0.0638541728 * B, L - 0.0894841775 * A - 1.2914855480 * B
    l, m_, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (
        4.0767416621 * l - 3.3077115913 * m_ + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m_ - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m_ + 1.7076147010 * s,
    )
    return [min(1.0, max(0.0, c)) for c in rgb], a


def _lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _over(fg, alpha, bg):
    return [fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3)]


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _theme(name):
    block = re.search(r'html\[data-theme="%s"\]\s*\{(.*?)\n\}' % name, CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(oklch\([^;]*\));", block))


def _contrast(tokens, fg, bg):
    page, _ = _oklch(tokens["bg"])
    bcol, ba = _oklch(tokens[bg])
    bcol = _over(bcol, ba, page)
    fcol, fa = _oklch(tokens[fg])
    return _ratio(_over(fcol, fa, bcol), bcol)


# (foreground, background, minimum ratio, why)
TEXT = [
    ("text", "bg", 4.5), ("text-2", "bg", 4.5), ("text-3", "bg", 4.5),
    ("text", "panel", 4.5), ("text-2", "panel", 4.5), ("text-3", "panel", 4.5),
    ("text-3", "panel-2", 4.5), ("text-2", "panel-2", 4.5),
    ("text", "code-bg", 4.5), ("text-2", "code-bg", 4.5), ("text-3", "code-bg", 4.5),
    ("primary", "bg", 4.5), ("primary", "panel", 4.5), ("accent", "panel", 4.5),
    ("danger", "panel", 4.5), ("success", "panel", 4.5),
    ("warning", "panel", 4.5), ("info", "panel", 4.5), ("warning", "bg", 4.5),
    ("on-primary", "primary", 4.5),
    # non-text (WCAG 1.4.11): focus ring must stand out from every surface it sits on
    ("focus", "bg", 3.0), ("focus", "panel", 3.0), ("focus", "panel-2", 3.0),
]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fg,bg,minimum", TEXT)
def test_contrast_pairs(theme, fg, bg, minimum):
    tokens = _theme(theme)
    got = _contrast(tokens, fg, bg)
    assert got >= minimum, f"{theme}: --{fg} on --{bg} is {got:.2f}:1, needs {minimum}:1"


def test_both_themes_define_the_same_tokens():
    dark, light = set(_theme("dark")), set(_theme("light"))
    assert dark == light, f"theme token mismatch: {sorted(dark ^ light)}"


def test_contrast_helper_is_not_vacuous():
    """Black on white must be 21:1 and a mid grey on grey must fail: otherwise the
    maths above could be passing everything."""
    white, black = [1, 1, 1], [0, 0, 0]
    assert round(_ratio(white, black), 1) == 21.0
    assert _ratio(_oklch("oklch(0.6 0 0)")[0], _oklch("oklch(0.62 0 0)")[0]) < 1.2


# ---- token discipline -------------------------------------------------------
def _outside_token_blocks(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r":root\s*\{.*?\n\}", "", css, flags=re.S)
    css = re.sub(r'html\[data-theme="\w+"\]\s*\{.*?\n\}', "", css, flags=re.S)
    return css


def test_no_hex_colours_outside_the_token_blocks():
    rest = _outside_token_blocks(CSS)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b(?![\w-])", re.sub(r"url\([^)]*\)", "", rest))


def test_no_magic_z_index():
    rest = _outside_token_blocks(CSS)
    bad = re.findall(r"z-index:\s*(-?\d+)\s*;", rest)
    # -1 (decorative background layer) and 0/1 (local stacking inside a component) are fine
    assert [z for z in bad if int(z) > 1] == [], f"use a --z-* token instead of {bad}"


def test_font_sizes_are_relative_units():
    """px font sizes ignore the user's text-size setting (WCAG 1.4.4)."""
    assert not re.findall(r"font-size:\s*[\d.]+px", CSS)


def test_reduced_motion_and_forced_colors_are_handled():
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "@media (forced-colors: active)" in CSS


def test_global_focus_ring_exists_and_is_not_suppressed_globally():
    assert re.search(r":focus-visible\s*\{[^}]*outline:\s*var\(--focus-ring\)", CSS)
    assert not re.search(r"(^|\n)\s*(\*|button|a|input)\s*(,\s*[\w.*:#-]+\s*)*\{[^}]*outline:\s*none", CSS)


# ---- privacy + structure ----------------------------------------------------
def test_no_third_party_origins_in_html_or_css():
    for name, text in (("index.html", HTML), ("styles.css", CSS)):
        urls = re.findall(r"""(?:src|href|url\()\s*=?\s*["']?(https?://[^"')\s>]+)""", text)
        # outbound *links* inside prose are not loads; only resource-loading attributes matter
        loads = [u for u in urls if re.search(r"""(?:<link[^>]+href|<script[^>]+src|<img[^>]+src|url\()\s*=?\s*["']?""" + re.escape(u), text)]
        assert not loads, f"{name} loads from a third party: {loads}"


def test_fonts_are_self_hosted_and_licensed():
    for f in ("inter-latin-wght-normal.woff2", "jetbrains-mono-latin-wght-normal.woff2", "OFL-Inter.txt", "OFL-JetBrainsMono.txt"):
        assert (WEB / "fonts" / f).stat().st_size > 1000, f
    for ref in re.findall(r'url\("\.\./fonts/([^"]+)"\)', CSS):
        assert (WEB / "fonts" / ref).exists(), ref


def test_page_structure_basics():
    assert re.search(r'<html[^>]+lang="en"', HTML)
    assert re.search(r'<a class="skip-link" href="#(\w+)"', HTML)
    target = re.search(r'<a class="skip-link" href="#(\w+)"', HTML).group(1)
    assert f'id="{target}"' in HTML
    assert "<main" in HTML and "<header" in HTML and "<footer" in HTML
    assert 'role="dialog"' in HTML and 'aria-modal="true"' in HTML and "aria-labelledby=" in HTML


def test_output_is_not_a_live_region():
    """A live region on the prompt would re-announce the whole text on every edit."""
    pre = re.search(r"<pre[^>]*id=\"output-pre\"[^>]*>", HTML).group(0)
    assert "aria-live" not in pre
    assert re.search(r'id="output-meta"[^>]*role="status"|role="status"[^>]*id="output-meta"', HTML)


def test_form_error_is_associated_with_its_field():
    assert 'aria-describedby="new-path-hint new-path-error"' in HTML
    assert 'id="new-path-error"' in HTML
