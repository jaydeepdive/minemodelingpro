#!/usr/bin/env python3
"""Reading values back off a published geophysical map image — measured.

A technical report states its magnetics as a figure, and the only honest way to
know whether reading one back works is to make a figure out of data you already
have and see what comes back. So this renders REAL Québec magnetic grid windows
as report-style plates — colour map, legend with numbers, a coordinate frame —
recovers them through `geophys_figure.read_figure`, and compares cell by cell
against the grid they were drawn from.

It also checks the refusals, which matter more than the successes: a figure with
no legend, a figure drawn with a colour map the legend does not describe, and a
figure with no coordinates must all come back `ok=False` rather than returning a
plausible-looking grid.
"""
import io
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro import geophys_figure as G                   # noqa: E402

COG = os.environ.get("MMP_MAG_DIR") or "/home/claude/qcgeo/cog"
OK, BAD, SKIP = [], [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


def plate(grid, vmin, vmax, cmap="viridis", size=560, bar="horizontal",
          labels=True, dpi=110, jpeg=0):
    """Render a grid the way a report plate is drawn, legend and all."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    fig = plt.figure(figsize=(size / dpi, size / dpi), dpi=dpi)
    ax = fig.add_axes([0.10, 0.22, 0.86, 0.74])
    ax.imshow(grid, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    ax.set_xticks([]); ax.set_yticks([])
    cax = fig.add_axes([0.10, 0.08, 0.86, 0.045])
    sm = plt.cm.ScalarMappable(cmap=cmap,
                               norm=matplotlib.colors.Normalize(vmin=vmin, vmax=vmax))
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    if labels:
        cb.set_ticks([vmin, (vmin + vmax) / 2, vmax])
        cb.set_label("nT")
    else:
        cb.set_ticks([])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    im = Image.open(buf).convert("RGB")
    if jpeg:
        b2 = io.BytesIO(); im.save(b2, "JPEG", quality=jpeg); b2.seek(0)
        im = Image.open(b2).convert("RGB")
    # the map panel in pixels, as a report's own frame would delimit it
    W, H = im.size
    box = (int(0.10 * W) + 2, int((1 - 0.96) * H) + 2,
           int(0.96 * W) - 2, int((1 - 0.22) * H) - 2)
    return im, box


def windows(n=6, half=64):
    """Real magnetic windows with enough signal in them to be worth drawing."""
    import rasterio
    from rasterio.windows import Window
    p = os.path.join(COG, "qc_mag_residual.tif")
    out = []
    with rasterio.open(p) as ds:
        rng = np.random.default_rng(5)
        tries = 0
        while len(out) < n and tries < 400:
            tries += 1
            r = int(rng.integers(half, ds.height - half))
            c = int(rng.integers(half, ds.width - half))
            a = ds.read(1, window=Window(c - half, r - half, 2 * half, 2 * half))
            a = np.where(a < -1e30, np.nan, a)
            if np.isfinite(a).mean() < 0.999:
                continue
            if np.nanpercentile(a, 95) - np.nanpercentile(a, 5) < 60:
                continue
            out.append(a)
    return out


# --------------------------------------------------------------- the refusals
one = np.add.outer(np.linspace(0, 1, 80), np.linspace(0, 1, 80)) * 100
im_nobar, _ = plate(one, 0, 100, labels=True)
from PIL import Image
flat = Image.new("RGB", (400, 400), (128, 128, 128))
r = G.read_figure(flat)
check("a plain image with no legend is refused",
      (not r.ok) and "colour bar" in r.why, r.why)

im_nolab, box_nolab = plate(one, 0, 100, labels=False)
r = G.read_figure(im_nolab, map_box=box_nolab)
check("a legend with no numbers is refused, not guessed",
      (not r.ok) and "could not be read" in r.why, r.why)

# a legend that describes a different colour map than the panel is drawn with
im_a, box_a = plate(one, 0, 100, cmap="viridis")
bar_b = G.find_colour_bar(plate(one, 0, 100, cmap="autumn")[0])
vals, off = G.invert_colours(im_a.crop(box_a), bar_b["ramp"], 0, 100)
check("a panel drawn with the wrong colour map is mostly off-ramp",
      off > G.MAX_OFF_RAMP, f"off_ramp {off:.1%}")

r = G.read_figure(im_a, vrange=(0, 100), map_box=box_a, points=[(0, 0, 1, 1)])
check("fewer than 3 control points is refused",
      (not r.ok) and "control points" in r.why, r.why)

# ------------------------------------------------------------ the affine itself
pts = [(0, 0, 1000.0, 5000.0), (100, 0, 1750.0, 5000.0),
       (0, 100, 1000.0, 4250.0), (100, 100, 1750.0, 4250.0)]
t, rms = G.affine_from_points(pts)
check("an exact affine is recovered from 4 corner ticks", rms is not None and rms < 1e-6,
      f"rms {rms}")
check("and it maps a tick back to its own coordinate",
      abs((t * (100, 100))[0] - 1750.0) < 1e-6 if t else False)

# =================================================== recovery against real data
if not os.path.exists(os.path.join(COG, "qc_mag_residual.tif")):
    SKIP.append("recovery against real Québec grids (no COGs here)")
    print(f"\n  skip  recovery against real data — nothing under {COG}")
else:
    try:
        import matplotlib  # noqa: F401
    except Exception:
        SKIP.append("recovery (matplotlib absent)")
    wins = windows(6)
    check("real magnetic windows were found to draw", len(wins) >= 4, str(len(wins)))

    def recover(a, **kw):
        lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
        im, box = plate(a, lo, hi, **kw)
        r = G.read_figure(im, vrange=(lo, hi), map_box=box)
        if not r.ok:
            return None, r
        v = r.values[box[1]:box[3], box[0]:box[2]]
        # compare on a common grid: the plate is a resampling of the window
        from PIL import Image
        tgt = np.asarray(Image.fromarray(v).resize((a.shape[1], a.shape[0]), Image.NEAREST))
        good = np.isfinite(tgt)
        if good.mean() < 0.5:
            return None, r
        err = np.abs(tgt[good] - a[good])
        return (float(np.median(err)), float(np.median(err) / (hi - lo)), float(good.mean())), r

    for label, kw in (("clean PNG, viridis", {}),
                      ("JPEG quality 70", {"jpeg": 70}),
                      ("a different ramp (plasma)", {"cmap": "plasma"})):
        res = [recover(a, **kw) for a in wins]
        good = [x for x, _ in res if x]
        check(f"recovered: {label}", len(good) >= max(3, len(wins) - 1),
              f"{len(good)}/{len(wins)} figures read")
        if good:
            med = float(np.median([g[0] for g in good]))
            pct = float(np.median([g[1] for g in good]))
            cov = float(np.median([g[2] for g in good]))
            print(f"           median error {med:7.1f} nT = {pct:5.2%} of the plate's range,"
                  f" {cov:.0%} of cells read")
            check(f"  {label}: error under 5% of the legend range", pct < 0.05, f"{pct:.2%}")

    # a greyscale map is not invertible against a colour legend, and must not pretend
    a = wins[0]
    lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
    im, box = plate(a, lo, hi, cmap="gray")
    rg = G.read_figure(im, vrange=(lo, hi), map_box=box)
    if rg.ok:
        v = rg.values[box[1]:box[3], box[0]:box[2]]
        from PIL import Image
        tgt = np.asarray(Image.fromarray(v).resize((a.shape[1], a.shape[0]), Image.NEAREST))
        g = np.isfinite(tgt)
        e = float(np.median(np.abs(tgt[g] - a[g]))) / (hi - lo) if g.any() else 1.0
        print(f"           greyscale: read, median error {e:.2%} of range")
    else:
        print(f"           greyscale: refused — {rg.why}")

print(f"\n{len(OK)} passed, {len(BAD)} failed" + (f", {len(SKIP)} skipped" if SKIP else ""))
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
