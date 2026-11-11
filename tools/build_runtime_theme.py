#!/usr/bin/env python3
"""Build the Transition Network runtime theme for Open edX Verawood+ MFEs.

Since tutor-indigo 22.1, MFEs no longer install @edx/brand at build time.
Indigo loads compiled CSS at runtime from INDIGO_BRAND_CSS_BASE_URL
(core.min.css, light.min.css, dark.min.css).

This script starts from the Indigo brand release that tutor-indigo pins
(so the Indigo header/footer styling keeps working) and appends a
Transition Network layer:

* light.min.css: TN palette as Paragon CSS custom properties. Shade scales
  are generated with the same mix ratios Indigo's own scale uses.
* core.min.css: the overrides from paragon/_overrides.scss.
* dark.min.css: unchanged (the dark toggle is disabled on TN).

Usage:  python3 tools/build_runtime_theme.py
Output: dist/{core,light,dark}.min.css
"""

import pathlib
import re
import urllib.request

INDIGO_BRAND_VERSION = "indigo-3.1.1"  # BRAND_VERSION in tutor-indigo 22.1.0
INDIGO_DIST = f"https://cdn.jsdelivr.net/gh/edly-io/brand-openedx@{INDIGO_BRAND_VERSION}/dist"

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Transition Network palette (see paragon/_variables.scss)
TN = {
    "primary": "#003D5B",    # night sky blue
    "brand": "#003D5B",      # btn-brand is navy on TN
    "secondary": "#D1495B",  # sunset coral
    "accent": "#00798C",     # teal
    "success": "#2E7D32",
    "warning": "#EDAE49",
    "danger": "#C62828",
    "info": "#00798C",
    "body": "#4D4D4D",
}
LEVELS = ["100", "200", "300", "400", "600", "700", "800", "900"]


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{INDIGO_DIST}/{name}") as resp:
        return resp.read().decode("utf-8")


def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")[:6]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def to_hex(c: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02X}" for v in c)


def mix(color: tuple, target: tuple, weight: float) -> tuple:
    """Return color moved `weight` (0..1) of the way towards target."""
    return tuple(c + (t - c) * weight for c, t in zip(color, target))


def scale_weights(light_css: str, name: str) -> dict[str, float]:
    """Derive Indigo's tint/shade weights for one colour scale."""
    def var(v):
        m = re.search(rf"--pgn-color-{name}-{v}:(#[0-9A-Fa-f]{{6,8}})", light_css)
        return rgb(m.group(1)) if m else None

    base = var("base")
    weights = {}
    for level in LEVELS:
        c = var(level)
        if c is None or base is None:
            continue
        target = (255, 255, 255) if int(level) < 500 else (0, 0, 0)
        ws = [(cv - bv) / (tv - bv) for cv, bv, tv in zip(c, base, target) if tv != bv]
        weights[level] = sum(ws) / len(ws) if ws else 0.0
    return weights


def scale_vars(name: str, base_hex: str, weights: dict[str, float]) -> list[str]:
    base = rgb(base_hex)
    out = [f"--pgn-color-{name}-base:{base_hex}"]
    for level, w in weights.items():
        target = (255, 255, 255) if int(level) < 500 else (0, 0, 0)
        out.append(f"--pgn-color-{name}-{level}:{to_hex(mix(base, target, w))}")
    return out


def main() -> None:
    DIST.mkdir(exist_ok=True)
    core, light, dark = fetch("core.min.css"), fetch("light.min.css"), fetch("dark.min.css")

    weights = scale_weights(light, "primary")
    tn_vars = []
    for name in ["primary", "brand", "secondary", "success", "warning", "danger", "info"]:
        tn_vars += scale_vars(name, TN[name], weights)
    tn_vars += [
        f"--pgn-color-accent-a:{TN['accent']}",
        # White backgrounds throughout (the TN cream #FBF8EF was dropped
        # 2026-11-11 as too overpowering); Indigo's neutral light scale is kept.
        f"--pgn-color-body-base:{TN['body']}",
        f"--pgn-color-link-base:{TN['primary']}",
        f"--pgn-color-link-hover:{TN['accent']}",
        "--pgn-size-border-radius-base:8px",
        "--pgn-size-border-radius-sm:6px",
        "--pgn-size-border-radius-lg:12px",
    ]
    light_layer = "\n/* Transition Network palette */\n:root{" + ";".join(tn_vars) + "}\n"

    # Indigo restyles the authn MFE (pale blue background, blurred blobs,
    # hidden navy panel). TN used the stock authn layout with its own
    # palette, so drop those rules and let _overrides.scss style it.
    core, removed = strip_rules(core, "#root .layout")
    print(f"removed {removed} Indigo authn layout rules")

    overrides = (ROOT / "paragon" / "_overrides.scss").read_text(encoding="utf-8")
    core_layer = "\n/* Transition Network overrides (paragon/_overrides.scss) */\n" + scss_to_css(overrides)

    (DIST / "light.min.css").write_text(light + light_layer, encoding="utf-8")
    (DIST / "core.min.css").write_text(core + core_layer, encoding="utf-8")
    (DIST / "dark.min.css").write_text(dark, encoding="utf-8")
    print("primary scale weights:", {k: round(v, 3) for k, v in weights.items()})
    for f in sorted(DIST.glob("*.css")):
        print(f"{f.name}: {f.stat().st_size} bytes")


def strip_rules(css: str, needle: str) -> tuple[str, int]:
    """Remove rules whose selector contains `needle`, including inside @media.

    Handles the flat minified output of Paragon/Indigo builds: top-level
    rules and one level of @-block nesting. Empty @-blocks are dropped.
    """
    out, removed, i, n = [], 0, 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            out.append(css[i:])
            break
        prelude = css[i:brace]
        if prelude.lstrip().startswith("@") and not prelude.lstrip().startswith("@import"):
            depth, j = 1, brace + 1
            while depth and j < n:
                depth += {"{": 1, "}": -1}.get(css[j], 0)
                j += 1
            inner, inner_removed = strip_rules(css[brace + 1 : j - 1], needle)
            removed += inner_removed
            if inner.strip():
                out.append(f"{prelude}{{{inner}}}")
            i = j
            continue
        end = css.find("}", brace)
        selector = prelude.split(";")[-1]
        if needle in selector:
            out.append(prelude[: len(prelude) - len(selector)])
            removed += 1
        else:
            out.append(css[i : end + 1])
        i = end + 1
    return "".join(out), removed


def scss_to_css(scss: str) -> str:
    """Flatten the simple one-level nesting used in _overrides.scss."""
    scss = re.sub(r"//[^\n]*", "", scss)
    out = []
    for m in re.finditer(r"([^{}]+)\{((?:[^{}]|\{[^{}]*\})*)\}", scss):
        selector, body = m.group(1).strip(), m.group(2)
        nested = list(re.finditer(r"([^{};]+)\{([^{}]*)\}", body))
        flat = re.sub(r"[^{};]+\{[^{}]*\}", "", body).strip()
        if flat:
            out.append(f"{selector}{{{flat}}}")
        for n in nested:
            parts = [p.strip().replace("&", selector) for p in n.group(1).split(",")]
            out.append(f"{','.join(parts)}{{{n.group(2).strip()}}}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
