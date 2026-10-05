from __future__ import annotations

import numpy as np
from PIL import Image


def picture_kind(crop: Image.Image) -> str:
    """Split artwork (color-rich) from a line drawing (white + ink)."""
    rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
    if rgb.size == 0:
        return "drawing"
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0), 0.0)
    white_ratio = float(((rgb > 235).all(axis=2)).mean())
    sat_mean = float(sat.mean())
    if sat_mean > 0.12 and white_ratio < 0.62:
        return "artwork"
    return "drawing"


def pick_artwork_index(crops: list[Image.Image]) -> int | None:
    if not crops:
        return None
    scores = []
    for i, crop in enumerate(crops):
        kind = picture_kind(crop)
        rgb = np.asarray(crop.convert("RGB"), dtype=np.float32)
        mx = rgb.max(axis=2)
        mn = rgb.min(axis=2)
        sat = float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0), 0.0).mean())
        scores.append((kind == "artwork", sat, i))
    scores.sort(reverse=True)
    best_is_art, _, idx = scores[0]
    return idx if best_is_art or len(crops) == 1 else None
