"""Regional magnetic data under a project, and what it says about the ground.

Drilling tests points. A magnetic grid covers the ground between and beyond
them, and — the property that matters — it was flown without reference to where
anyone later found ore. That independence is the whole reason this is worth
having: `structure.py` measured a flat null because MMP's bodies are drawn from
the drilling and the drilling chased the grade, so the geometry already
contained the answer. A magnetic survey does not.

Measured on 40 Quebec projects against decoys drawn from nearby SURVEYED ground
(see CLAUDE.md for the controls), a deposit sits on ground with roughly twice
the magnetic field relief and gradient of its own neighbourhood, and three to
four times that of regional ground. So this discriminates. What it does NOT do
is say "drill here": it is a prior over ground, not a target, and it is reported
as one.

A grid is read through a window, never loaded: the Quebec compilation is 643
million cells and the modeller has a gigabyte.
"""
import os

import numpy as np

# Where a grid lives, per region. A region with no entry simply has no magnetics
# and everything here returns None rather than guessing.
GRIDS = {
    "quebec": {
        "crs": "EPSG:32198",
        "grid_m": 75,
        "products": {"residual": "qc_mag_residual.tif",
                     "dv1": "qc_mag_dv1.tif",
                     "analytic_signal": "qc_mag_analytic_signal.tif",
                     "tilt": "qc_mag_tilt.tif"},
        "source": "MRNF / Géologie Québec high-resolution aeromagnetic compilation",
    },
}
ROOT_ENV = "MMP_MAG_DIR"
DUMMY_BELOW = -1e30
MIN_COVER = 0.80


def _root():
    return os.environ.get(ROOT_ENV) or ""


def region_key(region):
    r = (region or "").strip().lower()
    if r in ("quebec", "québec", "qc"):
        return "quebec"
    return None


def available(region):
    """Is there a magnetic grid for this region, on this machine, right now?"""
    k = region_key(region)
    if not k or not _root():
        return False
    p = GRIDS[k]["products"]
    return all(os.path.exists(os.path.join(_root(), f)) for f in p.values())


def _window(path, crs, lat, lon, half_m, grid_m):
    import rasterio
    from rasterio.warp import transform as warp
    with rasterio.open(path) as d:
        x, y = warp("EPSG:4326", d.crs, [lon], [lat])
        r, c = d.index(x[0], y[0])
        half = int(round(half_m / grid_m))
        a = d.read(1, window=rasterio.windows.Window(c - half, r - half,
                                                     2 * half + 1, 2 * half + 1),
                   boundless=True, fill_value=np.nan)
    return np.where(a < DUMMY_BELOW, np.nan, a)


def sample(region, lat, lon, half_m=750):
    """The magnetic signature of one spot, or None where nothing was flown.

    `half_m` is the half-width of the window: 750 m (a 1.5 km box) is where the
    deposit-versus-decoy contrast measured strongest, and it widens sensibly —
    at 2.5 km the same contrast is about three quarters as large, which is what
    a signal local to the deposit should do.
    """
    k = region_key(region)
    if not k or not available(region):
        return None
    g = GRIDS[k]
    root, crs, gm = _root(), g["crs"], g["grid_m"]
    res = _window(os.path.join(root, g["products"]["residual"]), crs, lat, lon, half_m, gm)
    cover = float(np.isfinite(res).mean())
    if cover < MIN_COVER:
        return {"covered": cover, "surveyed": False, "source": g["source"]}
    def vals(name):
        a = _window(os.path.join(root, g["products"][name]), crs, lat, lon, half_m, gm)
        return a[np.isfinite(a)]
    r, a, v, t = res[np.isfinite(res)], vals("analytic_signal"), vals("dv1"), vals("tilt")
    if min(r.size, a.size, v.size, t.size) < 10:
        return {"covered": cover, "surveyed": False, "source": g["source"]}
    return {
        "surveyed": True,
        "covered": round(cover, 3),
        "window_m": half_m * 2,
        "source": g["source"],
        "residual_median_nT": round(float(np.median(r)), 1),
        "residual_relief_nT": round(float(np.percentile(r, 95) - np.percentile(r, 5)), 1),
        "analytic_signal_p90": round(float(np.percentile(a, 90)), 4),
        "gradient_abs_median": round(float(np.median(np.abs(v))), 4),
        "tilt_sd": round(float(t.std()), 4),
    }


# Decile breaks of each feature over the 40 measured Quebec projects' own
# neighbourhoods, so "is this ground magnetically active" is answered against
# measured ground rather than an invented threshold. Filled by
# scripts/mag_calibrate.py; empty means favourability is not reported.
CALIBRATION = {
    "quebec": {
        "residual_relief_nT": [
            27.07,
            46.2,
            62.2,
            84.56,
            108.45,
            145.8,
            206.32,
            310.1,
            627.48
        ],
        "analytic_signal_p90": [
            0.0734,
            0.1285,
            0.1838,
            0.248,
            0.3197,
            0.4167,
            0.5994,
            0.9291,
            1.947
        ],
        "gradient_abs_median": [
            0.0283,
            0.0453,
            0.0624,
            0.0798,
            0.0998,
            0.1274,
            0.1715,
            0.2697,
            0.5133
        ]
    }
}


def favourability(region, lat, lon, half_m=750):
    """How this ground compares with ground near known deposits, 0 to 1.

    A prior, not a target. Returns None where there is no calibration or no
    survey, because a number with nothing behind it is worse than a blank.
    """
    cal = CALIBRATION.get(region_key(region) or "")
    s = sample(region, lat, lon, half_m)
    if not cal or not s or not s.get("surveyed"):
        return None
    parts = []
    for k, breaks in cal.items():
        if k not in s:
            continue
        parts.append(float(np.searchsorted(np.asarray(breaks), s[k]) / (len(breaks) + 1)))
    if not parts:
        return None
    return {"score": round(float(np.mean(parts)), 3), "of": len(parts), "from": s}
