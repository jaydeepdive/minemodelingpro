"""Where to drill next, from what the drilling already says.

A block model answers "what is here". The question a company actually asks is
"where should the next hole go", and the answer is not in the blocks: it is at
the EDGE of them, where the body is still carrying grade when the drilling
stops. MMP already computes that edge — the alpha criterion that keeps the
wireframe honest stops it exactly where the drill pattern ends — and then
throws the information away.

So this reads the rim of each body and asks, at every intercept on it:

  * how much metal is that hole cutting where the body ends (grade x the
    thickness it cut, in gram-metres, which is the number a release quotes);
  * which way is out, in the body's own plane, so a step-out follows the vein
    along itself rather than across it;
  * whether the ground one drill spacing out that way has been tested — if a
    hole already passes through it, it is not a target, it is a hole; and
  * where a rig would stand to cut it, and at what depth.

Everything it proposes is a step-out of one drill spacing from a hole that cut
mineralisation, which is the most conservative target there is: it suggests the
ground the existing pattern would have tested next, not a new discovery. It is
a reading of the drilling, not a prediction, and the page says so.

What this deliberately does NOT do is rank a target by the block model's
estimate beyond the edge. The estimate does not go beyond the edge — that is
what the edge means — and dressing an extrapolation up as an estimate is the
failure this whole module exists to avoid.
"""
import numpy as np


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else np.array([0.0, 0.0, 0.0])


def hole_samples(holes, step=10.0, max_points=400_000):
    """Every existing hole as points along its path, for "has this been tested".

    A collar is not a hole: a target 300 m below a collar is untested ground
    unless a hole actually passes through it, so the whole trace is sampled."""
    pts = []
    for h in holes or []:
        d = float(h.get("depth") or 0.0)
        p0 = np.array([float(h.get("x", 0.0)), float(h.get("y", 0.0)),
                       float(h.get("z", 0.0))], float)
        if d <= 0:
            pts.append(p0)
            continue
        az = np.radians(float(h.get("az") or 0.0))
        dp = np.radians(float(h.get("dip") or -90.0))
        v = np.array([np.cos(dp) * np.sin(az), np.cos(dp) * np.cos(az), np.sin(dp)])
        n = int(max(2, min(d / max(step, 1.0) + 1, 2000)))
        for m in np.linspace(0.0, d, n):
            pts.append(p0 + v * m)
        if len(pts) > max_points:
            break
    return np.asarray(pts, float) if pts else np.zeros((0, 3))


def collar_for(target, az_deg, dip_deg, surface_z, max_depth=1200.0):
    """Where a rig stands to put a hole through one point, and how deep.

    The hole is driven back up its own direction from the target until it
    reaches the surface. A dip of zero would never get there, so a flat
    direction is refused rather than projected to infinity."""
    dip = float(dip_deg)
    if dip >= -5.0:
        return None
    az = np.radians(float(az_deg))
    d = np.radians(dip)
    v = np.array([np.cos(d) * np.sin(az), np.cos(d) * np.cos(az), np.sin(d)])
    rise = float(surface_z) - float(target[2])
    if rise <= 0:
        return None
    length = rise / abs(v[2])
    if length > max_depth:
        return None
    collar = np.asarray(target, float) - v * length
    return {"x": round(float(collar[0]), 1), "y": round(float(collar[1]), 1),
            "z": round(float(surface_z), 1), "az": round(float(az_deg) % 360.0, 1),
            "dip": round(dip, 1), "depth_m": round(float(length), 1)}


