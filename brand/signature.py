#!/usr/bin/env python3
"""The "Torsten Young" signature: geometry, and the write-on animation.

Letters are Mrs Saint Delafield glyph outlines, positioned by Pango (so kerning and
joins match normal text rendering) and read with fontTools. The flourish is the
accent swash from design page 1-2 (brand/flourish.json, traced from the mockup).

Animation, as on the brand page:
  * each letter's outline is stroked over 0.55 s; letters start 0.2 s apart
  * 0.4 s after a letter starts, its fill fades in over 0.35 s while the stroke fades out
  * the accent flourish draws last over 0.9 s with round caps

Everything is in design-frame units (1080 x 675, see brand/tokens.toml).

    python3 brand/signature.py --svg brand/signature.svg     # static final artwork
    python3 brand/signature.py --frames DIR --height 1080    # Plymouth frames (install time)
"""

from __future__ import annotations

import argparse
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from fontTools.pens.basePen import BasePen  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402
from gi.repository import Pango, PangoCairo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FONT_FILE = ROOT / "brand" / "fonts" / "MrsSaintDelafield-Regular.ttf"
TEXT = "Torsten Young"

# Placement measured from design page 1 (ink bounding box of the signature).
SIG_BOX = (219.0, 234.0, 875.5, 452.0)   # x0, y0, x1, y1 in frame units

# Brand-page timing (seconds) and strokes (frame units, scaled from the site's SVG units).
T_STROKE, T_STAGGER, T_FILL_DELAY, T_FILL, T_FLOURISH = 0.55, 0.2, 0.4, 0.35, 0.9
LETTER_STROKE = 1.4 * 0.75
FLOURISH_STROKE = 3.0
HOLD = 0.6            # seconds the finished signature is held at the end of the frame set

Point = tuple[float, float]


@dataclass
class Letter:
    contours: list[list[Point]]     # closed polylines, frame units
    length: float


def _flatten_quad(p0: Point, p1: Point, p2: Point, n: int = 8) -> list[Point]:
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(1, n + 1))]


def _flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point, n: int = 10) -> list[Point]:
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


class PolyPen(BasePen):
    """Glyph outline → list of closed polylines (font units)."""

    def __init__(self, glyphset):
        super().__init__(glyphset)
        self.contours: list[list[Point]] = []
        self.cur: list[Point] = []

    def _moveTo(self, pt):  # noqa: N802
        self.cur = [pt]

    def _lineTo(self, pt):  # noqa: N802
        self.cur.append(pt)

    def _qCurveToOne(self, pt1, pt2):  # noqa: N802
        self.cur += _flatten_quad(self.cur[-1], pt1, pt2)

    def _curveToOne(self, pt1, pt2, pt3):  # noqa: N802
        self.cur += _flatten_cubic(self.cur[-1], pt1, pt2, pt3)

    def _closePath(self):  # noqa: N802
        if len(self.cur) > 1:
            self.contours.append(self.cur + [self.cur[0]])
        self.cur = []

    _endPath = _closePath


def _length(poly: list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(poly, poly[1:], strict=False))


def glyph_positions() -> list[tuple[str, float, float]]:
    """(character, x, baseline y) for TEXT, at a 1000-unit em, via Pango layout.

    Per-character positions include the font's GPOS kerning. The font has no GSUB
    substitutions, so each character maps straight to its cmap glyph.
    """
    surf = cairo.ImageSurface(cairo.FORMAT_A8, 10, 10)
    ctx = cairo.Context(surf)
    layout = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription.from_string("Mrs Saint Delafield")
    desc.set_absolute_size(1000 * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(TEXT, -1)
    family = layout.get_context().load_font(desc).describe().get_family()
    if "Saint" not in family:
        raise SystemExit("Mrs Saint Delafield is not installed (fc-list); install brand/fonts first")
    baseline = layout.get_baseline() / Pango.SCALE
    return [(ch, layout.index_to_pos(i).x / Pango.SCALE, baseline) for i, ch in enumerate(TEXT)]


def letters() -> list[Letter]:
    font = TTFont(FONT_FILE)
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    raw: list[list[list[Point]]] = []
    for ch, gx, gy in glyph_positions():
        pen = PolyPen(gs)
        gs[cmap[ord(ch)]].draw(pen)
        if not pen.contours:          # the space
            continue
        k = 1000 / upem
        raw.append([[(gx + px * k, gy - py * k) for px, py in c] for c in pen.contours])
    # Fit the ink box onto SIG_BOX (uniform scale, centred).
    xs = [p[0] for L in raw for c in L for p in c]
    ys = [p[1] for L in raw for c in L for p in c]
    bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
    s = min((SIG_BOX[2] - SIG_BOX[0]) / (bx1 - bx0), (SIG_BOX[3] - SIG_BOX[1]) / (by1 - by0))
    ox = (SIG_BOX[0] + SIG_BOX[2]) / 2 - s * (bx0 + bx1) / 2
    oy = (SIG_BOX[1] + SIG_BOX[3]) / 2 - s * (by0 + by1) / 2
    out = []
    for L in raw:
        cs = [[(ox + s * x, oy + s * y) for x, y in c] for c in L]
        out.append(Letter(cs, sum(_length(c) for c in cs)))
    return out


def flourish() -> list[Point]:
    return [tuple(p) for p in json.loads((ROOT / "brand" / "flourish.json").read_text())["points"]]


def colours() -> dict[str, tuple[float, float, float]]:
    t = tomllib.loads((ROOT / "brand" / "tokens.toml").read_text())

    def rgb(h: str) -> tuple[float, float, float]:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]

    return {"bg": rgb(t["boot"]["bg"]), "ink": rgb(t["color"]["dark"]["fg"]), "accent": rgb(t["color"]["accent"])}


