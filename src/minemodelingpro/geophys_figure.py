"""Recover values from a published geophysical map image.

A technical report states its magnetics as a FIGURE — a colour map with a
legend — and no table extractor reaches it. This file reaches it, because the
figure carries everything needed to invert it: a colour ramp with numeric
labels says what each colour means, and a coordinate frame says where each
pixel is. Reading those two things back is not inversion of the physics; it is
reading the map, which is what a geologist does by eye.

It is also easy to do badly and silently. A JPEG smears a colour ramp, an
annotation arrow is not a measurement, a discontinuous legend has no inverse,
and a map with no readable coordinates cannot be placed on the ground at all.
So every stage has a gate and `Recovered.ok` is False unless all of them pass,
with `why` saying which failed. A figure that cannot be read returns nothing
rather than a plausible-looking grid.

What it does NOT do: invent a coordinate frame, extrapolate past the legend's
own range, or read a greyscale map (grey is not injective against most
legends). Those refuse.

The accuracy is measured, not asserted — `scripts/test_geophys_figure.py`
renders real Québec magnetic data as a report-style figure, recovers it, and
compares against the grid it came from.
"""
import math
import re

import numpy as np

MIN_BAR_PX = 60           # a legend shorter than this is a colour swatch, not a ramp
MAX_OFF_RAMP = 0.30       # beyond this the image is not mostly this colour map
MIN_CHROMA = 12.0         # a greyscale legend has no usable inverse (see read_figure)
MAX_ACROSS = 2.5          # a colour bar is constant across its width; a map slice is not
RAMP_N = 256


class Recovered:
    """Values read off a figure, and whether to believe them."""

    def __init__(self, values=None, transform=None, vmin=None, vmax=None,
                 ok=False, why="", off_ramp=None, bar=None, unit=None, points=0):
        self.values = values            # float32, NaN where not read
        self.transform = transform      # affine (rasterio) or None
        self.vmin, self.vmax = vmin, vmax
        self.ok, self.why = ok, why
        self.off_ramp = off_ramp
        self.bar = bar
        self.unit = unit
        self.points = points

    def __repr__(self):
        if not self.ok:
            return f"Recovered(ok=False, why={self.why!r})"
        v = self.values
        n = int(np.isfinite(v).sum())
        return (f"Recovered(ok=True, {v.shape[1]}x{v.shape[0]}, {n:,} cells, "
                f"{self.vmin:g}..{self.vmax:g}{' ' + self.unit if self.unit else ''}, "
                f"off_ramp={self.off_ramp:.1%})")


# ----------------------------------------------------------------- the legend
def _lab(rgb):
    from skimage.color import rgb2lab
    a = np.asarray(rgb, dtype="f4")
    if a.max() > 1.0:
        a = a / 255.0
    return rgb2lab(a.reshape(-1, 1, 3)).reshape(-1, 3)


def _trim_margin(lab):
    """Drop the near-white page either side of a ramp."""
    L, A, B = lab[:, 0], lab[:, 1], lab[:, 2]
    paper = (L > 93.0) & (np.abs(A) < 8) & (np.abs(B) < 8)
    i, j = 0, len(lab)
    while i < j and paper[i]:
        i += 1
    while j > i and paper[j - 1]:
        j -= 1
    return i, j


def _ramp_quality(lab):
    """(arc length through Lab, distinct colours) along a candidate band.

    Lightness monotonicity was the first discriminator here and it is the wrong
    one: `jet` runs blue -> cyan -> yellow -> red, so its lightness rises and
    then falls, and the longest monotone stretch is two thirds of the bar. jet
    is one of the commonest colour maps in older geophysical plates, so a test
    it fails is useless. What every legend does have is a long, smooth,
    non-repeating path through colour space.
    """
    if len(lab) < MIN_BAR_PX:
        return 0.0, 0
    step = np.sqrt(((np.diff(lab, axis=0)) ** 2).sum(1))
    if step.size == 0:
        return 0.0, 0
    # a legend is smooth: no jumps between unrelated colours
    if np.percentile(step, 98) > 14.0:
        return 0.0, 0
    arc = float(step.sum())
    q = np.round(lab / 2.0).astype("i4")
    distinct = int(len({tuple(v) for v in q}))
    return arc, distinct


