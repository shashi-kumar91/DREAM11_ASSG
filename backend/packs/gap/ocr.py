"""Scoreboard frame reader: reads the number out of the pixels.

Why this is not a lookup table. `frames.json` already knows each frame's true score, so the
honest version of "inspect the frame" could have returned that string and the demo would
still pass - while proving nothing. The injected-fault frame is the test that separates the
two: it is rendered with a score that is *not* in the feed, and a real reader must report
143/4 there so the verifier can reject the clip.

The frames are synthetic and rendered with a font and geometry this module owns
(`BANDS`, `render_value`), so a template reader over the pixel bitmap is both genuinely
read-from-image and fully offline. When a band cannot be matched confidently the reader
returns nothing rather than a guess, and the session treats an unreadable frame as a failed
check - which is the safe direction.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ALPHABET = "0123456789/."
INK_THRESHOLD = 90
GAP_COLUMNS = 4
MIN_MATCH = 0.62

# band name -> (x0, x1, y0, y1, font size) : the only contract between writer and reader.
# The y ranges deliberately exclude the caption rows, because stray ink at a band edge is
# read as a glyph and there is no honest way to explain it away.
BANDS = {
    "score": (150, 640, 56, 130, 60),
    "over": (150, 360, 138, 196, 40),
    "rrr": (520, 740, 138, 196, 40),
}
CAPTION_FONT = 26
SPACING = 10
MIN_CLUSTER_PX = 4


# The writer (`fixtures/build_fixtures.py`) and this reader must use the *same* font, or the
# template match fails and every frame reads as unreadable. The font therefore ships with the
# fixtures instead of being resolved from the host OS: a bare `arialbd.ttf` lookup worked on the
# Windows machine this was first built on and silently degraded to Pillow's default bitmap font
# on Linux, which is exactly the kind of host-dependent claim this repo is not allowed to make.
FONT_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "fonts" / "DejaVuSans-Bold.ttf"


def font_path() -> Optional[str]:
    """The single font both writer and reader resolve to, or None if it is missing."""
    return str(FONT_PATH) if FONT_PATH.exists() else None


def _font(size: int):
    path = font_path()
    if path:
        return ImageFont.truetype(path, size)
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_value(draw: ImageDraw.ImageDraw, band: str, text: str, fill=(240, 240, 240)) -> None:
    """Draw a band's value with fixed inter-glyph spacing so the reader can segment it."""
    x0, _x1, y_top, _y1, size = BANDS[band]
    font = _font(size)
    x = x0
    for ch in text:
        draw.text((x, y_top + 2), ch, fill=fill, font=font)
        x += int(draw.textlength(ch, font=font)) + SPACING


@lru_cache(maxsize=8)
def _glyph_templates(size: int) -> dict[str, np.ndarray]:
    """Tight ink crops, rendered at the band's own size.

    No scaling happens during matching, because the reader and the writer use the same font
    at the same pixel size - so an aligned '8' and '6' stay distinguishable instead of
    collapsing into each other the way aspect-normalised thumbnails do.
    """
    font = _font(size)
    out: dict[str, np.ndarray] = {}
    for ch in ALPHABET:
        img = Image.new("L", (160, 160), 0)
        ImageDraw.Draw(img).text((60, 50), ch, fill=255, font=font)
        arr = np.asarray(img, dtype=np.uint8)
        rows, cols = np.where(arr > INK_THRESHOLD)
        if len(rows) == 0:
            continue
        out[ch] = arr[rows.min():rows.max() + 1, cols.min():cols.max() + 1] > INK_THRESHOLD
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    ca = np.zeros((h, w), dtype=bool)
    cb = np.zeros((h, w), dtype=bool)
    ca[:a.shape[0], :a.shape[1]] = a
    cb[:b.shape[0], :b.shape[1]] = b
    union = np.logical_or(ca, cb).sum()
    return float(np.logical_and(ca, cb).sum() / union) if union else 0.0


def _clusters(bw: np.ndarray) -> list[np.ndarray]:
    """Split a band bitmap into per-glyph masks using empty-column runs."""
    if bw.size == 0 or not bw.any():
        return []
    ink_cols = np.where(bw.any(axis=0))[0]
    lo, hi = int(ink_cols.min()), int(ink_cols.max())
    runs: list[tuple[int, int]] = []
    start, gap = lo, 0
    for x in range(lo, hi + 1):
        if bw[:, x].any():
            if gap > GAP_COLUMNS:
                runs.append((start, x - gap))
                start = x
            gap = 0
        else:
            gap += 1
    runs.append((start, hi + 1))
    out = []
    for a, b in runs:
        if b - a < 1:
            continue
        sub = bw[:, a:b]
        rows = np.where(sub.any(axis=1))[0]
        if len(rows) == 0:
            continue
        crop = sub[rows.min():rows.max() + 1, :]
        if min(crop.shape) < MIN_CLUSTER_PX:
            continue
        out.append(crop)
    return out


def read_band(bw: np.ndarray, size: int) -> Optional[str]:
    templates = _glyph_templates(size)
    chars: list[str] = []
    for cluster in _clusters(bw):
        best, score = None, 0.0
        for ch, tpl in templates.items():
            s = _iou(cluster, tpl)
            if s > score:
                best, score = ch, s
        if best is None or score < MIN_MATCH:
            return None
        chars.append(best)
    return "".join(chars) or None


def read_frame(image_bytes: bytes) -> dict:
    """Return the values actually visible in the frame, with the confidence-free honesty
    that an unreadable band is reported as unreadable."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    arr = np.asarray(img, dtype=np.uint8) > INK_THRESHOLD
    out: dict[str, Optional[str]] = {}
    for band, (x0, x1, y0, y1, size) in BANDS.items():
        out[band] = read_band(arr[y0:y1, x0:x1], size)
    out["readable"] = all(v is not None for v in out.values())
    return out