# ---- drawing --------------------------------------------------------------------

def _partial(ctx: cairo.Context, polys: list[list[Point]], frac: float, closed: bool) -> None:
    """Path of the first `frac` of the polylines' total length (like an SVG dash offset)."""
    total = sum(_length(p) for p in polys)
    left = total * max(0.0, min(1.0, frac))
    for poly in polys:
        if left <= 0:
            break
        ctx.move_to(*poly[0])
        for a, b in zip(poly, poly[1:], strict=False):
            d = math.dist(a, b)
            if d >= left:
                t = left / d if d else 0
                ctx.line_to(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                left = 0
                break
            ctx.line_to(*b)
            left -= d
        else:
            if closed:
                ctx.close_path()


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3      # ease-out cubic


def duration(n_letters: int) -> float:
    return (n_letters - 1) * T_STAGGER + T_FILL_DELAY + T_FILL + T_FLOURISH


def draw(ctx: cairo.Context, L: list[Letter], fl: list[Point], t: float, col: dict) -> None:
    """Draw the signature at time t (seconds) in frame units."""
    for i, letter in enumerate(L):
        lt = t - i * T_STAGGER
        if lt <= 0:
            continue
        fill_a = _ease((lt - T_FILL_DELAY) / T_FILL)
        stroke_a = 1 - fill_a
        if fill_a > 0:
            for c in letter.contours:
                ctx.move_to(*c[0])
                for p in c[1:]:
                    ctx.line_to(*p)
                ctx.close_path()
            ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
            ctx.set_source_rgba(*col["ink"], fill_a)
            ctx.fill()
        if stroke_a > 0:
            _partial(ctx, letter.contours, _ease(lt / T_STROKE), closed=True)
            ctx.set_source_rgba(*col["ink"], stroke_a)
            ctx.set_line_width(LETTER_STROKE)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            ctx.stroke()
    ft = t - ((len(L) - 1) * T_STAGGER + T_FILL_DELAY + T_FILL)
    if ft > 0:
        _partial(ctx, [fl], _ease(ft / T_FLOURISH), closed=False)
        ctx.set_source_rgb(*col["accent"])
        ctx.set_line_width(FLOURISH_STROKE)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.stroke()


def bbox(L: list[Letter], fl: list[Point], pad: float = 6) -> tuple[float, float, float, float]:
    pts = [p for le in L for c in le.contours for p in c] + fl
    return (min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad,
            max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad)


def write_svg(path: Path) -> None:
    L, fl, col = letters(), flourish(), colours()
    x0, y0, x1, y1 = bbox(L, fl)
    surf = cairo.SVGSurface(str(path), x1 - x0, y1 - y0)
    ctx = cairo.Context(surf)
    ctx.translate(-x0, -y0)
    draw(ctx, L, fl, duration(len(L)) + 1, col)
    surf.finish()


def write_frames(out: Path, height: int, width: int, fps: int) -> dict:
    """Opaque frames of the signature region at screen scale. Returns placement metadata."""
    L, fl, col = letters(), flourish(), colours()
    u = height / 675
    x0, y0, x1, y1 = bbox(L, fl)
    fw, fh = math.ceil((x1 - x0) * u), math.ceil((y1 - y0) * u)
    n = math.ceil((duration(len(L)) + HOLD) * fps)
    out.mkdir(parents=True, exist_ok=True)
    for f in range(n):
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, fw, fh)
        ctx = cairo.Context(surf)
        ctx.set_source_rgb(*col["bg"])
        ctx.paint()
        ctx.scale(u, u)
        ctx.translate(-x0, -y0)
        draw(ctx, L, fl, f / fps, col)
        surf.write_to_png(str(out / f"sig-{f}.png"))
    # Frame-unit x is relative to the 1080-wide frame; centre that frame on the screen.
    left = (width - 1080 * u) / 2 + x0 * u
    return {"frames": n, "fps": fps, "x": round(left), "y": round(y0 * u), "w": fw, "h": fh}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", type=Path)
    ap.add_argument("--frames", type=Path)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--at", type=float, help="with --png: render a single moment (seconds)")
    ap.add_argument("--png", type=Path)
    a = ap.parse_args()
    if a.svg:
        write_svg(a.svg)
        print(f"wrote {a.svg}")
    if a.frames:
        print(json.dumps(write_frames(a.frames, a.height, a.width, a.fps)))
    if a.png:
        L, fl, col = letters(), flourish(), colours()
        u = a.height / 675
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, round(1080 * u), a.height)
        ctx = cairo.Context(surf)
        ctx.set_source_rgb(*col["bg"])
        ctx.paint()
        ctx.scale(u, u)
        draw(ctx, L, fl, a.at if a.at is not None else 99, col)
        surf.write_to_png(str(a.png))
        print(f"wrote {a.png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