def _candidates(img, want=8):
    a = np.asarray(img)[:, :, :3].astype("f4")
    out = []
    for vertical in (False, True):
        b = np.swapaxes(a, 0, 1) if vertical else a
        H, W = b.shape[:2]
        if W < MIN_BAR_PX:
            continue
        lab = _lab(b.reshape(-1, 3)).reshape(H, W, 3)
        cum = np.vstack([np.zeros((1, W, 3), "f8"), np.cumsum(lab, axis=0)])
        cumsq = np.vstack([np.zeros((1, W, 3), "f8"), np.cumsum(lab ** 2, axis=0)])
        for th in (6, 10, 16, 24, 34):
            if th >= H:
                continue
            for y0 in range(0, H - th, 2):
                m = (cum[y0 + th] - cum[y0]) / th
                var = (cumsq[y0 + th] - cumsq[y0]) / th - m ** 2
                i, j = _trim_margin(m)
                if j - i < MIN_BAR_PX:
                    continue
                across = float(np.sqrt(np.maximum(var[i:j], 0)).mean())
                # THE discriminator, and it is near-exact. A colour bar is
                # constant across its width by construction: measured on a real
                # magnetic plate the legend reads 0.000 while every slice through
                # the map reads 0.55 to 1.56. The first threshold here was 6.0,
                # which let a vertical slice of the map win on every real figure
                # — a slice traverses the same colours, so no amount of coverage
                # scoring could tell them apart.
                if across > MAX_ACROSS:
                    continue
                arc, distinct = _ramp_quality(m[i:j])
                if arc < 60.0 or distinct < 24:
                    continue
                box = (i, y0, j, y0 + th)
                if vertical:
                    box = (y0, i, y0 + th, j)
                # uniformity dominates the score, for the same reason
                score = arc * distinct / (0.25 + across) ** 2
                out.append((score, box, b[y0:y0 + th, i:j].mean(axis=0) / 255.0,
                            vertical))
    out.sort(key=lambda t: -t[0])
    # keep the best few, spatially distinct, for the coverage re-rank below
    kept = []
    for c in out:
        if all(abs(c[1][0] - k[1][0]) + abs(c[1][1] - k[1][1]) > 20 for k in kept):
            kept.append(c)
        if len(kept) >= want:
            break
    return kept


def _as_ramp(cols):
    idx = np.linspace(0, len(cols) - 1, RAMP_N)
    r = np.stack([np.interp(idx, np.arange(len(cols)), cols[:, c]) for c in range(3)], 1)
    return r.astype("f4")


