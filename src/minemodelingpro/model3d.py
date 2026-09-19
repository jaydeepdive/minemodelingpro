"""3D deposit models — one per project, from every source MMP holds.

Pipeline per project (see ``projects.assemble`` for how sources are unified):

  1. collars  — one local metre frame (x east, y north, z = masl). Collars without
     an elevation are draped on the real DEM; mine-grid elevations that disagree
     with the DEM by >150 m are replaced by the DEM so holes start at surface.
  2. intervals — per element, overlapping reports of the same hole are resolved
     into non-overlapping downhole SEGMENTS: an interval inside a longer one is its
     "including" and splits it (the parent's remainder grade is back-calculated so
     metal is conserved). These segments colour the drill traces by grade.
  3. composites — each assayed hole is composited at the block height; stretches
     between reported intercepts are below the reporting threshold and are
     composited as zero grade, which keeps grade from smearing through waste.
  4. block model — inverse-distance (power 2) on the technical report's own block
     size (parent block from the 43-101 when we hold one; otherwise the median
     43-101 block size for that commodity), constrained to within ~¾ of the drill
     spacing of real composites and informed by ≥2 holes.
  5. grade-tonnage — tonnes, grade and CONTAINED METAL above every cut-off, set
     beside the published resource from the project's own technical report.

The model page is a self-contained three.js viewer (``model3d_viewer.html``).
Grades are for visualisation, not a mineral resource estimate.
"""
import os
import re
import io
import json
import math
import base64
import datetime
from collections import Counter, defaultdict

import numpy as np

from minemodelingpro import projects as P
from minemodelingpro import holeid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_VIEWER_TMPL = os.path.join(_HERE, "model3d_viewer.html")

# ------------------------------------------------------------ element settings
BINS = {
    "Au": [0.1, 0.3, 0.5, 1, 2, 5, 10], "AuEq": [0.1, 0.3, 0.5, 1, 2, 5, 10],
    "Ag": [5, 15, 30, 60, 100, 200, 500], "AgEq": [5, 15, 30, 60, 100, 200, 500],
    "Cu": [0.05, 0.1, 0.2, 0.4, 0.7, 1, 2], "CuEq": [0.05, 0.1, 0.2, 0.4, 0.7, 1, 2],
    "Ni": [0.1, 0.2, 0.3, 0.5, 1, 2], "NiEq": [0.1, 0.2, 0.3, 0.5, 1, 2],
    "Zn": [0.25, 0.5, 1, 2, 4, 8], "ZnEq": [0.5, 1, 2, 4, 8, 12], "Pb": [0.25, 0.5, 1, 2, 4, 8],
    "Co": [0.02, 0.05, 0.1, 0.2, 0.4], "Mo": [0.01, 0.02, 0.05, 0.1, 0.2],
    "U3O8": [0.02, 0.05, 0.1, 0.5, 1, 5], "Li2O": [0.2, 0.5, 0.8, 1.2, 1.6, 2],
    "Pt": [0.1, 0.3, 0.5, 1, 2, 5], "Pd": [0.1, 0.3, 0.5, 1, 2, 5], "PGE": [0.1, 0.3, 0.5, 1, 2, 5],
    "3E": [0.1, 0.3, 0.5, 1, 2, 5], "PGM": [0.1, 0.3, 0.5, 1, 2, 5],
    "Sn": [0.1, 0.2, 0.5, 1, 2], "W": [0.05, 0.1, 0.2, 0.5, 1], "WO3": [0.05, 0.1, 0.2, 0.5, 1],
    "Sb": [0.5, 1, 2, 4, 8], "TREO": [0.2, 0.5, 1, 2, 5], "REO": [0.2, 0.5, 1, 2, 5],
    "Fe": [15, 20, 25, 30, 40], "Mn": [5, 10, 15, 20, 30], "V2O5": [0.2, 0.4, 0.6, 0.8, 1.2],
    "Cg": [2, 4, 8, 12, 20], "P2O5": [2, 4, 6, 10, 15],
}
PALETTE = ["#3b4cc0", "#2c9fd0", "#2fb38a", "#9bcf3c", "#f2cf1d", "#f39a1f", "#e0521b", "#b8123a", "#7a0b6e"]
ELEMENT_PRIORITY = ["Au", "Cu", "Ni", "Ag", "Zn", "U3O8", "Li2O", "Pb", "Co", "Mo", "Pt", "Pd", "PGE", "3E",
                    "Sn", "W", "WO3", "Sb", "TREO", "REO", "V2O5", "Fe", "Mn", "Cg", "P2O5",
                    "AuEq", "CuEq", "AgEq", "ZnEq", "NiEq"]
