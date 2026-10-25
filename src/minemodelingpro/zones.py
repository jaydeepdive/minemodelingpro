"""Separate mineralised bodies on one property, found from the drilling itself.

A property is not a deposit. Aguila files 112,000 tonnes Indicated and MMP holds
forty-five drilled holes spread across the ground around it; Cajueiro files
254,000 tonnes against an Inferred eleven million. Those are not bad estimates,
they are a model of the whole property set beside a statement about one zone of
it — which is nineteen of the twenty projects MMP was more than ten times out on.

A zone here is a connected body: intercepts that can be reached from one another
in steps no longer than the distance a filing of this style carries grade between
holes. Two lenses a kilometre apart are two zones; two holes either side of the
same lens are one. The whole project is still modelled and still reported as one
thing — the zones are a decomposition of it, so a reader can see that four fifths
of the tonnage sits in one body and ask whether the filing beside it covers that
body or another.
"""
import numpy as np

MIN_HOLES = 2          # a body one hole has seen is not yet a body
MAX_CLUSTER_POINTS = 12_000
MIN_POINTS = 3


def _components(xyz, reach):
    """Which intercepts are reachable from which, in steps of at most `reach`."""
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    if len(xyz) == 0:
        return np.zeros(0, int), 0
    tree = cKDTree(xyz)
    g = tree.sparse_distance_matrix(tree, float(reach), output_type="coo_matrix")
    n, lab = connected_components(g, directed=False)
    return lab, n


def find(cent, hole, reach, min_holes=MIN_HOLES, min_points=MIN_POINTS):
    """Zones from intercept centroids.

    Returns (labels, zones). `labels` is one entry per intercept: a zone index
    from 1, or 0 for an intercept too lonely to call a body — those are kept and
    modelled, they are simply not reported as a zone of their own. `zones` is a
    list ordered by size, each carrying the indices that belong to it."""
    cent = np.asarray(cent, float)
    hole = np.asarray(hole)
    if len(cent) == 0:
        return np.zeros(0, int), []
    # a radius graph over every mineralised composite on a big property is a lot
    # of edges for a question answered just as well by a thinned cloud: cluster a
    # sample, then give every point the zone of the nearest sampled point
    if len(cent) > MAX_CLUSTER_POINTS:
        step = int(np.ceil(len(cent) / MAX_CLUSTER_POINTS))
        pick = np.arange(0, len(cent), step)
        lab_s, zones_s = find(cent[pick], hole[pick], reach, min_holes, min_points)
        labels = assign(cent, cent[pick], lab_s, reach)
        for z in zones_s:
            w = np.flatnonzero(labels == z["id"])
            z["idx"] = w
            z["intercepts"] = int(len(w))
            z["holes"] = int(len({str(h) for h in hole[w]})) if len(w) else z["holes"]
        return labels, zones_s
    raw, n = _components(cent, reach)
    order = []
    for c in range(n):
        w = np.flatnonzero(raw == c)
        order.append((len(w), len({str(h) for h in hole[w]}), c, w))
    # biggest first, so Zone 1 is the one a reader cares about
    order.sort(key=lambda t: (-t[0], t[2]))
    labels = np.zeros(len(cent), int)
    zones = []
    for npts, nholes, _c, w in order:
        if npts < min_points or nholes < min_holes:
            continue
        zid = len(zones) + 1
        labels[w] = zid
        lo = cent[w].min(0)
        hi = cent[w].max(0)
        zones.append({
            "id": zid, "name": "Zone %d" % zid,
            "intercepts": int(npts), "holes": int(nholes),
            "centre": [round(float(v), 1) for v in cent[w].mean(0)],
            "bbox": [[round(float(v), 1) for v in lo], [round(float(v), 1) for v in hi]],
            "span_m": [round(float(hi[0] - lo[0]), 1), round(float(hi[1] - lo[1]), 1),
                       round(float(hi[2] - lo[2]), 1)],
            "idx": w,
        })
    return labels, zones


def assign(points, cent, labels, reach):
    """Give every point the zone of the nearest intercept within reach, else 0.

    Used for composites (so each zone's body is drawn from its own drilling) and
    for blocks (so the model can be read a zone at a time)."""
    points = np.asarray(points, float)
    if len(points) == 0 or len(cent) == 0:
        return np.zeros(len(points), int)
    from scipy.spatial import cKDTree
    keep = np.flatnonzero(np.asarray(labels) > 0)
    if not len(keep):
        return np.zeros(len(points), int)
    tree = cKDTree(np.asarray(cent, float)[keep])
    d, i = tree.query(points, k=1, distance_upper_bound=float(reach))
    out = np.zeros(len(points), int)
    ok = np.isfinite(d)
    out[ok] = np.asarray(labels)[keep][i[ok]]
    return out


def summarise(zones, block_zone, grade, tonnes_per_block, cutoffs, el, unit, contained_fn):
    """What each zone holds, on the same cut-offs as the project's own curve.

    The project total is the sum of its zones plus whatever sits outside them;
    nothing is hidden and nothing is double-counted, so the decomposition can be
    read beside the headline without arithmetic."""
    out = []
    bz = np.asarray(block_zone)
    g = np.asarray(grade, float)
    for z in zones:
        m = bz == z["id"]
        if not m.any():
            continue
        gz = g[m]
        rows = []
        for c in cutoffs:
            sel = gz[gz >= c] if c > 0 else gz[gz > 0]
            n = int(sel.size)
            if not n:
                rows.append({"cutoff": c, "tonnes": 0, "grade": None, "contained": None})
                continue
            t = n * tonnes_per_block
            gm = float(sel.mean())
            rows.append({"cutoff": c, "tonnes": round(t), "grade": round(gm, 4),
                         "contained": round(contained_fn(t, gm, el, unit))})
        out.append({**{k: v for k, v in z.items() if k != "idx"},
                    "blocks": int(m.sum()), "series": rows})
    return out
