"""Where the deposit is, as distinct from what grade it carries.

MMP's single largest remaining error was measured rather than guessed: across the
projects with a filed statement, the grade-matched error correlates at +0.91 with
how much of the deposit's volume the model covers at all. Where the envelope reaches
less than half the filed body MMP finds 7% of the rock; where it reaches more than
half it finds 86%. Goldboro models 4% of its deposit's volume, Reid 17%. The grades
on those projects are not wrong — the model simply never arrives at most of the ore.

The cause is that MMP had no notion of a deposit, only of data. Every block had to
sit within a search distance of a composite, so the model was a string of bubbles
around individual intercepts. When two holes eighty metres apart both cut the same
mineralised horizon, a geologist draws a body through them and estimates the ground
between; MMP left it out, because no composite was near enough.

So this builds the thing that was missing: a skeleton of the deposit, made by
connecting mineralised intercepts in neighbouring holes that are close enough, in
the deposit's own anisotropic frame, to be the same body. The skeleton says WHERE
the rock is. The composites still say WHAT GRADE it carries, and the estimation
passes still decide how well each block is supported — a block in the middle of a
long link is real rock, thinly drilled, and is labelled that way rather than being
silently dropped or silently promoted.

It cuts both ways, which is the point. The same links that let the model reach
between holes stop at the end of the drilling, so a deposit cannot sprawl into
ground nobody has tested — and in the anisotropic frame a link follows a vein along
strike without bridging to the next vein across it.
"""
import numpy as np


def intercepts(pts, g, hid, threshold):
    """One point per mineralised intercept per hole.

    A hole that cuts the horizon twice contributes two, because they may belong to
    different limbs or lenses and connecting the wrong pair invents a body."""
    pts = np.asarray(pts, float)
    g = np.asarray(g, float)
    hid = np.asarray(hid)
    keep = g >= threshold
    if not keep.any():
        return np.zeros((0, 3)), np.zeros(0, int)
    P, H = pts[keep], hid[keep]
    out_p, out_h = [], []
    for h in np.unique(H):
        sel = H == h
        q = P[sel]
        if len(q) == 1:
            out_p.append(q[0]); out_h.append(h); continue
        order = np.argsort(q[:, 2])[::-1]          # down the hole
        q = q[order]
        # split where consecutive mineralised composites are far apart: that is a
        # second intercept, not the same one
        gap = np.r_[0.0, np.linalg.norm(np.diff(q, axis=0), axis=1)]
        brk = np.where(gap > 12.0)[0]
        for part in np.split(np.arange(len(q)), brk):
            if len(part):
                out_p.append(q[part].mean(0)); out_h.append(h)
    return np.asarray(out_p, float), np.asarray(out_h)


def links(cent, hole, rot=None, aniso=None, reach=120.0, k=4):
    """Join intercepts in DIFFERENT holes that are close enough to be one body.

    Measured in the deposit's own frame, so a tabular body is followed along itself
    and not across itself. Each intercept keeps only its few nearest partners, which
    is what stops a drill pattern being wired into a single blob."""
    from scipy.spatial import cKDTree
    if len(cent) < 2:
        return np.zeros((0, 2), int)
    W = (rot / np.asarray(aniso, float)[:, None]) if rot is not None else None
    Q = cent @ W.T if W is not None else cent
    tree = cKDTree(Q)
    pairs = set()
    kk = min(k + 1, len(cent))
    dd, ii = tree.query(Q, k=kk, distance_upper_bound=reach)
    for a in range(len(cent)):
        for d, b in zip(np.atleast_1d(dd[a]), np.atleast_1d(ii[a])):
            if not np.isfinite(d) or b >= len(cent) or b == a:
                continue
            if hole[a] == hole[b]:
                continue                 # the hole already tells us about itself
            pairs.add((min(a, b), max(a, b)))
    return np.asarray(sorted(pairs), int) if pairs else np.zeros((0, 2), int)


def skeleton(pts, g, hid, threshold, rot=None, aniso=None, reach=120.0, step=6.0,
             k=4, max_points=400_000):
    """The deposit's skeleton: the mineralised intercepts plus the ground between
    the ones that connect, sampled densely enough to be used as a search cloud.

    Returned as plain points so the estimator can treat "is this block in the
    deposit" with the same machinery it already uses for "is this block near data",
    while keeping the two questions separate."""
    cent, hole = intercepts(pts, g, hid, threshold)
    if not len(cent):
        return np.zeros((0, 3)), {"intercepts": 0, "links": 0}
    # How far apart the things being linked actually are. Deriving the link reach
    # from composite spacing measured it on the wrong thing entirely — composites
    # are a metre apart down a hole, so Goldboro was allowed to link across 30 m
    # when its mineralised intercepts sit far further apart than that, and not one
    # useful link formed. The spacing that matters is hole to hole, between the
    # intercepts themselves.
    reach = max(float(reach), _intercept_spacing(cent, hole) * 2.2)
    ed = links(cent, hole, rot, aniso, reach=reach, k=k)
    out = [cent]
    n_link = 0
    for a, b in ed:
        p, q = cent[a], cent[b]
        L = float(np.linalg.norm(q - p))
        if L <= 1e-6:
            continue
        n = int(min(max(L / max(step, 1.0), 1), 400))
        t = np.linspace(0.0, 1.0, n + 1)[1:-1]
        if len(t):
            out.append(p[None, :] + t[:, None] * (q - p)[None, :])
        n_link += 1
    S = np.concatenate(out) if len(out) > 1 else cent
    if len(S) > max_points:                      # thin it rather than refuse
        S = S[np.linspace(0, len(S) - 1, max_points).astype(int)]
    return S, {"intercepts": int(len(cent)), "links": int(n_link),
               "skeleton_points": int(len(S)), "reach_m": round(float(reach), 1)}


def _intercept_spacing(cent, hole, cap=250.0):
    """Median distance from a mineralised intercept to the nearest one in another
    hole — the real scale at which this deposit was tested."""
    from scipy.spatial import cKDTree
    if len(cent) < 3:
        return 0.0
    tree = cKDTree(cent)
    k = min(8, len(cent))
    dd, ii = tree.query(cent, k=k)
    out = []
    for a in range(len(cent)):
        for d, b in zip(np.atleast_1d(dd[a]), np.atleast_1d(ii[a])):
            if b < len(cent) and hole[b] != hole[a] and np.isfinite(d) and d > 0:
                out.append(float(d)); break
    if not out:
        return 0.0
    return float(min(np.median(out), cap))


def reach_for(spacing, style_reach=None, cap=200.0):
    """How far a body may be carried between two holes.

    Tied to the drill spacing, because that is the scale at which the deposit was
    actually tested: linking holes a couple of spacings apart is the interpolation a
    geologist would do on a section, and linking across ten is invention."""
    r = 2.2 * float(spacing or 50.0)
    if style_reach:
        r = min(r, float(style_reach))
    return float(np.clip(r, 25.0, cap))
