#!/usr/bin/env python3
"""Calibrate magnetic favourability against measured ground, not a guessed threshold.

The breaks are the deciles of ORDINARY surveyed ground — the decoy populations
drawn around the region's own projects — so a score is "this ground is more
magnetically active than N% of nearby surveyed ground". Deposit ground sits
high in that distribution by about 2x on relief and gradient, which is the
measurement this rests on, and the score says only that: a prior over ground.
"""
import json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from minemodelingpro import magnetics as M                     # noqa: E402

FEATS = ["residual_relief_nT", "analytic_signal_p90", "gradient_abs_median"]
rng = np.random.default_rng(11)


def main(region="Québec", index="site/models_index.json", per=24):
    models = [m for m in json.load(open(index))["models"]
              if M.region_key(m.get("region")) == M.region_key(region) and m.get("center")]
    print(f"  {len(models)} {region} projects", flush=True)
    ordinary, deposit = {k: [] for k in FEATS}, {k: [] for k in FEATS}
    import rasterio
    from rasterio.warp import transform as warp
    g = M.GRIDS[M.region_key(region)]
    path = os.path.join(M._root(), g["products"]["residual"])
    with rasterio.open(path) as ds:
        crs = ds.crs
    for m in models:
        lat, lon = m["center"]
        s = M.sample(region, lat, lon)
        if s and s.get("surveyed"):
            for k in FEATS:
                deposit[k].append(s[k])
        # ordinary ground: an annulus of surveyed decoys around the same project
        x, y = warp("EPSG:4326", crs, [lon], [lat]); x, y = x[0], y[0]
        got = 0
        for _ in range(per * 12):
            if got >= per:
                break
            th = rng.uniform(0, 2 * math.pi)
            rad = math.sqrt(rng.uniform((6000 / 45000) ** 2, 1)) * 45000
            dx, dy = rad * math.cos(th), rad * math.sin(th)
            # transform returns (xs, ys) -> (lon, lat); naming them the other
            # way round sends every decoy to a mirrored point off the grid
            lons, lats = warp(crs, "EPSG:4326", [x + dx], [y + dy])
            d = M.sample(region, lats[0], lons[0])
            if d and d.get("surveyed"):
                for k in FEATS:
                    ordinary[k].append(d[k])
                got += 1
    out = {}
    for k in FEATS:
        a = np.asarray(ordinary[k], float)
        out[k] = [round(float(v), 4) for v in np.percentile(a, [10, 20, 30, 40, 50, 60, 70, 80, 90])]
        dm, om = np.median(deposit[k]), np.median(a)
        print(f"  {k:24s} ordinary n={a.size:5d} median {om:9.3f}   "
              f"deposit median {dm:9.3f}  ({dm/om:.2f}x)")
    print("\n  deciles of ordinary ground:")
    for k, v in out.items():
        print(f"    {k:24s} {v}")
    return out


if __name__ == "__main__":
    cal = main()
    print("\n" + json.dumps({"quebec": cal}, indent=2)[:700])
    json.dump({"quebec": cal}, open("data/keep/mmp_mag_calibration.json", "w"), indent=1)
    print("\n  wrote data/keep/mmp_mag_calibration.json")
