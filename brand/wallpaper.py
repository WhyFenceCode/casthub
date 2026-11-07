#!/usr/bin/env python3
"""Build the Cast Hub Plasma wallpaper package: theme background plus the faint 12-column
grid (design pages 6-11), light and dark. Run at install time for the real screen size:

    python3 brand/wallpaper.py --out local/wallpaper-build --size 1920x1080 [--size WxH ...]

The grid matches uikit/GridLines.qml: one line at each column edge, `line` colour at 40 %.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tomllib
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parents[1]
FALLBACK_SIZES = ["1920x1080", "2560x1440", "3840x2160"]


def tokens() -> dict:
    return tomllib.loads((ROOT / "brand" / "tokens.toml").read_text())


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def column_edges(t: dict, width: int, height: int) -> list[int]:
    u = height / t["frame"]["height"]
    n, margin, gutter = t["grid"]["columns"], t["grid"]["margin"] * u, t["grid"]["gutter"] * u
    col = (width - 2 * margin - (n - 1) * gutter) / n
    xs = []
    for i in range(n):
        left = margin + i * (col + gutter)
        xs += [round(left), round(left + col)]
    return xs


def render(path: Path, t: dict, variant: str, width: int, height: int) -> None:
    c = t["color"][variant]
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(*hex_rgb(c["bg"]))
    ctx.paint()
    lw = max(1, round(height / t["frame"]["height"]))
    ctx.set_source_rgba(*hex_rgb(c["line"]), 0.4)
    for x in column_edges(t, width, height):
        ctx.rectangle(x, 0, lw, height)
    ctx.fill()
    surf.write_to_png(str(path))


def build(out: Path, sizes: list[str]) -> None:
    t = tokens()
    if out.exists():
        shutil.rmtree(out)
    for folder, variant in (("images", "light"), ("images_dark", "dark")):
        d = out / "contents" / folder
        d.mkdir(parents=True)
        for s in sizes:
            w, h = (int(v) for v in s.split("x"))
            render(d / f"{w}x{h}.png", t, variant, w, h)
    meta = {
        "KPlugin": {
            "Id": "CastHub",
            "Name": "Cast Hub",
            "Description": "Background and 12-column grid, light and dark",
            "License": "CC-BY-SA-4.0",
            "Authors": [{"Name": "Cast Hub"}],
        }
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=4) + "\n")
    print(f"built {out}: {', '.join(sizes)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--size", action="append", default=[], help="WxH; repeatable")
    a = ap.parse_args()
    sizes = list(dict.fromkeys(a.size + FALLBACK_SIZES))
    build(a.out, sizes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
