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
import sys
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
from minemodelingpro import params as PARAMS
from minemodelingpro import regional as REG

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


def min_curve(stations, depth):
    """Minimum-curvature desurvey. stations: [(md, az, dip)] sorted, dip negative
    down. Returns (md[], dx[], dy[], dz[]) offsets from the collar (x east, y
    north, z up), extended to `depth` on the last orientation."""
    st = [s for s in stations if s[0] is not None]
    if not st:
        return None
    if st[0][0] > 0:
        st = [(0.0, st[0][1], st[0][2])] + st
    if depth > st[-1][0]:
        st = st + [(float(depth), st[-1][1], st[-1][2])]
    md = [0.0]; x = [0.0]; y = [0.0]; z = [0.0]
    for (m1, a1, d1), (m2, a2, d2) in zip(st, st[1:]):
        L = m2 - m1
        if L <= 0:
            continue
        i1, i2 = math.radians(90 + d1), math.radians(90 + d2)      # inclination from vertical
        A1, A2 = math.radians(a1), math.radians(a2)
        cb = math.cos(i2 - i1) - math.sin(i1) * math.sin(i2) * (1 - math.cos(A2 - A1))
        b = math.acos(max(-1.0, min(1.0, cb)))
        rf = 1.0 if b < 1e-6 else 2 / b * math.tan(b / 2)
        dn = L / 2 * (math.sin(i1) * math.cos(A1) + math.sin(i2) * math.cos(A2)) * rf
        de = L / 2 * (math.sin(i1) * math.sin(A1) + math.sin(i2) * math.sin(A2)) * rf
        dv = L / 2 * (math.cos(i1) + math.cos(i2)) * rf
        md.append(m2); x.append(x[-1] + de); y.append(y[-1] + dn); z.append(z[-1] - dv)
    return np.array(md), np.array(x), np.array(y), np.array(z)


def _segments(ivs, max_grade=None):
    """Resolve overlapping intervals of ONE hole/element into non-overlapping
    segments. Longest first; an interval inside an accepted segment is an
    'including' and splits it (parent remainder grade back-calculated).

    The back-calculation is where impossible grades are manufactured. When the
    'including' interval is nearly the whole of its host — 9.9 m of a 10 m intercept
    — the remainder is a tenth of a metre carrying all of the host's remaining
    metal, and the arithmetic hands back 50% copper off two perfectly ordinary
    numbers. So the remainder has to be long enough to mean something before it is
    trusted, and it is capped at what the element can physically reach. These were
    the last impossible grades left in the gallery after the unit repairs."""
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
            # a sliver of remainder cannot carry a meaningful back-calculated grade
            if L - l < max(0.25, 0.08 * L):
                segs.remove(host)
                segs.append([host[0], host[1], g] if l / L > 0.8 else host)
                continue
            rem = max(0.0, (L * host[2] - l * g) / (L - l))
            if max_grade and rem > max_grade:
                rem = min(rem, float(max_grade))
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
# How confidently a block was estimated, in the words a resource statement uses.
# These are MMP's own confidence tiers on public data, not CIM categories assigned
# by a qualified person — the model page says so.
CLASSES = ("well drilled", "moderately drilled", "thinly drilled")


def _limit_per_hole(hid_cols, near, max_per_hole):
    """Keep at most `max_per_hole` samples from any one drill hole.

    Without this an inverse-distance estimate is not really inverse-distance: a
    single densely sampled hole can supply every one of the nearest neighbours, and
    the block simply inherits that hole's grade while a second hole fifteen metres
    away counts for nothing. It is the practical form of the declustering the CIM
    guidelines ask for — clustered data must not be allowed to carry weight in
    proportion to how clustered it is. Columns arrive sorted by distance, so the
    samples kept are the closest ones from each hole."""
    keep = near.copy()
    k = hid_cols.shape[1]
    for j in range(1, k):
        seen = np.zeros(len(hid_cols), np.int16)
        for i in range(j):
            seen += (keep[:, i] & (hid_cols[:, i] == hid_cols[:, j])).astype(np.int16)
        keep[:, j] &= seen < max_per_hole
    return keep


def _passes(radius, max_gap, min_holes, min_samples, given=None):
    """Three estimation passes of decreasing confidence, inside one envelope.

    A single pass with one rule gives every block the same standing, which is both
    less honest and less complete than what a resource estimate does. P&E estimate
    Murray Brook in three passes — tight and sample-hungry first, then progressively
    longer with fewer samples required — and classify each block by the pass that
    reached it. Requiring two holes everywhere, as MMP did, also silently drops the
    blocks that only one hole can speak for: real rock, worth showing, worth
    labelling as thinly drilled rather than leaving out.

    The last pass keeps today's envelope (``max_gap``), so nothing is estimated
    further from the drilling than before — the passes only tier what is inside it."""
    if given:
        return [(float(r), float(gp), int(mh), int(ms)) for r, gp, mh, ms in given][:3]
    # Tiers are decided by HOW MANY HOLES see the block and how close they are, not
    # by how many composites fall inside the radius. Sample counts measure how much
    # assay detail a filing happened to print — a report publishing significant
    # intercepts gives barely one interval per hole — so a sample-count rule grades
    # MMP's paperwork rather than the deposit's drilling. Hole count and distance are
    # properties of the drill pattern itself, which is what classification turns on:
    # P&E's own measured rule at Murray Brook is four holes at under 25 m spacing.
    # All three passes count holes over the same search radius — roughly 1.6 x the
    # drill spacing — and differ in how many of them must see the block. Tiering by a
    # shrinking radius instead was self-defeating: the envelope is already about
    # three quarters of the drill spacing, so "within half of that" is closer than
    # any second hole ever gets, and the top tier stayed empty no matter how the
    # sample counts moved.
    return [(radius, max_gap * 0.75, max(3, min_holes + 1), 3),
            (radius, max_gap, max(2, min_holes), 2),
            (radius, max_gap, 1, 2)]
def frame_from_trend(dip, dip_direction, plunge=0.0):
    """The body's own axes from a stated orientation, as rows (major, intermediate,
    across).

    This is the structural trend a geologist states rather than one inferred from
    the samples: dip, the azimuth it dips towards, and the rotation of the long
    axis within that plane. Principal components of the mineralised cloud are a
    good stand-in when a body is well sampled and a poor one when it is not — a
    handful of holes on one section have no shape to measure, and the trend they
    imply is the drill pattern's, not the deposit's.

    East-north-up, with the across-thickness axis last because that is the one the
    wireframe inflates along."""
    import math
    th = math.radians(float(dip_direction) % 360.0)
    dl = math.radians(min(max(float(dip), 0.0), 90.0))
    pl = math.radians(min(max(float(plunge or 0.0), -90.0), 90.0))
    # down-dip, strike, and the normal to the plane
    down = np.array([math.sin(th) * math.cos(dl), math.cos(th) * math.cos(dl),
                     -math.sin(dl)])
    strike = np.array([-math.cos(th), math.sin(th), 0.0])
    nrm = np.cross(strike, down)
    for v in (down, strike, nrm):
        n = np.linalg.norm(v)
        if n > 0:
            v /= n
    major = math.cos(pl) * strike + math.sin(pl) * down
    inter = -math.sin(pl) * strike + math.cos(pl) * down
    major /= np.linalg.norm(major)
    inter /= np.linalg.norm(inter)
    nrm = np.cross(major, inter)
    nrm /= np.linalg.norm(nrm)
    return np.vstack([major, inter, nrm])


def anisotropy(pts, g, ratios, trend=None):
    """The deposit's own frame, and the search ellipsoid in it.

    A vein is continuous along strike and down dip and stops abruptly across it. An
    isotropic search cannot express that: any reach long enough to follow the vein
    also bridges to the next one through barren rock, which is how a 1.4 m vein
    system came to be modelled over ninety-five times the volume of its filed
    resource. So the mineralised composites are given their own axes — principal
    components of the above-zero cloud, longest first — and the search is stretched
    along them by the style's ratios.

    Returns a matrix that maps world coordinates into a space where the search
    ellipsoid is the unit sphere, or None when the cloud has no usable shape."""
    try:
        a0 = np.asarray(ratios, float) if ratios is not None else None
        if trend and trend.get("dip") is not None and trend.get("dip_direction") is not None:
            # stated by a person who has seen the rock; it wins over the samples
            if a0 is None or a0.min() <= 0:
                return None, None
            return frame_from_trend(trend["dip"], trend["dip_direction"],
                                    trend.get("plunge") or 0.0), a0
        P = np.asarray(pts, float)[np.asarray(g, float) > 0]
        if len(P) < 12:
            return None, None
        P = P - P.mean(0)
        _u, sv, vt = np.linalg.svd(P, full_matrices=False)
        if sv[0] <= 0 or not np.isfinite(sv).all():
            return None, None
        a = np.asarray(ratios, float)
        if a.min() <= 0:
            return None, None
        # rows of vt are the body's axes, longest first; scale each by its ratio
        return vt, a
    except Exception:
        return None, None


