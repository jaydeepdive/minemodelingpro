#!/usr/bin/env python3
"""Within one property, does magnetics point at the better ground?

The earlier result was between-place: known deposits sit on ground with ~3x the
magnetic relief of ordinary ground. That is a prior over ground and it is NOT a
drill target. The drill-targeting question is finer and has to be asked inside
a single property, where the belt-scale signal is held constant: given this
claim block, do the magnetically distinctive cells carry better grades than the
dull ones?

Truth comes from the drilling: every mineralised segment is desurveyed to its
real plan position (an angled hole puts its intercept 100 m+ from the collar,
which matters at 75 m), binned, and each bin's best grade compared with that
bin's magnetic features. Correlation is measured WITHIN each project and only
then combined, so the between-place effect cannot leak in.

Two null controls: grades shuffled among a project's own bins, and magnetics
sampled at a random offset. Both must come back flat or the harness is broken.
"""
import json, math, os, re, sys
import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, "src")
os.environ.setdefault("MMP_MAG_DIR", "/home/claude/qcgeo/cog")
from minemodelingpro import magnetics as M                        # noqa: E402

BIN = 150.0            # m; 2 magnetic cells, so a bin is resolvable
MIN_BINS = 8
rng = np.random.default_rng(3)


def load(slug):
    p = f"site/models/{slug}.html"
    if not os.path.exists(p):
        return None
    s = open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r'<script id="model" type="application/json">(.*?)</script>', s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def desurvey(h, md):
    """Plan position of a point md metres down a straight hole on collar az/dip."""
    _id, x, y, _z, az, dip, _d = h[:7]
    a, p = math.radians(az), math.radians(dip)
    horiz = md * math.cos(p)                 # dip is negative downward
    return x + horiz * math.sin(a), y + horiz * math.cos(a)


def bins_for(d):
    """{(bin x, bin y): best grade} from the primary element's segments."""
    el = d.get("primary")
    E = (d.get("elements") or {}).get(el)
    if not E or not E.get("segments"):
        return None, None
    H = d["holes"]
    out = {}
    for seg in E["segments"]:
        hi, f, t, g = seg[0], float(seg[1]), float(seg[2]), float(seg[3])
        if hi >= len(H):
            continue
        x, y = desurvey(H[hi], (f + t) / 2.0)
        k = (int(math.floor(x / BIN)), int(math.floor(y / BIN)))
        if g > out.get(k, -1):
            out[k] = g
    return out, el


FEATS = ["residual_relief_nT", "analytic_signal_p90", "gradient_abs_median"]


def run(arm, jitter_m=0.0, shuffle=False):
    qc = [m for m in json.load(open("site/models_index.json"))["models"]
          if M.region_key(m.get("region")) == "quebec" and m.get("center")]
    per = {k: [] for k in FEATS}
    used = 0
    for mm in qc:
        d = load(mm["slug"])
        if not d:
            continue
        fr = d.get("frame") or {}
        if fr.get("kind") != "geo" or fr.get("lat0") is None:
            continue
        lat0, lon0 = fr["lat0"], fr["lon0"]
        mE = 111320.0 * math.cos(math.radians(lat0)); mN = 110540.0
        B, _el = bins_for(d)
        if not B or len(B) < MIN_BINS:
            continue
        keys = list(B)
        grades = np.array([B[k] for k in keys], float)
        if shuffle:
            grades = rng.permutation(grades)
        rows = {k: [] for k in FEATS}
        keep = []
        for i, (bx, by) in enumerate(keys):
            x = (bx + 0.5) * BIN; y = (by + 0.5) * BIN
            if jitter_m:
                th = rng.uniform(0, 2 * math.pi)
                x += jitter_m * math.cos(th); y += jitter_m * math.sin(th)
            lat = lat0 + y / mN; lon = lon0 + x / mE
            s = M.sample("Québec", lat, lon, half_m=150)
            if not s or not s.get("surveyed"):
                continue
            keep.append(i)
            for k in FEATS:
                rows[k].append(s[k])
        if len(keep) < MIN_BINS:
            continue
        g = grades[keep]
        if np.allclose(g, g[0]):
            continue
        used += 1
        for k in FEATS:
            v = np.array(rows[k], float)
            if np.allclose(v, v[0]):
                continue
            rho, _p = spearmanr(v, g)
            if np.isfinite(rho):
                per[k].append(rho)
    print(f"\n{arm}   projects usable: {used}")
    from math import comb
    for k in FEATS:
        a = np.array(per[k])
        if a.size < 5:
            print(f"    {k:24s} too few projects ({a.size})"); continue
        pos = int((a > 0).sum()); n = a.size
        p = min(1.0, sum(comb(n, i) for i in range(min(pos, n - pos) + 1)) / 2**n * 2)
        print(f"    {k:24s} median rho {np.median(a):+.3f}   mean {a.mean():+.3f}   "
              f"positive {pos}/{n}   sign p={p:.3f}" + ("  <--" if p < 0.05 else "   ns"))


run("REAL        magnetics at each drilled bin")
run("CONTROL A   grades shuffled among the same bins", shuffle=True)
run("CONTROL B   magnetics sampled 400 m off each bin", jitter_m=400.0)
