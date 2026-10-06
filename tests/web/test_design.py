"""The design contract of A.6: tokens in one file, contrast, no decoration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "web" / "static" / "css"
TEMPLATES = Path(__file__).resolve().parents[2] / "web" / "templates"
TOKENS = (STATIC / "tokens.css").read_text()


def token(name: str) -> str:
    match = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})\s*;", TOKENS)
    assert match, f"token {name} is not a six-digit hex colour"
    return match.group(1)


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(token(a)), luminance(token(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Each text colour on each background it is drawn on: WCAG AA for text, 4.5:1.
PAIRS = [
    ("color-text", "color-bg"), ("color-text", "color-surface"), ("color-text", "color-border"),
    ("color-text", "color-canvas"), ("color-text", "color-selected"), ("color-text", "color-attention-tint"),
    ("color-text-secondary", "color-bg"), ("color-text-secondary", "color-canvas"),
    ("color-text-secondary", "color-surface"), ("color-text-secondary", "color-selected"),
    ("color-text-muted", "color-bg"), ("color-text-muted", "color-surface"), ("color-text-muted", "color-canvas"),
    ("color-text-muted", "color-selected"), ("color-text-muted", "color-pill-quiet"),
    ("color-text-muted", "color-border"), ("color-text-muted", "color-attention"),
    ("color-text-muted", "color-attention-tint"),
    ("color-accent", "color-bg"), ("color-accent", "color-surface"), ("color-accent", "color-canvas"),
    ("color-accent", "color-selected"), ("color-accent", "color-attention"),
    ("color-accent-strong", "color-selected"), ("color-accent-strong", "color-bg"),
    ("color-accent-strong", "color-attention"),
    ("color-on-accent", "color-accent"), ("color-on-accent", "color-accent-strong"),
    ("color-on-attention", "color-attention"), ("color-on-attention-soft", "color-attention"),
    ("color-danger", "color-bg"), ("color-danger", "color-surface"), ("color-danger", "color-attention"),
    ("color-on-danger", "color-danger"),
    ("color-bg", "color-text"),   # the tooltip
]


@pytest.mark.parametrize("foreground, background", PAIRS)
def test_every_text_pair_meets_wcag_aa(foreground, background):
    assert contrast(foreground, background) >= 4.5, (foreground, background, contrast(foreground, background))


def test_the_border_that_marks_a_control_meets_the_non_text_contrast_of_3_to_1():
    assert contrast("color-border-strong", "color-bg") >= 3


def stylesheets():
    return [p for p in sorted(STATIC.glob("*.css")) if p.name != "tokens.css"]


def test_no_stylesheet_but_the_tokens_declares_a_colour():
    for path in stylesheets():
        text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", text), path.name


def test_no_gradient_no_decorative_shadow_no_texture():
    for path in stylesheets():
        text = path.read_text()
        assert "gradient" not in text and "box-shadow" not in text and "text-shadow" not in text, path.name
        assert "url(" not in text, path.name


def test_the_templates_hold_no_style_of_their_own():
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text()
        assert "<style" not in text and 'style="' not in text, path.name


def test_geist_for_the_interface_and_geist_mono_for_code_and_no_serif():
    assert re.search(r'--font-sans:\s*"Geist"', TOKENS) and re.search(r'--font-mono:\s*"Geist Mono"', TOKENS)
    assert "--font-serif" not in TOKENS
    for path in stylesheets():
        families = re.findall(r"font-family:\s*([^;}]+)", path.read_text())
        assert all(f.strip().startswith("var(--font-") for f in families), path.name
        # The shorthand `font:` names a family only through a token, too.
        assert not re.search(r"font:[^;}]*[\"']", path.read_text()), path.name


def test_every_control_keeps_a_visible_keyboard_focus():
    base = (STATIC / "base.css").read_text()
    assert ":focus-visible" in base and "outline: 2px solid var(--color-accent)" in base
    for path in stylesheets():
        assert not re.search(r"outline:\s*(none|0)\b", path.read_text()), path.name


def test_the_nodes_stand_out_from_the_panel_by_their_outline_and_the_highlighted_edge_by_its_colour():
    # The pastel fills are light: the thin outline every node is drawn with carries the 3:1.
    assert contrast("color-node-outline", "color-bg") >= 3
    assert contrast("color-node-concept", "color-bg") >= 3
    assert contrast("color-edge-highlight", "color-bg") >= 3
    # The kinds differ from one another and from the dimmed nodes.
    fills = {token(f"color-node-{kind}") for kind in ("concept", "summary", "source", "dim")}
    assert len(fills) == 4