def estimate_blocks(pts, g, hid, block, spacing, max_cells=6_000_000, opts=None,
                    domain_pts=None, domain_radius=None, domain_reach=None):
    """Grade estimate on a regular grid of `block` (bx,by,bz) cells, only within
    max_gap of real composites and informed by >= min_holes holes. Returns
    (origin, block, ijk, grade, params).

    `opts` (per-project settings) can set radius_m, max_gap_m, min_holes,
    min_samples, power and method ("idw" | "nn" | "mean"); anything absent is
    derived from the drill spacing as before."""
    from scipy.spatial import cKDTree
    from scipy import ndimage
    o = opts or {}
    bx, by, bz = [float(v) for v in block]
    bmax = max(bx, by, bz)
    radius = float(o.get("radius_m") or np.clip(spacing * 1.6, 4 * bmax, 160.0))
    max_gap = float(o.get("max_gap_m") or np.clip(spacing * 0.75, 1.6 * bmax, 70.0))
    min_holes = int(o.get("min_holes") or 2)
    min_samples = int(o.get("min_samples") or 3)
    power = float(o.get("power") if o.get("power") is not None else 2.0)
    method = o.get("method") or "idw"
    max_per_hole = int(o.get("max_per_hole") or 4)
    passes = _passes(radius, max_gap, min_holes, min_samples, o.get("passes"))
    # the deposit's own frame: searching a vein in world axes bridges between veins
    rot, aniso = (None, None)
    if o.get("anisotropy"):
        rot, aniso = anisotropy(pts, g, o["anisotropy"], trend=o.get("trend"))
    # The deposit is where the skeleton says it is; the composites say what grade it
    # carries. Keeping the two apart is what lets a block between two holes be
    # estimated at all — before this every block had to sit within a search distance
    # of a sample, so the model was a string of bubbles around intercepts and, on
    # sparsely published projects, covered a twentieth of the deposit.
    dom = np.asarray(domain_pts, float) if domain_pts is not None and len(domain_pts) else None
    dr = float(domain_radius or max_gap)
    # The skeleton ADDS reach; it must never take any away. Seeding the candidate
    # search from the skeleton alone shrank every model — a skeleton is a handful of
    # intercept centroids and the links between them, where the composites are
    # thousands of points — and the measured error went from 0.54 to 0.11. The
    # deposit is the union: everything near real data, plus everything inside the
    # body the data describes.
    seeds = np.concatenate([pts, dom]) if dom is not None else pts
    seed_reach = max(max_gap, dr) if dom is not None else max_gap
    # How far to look for composites when a block is inside the body. Membership
    # alone changed nothing, because the binding constraint was never membership —
    # a block halfway between two holes eighty metres apart has no sample inside a
    # thirty-seven metre search however certain we are that it is ore. To estimate
    # the middle of a link the search has to reach the ends of it.
    d_reach = float(domain_reach or max(radius, dr))
    pad = max(max_gap, dr)
    lo = np.minimum(pts.min(0), seeds.min(0)) - pad
    hi = np.maximum(pts.max(0), seeds.max(0)) + pad
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
    ci = np.floor((seeds - lo) / (np.array([bx, by, bz]) * f)).astype(int)
    ci = np.clip(ci, 0, cd - 1)
    occ[ci[:, 0], ci[:, 1], ci[:, 2]] = True
    dist = ndimage.distance_transform_edt(~occ, sampling=(bx * f, by * f, bz * f))
    cdiag = math.sqrt((bx * f) ** 2 + (by * f) ** 2 + (bz * f) ** 2)
    cand_c = np.argwhere(dist <= seed_reach + cdiag)
    # How many fine blocks those candidate regions could hold. A property drilled
    # over twenty kilometres on a three-metre block is tens of millions of blocks,
    # each carrying a grade, a class and an index — enough to exhaust the machine
    # and take the whole build down with it, which is what it did. Coarsening the
    # block is the honest response: a model of the right extent at a coarser block
    # beats no model at all, and every page says what block it used.
    MAXB = int(o.get("max_blocks") or 4_000_000)
    while len(cand_c) * (f ** 3) > MAXB and max(bx, by, bz) < 400.0:
        bx, by, bz = bx * 1.6, by * 1.6, bz * 1.6
        coarsened *= 1.6
        dims = np.ceil((hi - lo) / [bx, by, bz]).astype(int) + 1
        f = 1
        while (np.ceil(dims / f)).prod() > max_cells:
            f += 1
        cd = np.ceil(dims / f).astype(int)
        occ = np.zeros(cd, dtype=bool)
        ci = np.floor((seeds - lo) / (np.array([bx, by, bz]) * f)).astype(int)
        ci = np.clip(ci, 0, cd - 1)
        occ[ci[:, 0], ci[:, 1], ci[:, 2]] = True
        dist = ndimage.distance_transform_edt(~occ, sampling=(bx * f, by * f, bz * f))
        cdiag = math.sqrt((bx * f) ** 2 + (by * f) ** 2 + (bz * f) ** 2)
        cand_c = np.argwhere(dist <= seed_reach + cdiag)
    # expand to fine cells
    off = np.stack(np.meshgrid(np.arange(f), np.arange(f), np.arange(f), indexing="ij"), -1).reshape(-1, 3)
    if rot is not None:
        # map into the space where the search ellipsoid is a sphere of radius `radius`
        W = (rot.T / aniso) @ rot          # symmetric: world -> search space
        tree = cKDTree(pts @ W.T)
    else:
        W = None
        tree = cKDTree(pts)
    dtree = cKDTree(dom @ W.T if (dom is not None and W is not None) else dom) \
        if dom is not None else None
    ijk_out, g_out, c_out = [], [], []
    # Blocks are estimated in chunks, and the chunk is sized by the arrays the
    # search builds, not by the block count: every block carries its 24 nearest
    # samples, so a chunk costs 24 numbers per block several times over. At
    # 400,000 blocks a chunk that was half a gigabyte in flight on a deposit that
    # otherwise fits in memory — the modeller has a gigabyte, and the kernel
    # kills it without a word, which reads from the page as a job that never ends.
    CH = max(1, int(os.environ.get("MMP_BLOCK_CHUNK", "60000")) // len(off))
    for s in range(0, len(cand_c), CH):
        cc = cand_c[s:s + CH]
        fine = (cc[:, None, :] * f + off[None, :, :]).reshape(-1, 3)
        fine = fine[(fine < dims).all(1)]
        ctr = lo + (fine + 0.5) * [bx, by, bz]
        in_domain = None
        if dom is not None:
            dsd, _di = dtree.query(ctr @ W.T if W is not None else ctr, k=1,
                                   distance_upper_bound=dr)
            in_domain = np.isfinite(dsd)
        # the last pass reaches further for its samples, because inside the body the
        # nearest hole may be most of a drill spacing away
        dd, ii = tree.query(ctr @ W.T if W is not None else ctr, k=24,
                            distance_upper_bound=max(radius, d_reach) if dom is not None else radius)
        near = np.isfinite(dd)
        iis0 = np.where(near, ii, 0)
        hh = np.where(near, hid[iis0], -1)
        near = _limit_per_hole(hh, near, max_per_hole)
        hh = np.where(near, hh, -1)
        dd = np.where(near, dd, np.inf)
        hs = np.sort(hh, axis=1)
        nuniq = (np.diff(hs, axis=1) != 0).sum(1) + 1 - (hs[:, 0] == -1)
        nsamp = near.sum(1)
        cls = np.zeros(len(fine), np.uint8)
        for pi, (p_rad, p_gap, p_holes, p_samp) in enumerate(passes, 1):
            if (cls > 0).all():
                break
            ok = ((cls == 0) & near[:, 0] & (dd[:, 0] <= p_gap)
                  & (nsamp >= p_samp) & (nuniq >= p_holes))
            if dom is not None and pi == len(passes):
                # the last pass also takes the body itself: inside the skeleton a
                # block is real rock however far the nearest sample happens to be,
                # and the tier records that it is thinly drilled. OR, not AND —
                # this pass widens the deposit, it does not narrow it.
                ok |= ((cls == 0) & in_domain & near[:, 0]
                       & (dd[:, 0] <= d_reach)
                       & (nsamp >= p_samp) & (nuniq >= p_holes))
            # a pass only counts samples inside its own, shorter range
            if p_rad < radius:
                ok &= (np.where(near, dd, np.inf) <= p_rad).sum(1) >= p_samp
            cls[ok] = pi
        keep = cls > 0
        if not keep.any():
            continue
        dd, ii, fine, cls = dd[keep], ii[keep], fine[keep], cls[keep]
        nearm = np.isfinite(dd)
        iis = np.where(nearm, ii, 0)
        if method == "nn":
            est = g[iis[:, 0]]
        elif method == "mean":
            w = nearm.astype(float)
            est = (w * g[iis]).sum(1) / w.sum(1)
        else:
            w = np.where(nearm, 1.0 / np.maximum(dd, 0.5) ** power, 0.0)
            est = (w * g[iis]).sum(1) / w.sum(1)
        ijk_out.append(fine.astype(np.int32)); g_out.append(est.astype(np.float32))
        c_out.append(cls)
    if ijk_out:
        ijk = np.concatenate(ijk_out); gr = np.concatenate(g_out)
        cl = np.concatenate(c_out)
    else:
        ijk = np.zeros((0, 3), np.int32); gr = np.zeros(0, np.float32)
        cl = np.zeros(0, np.uint8)
    params = {"block": [bx, by, bz], "radius_m": round(radius, 1), "max_gap_m": round(max_gap, 1),
              "spacing_m": round(spacing, 1), "power": power, "min_holes": min_holes,
              "min_samples": min_samples, "method": method, "coarsened": coarsened,
              "max_per_hole": max_per_hole,
              "domain_radius_m": round(dr, 1) if dom is not None else None,
              "domain_reach_m": round(d_reach, 1) if dom is not None else None,
              "anisotropy": [round(float(x), 2) for x in aniso] if aniso is not None else None,
              "passes": [{"range_m": round(r, 1), "max_gap_m": round(gp, 1),
                          "min_holes": mh, "min_samples": ms, "class": CLASSES[i]}
                         for i, (r, gp, mh, ms) in enumerate(passes)]}
    return lo, (bx, by, bz), ijk, gr, params, cl


def grade_at(pts, g, hid, centres, opts=None, params=None):
    """Estimate a grade at given block centres, with the same rules the block model
    used. This is what lets every metal be reported on ONE tonnage: the deposit is a
    single body, so the rock does not change between metals — only the grade in it
    does, and whether that metal was assayed there at all.

    Returns an array aligned to `centres`, NaN where the metal has no data close
    enough to estimate."""
    from scipy.spatial import cKDTree
    o = dict(params or {})
    o.update({k: v for k, v in (opts or {}).items() if v is not None})
    radius = float(o.get("radius_m") or 60.0)
    max_gap = float(o.get("max_gap_m") or 30.0)
    min_holes = int(o.get("min_holes") or 2)
    min_samples = int(o.get("min_samples") or 3)
    power = float(o.get("power") if o.get("power") is not None else 2.0)
    method = o.get("method") or "idw"
    out = np.full(len(centres), np.nan, np.float32)
    if not len(pts):
        return out
    tree = cKDTree(pts)
    CH = 200_000
    for s in range(0, len(centres), CH):
        ctr = centres[s:s + CH]
        dd, ii = tree.query(ctr, k=16, distance_upper_bound=radius)
        near = np.isfinite(dd)
        keep = near[:, 0] & (dd[:, 0] <= max_gap) & (near.sum(1) >= min_samples)
        if not keep.any():
            continue
        idx = np.where(keep)[0]
        dd_, ii_ = dd[keep], ii[keep]
        nearm = np.isfinite(dd_)
        iis = np.where(nearm, ii_, 0)
        hh = np.where(nearm, hid[iis], -1)
        hs = np.sort(hh, axis=1)
        nuniq = (np.diff(hs, axis=1) != 0).sum(1) + 1 - (hs[:, 0] == -1)
        k2 = nuniq >= min_holes
        if not k2.any():
            continue
        idx, dd_, iis, nearm = idx[k2], dd_[k2], iis[k2], nearm[k2]
        if method == "nn":
            est = g[iis[:, 0]]
        elif method == "mean":
            w = nearm.astype(float)
            est = (w * g[iis]).sum(1) / w.sum(1)
        else:
            w = np.where(nearm, 1.0 / np.maximum(dd_, 0.5) ** power, 0.0)
            est = (w * g[iis]).sum(1) / w.sum(1)
        out[s + idx] = est.astype(np.float32)
    return out


def _composite_el(per_hole, H, clen, gap_fill=True, margin=None, max_grade=None):
    """Downhole composites for one element: fixed-length samples along every hole
    that assayed it, with un-assayed ground carried as zero grade.

    ``margin`` limits how far those zeros run. A technical report usually prints
    significant intercepts rather than the whole assay file, so compositing the
    entire hole at zero grade buries a 6 m intercept in 300 m of invented barren
    rock and drags the interpolated grade down with it. Scored against the filings,
    that showed up as MMP needing an extreme cut-off to reach a filed grade and then
    finding almost no tonnage there. Keeping the zeros but stopping them a search
    distance beyond the reported intervals preserves what they are for — bounding
    the deposit, since MMP has no mineralisation wireframe — without the dilution.

    Pulled out of ``build_project`` so that a metal MMP does not draw as its own
    block model can still be composited and estimated on another metal's blocks
    (see ``grade_at``) — that is what puts every metal on one deposit tonnage.

    Returns ``(segments, pts, grades, hole_ids, zone_centres)``."""
    segs_out = []          # [hole_idx, from, to, grade]
    comp_p, comp_g, comp_h, cent = [], [], [], []
    for hi, ivs in per_hole.items():
        segs = _segments(ivs, max_grade=max_grade)
        if not segs:
            continue
        h = H[hi]
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
        if not gap_fill:                     # only composite the reported intervals
            keep_m = np.zeros(len(mids), bool)
            for s in segs:
                keep_m |= (mids >= s[0]) & (mids < s[1])
            mids = mids[keep_m] if keep_m.any() else mids[:1]
            gg = np.zeros(len(mids))
        for s in segs:
            m = (mids >= s[0]) & (mids < s[1])
            gg[m] = s[2]
            if not m.any():                  # a segment shorter than a composite
                mid = (s[0] + s[1]) / 2
                j = min(len(mids) - 1, int(mid // clen))
                gg[j] = max(gg[j], s[2] * (s[1] - s[0]) / clen)
        if margin and len(mids):
            near = np.zeros(len(mids), bool)
            for s in segs:
                near |= (mids >= s[0] - margin) & (mids <= s[1] + margin)
            if near.any():
                mids, gg = mids[near], gg[near]
        xyz = hole_xyz(h, mids)
        comp_p.append(xyz); comp_g.append(gg); comp_h.append(np.full(len(mids), hi))
        pos = gg > 0
        if pos.any():
            cent.append(xyz[pos].mean(0))
    return segs_out, comp_p, comp_g, comp_h, cent


# ---------------------------------------------------------------- build model
def _primary_commodity(ds):
    c = [x for x in (ds.get("commodities") or []) if x]
    return Counter(c).most_common(1)[0][0] if c else None


def auto_params(ds):
    """Where a project's parameters start before anyone adjusts them.

    1. the project's **most recent technical report** — its parent block size,
       density, composite length, search range, grade cap, reporting cut-off
       (``projects._report_params`` picks these newest-first);
    2. failing that, what is **typical for the region and commodity**, measured
       across the whole 43-101 corpus (``minemodelingpro.regional``).

    Returns ``(params, sources)``; `sources` labels each key for the model page.
    """
    out, src = {}, {}
    rp = dict(ds.get("report_params") or {})
    rfrom = ds.get("report_params_from") or {}
    for k, v in rp.items():
        if v is None:
            continue
        out[k] = v
        d = (rfrom.get(k) or {}).get("date") or ""
        src[k] = "technical report" + (f" ({d[:4]})" if len(d) >= 4 else "")
    el = _primary_commodity(ds)
    tp, label = REG.typical(ds.get("region"), el)
    for k in ("block", "density"):            # geometry/material norms only:
        if k in tp and k not in out:          # radius and composites follow the
            out[k] = tp[k]                    # project's own measured spacing
            src[k] = label
    return PARAMS.validate(out)[0], src


def _snap(v):
    std = [2, 2.5, 3, 4, 5, 6, 6.25, 8, 10, 12, 12.5, 15, 20, 25]
    return min(std, key=lambda s: abs(s - v))


MAX_BY_METAL = 24


def _class_split(cls_sorted, ks, bt):
    """Tonnes by confidence tier at each cut-off.

    A resource statement is not one number — Murray Brook is 17.5 Mt measured,
    5.6 Mt indicated and 0.1 Mt inferred — because how well a tonne is known matters
    as much as that it is there. The tier comes from which estimation pass reached
    the block (see ``_passes``)."""
    if cls_sorted is None or not len(cls_sorted):
        return None
    out = {}
    for pi, name in enumerate(CLASSES, 1):
        cc = np.cumsum((cls_sorted == pi).astype(np.int64))
        out[name] = [round(int(cc[k - 1]) * bt) if k > 0 else 0 for k in ks]
    return out


def _own_series(vals, bt, el, unit, n_cuts=26):
    """A grade-tonnage curve over the deposit's blocks, cut on one metal's grade.

    Fine enough to interpolate: the reporting-bin series is half a dozen points,
    and a filed grade falling between two of them clamped to the end of the curve
    instead of landing between them."""
    v = np.asarray(vals, float)
    ok = np.isfinite(v) & (v > 0)
    if ok.sum() < 8:
        return None
    vs = np.sort(v[ok])[::-1]
    csum = np.cumsum(vs)
    # cut-offs spread through the metal's own distribution, plus zero
    qs = np.unique(np.round(np.percentile(vs, np.linspace(0.5, 99.5, n_cuts - 1)), 5))
    cuts = np.unique(np.concatenate([[0.0], qs]))
    out_c, out_t, out_g = [], [], []
    for c in cuts:
        k = int(len(vs)) if c <= 0 else int(np.searchsorted(-vs, -c, "right"))
        if k <= 0:
            continue
        out_c.append(round(float(c), 5))
        out_t.append(round(k * bt))
        out_g.append(round(float(csum[k - 1] / k), 4))
    if len(out_c) < 3:
        return None
    return {"cuts": out_c, "tonnes": out_t, "grade": out_g}


def mem_budget_mb():
    """How much memory this process may use, as the container reports it.

    The modeller runs in a container with a hard limit and no warning before the
    kernel enforces it, so the few decisions that trade memory for completeness
    ask this rather than assuming a desktop."""
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = open(p).read().strip()
            if v and v != "max" and 0 < int(v) < (1 << 50):
                return int(v) / 1e6
        except Exception:
            pass
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1000.0
    except Exception:
        pass
    return 2048.0


def _by_metal(primary, grids, by_el, units, key2i, H, PS, margin=None, style=None,
              med_w=None, verbose=False):
    """Every metal reported on ONE deposit tonnage.

    A deposit is a single body of rock. Estimating each metal on its own blocks
    gives each metal its own tonnage, which is wrong on its face: the rock does
    not change between metals, only the grade in it does — and whether that metal
    was assayed there at all. So this takes the primary metal's blocks as *the*
    deposit, then estimates every other metal at those same block centres
    (``grade_at``). At each cut-off the tonnage is the primary's tonnage, and each
    metal contributes a grade, a contained quantity, and the share of that tonnage
    where it was actually assayed ("coverage") — because a metal measured in a
    third of the holes cannot honestly claim the whole deposit's tonnes.

    Returns ``None`` when there is nothing to put on one basis.
    """
    gp = grids.get(primary)
    if not gp or not len(gp.get("gr", ())):
        return None
    nblk = len(gp["gr"])
    if nblk > 2_000_000:                     # a nightly build of every project must still finish
        return None
    lo, bsz, ijk, gr = gp["lo"], gp["bsz"], gp["ijk"], gp["gr"]
    bt, clen = gp["bt"], gp["clen"]
    centres = lo + (ijk.astype(np.float32) + 0.5) * np.asarray(bsz, np.float32)

    # cut-offs: the reporting bins, compact enough for a table on a phone
    el0 = units.get(primary) or "g/t"
    cuts = sorted({round(float(c), 4) for c in
                   [0.0] + list(gp.get("bins") or PS.get("bins") or []) +
                   ([float(PS["default_cutoff"])] if PS.get("default_cutoff") is not None else [])})
    if len(cuts) < 3:
        q = [float(np.percentile(gr[gr > 0], x)) for x in (50, 75, 90, 95)] if (gr > 0).any() else []
        cuts = sorted({round(c, 4) for c in cuts + q})
    cuts = cuts[:10]

    # blocks sorted by primary grade: a cut-off is then a prefix, so every metal's
    # grade and coverage at every cut-off comes out of one cumulative sum
    order = np.argsort(-gr)
    grs = gr[order]
    ks = []
    for c in cuts:
        ks.append(int((grs > 0).sum()) if c <= 0 else int(np.searchsorted(-grs, -c, "right")))
    tonnes = [round(k * bt) for k in ks]

    # ordered by how many holes carry the metal, counted off the key column so
    # that ranking nineteen metals does not build nineteen metals' worth of rows
    want = [primary] + sorted([e for e in by_el if e != primary],
                              key=lambda e: -len(by_el.keys_of(e)))
    metals, order_out, by_metal_grades = {}, [], {}
    tcp = float(PS.get("top_cut_pct") or 99.5)
    # Every metal the drilling assayed is reported over the one deposit tonnage.
    # A multi-element package is most of why a client uploads their database at
    # all: the grade-tonnage table for the metal they are chasing is the point,
    # and the by-products beside it are what make the deposit worth mining. How
    # many fit is a question about this machine, not about the deposit, so it is
    # asked of the machine — a worker with a gigabyte reports fewer rather than
    # being killed halfway through reporting all of them.
    cap = PS.get("max_by_metal") or os.environ.get("MMP_MAX_BY_METAL")
    cap = int(cap) if cap else (MAX_BY_METAL if mem_budget_mb() >= 1800 else 8)
    for el in want[:cap]:
        unit = units.get(el) or ("g/t" if el in PRECIOUS else "%")
        if el == primary:
            vals_unsorted = gr.astype(np.float64)
            vals = gr[order].astype(np.float64)
            ok = np.ones(len(vals), bool)
        else:
            per_hole = defaultdict(list)
            for x in by_el[el]:
                if x["key"] in key2i:
                    per_hole[key2i[x["key"]]].append(x)
            if len(per_hole) < 2:
                continue
            from minemodelingpro import sanity as _SAN
            _, cp, cg, ch, _ = _composite_el(per_hole, H, clen,
                                             gap_fill=PS.get("gap_fill") is not False,
                                             margin=margin,
                                             max_grade=_SAN.MAX_PLAUSIBLE.get(el.replace("Eq", "")))
            if not cp:
                continue
            pts = np.concatenate(cp); g = np.concatenate(cg); hid = np.concatenate(ch)
            if (g > 0).sum() < 3:
                continue
            top = float(np.percentile(g[g > 0], tcp)) if (g > 0).sum() > 20 else float(g.max())
            g = np.minimum(g, top)
            # The primary's own search rules, but at the last pass's tolerance: the
            # block is already inside the deposit — the primary put it there — so the
            # question for a second metal is only "is there enough data to say what
            # its grade is here", not "does this rock exist". Holding secondary
            # metals to the strict first-pass rule left them NaN on blocks the
            # deposit plainly occupies, which quietly understated every contained
            # figure and every dollar of NSR.
            o = dict(gp.get("opts") or {})
            o.update({"min_holes": 1, "min_samples": 2})
            est = grade_at(pts, g, hid, centres, params=o)
            vals_unsorted = est.astype(np.float64)
            vals = est[order].astype(np.float64)
            ok = np.isfinite(vals)
            if not ok.any():
                continue
        by_metal_grades[el] = vals_unsorted
        csum = np.cumsum(np.where(ok, vals, 0.0))
        ccnt = np.cumsum(ok.astype(np.int64))
        row = {"unit": unit, "contained_unit": contained_unit(el, unit),
               "grade": [], "contained": [], "coverage": [], "tonnes": []}
        for k in ks:
            if k <= 0:
                row["grade"].append(None); row["contained"].append(None)
                row["coverage"].append(0.0); row["tonnes"].append(0)
                continue
            n = int(ccnt[k - 1]); ssum = float(csum[k - 1])
            if n == 0:
                row["grade"].append(None); row["contained"].append(None)
                row["coverage"].append(0.0); row["tonnes"].append(0)
                continue
            gavg = ssum / n
            tn = n * bt                       # tonnes this metal was actually measured over
            row["grade"].append(round(gavg, 4))
            row["contained"].append(round(contained(tn, gavg, el, unit)))
            row["coverage"].append(round(n / k, 4))
            row["tonnes"].append(round(tn))
        if not any(v for v in row["contained"]):
            continue
        # The same deposit, selected on THIS metal.
        #
        # Every series above is cut on the primary metal's grade, which is right
        # for "what else is in the rock a copper cut-off selects" and wrong for
        # reproducing a filed statement about a second metal. Raising a lead
        # cut-off does not concentrate copper: Murray Brook's copper spans
        # 0.22-0.48% along its lead curve and can never reach the 0.51% its own
        # filing reports, so the comparison ran off the end of the curve and
        # scored a sound model at 0.004x. Copper cut on copper spans 0.37-3.01%
        # on the same blocks.
        #
        # So each metal also gets a curve over the deposit's blocks ordered by
        # its OWN grade. The tonnage is still the deposit's — one body of rock —
        # and only the selection changes.
        own = _own_series(vals_unsorted, bt, el, unit)
        if own:
            row["own"] = own
        metals[el] = row
        order_out.append(el)
    if len(metals) < 1:
        return None
    out = {"basis": primary, "basis_unit": el0, "cuts": cuts, "tonnes": tonnes,
           "density": gp.get("dens"), "tonnes_per_block": round(bt, 1),
           "order": order_out, "metals": metals,
           "classes": _class_split(gp["cls"][order] if gp.get("cls") is not None
                                   and len(gp["cls"]) == len(gr) else None, ks, bt)}
    val = _by_value(grids, by_metal_grades, order_out, units, bt, PS,
                    cls=gp.get("cls") if gp.get("cls") is not None
                    and len(gp["cls"]) == len(gr) else None, verbose=verbose)
    if val:
        out["value"] = val
    meth = _by_method(gp, by_metal_grades, order_out, units, bt,
                      val.get("_values") if val else None, cuts, H, style, med_w, PS)
    if val:
        val.pop("_values", None)          # a block-length array, not for the page
    if meth:
        out["method"] = meth
    return out


def _by_method(gp, grades, order, units, bt, value, basis_cuts, H, style, med_w, PS):
    """The same deposit split the way a company would present it: what an open pit
    could reach, and what would have to be mined from underground at a higher
    cut-off. See minemodelingpro.mining_method for why the split is geometric."""
    from minemodelingpro import mining_method as MM
    from scipy.spatial import cKDTree
    try:
        lo, bsz, ijk = gp["lo"], gp["bsz"], gp["ijk"]
        if ijk is None or not len(ijk):
            return None
        centres = lo + (ijk.astype(np.float32) + 0.5) * np.asarray(bsz, np.float32)
        # the surface above each block: the nearest collar's elevation
        cz = np.asarray([[h["x"], h["y"]] for h in H], float)
        czz = np.asarray([h["z"] for h in H], float)
        _d, idx = cKDTree(cz).query(centres[:, :2], k=1)
        is_pit, limit = MM.split(centres, czz[idx], style, med_w)
        if is_pit is None:
            return None
        # the value of each block, when there is a value model, else the basis grade
        v = value if value is not None else gp["gr"].astype(np.float64)
        cut = PS.get("value_cutoff") if value is not None else None
        if cut is None:
            cut = basis_cuts[1] if len(basis_cuts) > 1 else basis_cuts[0]
        out = {"limit_m": limit, "note": MM.describe(limit, style),
               "basis": "value" if value is not None else "grade"}
        for name, mask in (("open_pit", is_pit), ("underground", ~is_pit)):
            c = MM.cutoff_for("underground" if name == "underground" else "pit", cut)
            sel = mask & (np.asarray(v, float) >= c)
            n = int(sel.sum())
            row = {"cutoff": c, "tonnes": round(n * bt), "blocks": n, "metals": {}}
            for el in order:
                g = grades.get(el)
                if g is None:
                    continue
                gv = np.asarray(g, float)[sel]
                gv = gv[np.isfinite(gv)]
                if not len(gv):
                    continue
                unit = units.get(el) or ("g/t" if el in PRECIOUS else "%")
                gm = float(gv.mean()); tn = len(gv) * bt
                row["metals"][el] = {"grade": round(gm, 4), "unit": unit,
                                     "contained": round(contained(tn, gm, el, unit)),
                                     "contained_unit": contained_unit(el, unit),
                                     "coverage": round(len(gv) / max(n, 1), 3)}
            out[name] = row
        return out
    except Exception:
        return None


def _by_value(grids, grades, order, units, bt, PS, cls=None, verbose=False):
    """The same deposit, cut in dollars per tonne instead of on one metal's grade.

    This is the correction Murray Brook forced. P&E report 23.1 Mt there at a
    CAD$23/t NSR cut-off; MMP reported 4.8 Mt from the same drilling because it cut
    at 0.1% Cu, and copper is barely a third of that rock's value — every zinc-rich,
    copper-poor block fell out. Cut on NSR instead and the same block model reports
    21.3 Mt. Nothing about the estimate changed; the question put to it did.

    So wherever a deposit carries more than one metal worth money, the cut-off
    belongs in dollars, and ideally in the report's own formula — then MMP's tonnage
    and the published tonnage are the same measurement."""
    from minemodelingpro import nsr as NSR
    model = PS.get("nsr")
    approx = False
    if not model or not model.get("factors"):
        met = PS.get("metallurgy") or {}
        model = NSR.indicative([e for e in order], {e: units.get(e) for e in order},
                               recoveries=(met or {}).get("recoveries"))
        approx = True
    if not model:
        return None
    have = {e: g for e, g in grades.items() if g is not None}
    if sum(1 for e in model["factors"] if e in have) < 2:
        return None                           # one priced metal is a grade cut-off
    v = NSR.value(have, model)
    if v is None or not np.isfinite(v).any():
        return None
    pos = v[v > 0]
    if not len(pos):
        return None
    top = float(np.percentile(pos, 99.5))
    cuts = [0.0] + [round(float(x), 1) for x in np.linspace(top / 8.0, top * 0.75, 7)]
    cog = PS.get("value_cutoff")
    if cog:
        cuts.append(round(float(cog), 1))
    cuts = sorted({round(c, 1) for c in cuts})
    order_v = np.argsort(-v)
    vs = v[order_v]
    ks = [int((vs > 0).sum()) if c <= 0 else int(np.searchsorted(-vs, -c, "right"))
          for c in cuts]
    rows = {}
    for el in order:
        g = have.get(el)
        if g is None:
            continue
        vals = np.asarray(g, np.float64)[order_v]
        ok = np.isfinite(vals)
        csum = np.cumsum(np.where(ok, vals, 0.0)); ccnt = np.cumsum(ok.astype(np.int64))
        unit = units.get(el) or ("g/t" if el in PRECIOUS else "%")
        r = {"unit": unit, "contained_unit": contained_unit(el, unit),
             "grade": [], "contained": [], "coverage": [], "tonnes": [],
             "priced": el in model["factors"]}
        for k in ks:
            if k <= 0 or int(ccnt[k - 1]) == 0:
                r["grade"].append(None); r["contained"].append(None)
                r["coverage"].append(0.0); r["tonnes"].append(0); continue
            n = int(ccnt[k - 1]); gavg = float(csum[k - 1]) / n; tn = n * bt
            r["grade"].append(round(gavg, 4))
            r["contained"].append(round(contained(tn, gavg, el, unit)))
            r["coverage"].append(round(n / k, 4))
            r["tonnes"].append(round(tn))
        rows[el] = r
    nsr_avg = []
    for k in ks:
        nsr_avg.append(round(float(vs[:k].mean()), 2) if k > 0 else None)
    return {"_values": v, "currency": model.get("currency") or "$", "formula": NSR.describe(model),
            "classes": _class_split(cls[order_v] if cls is not None else None, ks, bt),
            "factors": model["factors"], "approximate": bool(approx or model.get("approximate")),
            "covers": NSR.covered(have, model), "source": model.get("source"),
            "cuts": cuts, "tonnes": [round(k * bt) for k in ks], "nsr": nsr_avg,
            "cutoff": round(float(cog), 1) if cog else None,
            "order": [e for e in order if e in rows], "metals": rows}


def build_project(ds, max_elements=3, verbose=False, settings=None):
    """Build one project model. `settings` (or data/keep/mmp_model_params.json via
    the project slug, or ds["params"] from an imported dataset) overrides any of
    the data-driven defaults — see minemodelingpro.params."""
    base, base_src = auto_params(ds)
    PS, PORIGIN = PARAMS.layered(ds.get("slug"), base=base,
                                 extra={**(ds.get("params") or {}), **(settings or {})})
    max_elements = int(PS.get("max_elements") or max_elements)
    fr = ds["frame"]
    holes = [h for h in ds["holes"].values() if h.get("_ok")]
    if not holes:
        raise ValueError("no located holes")
    key2i = {h["key"]: i for i, h in enumerate(holes)}
    # --- intervals by element (only those on located holes)
    # The rows stay in columns and are built into row objects one element at a
    # time, as the estimator asks for them. by_el answers exactly as the
    # dictionary of lists it replaces did.
    from minemodelingpro.ivtable import Intervals, ByElement
    table = Intervals.coerce(ds["intervals"])
    table.restrict_keys(set(key2i))
    by_el = ByElement(table)
    units = {e: (table.unit_of(e) or ("g/t" if e in PRECIOUS else "%")) for e in table.elements()}
    if not by_el:
        raise ValueError("no intervals on located holes")
    # repair or drop grades that cannot be true before they reach a model: a column
    # of parts per million read as per cent sets the grade, the cut-off, the cap and
    # the tonnage, all wrong, and nothing downstream can tell
    from minemodelingpro import sanity as SANITY
    integrity, _dropped = SANITY.fix_table(table, verbose=verbose)
    for _el in _dropped:
        units.pop(_el, None)
    if not by_el:
        raise ValueError("every element's grades were impossible for their unit")
    # primary element: the published resource's commodity when known, else most data
    res_unit = next((r.get("contained_unit") for r in ds.get("resources") or []), None)
    res_el = next((r.get("element") for r in ds.get("resources") or [] if r.get("element")), None)

    def score(el):
        n = len(table.keys_of(el))
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
    els = sorted([e for e in by_el if by_el.count(e) >= 4], key=score, reverse=True)[:max_elements]
    if not els:
        els = sorted(by_el, key=score, reverse=True)[:1]
    if PS.get("elements"):                      # explicit element list from project settings
        want = [e for e in PS["elements"] if e in by_el]
        if want:
            els = want[:max_elements]

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
    max_to = defaultdict(float, table.max_to_by_key())
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
        rec = {"id": str(h["id"]), "x": float(h["x"]), "y": float(h["y"]), "z": float(z),
               "az": h.get("az") if h.get("az") is not None else 0.0,
               "dip": dip if dip is not None else -90.0, "depth": float(d), "key": h["key"],
               "src": sorted(h.get("src") or [])}
        sv = h.get("survey") or []
        if sv:
            # a collar table of x/y/z alone says nothing about where the hole went;
            # the shallowest survey station does, even when it is the only one
            rec["depth"] = max(rec["depth"], max(s_[0] for s_ in sv))
            if h.get("az") is None:
                rec["az"] = sv[0][1]
            if h.get("dip") is None:
                rec["dip"] = sv[0][2]
            if len(sv) >= 2:
                rec["path"] = min_curve([(0.0, rec["az"], rec["dip"])]
                                        + [s_ for s_ in sv if s_[0] > 0], rec["depth"])
        H.append(rec)
    # --- recentre the frame on the drilling
    cx = float(np.median([h["x"] for h in H])); cy = float(np.median([h["y"] for h in H]))
    for h in H:
        h["x"] -= cx; h["y"] -= cy
    if geo:
        lat0 = lat0 + cy / fr["mN"]; lon0 = lon0 + cx / fr["mE"]

    # ---- what kind of deposit is this? Settle it before anything that depends on
    # it, because the style sets the density, how far mineralisation is carried from
    # an intercept, and how big a block may be. Treating a 1 m vein as a bulk body
    # invented tonnage and destroyed grade in the same step.
    from minemodelingpro import deposit_style as DSTY
    _pe = els[0] if els else next(iter(by_el), None)
    _iv = by_el.get(_pe) or []
    _w = [float(x["to"]) - float(x["from"]) for x in _iv
          if x.get("to") is not None and x.get("from") is not None]
    _g = [float(x.get("grade") or 0) for x in _iv]
    _from_report = (ds.get("report_params") or {}).get("deposit_style")
    _stated = PS.get("deposit_style") or _from_report
    _suite = set(by_el) | {e for r in (ds.get("sources") or []) for e in (r.get("suite") or [])} \
        | set(ds.get("suite") or [])
    skey, sdef, swhy, sconf = DSTY.classify(_w, _g, _suite, stated=_stated,
                                            nearby=ds.get("nearby_style"))
    # PORIGIN says where the value came from: "auto" means the report supplied it,
    # anything else means a person set it for this project
    if _stated and skey == _from_report and PORIGIN.get("deposit_style", "auto") == "auto":
        # it came from the technical report's own words, which is evidence, not a
        # hand setting — the page says which, so the distinction has to be right
        sconf, swhy = "report", f"the technical report describes a {sdef['label']}"
    med_w = float(np.median([x for x in _w if x > 0])) if any(x > 0 for x in _w) else None
    mean_by_el = {}
    for _e in table.elements():
        _g = table.grades(_e)
        _g = _g[np.isfinite(_g) & (_g > 0)]
        if len(_g):
            mean_by_el[_e] = round(float(np.mean(_g)), 4)
    style_info = DSTY.describe(skey, swhy, sconf)

    # how far the zero-filled gaps may run past a reported intercept. One drill
    # spacing suits a massive sulphide lens; for a vein it inflates the modelled
    # volume by the ratio of the spacing to the vein width, which is where the
    # worst errors measured against filed statements came from.
    collar_sp = _spacing([[h["x"], h["y"]] for h in H])
    gap_margin = (float(PS["gap_fill_margin_m"]) if PS.get("gap_fill_margin_m")
                  else DSTY.halo_for(skey, med_w, collar_sp))
    style_info["halo_m"] = gap_margin
    if PS.get("dip") is not None and PS.get("dip_direction") is not None:
        style_info["trend"] = {"dip": PS.get("dip"), "dip_direction": PS.get("dip_direction"),
                               "plunge": PS.get("plunge") or 0.0,
                               "source": "set for this project"}
    style_info["median_intercept_m"] = round(med_w, 2) if med_w else None

    # --- per element: segments, composites, blocks, GT curve
    elements = {}
    grids = {}
    for el in els:
        unit = units.get(el) or "g/t"
        per_hole = defaultdict(list)
        for x in by_el[el]:
            per_hole[key2i[x["key"]]].append(x)
        # block size: whoever set it (see auto_params for where it starts), and
        # failing that one the deposit's own bodies can contain
        blk = PS.get("block") or DSTY.block_for(skey, med_w) or DEFAULT_BLOCK.get(el) or [10, 10, 5]
        blk = [float(v) for v in blk]
        clen = float(PS.get("composite_m") or max(1.0, min(blk[2], 5.0)))
        segs_out, comp_p, comp_g, comp_h, cent = _composite_el(
            per_hole, H, clen, gap_fill=PS.get("gap_fill") is not False,
            margin=gap_margin, max_grade=SANITY.MAX_PLAUSIBLE.get(el.replace("Eq", "")))
        if not segs_out:
            continue
        grades = [s[3] for s in segs_out]
        bins = [float(b) for b in PS["bins"]] if (PS.get("bins") and el == els[0]) else bins_for(el, grades)
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
            tcp = float(PS.get("top_cut_pct") or 99.5)
            top = float(PS.get("top_cut") or (float(np.percentile(g[g > 0], tcp))
                                              if (g > 0).sum() > 20 else float(g.max())))
            top, _capwhy = SANITY.top_cut_floor(g[g > 0], top)
            if _capwhy:
                integrity.append({"level": "repaired", "element": el, "message": _capwhy})
            g = np.minimum(g, top)
            spacing = float(PS.get("spacing_m") or _spacing(cent))
            eopts = dict(PS)
            eopts.setdefault("anisotropy", sdef.get("anisotropy"))
            # a stated structural trend, if the project carries one
            eopts["trend"] = {k: PS.get(k) for k in ("dip", "dip_direction", "plunge")} \
                if PS.get("dip") is not None and PS.get("dip_direction") is not None else None
            # The deposit's own wireframe: a surface (or, for a body with no
            # plane to it, a solid) through the mineralised intercepts, inflated
            # to the thickness those intercepts show, so the ground a geologist
            # would draw a body through is inside the model instead of falling
            # outside every search.
            #
            # It took four measured corrections to earn its place. It replaced the
            # composite reach instead of extending it; then membership was never
            # the binding constraint; then the link reach was measured on composite
            # spacing rather than on the distance between the intercepts being
            # linked; and finally — the one that mattered — it joined intercepts
            # with LINES, and a line has no area. Three holes define a patch of
            # ground, not two, so inflating a line gave a sleeve a few metres wide
            # that added nothing to a model whose composites already carried that
            # halo. Triangulating a surface through the intercepts instead moved
            # every project with a filed statement towards its filed tonnage and
            # none away: Aurmac 0.83 -> 1.01, Empire 0.89 -> 1.07, Murray Brook
            # 0.70 -> 0.74, and the median distance from the filed figure from
            # 0.391 to 0.371.
            #
            # Where a project publishes five mineralised intercepts of a six
            # hundred million tonne deposit it still adds nothing, and that is
            # correct: the alpha criterion drops any patch wider than the drill
            # pattern rather than closing the body over ground nobody has tested.
            # What those projects need is more drilling in the model, not a longer
            # reach in the estimator.
            from minemodelingpro import domain as DOM
            dom_pts, dom_info = (None, None)
            if PS.get("domain") is not False:
                rot_d, an_d = anisotropy(pts, g, eopts.get("anisotropy"),
                                         trend=eopts.get("trend"))
                reach = DOM.reach_for(PS.get("link_reach_m") or spacing,
                                      style_reach=sdef.get("halo_max_m", 0) * 3 or None)
                # the logged geology where the project states which units are ore,
                # the grade threshold otherwise
                lp, lh, lu = logged_body(ds, H, key2i, PS.get("domain_units"))
                if lp is not None and len(lp) >= 6:
                    dom_pts, dom_info = DOM.wireframe(
                        lp, np.ones(len(lp)), lh, 0.5, rot=rot_d, aniso=an_d,
                        reach=reach, step=max(float(blk[0]), 3.0),
                        thickness=PS.get("true_width_m"))
                    if dom_info:
                        dom_info["from"] = "logged geology"
                        dom_info["units"] = lu
                else:
                    dom_pts, dom_info = DOM.wireframe(
                        pts, g, hid, max(bins[0], 1e-9), rot=rot_d, aniso=an_d,
                        reach=reach, step=max(float(blk[0]), 3.0),
                        thickness=PS.get("true_width_m") or med_w)
                    if dom_info:
                        dom_info["from"] = "mineralised intercepts"
                # How THICK the body is, and how far to look for grade inside it.
                # Both were wrong, which is why the skeleton measured neutral: a
                # domain the thickness of the composite halo is a sleeve a few
                # metres wide around a line, and adds almost no rock to a model
                # whose composites already carry that halo. The body is as thick as
                # the intercepts say it is; and to estimate the middle of a link the
                # search has to reach the ends of it, so the reach follows the link
                # length rather than the drill spacing.
                # the cloud now fills the body rather than tracing its spine, so
                # membership is "inside this wireframe", a block's own size, not
                # "within a halo of a line"
                dom_r = max(0.75 * float(max(blk)), 2.0)
                dom_reach = max(float(PS.get("radius_m") or 0.0), 0.6 * float(reach))
                if dom_info:
                    dom_info["thickness_m"] = round(dom_r, 1)
                    dom_info["grade_reach_m"] = round(dom_reach, 1)
            lo, bsz, ijk, gr, params, bcls = estimate_blocks(
                pts, g, hid, blk, spacing, opts=eopts,
                domain_pts=dom_pts, domain_radius=dom_r,
                domain_reach=dom_reach)
            if dom_info:
                params["domain"] = dom_info
            grids[el] = {"lo": lo, "bsz": bsz, "ijk": ijk, "gr": gr, "clen": clen,
                         "opts": {k: v for k, v in params.items() if k != "passes"},
                         "bins": list(bins), "cls": bcls}
            params["top_cut"] = round(top, 3)
            params["composite_m"] = clen
            params["block_source"] = (base_src.get("block") if PORIGIN.get("block") == "auto"
                                      else "project settings" if PS.get("block")
                                      else "default for " + el)
            params["gap_fill"] = PS.get("gap_fill") is not False
            params["gap_fill_margin_m"] = round(gap_margin, 1)
            params["custom"] = sorted(k for k in PS if k in PARAMS.ALL
                                      and PORIGIN.get(k) != "auto")
            params["auto"] = {k: v for k, v in base_src.items() if PORIGIN.get(k) == "auto"}
            # density follows the WHOLE rock, not just the element being drawn: a
            # massive sulphide is dense because of its combined sulphide content, so
            # the copper model and the zinc model must use the same tonnes per cubic
            # metre or the deposit weighs different amounts depending which metal
            # you are looking at
            # The regional median beats the style's own density, measured. Letting
            # the deposit style supply it instead — on the grounds that a copper-zinc
            # massive sulphide is 3.2 t/m³ and the corpus average is 2.7, and that
            # the style is now read from the qualified person's own deposit-type
            # section — is better reasoned and came out slightly worse: over the
            # projects where the comparison is valid, |log error| 0.978 -> 1.005 and
            # the count within 1.5x of filed 48 -> 44. The regional medians are
            # measured on the same corpus that filed the statements, which is
            # apparently worth more than the physics. So a density from the regional
            # table still counts as stated. 239 of 595 models rest on it.
            dens, dens_why = DSTY.density_for(skey, mean_by_el, stated=PS.get("density"))
            params["density_source"] = dens_why
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
            grids[el].update({"bt": bt, "dens": dens, "unit": unit})
            if el == els[0]:
                el_out["check"] = _self_check(el, segs_out, pts, g, hid, lo, bsz,
                                              ijk, gr, params)
            el_out["blocks"] = {"cls": (_b64(bcls[keep].astype(np.uint8))
                                       if len(bcls) == len(gr) else None),
                                "classes": list(CLASSES),
                                "origin": [round(float(v), 2) for v in lo], "size": list(bsz),
                                "ijk": _b64(ijk[keep].astype(np.uint16)), "g": _b64(gr[keep].astype(np.float32)),
                                "n": int(keep.sum()), "n_total": int(len(gr)), "shown_from": round(show, 4),
                                "tonnes_per_block": round(bt, 1), "density": dens, "params": params}
            el_out["gt"] = gt
            if PS.get("default_cutoff") is not None:
                el_out["default_cutoff"] = float(PS["default_cutoff"])
            el_out["contained_unit"] = contained_unit(el, unit)
        elements[el] = el_out
    if not elements:
        raise ValueError("no modelable element")
    primary = els[0] if els[0] in elements else next(iter(elements))
    try:
        by_metal = _by_metal(primary, grids, by_el, units, key2i, H, PS,
                             margin=gap_margin, style=skey, med_w=med_w, verbose=verbose)
    except Exception as e:                   # never lose a model over its summary table
        by_metal = None
        print(f"[model3d] {ds.get('slug')}: by_metal failed: {type(e).__name__}: {str(e)[:140]}",
              flush=True)

    try:
        _cover = _coverage(ds, grids, primary, (grids.get(primary) or {}).get("dens"))
    except Exception as e:
        _cover = None
        print(f"[model3d] {ds.get('slug')}: coverage failed: {type(e).__name__}: {str(e)[:120]}",
              flush=True)

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
    hole_rows = []
    for h in H:
        row = [h["id"], round(h["x"], 2), round(h["y"], 2), round(h["z"], 2), round(h["az"], 1),
               round(h["dip"], 1), round(h["depth"], 1)]
        if h.get("path") is not None:
            pm, px, py, pz = h["path"]
            row.append([round(float(v), 2) for t in zip(pm, px, py, pz) for v in t])   # md,dx,dy,dz,...
        hole_rows.append(row)
    return _finish({
        "slug": ds["slug"], "project": ds["name"], "company": ds.get("company"), "region": region,
        "kinds": ds.get("kinds"), "updated": ds.get("updated"),
        "generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "frame": {"kind": fr["kind"], "lat0": round(lat0, 6) if geo else None, "lon0": round(lon0, 6) if geo else None,
                  "grid": fr.get("grid")},
        "holes": hole_rows, "elements": elements, "primary": primary, "by_metal": by_metal,
        "style": style_info, "metallurgy": PS.get("metallurgy") or ds.get("metallurgy"),
        "rock_units": ds.get("rock_units"),
        "coverage": _cover,
        "track_record": _validation(),
        "integrity": integrity or None,
        "settings": {k: v for k, v in PS.items() if k in PARAMS.ALL},
        "param_sources": {k: (base_src.get(k) if o == "auto" else o)
                          for k, o in PORIGIN.items() if k in PARAMS.ALL},
        "terrain": terr, "sources": ds["sources"], "resources": ds.get("resources") or [],
        "counts": {"holes": len(H), "assayed_holes": len({s[0] for e in elements.values() for s in e["segments"]}),
                   "intervals": sum(len(e["segments"]) for e in elements.values()),
                   "surveyed": sum(1 for h in H if h.get("path") is not None),
                   "blocks": (elements[primary].get("blocks") or {}).get("n_total", 0),
                   "reports": sum(1 for s in ds["sources"] if s["kind"].startswith("NI")),
                   "releases": sum(1 for s in ds["sources"] if s["kind"] == "news release")},
    })


def logged_body(ds, H, key2i, units, step=2.0):
    """Points along the logged geology units a project calls its mineralised body.

    This is the right way round, and the way a geologist works: the ROCK defines
    the domain and the assays only say what grade is in it. MMP's own wireframe
    has to pick its body by grade threshold, because a technical report publishes
    assays and not a logged geology file — but an imported dataset usually logs
    both, and then guessing is unnecessary.

    Returns (points, hole_index, matched_units) sampled down each logged interval
    so the body's thickness comes out of the logging rather than an assumption."""
    rows = ds.get("lithology") or []
    want = {str(u).strip().lower() for u in (units or []) if str(u).strip()}
    if not rows or not want:
        return None, None, None
    pts, hid, got = [], [], set()
    for x in rows:
        rock = str(x.get("rock") or "").strip().lower()
        alt = str(x.get("alt") or "").strip().lower()
        if rock not in want and alt not in want:
            continue
        hi = key2i.get(x.get("key"))
        if hi is None:
            continue
        h = H[hi]
        a, b = float(x["from"]), float(x["to"])
        if not (b > a):
            continue
        n = max(2, int(math.ceil((b - a) / max(step, 0.5))) + 1)
        md = np.linspace(a, b, n)
        md = md[md <= max(h["depth"], b)]
        if not len(md):
            continue
        xyz = hole_xyz(h, md)
        pts.append(xyz)
        hid.append(np.full(len(md), hi))
        got.add(rock if rock in want else alt)
    if not pts:
        return None, None, None
    return np.concatenate(pts), np.concatenate(hid), sorted(got)


def _coverage(ds, grids, primary, dens):
    """How much of the deposit the model actually reaches.

    The measured driver of MMP's error is not grade but reach: across the projects
    with a filed statement, the grade-matched error correlates at +0.91 with the
    share of the deposit's volume the model covers at all. Until now that share
    could only be computed where somebody had filed a tonnage — which is precisely
    the case MMP does not need help with.

    A report's own description of the body gives the same check without a filed
    statement: "traced over 1,200 m of strike, to 450 m depth, true widths of 2 to
    15 m" is a slab, and a model that fills a fiftieth of that slab is reaching a
    corner of its deposit whatever its grades say. It cuts the other way too — a
    model far larger than the described body is estimating ground the report says
    is barren.

    Reported, never applied: this does not change an estimate, it says how much of
    the deposit the estimate is of."""
    from minemodelingpro import extent as EXT
    st = ds.get("extent") or None
    g = (grids or {}).get(primary) or {}
    ijk, bsz = g.get("ijk"), g.get("bsz")
    out = {}
    if st:
        out["stated"] = st
        out["stated_text"] = EXT.describe(st)
        out["stated_from"] = ds.get("extent_from")
    if ijk is not None and len(ijk) and bsz is not None:
        bv = float(bsz[0]) * float(bsz[1]) * float(bsz[2])
        c = ijk.astype(float) * np.asarray(bsz, float)
        span = c.max(0) - c.min(0) + np.asarray(bsz, float)
        # the body's own long axis, not the map's: a vein striking north-east spans
        # the same distance in x and y while being one body half as long
        horiz = float(np.hypot(span[0], span[1]))
        out["model"] = {"volume_m3": round(len(ijk) * bv),
                        "strike_m": round(max(float(span[0]), float(span[1])), 1),
                        "diagonal_m": round(horiz, 1),
                        "depth_m": round(float(span[2]), 1),
                        "blocks": int(len(ijk))}
    sv = EXT.volume(st) if st else None
    if sv:
        out["stated_volume_m3"] = round(sv)
        if dens:
            out["stated_tonnes"] = round(sv * float(dens))
        mv = (out.get("model") or {}).get("volume_m3")
        if mv:
            out["volume_ratio"] = round(mv / sv, 3)
    sm = (st or {}).get("strike", {}).get("median") if st else None
    mm = (out.get("model") or {}).get("strike_m")
    if sm and mm:
        out["strike_ratio"] = round(mm / float(sm), 3)
    return out or None


def _self_check(el, segs, pts, g, hid, lo, bsz, ijk, gr, params):
    """Checks that need no filed statement to be useful.

    This is the case MMP exists for: a deposit nobody has published a resource on.
    Nothing external can be compared against, but two internal comparisons still
    say a great deal, and the CIM guidelines ask for both.

    **Global bias against a nearest-neighbour model.** Inverse distance smooths;
    nearest neighbour does not. If the two disagree badly on the mean grade of the
    same blocks, the estimate is smoothing or diluting rather than interpolating.

    **Composites against raw assays.** Compositing is supposed to regularise the
    sampling, not change what it says. MMP fills un-assayed ground with zero grade
    to bound the deposit, and this measures what that costs — the dilution the
    accuracy scoring eventually found the hard way, visible here directly."""
    out = {}
    try:
        if len(gr):
            centres = lo + (ijk.astype(np.float32) + 0.5) * np.asarray(bsz, np.float32)
            o = dict(params); o["method"] = "nn"
            nn = grade_at(pts, g, hid, centres, params=o)
            ok = np.isfinite(nn)
            if ok.sum() > 50:
                a, b = float(gr[ok].mean()), float(nn[ok].mean())
                out["idw_mean"] = round(a, 4)
                out["nn_mean"] = round(b, 4)
                out["bias_pct"] = round((a - b) / b * 100.0, 1) if b else None
    except Exception:
        pass
    try:
        raw = np.asarray([s[3] for s in segs], float)
        wts = np.asarray([max(s[2] - s[1], 0.01) for s in segs], float)
        if len(raw) and wts.sum():
            rm = float((raw * wts).sum() / wts.sum())
            cm = float(g.mean()) if len(g) else 0.0
            out["assay_mean"] = round(rm, 4)
            out["composite_mean"] = round(cm, 4)
            out["dilution_pct"] = round((1 - cm / rm) * 100.0, 1) if rm else None
    except Exception:
        pass
    return out or None


def _finish(model):
    """A last look for anything implausible that survived the repairs, so a model
    that is wrong says so instead of being presented as sound."""
    from minemodelingpro import sanity as SANITY
    flags = list(model.get("integrity") or [])
    flags += SANITY.check_model(model)
    model["integrity"] = flags or None
    model["ok"] = not any(f.get("level") == "bad" for f in flags)
    return model


def hole_xyz(h, md):
    """3D positions at measured depths along a hole (min-curvature path when the
    hole has a downhole survey, else straight on collar azimuth/dip)."""
    md = np.asarray(md, float)
    p = h.get("path")
    if p is not None:
        pm, px, py, pz = p
        if md.size and md.max() > pm[-1]:
            # extend on the last leg direction
            v = np.array([px[-1] - px[-2], py[-1] - py[-2], pz[-1] - pz[-2]]) / max(pm[-1] - pm[-2], 1e-6)
            pm = np.append(pm, md.max()); ext = md.max() - p[0][-1]
            px = np.append(px, px[-1] + v[0] * ext); py = np.append(py, py[-1] + v[1] * ext); pz = np.append(pz, pz[-1] + v[2] * ext)
        return np.stack([h["x"] + np.interp(md, pm, px), h["y"] + np.interp(md, pm, py), h["z"] + np.interp(md, pm, pz)], 1)
    d = _dir(h["az"], h["dip"])
    return np.stack([h["x"] + md * d[0], h["y"] + md * d[1], h["z"] + md * d[2]], 1)


VIEWER_JS = os.path.join(_HERE, "viewer.js")


def write_viewer(model, out_html):
    """Write a model page. The renderer lives in a shared viewer.js next to the
    pages (and is copied there when missing or stale), so each model page carries
    only its own data."""
    tmpl = open(_VIEWER_TMPL).read()
    try:
        dest = os.path.join(os.path.dirname(out_html) or ".", "viewer.js")
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        import shutil as _sh
        if (not os.path.exists(dest)) or os.path.getmtime(VIEWER_JS) > os.path.getmtime(dest):
            _sh.copyfile(VIEWER_JS, dest)
        # the Studio app renders private models with this same shell + viewer.js
        shell = os.path.join(os.path.dirname(dest), "_shell.html")
        if (not os.path.exists(shell)) or os.path.getmtime(_VIEWER_TMPL) > os.path.getmtime(shell):
            _sh.copyfile(_VIEWER_TMPL, shell)
    except Exception:
        pass
    title = model["project"] + (" — " + model["company"] if model.get("company") else "")
    # stamp the renderer's own mtime into its URL: a browser holding an old viewer.js
    # must not be able to shadow a new one
    try:
        stamp = str(int(os.path.getmtime(VIEWER_JS)))
    except Exception:
        stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    html = (tmpl.replace("__TITLE__", _esc(title))
                .replace("__BUILD__", stamp)
                .replace("__PROJECT__", _esc(model["project"]))
                .replace("__MODEL_JSON__", json.dumps(model, separators=(",", ":")).replace("</", "<\\/")))
    os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
    open(out_html, "w").write(html)
    return out_html


def _esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ thumbnails
def thumb_svg(m, w=360, h=190):
    """A small plan view of the drill pattern, holes coloured by their best grade.

    The gallery is a library of deposits; a name and two numbers do not tell them
    apart, but the drill pattern does — a 900-hole grid looks nothing like four
    scout holes on a ridge."""
    holes = m.get("holes") or []
    if not holes:
        return None
    el = m["elements"][m["primary"]]
    bins = el.get("bins") or []
    cols = el.get("colors") or []
    best = {}
    for sgm in el.get("segments") or []:
        i, g = sgm[0], sgm[3]
        if g > best.get(i, 0):
            best[i] = g
    # frame on the body of the drilling, not on one stray hole 20 km away
    xs = np.array([hh[1] for hh in holes], float); ys = np.array([hh[2] for hh in holes], float)
    if len(xs) > 12:
        x0, x1 = np.percentile(xs, [2, 98]); y0, y1 = np.percentile(ys, [2, 98])
    else:
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    span = max(float(x1 - x0), float(y1 - y0), 50.0) * 1.08
    cx = float(x0 + x1) / 2; cy = float(y0 + y1) / 2
    pad = 10
    sc = min((w - 2 * pad), (h - 2 * pad)) / span
    def P(x, y):                       # north up, east right
        return (w / 2 + (x - cx) * sc, h / 2 - (y - cy) * sc)
    def colour(i):
        g = best.get(i)
        if not g or not bins:
            return None
        k = 0
        for j, b in enumerate(bins):
            if g >= b:
                k = j
        return cols[min(k, len(cols) - 1)] if cols else "#D71920"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
             f'role="img" aria-label="drill plan">',
             f'<rect width="{w}" height="{h}" fill="#f7f9fb"/>']
    # faint graticule
    for gx in range(1, 6):
        parts.append(f'<line x1="{gx * w / 6:.0f}" y1="0" x2="{gx * w / 6:.0f}" y2="{h}" stroke="#e8edf2"/>')
    for gy in range(1, 3):
        parts.append(f'<line x1="0" y1="{gy * h / 3:.0f}" x2="{w}" y2="{gy * h / 3:.0f}" stroke="#e8edf2"/>')
    # a thumbnail does not need 900 holes drawn: keep every assayed hole and thin the
    # rest, so the file stays a few KB
    idx = list(range(len(holes)))
    plain = [i for i in idx if i not in best]
    if len(plain) > 320:
        step = len(plain) / 320.0
        keep = {plain[int(k * step)] for k in range(320)}
        idx = [i for i in idx if i in best or i in keep]
    # one path per colour keeps the file small (a few KB) however many holes there are
    lanes = defaultdict(list)
    dots = defaultdict(list)
    for i in idx:
        hh = holes[i]
        az = math.radians(float(hh[4] or 0.0)); dip = math.radians(float(hh[5] or -90.0))
        run = max(0.0, float(hh[6] or 0.0)) * math.cos(dip)
        ax, ay = P(hh[1], hh[2])
        bx, by = P(hh[1] + run * math.sin(az), hh[2] + run * math.cos(az))
        c = colour(i)
        if max(abs(ax), abs(bx)) > 4 * w or max(abs(ay), abs(by)) > 4 * h:
            continue                      # an outlier hole outside the framed body
        lanes[c or "plain"].append(f"M{ax:.1f} {ay:.1f}L{bx:.1f} {by:.1f}")
        if c:
            dots[c].append(f"M{ax - 2.2:.1f} {ay:.1f}a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0")
    if lanes.get("plain"):
        parts.append(f'<path d="{"".join(lanes.pop("plain"))}" stroke="#b9c3cd" stroke-width="1.5" '
                     f'fill="none" stroke-linecap="round"/>')
    for c, d in lanes.items():
        parts.append(f'<path d="{"".join(d)}" stroke="{c}" stroke-width="2.6" fill="none" '
                     f'stroke-linecap="round" opacity=".95"/>')
    for c, d in dots.items():
        parts.append(f'<path d="{"".join(d)}" fill="{c}"/>')
    parts.append("</svg>")
    return "".join(parts)


def write_thumb(m, path):
    svg = thumb_svg(m)
    if not svg:
        return None
    with open(path, "w") as f:
        f.write(svg)
    return path


def card_of(m):
    """The gallery row for one model: what it is, how much real data stands behind
    it, and what it adds up to at the opening cut-off."""
    pe = m["elements"][m["primary"]]
    cu = pe.get("contained_unit") or contained_unit(m["primary"], pe["unit"])
    best_res = next((r for r in m["resources"] if r.get("contained_unit") == cu and r.get("contained")
                     and (not r.get("element") or r["element"] == m["primary"].replace("Eq", ""))), None)
    gt = pe.get("gt") or []
    cut = pe.get("default_cutoff") or (pe["bins"][1] if len(pe.get("bins") or []) > 1
                                       else (pe.get("bins") or [0])[0])
    row = next((r for r in gt if r[0] >= cut), (gt[0] if gt else [0, 0, 0, 0]))
    c = m["counts"]
    # how much a model is worth looking at: assayed holes and intervals, with credit
    # for a published resource to check against
    score = (c.get("assayed_holes") or 0) * 3 + (c.get("intervals") or 0) + \
            (160 if best_res else 0) + (40 if (c.get("reports") or 0) else 0)
    return {"slug": m["slug"], "project": m["project"], "company": m.get("company"),
            "region": m.get("region"), "element": m["primary"],
            "elements": list(m["elements"].keys()), "unit": pe["unit"],
            "counts": c, "updated": m.get("updated"), "kinds": m.get("kinds"),
            "max": pe["stats"]["max"], "terrain": bool(m.get("terrain")),
            "cut": row[0], "tonnes": row[1], "grade": row[2], "contained": row[3],
            "contained_unit": cu, "score": score,
            "block": (pe.get("blocks") or {}).get("size"),
            "block_source": ((pe.get("blocks") or {}).get("params") or {}).get("block_source"),
            "resource": ({"contained": best_res.get("contained"), "unit": best_res.get("contained_unit"),
                          "date": best_res.get("date")} if best_res else None)}


# --------------------------------------------------------------- build all
def _validation():
    """MMP's measured track record, for the pages to quote.

    A deposit with no filed resource is the case MMP exists for, and the only
    honest basis for trusting one of those models is how the same method scores
    where a filed statement does exist. ``scripts/validate.py`` writes that file."""
    try:
        path = os.path.join(_ROOT, "data", "keep", "mmp_validation.json")
        d = json.load(open(path))
        h = d.get("headline") or {}
        if not h.get("n"):
            return None
        return {"n": h["n"], "median_ratio": h.get("median_ratio"),
                "within_1_5x": h.get("within_1_5x"), "within_2x": h.get("within_2x"),
                "covered_n": h.get("covered_n"),
                "covered_median": h.get("covered_median"),
                "covered_within_1_5x": h.get("covered_within_1_5x"),
                "bands": h.get("bands"),
                "tiers": (d.get("tiers") or {}).get("n") and {
                    "n": d["tiers"]["n"],
                    "filed_mi_share": d["tiers"].get("filed_mi_share_median"),
                    "mmp_top2_share": d["tiers"].get("mmp_top2_share_median")},
                "generated": d.get("generated")}
    except Exception:
        return None


def _index_entry(m, site_base):
    """One project's row in models_index.json (Closeology deep-links through it)."""
    slug = m["slug"]
    e = {"slug": slug, "url": site_base + "models/" + slug + ".html", "project": m["project"],
         "company": m.get("company"), "region": m.get("region"), "element": m["primary"],
         "updated": m.get("updated"), "source": "+".join(m.get("kinds") or []),
         "holes": m["counts"]["holes"], "samples": m["counts"]["intervals"],
         "releases": [s["url"] for s in m["sources"]
                      if s["kind"] == "news release" and s.get("url")]}
    if (m.get("frame") or {}).get("lat0") is not None:
        e["center"] = [m["frame"]["lat0"], m["frame"]["lon0"]]
    return e


def _page_model(path):
    """The model a built page carries, for a resumed build to reuse."""
    try:
        s = open(path, encoding="utf-8").read()
        i = s.index('<script id="model"')
        a = s.index(">", i) + 1
        b = s.index("</script>", a)
        return json.loads(s[a:b].replace("<\\/", "</"))
    except Exception:
        return None



def build_all(site_dir="site", only=None, verbose=True, resume_after=None):
    """One model page per PROJECT (all sources merged), the gallery, and
    models_index.json (Closeology's drill radar deep-links release URLs to it).

    `resume_after` is a unix time: a project whose page was written after it is
    taken as already built from the current inputs and reused, so a build that
    stopped three hours in is picked up rather than started over. The gallery and
    the index are assembled from every page present either way."""
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
        if resume_after:
            pg = os.path.join(out_dir, ds["slug"] + ".html")
            try:
                fresh = os.path.getmtime(pg) >= float(resume_after)
            except OSError:
                fresh = False
            if fresh:
                m = _page_model(pg)
                if m:
                    built.add(ds["slug"] + ".html")
                    built.add(ds["slug"] + ".svg")
                    cards.append(card_of(m))
                    index.append(_index_entry(m, site_base))
                    continue
        try:
            m = build_project(ds, verbose=verbose)
        except Exception as ex:
            if verbose:
                print(f"[model3d] skip {ds['slug']}: {str(ex)[:100]}")
            continue
        slug = m["slug"]
        write_viewer(m, os.path.join(out_dir, slug + ".html"))
        built.add(slug + ".html")
        write_thumb(m, os.path.join(out_dir, slug + ".svg"))
        built.add(slug + ".svg")
        cards.append(card_of(m))
        index.append(_index_entry(m, site_base))
        if verbose:
            print(f"[model3d] {slug}: {m['counts']['holes']}h {m['counts']['intervals']}iv "
                  f"{m['counts']['blocks']}blk {m['primary']} kinds={m.get('kinds')} terrain={'y' if m.get('terrain') else 'n'}")
    # remove stale model pages (old per-source pages) — keep the three.js runtime
    if not only:
        for f in os.listdir(out_dir):
            if f.endswith((".html", ".svg")) and f not in built and not f.startswith("_"):
                os.remove(os.path.join(out_dir, f))      # stale model pages (keep _shell.html)
    _write_gallery(cards, os.path.join(site_dir, "models.html"))
    # the landing page shows one real model; keep it in step with the build
    hero = _hero_slug(os.environ.get("MMP_HERO_SLUG", "murray-brook"), out_dir, index)
    if not only and hero and os.path.exists(os.path.join(out_dir, hero + ".html")):
        try:
            sys.path.insert(0, os.path.join(_ROOT, "scripts"))
            import hero_model
            hero_model.build(hero, site_dir)
        except Exception as ex:
            if verbose:
                print(f"[model3d] hero: {str(ex)[:120]}")
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


def _hero_slug(want, out_dir, index):
    """The model the landing page shows.

    A hard-coded slug goes stale the moment the corpus is refreshed: enlarging the
    report catalogue renamed `murray-brook` to `murray-brook-mqm`, the file the
    landing page wanted stopped existing, and the hero quietly stayed as it was
    from a build weeks earlier. So the name is a preference, not a requirement —
    the same project under a new suffix is taken first, and failing that the
    best-drilled model there is."""
    if want and os.path.exists(os.path.join(out_dir, want + ".html")):
        return want
    if want:
        pre = [e["slug"] for e in index if e["slug"].startswith(want + "-")]
        if pre:
            return sorted(pre, key=lambda s: -next(
                (e["samples"] or 0) for e in index if e["slug"] == s))[0]
    if index:
        return max(index, key=lambda e: (e.get("samples") or 0))["slug"]
    return None


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
