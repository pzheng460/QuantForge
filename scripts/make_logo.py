#!/usr/bin/env python3
"""Generate the QuantForge pixel-art wordmark (letters only).

Two lines of chunky 7x7 square ("cube") block-capital letters spell
QUANT / FORGE. No box, no background tile — the letters are drawn on a
transparent canvas, flat single accent color.

Design constraints (per user feedback):
- letters must be CUBE-like: each glyph is exactly 7 wide x 7 tall
- no 3D / shadow / bevel / border / background — letters only
- the letterforms must read unambiguously as QuantForge

Exports:
  assets/quantforge-logo.svg  (vector pixel grid — README reference)
  assets/quantforge-logo.png  (nearest-neighbour PNG)
  assets/quantforge-logo-white.png (white lettering variant)

Re-run after editing the design:
  .venv/bin/python scripts/make_logo.py
"""
from __future__ import annotations

from pathlib import Path

# ---- 7x7 square block font, ALL-CAPS (each glyph has a distinct, legible
# silhouette so the word never misreads; '#' = filled) ----
GLYPHS: dict[str, tuple[str, ...]] = {
    "Q": (".#####.", "##...##", "##...##", "##...##", "##..###", ".#####.", ".....##"),
    "U": ("##...##", "##...##", "##...##", "##...##", "##...##", "##...##", ".#####."),
    "A": ("..###..", ".#...#.", "##...##", "##...##", "#######", "##...##", "##...##"),
    "N": ("##...##", "###..##", "####.##", "##.####", "##..###", "##...##", "##...##"),
    "T": ("#######", "#######", "..##...", "..##...", "..##...", "..##...", "..##..."),
    "F": ("######.", "##.....", "##.....", "#####..", "##.....", "##.....", "##....."),
    "O": (".#####.", "##...##", "##...##", "##...##", "##...##", "##...##", ".#####."),
    "R": ("#####..", "##...##", "##...##", "#####..", "##.##..", "##..##.", "##...##"),
    "G": (".#####.", "##...##", "##.....", "##..###", "##...##", "##...##", ".#####."),
    "E": ("#######", "##.....", "##.....", "#####..", "##.....", "##.....", "#######"),
}

GLYPH_W, GLYPH_H = 7, 7
SPACING = 1
EDGE = 2         # transparent padding around the letters
GAP = 2          # empty rows between the two letter lines

LETTER = (251, 191, 36)       # gold
LETTER_WHITE = (230, 240, 252)


def line_pixels(line: str, y0: int, x0: int, out: dict[tuple[int, int], tuple | None]) -> None:
    for i, ch in enumerate(line):
        glyph = GLYPHS[ch]
        for y, row in enumerate(glyph):
            for x, c in enumerate(row):
                if c == "#":
                    out[(x0 + i * (GLYPH_W + SPACING) + x, y0 + y)] = LETTER


def build() -> tuple[dict[tuple[int, int], tuple | None], int, int]:
    line = "QUANT"
    text_w = len(line) * GLYPH_W + (len(line) - 1) * SPACING
    W = text_w + 2 * EDGE
    H = GLYPH_H * 2 + GAP + 2 * EDGE

    out: dict[tuple[int, int], tuple | None] = {}
    # letters only — no border, no background
    line_pixels("QUANT", y0=EDGE, x0=EDGE, out=out)
    line_pixels("FORGE", y0=EDGE + GLYPH_H + GAP, x0=EDGE, out=out)
    return out, W, H


def export_svg(path: Path, tile: dict, w: int, h: int) -> None:
    groups: dict[tuple, list[str]] = {}
    for (x, y), c in tile.items():
        if c is not None:
            groups.setdefault(c, []).append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    body = "\n".join(
        f'<g fill="rgb({r},{g},{b})">{pixels}</g>'
        for (r, g, b), pixels in groups.items()
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w * 6}" height="{h * 6}" shape-rendering="crispEdges">\n'
        f"{body}\n</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def export_png(path: Path, tile: dict, w: int, h: int, scale: int = 512) -> None:
    from PIL import Image

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pix = img.load()
    for (x, y), c in tile.items():
        if c is not None:
            pix[x, y] = (*c, 255)
    factor = max(1, scale // max(w, h))
    img = img.resize((w * factor, h * factor), Image.NEAREST)
    img.save(path)


def main() -> None:
    assets = Path(__file__).resolve().parents[1] / "assets"
    assets.mkdir(exist_ok=True)

    tile, w, h = build()
    export_svg(assets / "quantforge-logo.svg", tile, w, h)
    export_png(assets / "quantforge-logo.png", tile, w, h)
    print(f"wrote letters-only wordmark {w}x{h} px (transparent)")

    white = {k: (LETTER_WHITE if v == LETTER else v) for k, v in tile.items()}
    export_png(assets / "quantforge-logo-white.png", white, w, h)
    print("wrote white variant")


if __name__ == "__main__":
    main()