def find_colour_bar(img, panel=None):
    """The legend's pixel box and its ramp, or None.

    Candidates are bands that are uniform across their width and trace a long
    smooth path through colour space. That alone is not enough — a smooth
    gradient inside the map itself passes it, and on a `jet` plate and a JPEG
    one it outscored the real legend. So the candidates are re-ranked by the
    thing that actually matters: how much of the REST of the image that
    candidate's ramp can account for. A legend explains the map it belongs to;
    a patch of the map explains only itself.
    """
    from PIL import Image
    im = img if isinstance(img, Image.Image) else Image.fromarray(np.asarray(img))
    cands = _candidates(im)
    if not cands:
        return None
    a = np.asarray(im)[:, :, :3]
    H, W = a.shape[:2]
    # a thumbnail is enough to judge coverage and keeps this cheap
    small = np.asarray(im.resize((max(32, W // 6), max(32, H // 6)), Image.NEAREST))[:, :, :3]
    best = None
    for score, box, cols, vertical in cands:
        ramp = _as_ramp(cols)
        _v, off = invert_colours(small, ramp, 0.0, 1.0, tol=14.0)
        covered = 1.0 - off
        rank = covered * (1.0 + 0.15 * (score / cands[0][0]))
        if best is None or rank > best[0]:
            best = (rank, box, cols, vertical, covered)
    _r, box, cols, vertical, covered = best
    # Which end is low. A horizontal bar reads left to right; a vertical one is
    # drawn low at the BOTTOM, and scanning it gives top-to-bottom, so it has to
    # be turned round. Labels override this when they can be read.
    ramp = _as_ramp(cols)
    if vertical:
        ramp = ramp[::-1]
    return {"box": tuple(int(v) for v in box), "ramp": ramp,
            "vertical": bool(vertical), "reversed": bool(vertical),
            "covers": round(covered, 3)}


_NUM = re.compile(r"-?\d{1,3}(?:[ ,]\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")


def read_bar_labels(img, bar, pad=70):
    """(low, high, unit) read off the numbers printed beside the legend."""
    try:
        import pytesseract
    except Exception:
        return None
    x0, y0, x1, y1 = bar["box"]
    W, H = img.size if hasattr(img, "size") else (img.shape[1], img.shape[0])
    if bar["vertical"]:
        crop = (max(0, x0 - pad), max(0, y0 - 8), min(W, x1 + pad), min(H, y1 + 8))
    else:
        crop = (max(0, x0 - 8), max(0, y0 - pad), min(W, x1 + 8), min(H, y1 + pad))
    from PIL import Image
    im = img if isinstance(img, Image.Image) else Image.fromarray(np.asarray(img))
    txt = pytesseract.image_to_string(im.crop(crop).resize(
        ((crop[2] - crop[0]) * 3, (crop[3] - crop[1]) * 3), Image.LANCZOS))
    nums = []
    for m in _NUM.finditer(txt):
        try:
            nums.append(float(m.group(0).replace(" ", "").replace(",", "")))
        except ValueError:
            pass
    if len(nums) < 2:
        return None
    unit = None
    for u in ("nT/m", "nT", "mGal", "ppm", "%", "ohm-m", "mV/V", "ms", "SI"):
        if u.lower() in txt.lower():
            unit = u
            break
    lo, hi = min(nums), max(nums)
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        return None
    return lo, hi, unit


# ------------------------------------------------------------- the inversion
def invert_colours(img, ramp, vmin, vmax, tol=10.0, mask=None, denoise=True):
    """Every pixel to a value by nearest ramp colour; NaN where nothing matches.

    Matching in CIE Lab rather than RGB, because a report's JPEG moves a colour
    by several RGB units without moving it perceptually, and an RGB nearest
    neighbour then jumps to the wrong end of a ramp that doubles back.
    """
    a = np.asarray(img)[:, :, :3]
    if denoise and min(a.shape[:2]) >= 9:
        # A report plate is a JPEG, and JPEG moves a colour by enough to land on
        # the wrong part of a ramp that doubles back: measured, recovery error
        # went from 2% on a clean PNG to 25% at quality 70. A 3x3 median takes
        # out the ringing without moving a real anomaly edge.
        import cv2
        a = cv2.medianBlur(a.astype("uint8"), 3)
    h, w = a.shape[:2]
    px = _lab(a.reshape(-1, 3))
    rl = _lab(ramp)
    # nearest ramp entry, in chunks so a full-page figure does not allocate GB
    out = np.empty(px.shape[0], "f4"); dist = np.empty(px.shape[0], "f4")
    step = 200_000
    for i in range(0, px.shape[0], step):
        c = px[i:i + step]
        d = ((c[:, None, :] - rl[None, :, :]) ** 2).sum(-1)
        j = d.argmin(1)
        out[i:i + step] = j / (len(rl) - 1.0)
        dist[i:i + step] = np.sqrt(d[np.arange(len(j)), j])
    frac = out.reshape(h, w); dd = dist.reshape(h, w)
    vals = vmin + frac * (vmax - vmin)
    off_ramp = dd > tol
    keep = np.ones_like(off_ramp) if mask is None else mask
    vals = np.where(off_ramp | ~keep, np.nan, vals).astype("f4")
    # the share of the pixels we were ASKED to read that did not match. Measured
    # over the whole image instead, it is dominated by the page around the panel
    # and every correct figure looks unreadable.
    n = int(keep.sum())
    return vals, (float((off_ramp & keep).sum() / n) if n else 1.0)


# ---------------------------------------------------------- the ground truth
def affine_from_points(points):
    """Least-squares affine from >=3 (col, row, X, Y) control points.

    Returns (transform, rms_m). A map frame gives these as its printed tick
    labels; without them the figure has no place on the ground and this
    refuses rather than assuming the image spans some bounding box.
    """
    if len(points) < 3:
        return None, None
    A, bx, by = [], [], []
    for c, r, X, Y in points:
        A.append([c, r, 1.0]); bx.append(X); by.append(Y)
    A = np.asarray(A, float)
    sx, *_ = np.linalg.lstsq(A, np.asarray(bx, float), rcond=None)
    sy, *_ = np.linalg.lstsq(A, np.asarray(by, float), rcond=None)
    pred = np.stack([A @ sx, A @ sy], 1)
    want = np.asarray([[p[2], p[3]] for p in points], float)
    rms = float(np.sqrt(((pred - want) ** 2).sum(1).mean()))
    try:
        from affine import Affine
        t = Affine(sx[0], sx[1], sx[2], sy[0], sy[1], sy[2])
    except Exception:
        t = (sx[0], sx[1], sx[2], sy[0], sy[1], sy[2])
    return t, rms


def read_figure(img, points=None, vrange=None, unit=None, tol=14.0,
                map_box=None):
    """A figure in, values out — or a refusal that says which gate failed.

    `points`  (col, row, X, Y) control points from the map's own frame.
    `vrange`  (low, high) when the legend is known; otherwise it is read.
    `map_box` (x0, y0, x1, y1) of the map panel, so the legend, title and
              north arrow are not inverted as though they were ground.
    """
    from PIL import Image
    im = img if isinstance(img, Image.Image) else Image.open(img)
    im = im.convert("RGB")
    bar = find_colour_bar(im)
    if bar is None:
        return Recovered(ok=False, why="no colour bar found — a map with no legend has no inverse")
    chroma = float(np.sqrt((_lab(bar["ramp"])[:, 1:] ** 2).sum(1)).max())
    if chroma < MIN_CHROMA:
        return Recovered(ok=False, bar=bar,
                         why="the legend is greyscale — against black annotation and a "
                             "white page it has no usable inverse (measured 45% error)")
    if vrange is None:
        lab = read_bar_labels(im, bar)
        if lab is None:
            return Recovered(ok=False, why="legend found but its numbers could not be read",
                             bar=bar)
        vmin, vmax, u = lab
        unit = unit or u
    else:
        vmin, vmax = float(vrange[0]), float(vrange[1])
    a = np.asarray(im)
    mask = np.ones(a.shape[:2], bool)
    if map_box:
        mask[:] = False
        x0, y0, x1, y1 = (int(v) for v in map_box)
        mask[y0:y1, x0:x1] = True
    else:
        bx0, by0, bx1, by1 = bar["box"]
        mask[by0:by1, bx0:bx1] = False        # never invert the legend itself
    vals, off = invert_colours(im, bar["ramp"], vmin, vmax, tol=tol, mask=mask)
    if off > MAX_OFF_RAMP:
        return Recovered(ok=False, bar=bar, off_ramp=off,
                         why=f"{off:.0%} of the panel does not match the legend's colours — "
                             f"this is not a map drawn with that ramp")
    t = rms = None
    if points:
        t, rms = affine_from_points(points)
        if t is None:
            return Recovered(ok=False, bar=bar, off_ramp=off,
                             why="fewer than 3 coordinate control points")
    return Recovered(values=vals, transform=t, vmin=vmin, vmax=vmax, ok=True,
                     off_ramp=off, bar=bar, unit=unit, points=len(points or []))


# ------------------------------------------------------- getting it out of a PDF
def figures_in_pdf(path, pages=None, min_px=400):
    """Raster images embedded in a report, largest first.

    A magnetic plate is the biggest picture on its page; a logo is not. Yields
    (page number, PIL image).
    """
    import pymupdf
    from PIL import Image
    import io
    doc = pymupdf.open(path)
    out = []
    for pno in (pages or range(doc.page_count)):
        if pno >= doc.page_count:
            continue
        for info in doc[pno].get_images(full=True):
            try:
                pix = pymupdf.Pixmap(doc, info[0])
                if pix.width < min_px or pix.height < min_px:
                    continue
                if pix.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                out.append((pno + 1, im))
            except Exception:
                continue
    out.sort(key=lambda t: -(t[1].size[0] * t[1].size[1]))
    return out
