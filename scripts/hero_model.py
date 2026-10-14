#!/usr/bin/env python3
"""Cut a real model down to a hero-sized one for the landing page.

The front page should show the product, not a cartoon of it — so it renders an
actual MMP model: real collars, real desurveyed traces, real grade-coloured
intervals and a real block model, trimmed to something a landing page can load.

    PYTHONPATH=src python3 scripts/hero_model.py aurmac-banyan-gold [site]

Writes site/hero.json. Only models built from public filings belong here.
"""
import os
import re
import sys
import json
import math
import base64

import numpy as np

RX = re.compile(r'<script id="model"[^>]*>(.*?)</script>', re.S)
MAX_BLOCKS = 9000
MAX_TERRAIN = 110


def _un64(s, dt):
    return np.frombuffer(base64.b64decode(s), dtype=dt)


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def build(slug, site="site"):
    page = os.path.join(site, "models", slug + ".html")
    m = RX.search(open(page).read())
    d = json.loads(m.group(1))
    el = d["elements"][d["primary"]]
    b = el["blocks"]

    ijk = _un64(b["ijk"], np.uint16).reshape(-1, 3)
    g = _un64(b["g"], np.float32)
    # keep the blocks that carry the deposit: above the third legend break, then
    # thinned evenly so the shape survives
    bins = el["bins"]
    cut = bins[min(2, len(bins) - 1)]
    keep = np.where(g >= cut)[0]
    if len(keep) < 2000:
        keep = np.argsort(g)[-min(len(g), MAX_BLOCKS):]
    if len(keep) > MAX_BLOCKS:
        keep = keep[np.linspace(0, len(keep) - 1, MAX_BLOCKS).astype(int)]
    blocks = {"origin": b["origin"], "size": b["size"],
              "ijk": _b64(ijk[keep].astype(np.uint16)), "g": _b64(g[keep].astype(np.float32)),
              "n": int(len(keep)), "n_total": b.get("n_total"), "cut": float(cut)}

    # trim to the deposit: the block model says where it is, so keep the holes around
    # it and drop the regional step-outs that would shrink it to a speck
    ox, oy, oz = b["origin"]; bs = b["size"]
    bx = ox + ijk[keep, 0].astype(float) * bs[0]
    by = oy + ijk[keep, 1].astype(float) * bs[1]
    mx, my = float(np.median(bx)), float(np.median(by))
    rad = float(np.percentile(np.hypot(bx - mx, by - my), 96)) * 1.45 + 150
    hs = d["holes"]
    near = [i for i, h in enumerate(hs) if math.hypot(h[1] - mx, h[2] - my) <= rad]
    if len(near) < 12:
        near = list(range(len(hs)))
    remap = {h: i for i, h in enumerate(near)}
    holes = [hs[i] for i in near]
    segments = [[remap[s[0]], s[1], s[2], s[3]] for s in el["segments"] if s[0] in remap]

    terr = None
    t = d.get("terrain")
    if t:
        z = _un64(t["z"], np.float32).reshape(t["ny"], t["nx"])
        # crop the DEM to the ground the drilling actually sits on, with a margin —
        # the full tile dwarfs the deposit
        hx = np.array([h[1] for h in holes], float); hy = np.array([h[2] for h in holes], float)
        mx = max(hx.max() - hx.min(), hy.max() - hy.min()) * 0.75 + 400
        i0 = int(max(0, (hx.min() - mx - t["x0"]) / t["dx"])); i1 = int(min(t["nx"], (hx.max() + mx - t["x0"]) / t["dx"]) + 1)
        j0 = int(max(0, (hy.min() - mx - t["y0"]) / t["dy"])); j1 = int(min(t["ny"], (hy.max() + mx - t["y0"]) / t["dy"]) + 1)
        if i1 - i0 < 8 or j1 - j0 < 8:
            i0, i1, j0, j1 = 0, t["nx"], 0, t["ny"]
        z = z[j0:j1, i0:i1]
        sy = max(1, z.shape[0] // MAX_TERRAIN); sx = max(1, z.shape[1] // MAX_TERRAIN)
        zz = z[::sy, ::sx]
        terr = {"nx": int(zz.shape[1]), "ny": int(zz.shape[0]),
                "x0": t["x0"] + i0 * t["dx"], "y0": t["y0"] + j0 * t["dy"],
                "dx": t["dx"] * sx, "dy": t["dy"] * sy, "z": _b64(zz.astype(np.float32))}

    out = {"project": d["project"], "company": d.get("company"), "region": d.get("region"),
           "element": d["primary"], "unit": el["unit"], "bins": bins, "colors": el["colors"],
           "counts": {"holes": d["counts"]["holes"], "intervals": d["counts"]["intervals"],
                      "blocks": d["counts"]["blocks"]},
           "sources": [s.get("title") for s in (d.get("sources") or [])[:3]],
           # holes without their ids: [x, y, z, azimuth, dip, depth]
           "holes": [[round(h[1], 1), round(h[2], 1), round(h[3], 1), h[4], h[5], h[6]] for h in holes],
           "segments": segments,
           "blocks": blocks, "terrain": terr}
    path = os.path.join(site, "hero.json")
    json.dump(out, open(path, "w"), separators=(",", ":"))
    print(f"{path}: {os.path.getsize(path) / 1024:.0f} KB — {out['project']}, "
          f"{len(out['holes'])} holes, {len(out['segments'])} intervals, {blocks['n']:,} blocks")
    return path


if __name__ == "__main__":
    build(*(sys.argv[1:] or ["aurmac-banyan-gold"]))