PRECIOUS = {"Au", "Ag", "Pt", "Pd", "AuEq", "AgEq", "PGE", "3E", "PGM"}
LB_METALS = {"Cu", "Ni", "Zn", "Pb", "Co", "Mo", "U3O8", "CuEq", "ZnEq", "NiEq", "Sn", "W", "WO3", "Sb", "V2O5"}
DENSITY = {"Zn": 3.0, "Pb": 3.0, "ZnEq": 3.0, "Ni": 2.9, "NiEq": 2.9, "Fe": 3.4, "Mn": 3.0, "Li2O": 2.7}
# typical 43-101 parent block when no report block is known (overridden by the median
# of extracted report block sizes for the commodity at build time)
DEFAULT_BLOCK = {"Au": [10, 10, 5], "Ag": [5, 5, 5], "Cu": [15, 15, 10], "Ni": [10, 10, 10],
                 "Zn": [5, 5, 5], "Pb": [5, 5, 5], "U3O8": [5, 5, 2.5], "Li2O": [5, 5, 5]}


def bins_for(el, grades):
    b = BINS.get(el)
    if b:
        return list(b)
    g = np.asarray([x for x in grades if x > 0], float)
    if len(g) < 5:
        return [0.1, 0.5, 1, 2, 5]
    q = np.unique(np.round(np.percentile(g, [20, 40, 60, 75, 90, 97]), 3))
    return [float(x) for x in q if x > 0] or [0.1, 0.5, 1]


def contained_unit(el, unit):
    if el in PRECIOUS or unit == "g/t":
        return "oz"
    if el in LB_METALS:
        return "lb"
    return "t"


def contained(tonnes, grade, el, unit):
    cu = contained_unit(el, unit)
    if cu == "oz":
        return tonnes * grade / 31.1034768
    if cu == "lb":
        return tonnes * grade / 100.0 * 2204.62262
    return tonnes * grade / 100.0


# ------------------------------------------------------------------ helpers
def _b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def _dir(az, dip):
    az = math.radians(az if az is not None else 0.0)
    dp = math.radians(dip if dip is not None else -90.0)
    return (math.cos(dp) * math.sin(az), math.cos(dp) * math.cos(az), math.sin(dp))


def _segments(ivs):
    """Resolve overlapping intervals of ONE hole/element into non-overlapping
    segments. Longest first; an interval inside an accepted segment is an
    'including' and splits it (parent remainder grade back-calculated)."""
    ivs = sorted(ivs, key=lambda x: (-(x["to"] - x["from"]), x["from"]))
    segs = []            # [from, to, grade]
    for x in ivs:
        f, t, g = float(x["from"]), float(x["to"]), float(x["grade"])
        if t <= f or g < 0:
            continue
        host = None
        overlap = False
        for s in segs:
            if f >= s[0] - 1e-6 and t <= s[1] + 1e-6:
                host = s
                break
            if f < s[1] and t > s[0]:
                overlap = True
        if host is not None:
            L, l = host[1] - host[0], t - f
            if L - l < 0.05:
                continue
            rem = max(0.0, (L * host[2] - l * g) / (L - l))
            segs.remove(host)
            if f - host[0] > 0.05:
                segs.append([host[0], f, rem])
            segs.append([f, t, g])
            if host[1] - t > 0.05:
                segs.append([t, host[1], rem])
        elif not overlap:
            segs.append([f, t, g])
    segs.sort()
    return segs


def _spacing(hole_centroids):
    c = np.asarray(hole_centroids, float)
    if len(c) < 2:
        return 60.0
    from scipy.spatial import cKDTree
    d, _ = cKDTree(c).query(c, k=2)
    nn = d[:, 1]
    nn = nn[nn > 0.5]
    return float(np.median(nn)) if len(nn) else 60.0