def surface_from_collars(holes, k=4):
    """Ground level at a point, from the collars around it.

    A step-out sits one drill spacing from a hole that has already been
    collared, so the elevations of the nearest few collars are a better guide
    to where the rig stands than anything else available at this point in the
    build — and on a project with no map position they are the only guide there
    is. Inverse distance over the nearest handful, which is what the terrain
    layer is built from on those projects anyway."""
    xy, z = [], []
    for h in holes or []:
        try:
            xy.append([float(h["x"]), float(h["y"])])
            z.append(float(h["z"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not xy:
        return lambda x, y: None
    XY = np.asarray(xy, float)
    Z = np.asarray(z, float)
    kk = int(min(max(1, k), len(Z)))

    def at(x, y):
        d2 = ((XY - np.array([float(x), float(y)])) ** 2).sum(1)
        i = np.argpartition(d2, kk - 1)[:kk] if len(d2) > kk else np.arange(len(d2))
        w = 1.0 / (d2[i] + 25.0)
        return float((w * Z[i]).sum() / w.sum())
    return at


def hole_attitude(dip=None, dip_direction=None, normal=None, min_dip=45.0):
    """The azimuth and dip of a hole that cuts the body squarely.

    From the stated attitude where a report gives one, and otherwise from the
    body's own across-axis, which is the direction the wireframe is thinnest in
    — the same frame the search ellipsoid uses. A hole perpendicular to a
    steeply dipping vein is nearly flat, and nobody drills a flat hole, so the
    dip is held at `min_dip` at the shallowest: it still cuts the body, just at
    an angle, which is what a driller would do."""
    if dip is not None and dip_direction is not None:
        az = (float(dip_direction) + 180.0) % 360.0       # drill into the dip
        hd = -(90.0 - abs(float(dip)))
        return (az, min(hd, -float(min_dip)), "the attitude the report states")
    if normal is None:
        return None
    v = np.asarray(normal, float)
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        return None
    v = v / n
    if v[2] > 0:
        v = -v                                            # a hole goes downwards
    az = float(np.degrees(np.arctan2(v[0], v[1]))) % 360.0
    hd = float(np.degrees(np.arcsin(max(-1.0, min(1.0, v[2])))))
    return (az, min(hd, -float(min_dip)), "square to the body's own plane")


def propose(cent, grade, width, hole, rim_idx, rim_dir, tested, step,
            dip=None, dip_direction=None, normal=None, surface=None,
            max_targets=12, min_separation=None):
    """Step-outs from the rim of a body, best first.

    `tested` is the sampled existing drilling; `step` is one drill spacing, the
    distance this pattern has already shown carries between holes. A proposal
    whose ground a hole already passes through is dropped, and proposals closer
    than half a spacing to one another are merged, because two rigs do not go
    in the same place."""
    out = []
    if not len(rim_idx):
        return out
    sep = float(min_separation if min_separation is not None else step * 0.5)
    from scipy.spatial import cKDTree
    tree = cKDTree(tested) if len(tested) else None
    order = np.argsort(-(np.asarray(grade, float)[rim_idx]
                         * np.asarray(width, float)[rim_idx]))
    taken = []
    for j in order:
        i = int(rim_idx[j])
        d = _unit(np.asarray(rim_dir[j], float))
        if not d.any():
            continue
        p = np.asarray(cent[i], float) + d * step
        if tree is not None:
            near = float(tree.query(p)[0])
            if near < sep:
                continue                      # a hole already goes through here
        else:
            near = None
        if any(np.linalg.norm(p - q) < sep for q in taken):
            continue
        gt = float(grade[i]) * float(width[i])
        rec = {"x": round(float(p[0]), 1), "y": round(float(p[1]), 1),
               "z": round(float(p[2]), 1),
               "from_hole": str(hole[i]), "grade": round(float(grade[i]), 3),
               "width_m": round(float(width[i]), 1), "gram_metres": round(gt, 1),
               "step_m": round(float(step), 1),
               "bearing": round(float((np.degrees(np.arctan2(d[0], d[1]))) % 360.0), 0),
               "nearest_drilling_m": round(near, 1) if near is not None else None}
        att = hole_attitude(dip, dip_direction, normal)
        if att and surface is not None:
            az, hd, why = att
            sz = surface(p[0], p[1])
            if sz is not None:
                c = collar_for(p, az, hd, sz)
                if c:
                    rec["collar"] = c
                    rec["attitude_from"] = why
        taken.append(p)
        out.append(rec)
        if len(out) >= max_targets:
            break
    return out


def describe(t):
    """One line a person can act on."""
    if not t:
        return None
    bits = ["%s steps out %g m from %s, which cut %g m at %g"
            % (("A hole" if not t.get("collar") else "A %g m hole"
                % t["collar"]["depth_m"]), t["step_m"], t["from_hole"],
               t["width_m"], t["grade"])]
    if t.get("collar"):
        bits.append("collared at %g E %g N on %g deg, dipping %g"
                    % (t["collar"]["x"], t["collar"]["y"], t["collar"]["az"],
                       t["collar"]["dip"]))
    if t.get("nearest_drilling_m") is not None:
        bits.append("nearest existing drilling %g m" % t["nearest_drilling_m"])
    return "; ".join(bits)