# ------------------------------------------------------------- block estimate
def estimate_blocks(pts, g, hid, block, spacing, max_cells=6_000_000):
    """IDW² on a regular grid of `block` (bx,by,bz) cells, only within max_gap of
    real composites and informed by >=2 holes. Returns (origin, block, ijk, grade,
    params)."""
    from scipy.spatial import cKDTree
    from scipy import ndimage
    bx, by, bz = [float(v) for v in block]
    bmax = max(bx, by, bz)
    radius = float(np.clip(spacing * 1.6, 4 * bmax, 160.0))
    max_gap = float(np.clip(spacing * 0.75, 1.6 * bmax, 70.0))
    lo = pts.min(0) - max_gap
    hi = pts.max(0) + max_gap
    dims = np.ceil((hi - lo) / [bx, by, bz]).astype(int) + 1
    coarsened = 1.0
    while dims.max() > 65000:                  # uint16 cell index limit (>300 km at 5 m)
        bx, by, bz = bx * 2, by * 2, bz * 2
        coarsened *= 2
        dims = np.ceil((hi - lo) / [bx, by, bz]).astype(int) + 1
    # coarse occupancy -> EDT to find candidate regions
    f = 1
    while (np.ceil(dims / f)).prod() > max_cells:
        f += 1
    cd = np.ceil(dims / f).astype(int)
    occ = np.zeros(cd, dtype=bool)
    ci = np.floor((pts - lo) / (np.array([bx, by, bz]) * f)).astype(int)
    ci = np.clip(ci, 0, cd - 1)
    occ[ci[:, 0], ci[:, 1], ci[:, 2]] = True
    dist = ndimage.distance_transform_edt(~occ, sampling=(bx * f, by * f, bz * f))
    cdiag = math.sqrt((bx * f) ** 2 + (by * f) ** 2 + (bz * f) ** 2)
    cand_c = np.argwhere(dist <= max_gap + cdiag)
    # expand to fine cells
    off = np.stack(np.meshgrid(np.arange(f), np.arange(f), np.arange(f), indexing="ij"), -1).reshape(-1, 3)
    tree = cKDTree(pts)
    ijk_out, g_out = [], []
    CH = max(1, 400_000 // len(off))
    for s in range(0, len(cand_c), CH):
        cc = cand_c[s:s + CH]
        fine = (cc[:, None, :] * f + off[None, :, :]).reshape(-1, 3)
        fine = fine[(fine < dims).all(1)]
        ctr = lo + (fine + 0.5) * [bx, by, bz]
        dd, ii = tree.query(ctr, k=16, distance_upper_bound=radius)
        near = np.isfinite(dd)
        keep = near[:, 0] & (dd[:, 0] <= max_gap) & (near.sum(1) >= 3)
        if not keep.any():
            continue
        dd, ii, fine = dd[keep], ii[keep], fine[keep]
        nearm = np.isfinite(dd)
        iis = np.where(nearm, ii, 0)
        hh = np.where(nearm, hid[iis], -1)
        # >= 2 distinct holes
        hs = np.sort(hh, axis=1)
        nuniq = (np.diff(hs, axis=1) != 0).sum(1) + 1 - (hs[:, 0] == -1)
        k2 = nuniq >= 2
        if not k2.any():
            continue
        dd, iis, nearm, fine = dd[k2], iis[k2], nearm[k2], fine[k2]
        w = np.where(nearm, 1.0 / np.maximum(dd, 0.5) ** 2, 0.0)
        est = (w * g[iis]).sum(1) / w.sum(1)
        ijk_out.append(fine.astype(np.int32)); g_out.append(est.astype(np.float32))
    if ijk_out:
        ijk = np.concatenate(ijk_out); gr = np.concatenate(g_out)
    else:
        ijk = np.zeros((0, 3), np.int32); gr = np.zeros(0, np.float32)
    params = {"block": [bx, by, bz], "radius_m": round(radius, 1), "max_gap_m": round(max_gap, 1),
              "spacing_m": round(spacing, 1), "power": 2, "min_holes": 2, "min_samples": 3,
              "coarsened": coarsened}
    return lo, (bx, by, bz), ijk, gr, params


# ---------------------------------------------------------------- build model
_REPORT_BLOCKS = None


def _median_blocks():
    """Median 43-101 parent block size by commodity, from every extracted report."""
    global _REPORT_BLOCKS
    if _REPORT_BLOCKS is not None:
        return _REPORT_BLOCKS
    by = defaultdict(list)
    for r in P.load_reports():
        b = r.get("block_size")
        el = (r.get("commodity") or "").split("-")[0]
        if b and el and all(2 <= v <= 30 for v in b):
            by[el].append(b)
    out = {}
    for el, bl in by.items():
        if len(bl) >= 3:
            a = np.median(np.asarray(bl, float), 0)
            out[el] = [float(_snap(v)) for v in a]
    _REPORT_BLOCKS = out
    return out


def _snap(v):
    std = [2, 2.5, 3, 4, 5, 6, 6.25, 8, 10, 12, 12.5, 15, 20, 25]
    return min(std, key=lambda s: abs(s - v))


def build_project(ds, max_elements=3, verbose=False):
    fr = ds["frame"]
    holes = [h for h in ds["holes"].values() if h.get("_ok")]
    if not holes:
        raise ValueError("no located holes")
    key2i = {h["key"]: i for i, h in enumerate(holes)}
    # --- intervals by element (only those on located holes)
    by_el = defaultdict(list)
    units = {}
    for x in ds["intervals"]:
        if x["key"] in key2i and x.get("el"):
            by_el[x["el"]].append(x)
            units.setdefault(x["el"], x.get("unit") or ("g/t" if x["el"] in PRECIOUS else "%"))
    if not by_el:
        raise ValueError("no intervals on located holes")
    # primary element: the published resource's commodity when known, else most data
    res_unit = next((r.get("contained_unit") for r in ds.get("resources") or []), None)
    res_el = next((r.get("element") for r in ds.get("resources") or [] if r.get("element")), None)

    def score(el):
        n = len({x["key"] for x in by_el[el]})
        pri = ELEMENT_PRIORITY.index(el) if el in ELEMENT_PRIORITY else 50
        bonus = 0
        if res_unit == "oz" and el in ("Au", "Ag"):
            bonus = 1.5
        if res_unit == "lb" and el in LB_METALS:
            bonus = 1.3
        if res_el and el == res_el:
            bonus = 2.5
        elif el in (ds.get("commodities") or []):
            bonus = max(bonus, 2.0)
        if el.endswith("Eq"):
            bonus = 0.6
        return n * (bonus or 1.0) - pri * 0.01
    els = sorted([e for e in by_el if len(by_el[e]) >= 4], key=score, reverse=True)[:max_elements]
    if not els:
        els = sorted(by_el, key=score, reverse=True)[:1]

    # --- collar elevations / depths
    geo = fr["kind"] == "geo"
    lat0, lon0 = fr.get("lat0"), fr.get("lon0")
    dem_z = [None] * len(holes)
    if geo:
        try:
            from minemodelingpro import terrain
            lats = [lat0 + h["y"] / fr["mN"] for h in holes]
            lons = [lon0 + h["x"] / fr["mE"] for h in holes]
            span = max(np.ptp([h["x"] for h in holes]), np.ptp([h["y"] for h in holes]), 200)
            zz = terrain.zoom_for(max(span / 150, 10), lat0)
            dz = terrain.elevations(lats, lons, max(zz, 13))
            dem_z = [None if not np.isfinite(v) else float(v) for v in dz]
        except Exception as e:
            if verbose:
                print("  dem collars failed", e)
    max_to = defaultdict(float)
    for el in by_el:
        for x in by_el[el]:
            max_to[x["key"]] = max(max_to[x["key"]], float(x["to"]))
    zs_known = [h.get("z") for h in holes if h.get("z") is not None]
    zfallback = float(np.median(zs_known)) if zs_known else 0.0
    H = []
    for i, h in enumerate(holes):
        z = h.get("z")
        dz = dem_z[i]
        if dz is not None and (z is None or abs(z - dz) > 150):
            z = dz
        if z is None:
            z = zfallback
        mt = max_to.get(h["key"], 0.0)
        d = h.get("depth")
        if d is None or d > 3000 or d < mt or (mt > 0 and d > mt * 6 and d > 800):
            d = mt * 1.04 + 2 if mt > 0 else (d if d and d <= 3000 else 0.0)
        dip = h.get("dip")
        if dip is not None and dip > 0:
            dip = -dip
        H.append({"id": str(h["id"]), "x": float(h["x"]), "y": float(h["y"]), "z": float(z),
                  "az": h.get("az") if h.get("az") is not None else 0.0,
                  "dip": dip if dip is not None else -90.0, "depth": float(d), "key": h["key"],
                  "src": sorted(h.get("src") or [])})
    # --- recentre the frame on the drilling
    cx = float(np.median([h["x"] for h in H])); cy = float(np.median([h["y"] for h in H]))
    for h in H:
        h["x"] -= cx; h["y"] -= cy
    if geo:
        lat0 = lat0 + cy / fr["mN"]; lon0 = lon0 + cx / fr["mE"]

    # --- per element: segments, composites, blocks, GT curve
    elements = {}
    report_block = ds.get("report_block")
    med_blocks = _median_blocks()
    for el in els:
        unit = units.get(el) or "g/t"
        per_hole = defaultdict(list)
        for x in by_el[el]:
            per_hole[key2i[x["key"]]].append(x)
        segs_out = []          # [hole_idx, from, to, grade]
        comp_p, comp_g, comp_h = [], [], []
        # block size: the report's parent block, else the commodity median
        blk = report_block or med_blocks.get(el) or DEFAULT_BLOCK.get(el) or [10, 10, 5]
        blk = [float(v) for v in blk]
        clen = max(1.0, min(blk[2], 5.0))
        cent = []
        for hi, ivs in per_hole.items():
            segs = _segments(ivs)
            if not segs:
                continue
            h = H[hi]
            d = _dir(h["az"], h["dip"])
            depth = max(h["depth"], segs[-1][1])
            for s in segs:
                segs_out.append([hi, round(s[0], 2), round(s[1], 2), round(s[2], 4)])
            # composites along the full hole; gaps between intercepts = 0 grade
            n = max(1, int(math.ceil(depth / clen)))
            mids = (np.arange(n) + 0.5) * clen
            mids = mids[mids < depth]
            if len(mids) == 0:
                mids = np.array([depth / 2.0])
            gg = np.zeros(len(mids))
            for s in segs:
                m = (mids >= s[0]) & (mids < s[1])
                gg[m] = s[2]
                if not m.any():               # a segment shorter than a composite
                    mid = (s[0] + s[1]) / 2
                    j = min(len(mids) - 1, int(mid // clen))
                    gg[j] = max(gg[j], s[2] * (s[1] - s[0]) / clen)
            xyz = np.stack([h["x"] + mids * d[0], h["y"] + mids * d[1], h["z"] + mids * d[2]], 1)
            comp_p.append(xyz); comp_g.append(gg); comp_h.append(np.full(len(mids), hi))
            pos = gg > 0
            if pos.any():
                cent.append(xyz[pos].mean(0))
        if not segs_out:
            continue
        grades = [s[3] for s in segs_out]
        bins = bins_for(el, grades)
        el_out = {"unit": unit, "bins": bins, "colors": PALETTE[:len(bins) + 1][-(len(bins) + 1):]
                  if len(bins) + 1 <= len(PALETTE) else PALETTE,
                  "segments": segs_out, "n_holes": len(per_hole),
                  "stats": {"max": round(float(np.max(grades)), 3),
                            "mean": round(float(np.mean(grades)), 3),
                            "p50": round(float(np.median(grades)), 3)}}
        # colours: map bins onto the palette evenly (low = blue, high = magenta)
        nb = len(bins)
        idx = np.linspace(0, len(PALETTE) - 1, nb).round().astype(int)
        el_out["colors"] = [PALETTE[i] for i in idx]
        n_assayed_holes = len([c for c in comp_p if len(c)])
        if n_assayed_holes >= 4 and len(cent) >= 3:
            pts = np.concatenate(comp_p); g = np.concatenate(comp_g); hid = np.concatenate(comp_h)
            top = float(np.percentile(g[g > 0], 99.5)) if (g > 0).sum() > 20 else float(g.max())
            g = np.minimum(g, top)
            spacing = _spacing(cent)
            lo, bsz, ijk, gr, params = estimate_blocks(pts, g, hid, blk, spacing)
            params["top_cut"] = round(top, 3)
            params["composite_m"] = clen
            params["block_source"] = ("technical report" if report_block else
                                      "median 43-101 block for " + el if med_blocks.get(el) else "default")
            dens = ds.get("report_density")
            if not dens or not (2.3 <= dens <= 3.6):
                dens = DENSITY.get(el, 2.7)
            bt = bsz[0] * bsz[1] * bsz[2] * dens
            # grade-tonnage curve over all estimated blocks
            gmax = float(np.percentile(gr, 99.5)) if len(gr) else 0.0
            cuts = sorted(set([0.0] + [round(b, 4) for b in bins] +
                              list(np.round(np.linspace(0, max(gmax, bins[-1]), 48), 4))))
            gt = []
            for c in cuts:
                m = gr >= c if c > 0 else gr > 0
                t = float(m.sum()) * bt
                ag = float(gr[m].mean()) if m.any() else 0.0
                gt.append([round(c, 4), round(t), round(ag, 4), round(contained(t, ag, el, unit))])
            # ship only blocks at/above the lowest bin (cap count for the browser)
            show = bins[0]
            keep = gr >= show
            cap = 110_000 if el == els[0] else 35_000
            while keep.sum() > cap:
                show *= 1.25
                keep = gr >= show
            el_out["blocks"] = {"origin": [round(float(v), 2) for v in lo], "size": list(bsz),
                                "ijk": _b64(ijk[keep].astype(np.uint16)), "g": _b64(gr[keep].astype(np.float32)),
                                "n": int(keep.sum()), "n_total": int(len(gr)), "shown_from": round(show, 4),
                                "tonnes_per_block": round(bt, 1), "density": dens, "params": params}
            el_out["gt"] = gt
            el_out["contained_unit"] = contained_unit(el, unit)
        elements[el] = el_out
    if not elements:
        raise ValueError("no modelable element")
    primary = els[0] if els[0] in elements else next(iter(elements))

    # --- terrain over a padded footprint
    terr = None
    xs = [h["x"] for h in H]; ys = [h["y"] for h in H]
    if geo:
        try:
            from minemodelingpro import terrain
            w = max(max(xs) - min(xs), max(ys) - min(ys), 300.0)
            pad = max(400.0, 0.35 * w)
            t = terrain.grid(lat0, lon0, min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad, n_max=200)
            if t:
                terr = {k: v for k, v in t.items() if k != "z"}
                terr["z"] = _b64(t["z"].astype(np.float32))
        except Exception as e:
            if verbose:
                print("  terrain failed", e)
    region = ds.get("region")
    if geo:
        try:
            from minemodelingpro import terrain as _t
            region = _t.region_of(lat0, lon0) or region
        except Exception:
            pass
    hole_rows = [[h["id"], round(h["x"], 2), round(h["y"], 2), round(h["z"], 2), round(h["az"], 1),
                  round(h["dip"], 1), round(h["depth"], 1)] for h in H]
    return {
        "slug": ds["slug"], "project": ds["name"], "company": ds.get("company"), "region": region,
        "kinds": ds.get("kinds"), "updated": ds.get("updated"),
        "generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "frame": {"kind": fr["kind"], "lat0": round(lat0, 6) if geo else None, "lon0": round(lon0, 6) if geo else None,
                  "grid": fr.get("grid")},
        "holes": hole_rows, "elements": elements, "primary": primary,
        "terrain": terr, "sources": ds["sources"], "resources": ds.get("resources") or [],
        "counts": {"holes": len(H), "assayed_holes": len({s[0] for e in elements.values() for s in e["segments"]}),
                   "intervals": sum(len(e["segments"]) for e in elements.values()),
                   "blocks": (elements[primary].get("blocks") or {}).get("n_total", 0),
                   "reports": sum(1 for s in ds["sources"] if s["kind"].startswith("NI")),
                   "releases": sum(1 for s in ds["sources"] if s["kind"] == "news release")},
    }


def write_viewer(model, out_html):
    tmpl = open(_VIEWER_TMPL).read()
    title = model["project"] + (" — " + model["company"] if model.get("company") else "")
    html = (tmpl.replace("__TITLE__", _esc(title))
                .replace("__PROJECT__", _esc(model["project"]))
                .replace("__MODEL_JSON__", json.dumps(model, separators=(",", ":")).replace("</", "<\\/")))
    os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
    open(out_html, "w").write(html)
    return out_html


def _esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------- build all
def build_all(site_dir="site", only=None, verbose=True):
    """One model page per PROJECT (all sources merged), the gallery, and
    models_index.json (Closeology's drill radar deep-links release URLs to it)."""
    out_dir = os.path.join(site_dir, "models")
    os.makedirs(out_dir, exist_ok=True)
    site_base = os.environ.get("MMP_SITE_URL", "https://jaydeepdive.github.io/minemodelingpro/")
    if not site_base.endswith("/"):
        site_base += "/"
    datasets = P.assemble(verbose=verbose)
    if only:
        datasets = [d for d in datasets if any(o in d["slug"] for o in only)]
    cards, index = [], []
    built = set()
    for ds in datasets:
        try:
            m = build_project(ds, verbose=verbose)
        except Exception as ex:
            if verbose:
                print(f"[model3d] skip {ds['slug']}: {str(ex)[:100]}")
            continue
        slug = m["slug"]
        write_viewer(m, os.path.join(out_dir, slug + ".html"))
        built.add(slug + ".html")
        pe = m["elements"][m["primary"]]
        cu = pe.get("contained_unit") or contained_unit(m["primary"], pe["unit"])
        best_res = next((r for r in m["resources"] if r.get("contained_unit") == cu and r.get("contained")
                         and (not r.get("element") or r["element"] == m["primary"].replace("Eq", ""))), None)
        card = {"slug": slug, "project": m["project"], "company": m.get("company"), "region": m.get("region"),
                "element": m["primary"], "elements": list(m["elements"].keys()), "unit": pe["unit"],
                "counts": m["counts"], "updated": m.get("updated"), "kinds": m.get("kinds"),
                "max": pe["stats"]["max"], "terrain": bool(m.get("terrain")),
                "resource": ({"contained": best_res.get("contained"), "unit": best_res.get("contained_unit")}
                             if best_res else None)}
        cards.append(card)
        e = {"slug": slug, "url": site_base + "models/" + slug + ".html", "project": m["project"],
             "company": m.get("company"), "region": m.get("region"), "element": m["primary"],
             "updated": m.get("updated"), "source": "+".join(m.get("kinds") or []),
             "holes": m["counts"]["holes"], "samples": m["counts"]["intervals"],
             "releases": [s["url"] for s in m["sources"] if s["kind"] == "news release" and s.get("url")]}
        if m["frame"].get("lat0") is not None:
            e["center"] = [m["frame"]["lat0"], m["frame"]["lon0"]]
        index.append(e)
        if verbose:
            print(f"[model3d] {slug}: {m['counts']['holes']}h {m['counts']['intervals']}iv "
                  f"{m['counts']['blocks']}blk {m['primary']} kinds={m.get('kinds')} terrain={'y' if m.get('terrain') else 'n'}")
    # remove stale model pages (old per-source pages) — keep the three.js runtime
    if not only:
        for f in os.listdir(out_dir):
            if f.endswith(".html") and f not in built:
                os.remove(os.path.join(out_dir, f))
    _write_gallery(cards, os.path.join(site_dir, "models.html"))
    by_release = {}
    for e in index:
        for u in e.get("releases") or []:
            by_release[u] = e["url"]
    if not only:
        json.dump({"generated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
                   "site": site_base, "count": len(index), "models": index, "by_release": by_release},
                  open(os.path.join(site_dir, "models_index.json"), "w"), separators=(",", ":"))
    if verbose:
        print(f"[model3d] built {len(cards)} project model(s); {len(by_release)} release links")
    return cards


def _write_gallery(cards, out_html):
    try:
        import site_theme as T
        head, foot, css, fonts = (T.header("models.html"), T.footer(), T.THEME_CSS, T.FONTS)
    except Exception:
        head = foot = fonts = ""
        css = ""
    data = json.dumps(sorted(cards, key=lambda c: (c["project"] or "").lower()), separators=(",", ":"))
    tmpl = open(os.path.join(_HERE, "gallery.html")).read()
    html = (tmpl.replace("__FONTS__", fonts).replace("__THEME_CSS__", css).replace("__HEADER__", head)
                .replace("__FOOTER__", foot).replace("__CARDS_JSON__", data.replace("</", "<\\/"))
                .replace("__N__", str(len(cards))))
    open(out_html, "w").write(html)


if __name__ == "__main__":
    import sys
    only = sys.argv[1:] or None
    build_all("site", only=only)
