"""Where the structure makes room for ore.

Grade does not sit evenly along a vein. It collects where the structure opens:
at a bend, where two veins splay from one another, in the relay between two
en-echelon segments, where one trend crosses another, and at a fold hinge. Those
are the sites a structural geologist draws on a plan before deciding where to
drill, and MMP has had the geometry to find them since the bodies got their own
attitudes and spines — a bend is a curvature maximum along a vein's own trace,
and a relay is a measurable relationship between two of them.

What this module does NOT do is assert that ore is there. It finds the sites;
`scripts/test_structure.py` and the corpus harness ask whether the grade
actually concentrates at them, because a circle drawn where two veins cross
looks geological whether or not it means anything. Everything here is geometry
over data already in the model: per-vein contact midpoints and attitudes.

Not derivable here, and stated so nobody looks for it:

  * contacts between rocks of differing strength need LOGGED LITHOLOGY, and no
    public project carries any — `rock_units` arrives only with an uploaded
    drill database. `competency_contacts` is built for that case and simply
    returns nothing without it.
  * fracture density needs RQD or fracture counts, which even a client's assay
    table rarely carries. It belongs to a geotech log.
  * a deformation corridor cannot be read from the geophysics MMP extracts:
    93 projects mention surveys and every mention is prose with a page number
    and no coordinates. `corridors` therefore infers trends from the BODIES —
    a set of bodies sharing a strike is a trend, and where two trends cross is
    the intersection site — which is evidence of the same thing from the rock
    rather than from a map nobody can parse.
"""
import numpy as np


KINDS = ("bend", "splay", "relay", "intersection", "hinge", "competency")

# How competent a logged rock is, for a contact that concentrates strain. Only
# the ORDER matters: a contact between rocks far apart on this scale is a
# rheological boundary, and ore collects on those. Codes are matched as
# substrings of the logged unit, longest first, because a log says "QZITE" and
# "qtz-ite" and "Quartzite" for the same rock.
COMPETENCE = {
    "quartzite": 9, "qzite": 9, "chert": 9, "bif": 9, "ironstone": 9,
    "iron formation": 9, "jasper": 9,
    "granite": 8, "granodiorite": 8, "diorite": 8, "monzonite": 8,
    "syenite": 8, "porphyry": 8, "tonalite": 8, "pegmatite": 8, "aplite": 8,
    "gabbro": 7, "diabase": 7, "dolerite": 7, "basalt": 7, "andesite": 7,
    "rhyolite": 7, "dacite": 7, "tuff": 6, "volcanic": 6,
    "dolomite": 6, "dolostone": 6, "limestone": 5, "marble": 5,
    "sandstone": 5, "arkose": 5, "greywacke": 5, "wacke": 5,
    "gneiss": 6, "amphibolite": 6, "skarn": 6,
    "siltstone": 3, "mudstone": 2, "shale": 2, "argillite": 2, "slate": 3,
    "schist": 3, "phyllite": 2, "serpentinite": 2, "talc": 1,
    "ultramafic": 3, "komatiite": 4, "graphite": 1, "graphitic": 1,
}
_COMP_KEYS = sorted(COMPETENCE, key=len, reverse=True)


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.array([1.0, 0.0])


def _trace(mid, bins=12, smooth=1):
    """A body's plan-view trace: a short polyline through its contacts.

    The same construction the vein builder's spine uses, at a coarser scale,
    because a bend is a feature of the body and not of the sampling.
    """
    P = np.asarray(mid, float)[:, :2]
    if len(P) < 4:
        return None
    c = P.mean(0)
    C = P - c
    try:
        v0 = _unit(np.linalg.svd(C, full_matrices=False)[2][0])
    except Exception:
        return None
    t = C @ v0
    n = int(np.clip(bins, 3, max(3, len(P) // 2)))
    edges = np.linspace(t.min(), t.max(), n + 1)
    k = np.clip(np.searchsorted(edges, t) - 1, 0, n - 1)
    pts = [P[k == b].mean(0) for b in range(n) if (k == b).any()]
    S = np.asarray(pts, float)
    if len(S) < 3:
        return None
    for _ in range(int(smooth)):
        S[1:-1] = (S[:-2] + 2 * S[1:-1] + S[2:]) / 4.0
    return S


def _strike_of(S):
    """Plan-view strike direction of a trace, as a unit vector."""
    d = S[-1] - S[0]
    return _unit(d)


def bends(mid, min_turn=25.0, min_arm=30.0):
    """Where a body's own trace turns.

    A bend is a curvature maximum along the trace, reported with how far it
    turns. Only turns of at least `min_turn` degrees over arms of at least
    `min_arm` metres count — below that it is the sampling wobbling, not the
    vein bending.
    """
    S = _trace(mid)
    if S is None or len(S) < 5:
        return []
    # Over a WINDOW of segments, not between two adjacent ones. The trace is
    # smoothed — a trace that is not smoothed reports the sampling's wobble as
    # geology — and smoothing spreads a turn across several segments, so
    # adjacent-pair angles miss a 50-degree bend entirely while the trend
    # either side of it differs by the full 50.
    seg = np.diff(S, axis=0)
    L = np.linalg.norm(seg, axis=1)
    # The arm scales with the body. A turn is spread over several segments by
    # the smoothing, so an arm of one segment still reads a 50-degree bend as
    # 24 — just under any sane threshold — while an arm of a seventh of the
    # trace reads it as 36 and a geologist reads it as 50.
    arm = max(float(min_arm), 0.15 * float(L.sum()))
    out = []
    for i in range(1, len(S) - 1):
        # grow an arm each way until it is long enough to mean something
        ja = i - 1
        while ja > 0 and L[ja:i].sum() < arm:
            ja -= 1
        jb = i + 1
        while jb < len(S) - 1 and L[i:jb].sum() < arm:
            jb += 1
        la = float(L[ja:i].sum())
        lb = float(L[i:jb].sum())
        if la < min_arm or lb < min_arm:
            continue
        u = _unit(S[i] - S[ja])
        v = _unit(S[jb] - S[i])
        ang = float(np.degrees(np.arccos(np.clip(float(np.dot(u, v)), -1, 1))))
        if ang >= min_turn:
            out.append({"kind": "bend", "xy": S[i].copy(),
                        "turn_deg": round(ang, 1),
                        "arm_m": round(float(min(la, lb)), 1)})
    # one bend per turn: keep the sharpest of each run of neighbours
    keep = []
    for b in out:
        if keep and float(np.linalg.norm(b["xy"] - keep[-1]["xy"])) < arm:
            if b["turn_deg"] > keep[-1]["turn_deg"]:
                keep[-1] = b
            continue
        keep.append(b)
    return keep


def hinges(mid, min_turn=20.0):
    """Where a flat-lying body's horizon reverses — a fold hinge.

    The mirror of `bends`: a bend turns in plan, a hinge turns in section. Read
    off the body's elevation against distance along its own dip direction, so a
    horizon that rolls over reports the roll's position and how sharp it is.
    """
    P = np.asarray(mid, float)
    if len(P) < 8:
        return []
    C = P - P.mean(0)
    # The direction a fold CLOSES in is the plan direction along which the
    # elevation varies most — perpendicular to the hinge line. It is not the
    # body's second plan axis: a horizon folded along its own long axis has no
    # elevation change across that axis at all, and looking there finds no
    # hinge however hard the horizon rolls.
    best, across = -1.0, None
    for a_deg in np.arange(0.0, 180.0, 10.0):
        a = np.radians(a_deg)
        d = np.array([np.sin(a), np.cos(a), 0.0])
        tt = C @ d
        nb = int(np.clip(len(P) // 4, 3, 10))
        ed = np.linspace(tt.min(), tt.max(), nb + 1)
        kk = np.clip(np.searchsorted(ed, tt) - 1, 0, nb - 1)
        mz = [float(C[kk == b, 2].mean()) for b in range(nb) if (kk == b).any()]
        if len(mz) < 3:
            continue
        v = float(np.ptp(mz))
        if v > best:
            best, across = v, d
    if across is None:
        return []
    t = C @ across
    z = C[:, 2]
    n = int(np.clip(len(P) // 4, 3, 10))
    edges = np.linspace(t.min(), t.max(), n + 1)
    k = np.clip(np.searchsorted(edges, t) - 1, 0, n - 1)
    tz = [(float(t[k == b].mean()), float(z[k == b].mean()))
          for b in range(n) if (k == b).sum() >= 2]
    if len(tz) < 3:
        return []
    # The dip of the arm BEFORE against the dip of the arm AFTER, over a
    # window — not between two adjacent bins. A fold's hinge turns 44 degrees
    # from limb to limb while no two adjacent bins differ by ten, so an
    # adjacent-bin test finds a textbook arch perfectly flat.
    T = np.array([x[0] for x in tz])
    Z = np.array([x[1] for x in tz])
    half = max(2, len(T) // 4)
    out = []
    for i in range(half, len(T) - half):
        a0 = np.polyfit(T[i - half:i + 1], Z[i - half:i + 1], 1)[0]
        a1 = np.polyfit(T[i:i + half + 1], Z[i:i + half + 1], 1)[0]
        turn = abs(float(np.degrees(np.arctan(a1)) - np.degrees(np.arctan(a0))))
        if turn >= min_turn:
            xy = P.mean(0)[:2] + across[:2] * T[i]
            out.append({"kind": "hinge", "xy": xy, "turn_deg": round(turn, 1),
                        "z": round(float(Z[i] + P.mean(0)[2]), 1)})
    # one hinge per closure: the sharpest of each run
    keep = []
    for h in out:
        if keep and float(np.linalg.norm(h["xy"] - keep[-1]["xy"])) < 1e-6:
            continue
        if keep and h["turn_deg"] > keep[-1]["turn_deg"] and \
                float(np.linalg.norm(h["xy"] - keep[-1]["xy"])) < 0.3 * float(np.ptp(T)):
            keep[-1] = h
            continue
        if keep and float(np.linalg.norm(h["xy"] - keep[-1]["xy"])) < 0.3 * float(np.ptp(T)):
            continue
        keep.append(h)
    return keep


def _geom(mid):
    """One body reduced to what a pairwise test needs."""
    P = np.asarray(mid, float)
    S = _trace(P)
    if S is None:
        if len(P) < 3:
            return None
        c = P[:, :2].mean(0)
        S = np.vstack([c, c])
    u = _strike_of(S) if len(S) > 1 else np.array([1.0, 0.0])
    nrm = np.array([-u[1], u[0]])
    t = (P[:, :2] - S[0]) @ u
    return {"P": P, "S": S, "u": u, "n": nrm,
            "lo": S[0] + u * float(t.min()), "hi": S[0] + u * float(t.max()),
            "len": float(t.max() - t.min()),
            "mid": P[:, :2].mean(0), "z": (float(P[:, 2].min()), float(P[:, 2].max()))}


PAIR_MAX = 40


def pairs(bodies, gap_max=250.0, splay_angle=(8.0, 45.0), relay_angle=20.0,
          cross_angle=45.0, z_overlap=0.25, most=PAIR_MAX):
    """Splays, relays and intersections, as relations between two bodies.

    - a SPLAY is two bodies whose strikes differ modestly and whose ends
      approach: one branching from the other.
    - a RELAY is two sub-parallel bodies that overlap along strike while
      standing apart across it — the step between two en-echelon segments,
      where the strain between them has to go somewhere.
    - an INTERSECTION is two bodies whose traces cross at a high angle, which
      is the corridor-crossing case.

    Bodies that do not overlap in depth are not related: two veins a kilometre
    apart vertically do not make a relay, whatever their plan view says.
    """
    # Quadratic in the bodies, and a property can come back with hundreds of
    # them — Alamos has 239. Every pair of those is a candidate relation, so
    # the honest reading is that a property with 239 bodies does not have
    # thirty thousand structural sites; it has a fragmented model. The largest
    # bodies are the ones a relation between could mean anything.
    bods = sorted(bodies, key=lambda b: -len(b))[:int(most)]
    G = [g for g in (_geom(b) for b in bods) if g is not None]
    out = []
    for i in range(len(G)):
        for j in range(i + 1, len(G)):
            a, b = G[i], G[j]
            # depth overlap first, so a plan-view coincidence is not a relation
            lo = max(a["z"][0], b["z"][0])
            hi = min(a["z"][1], b["z"][1])
            span = min(a["z"][1] - a["z"][0], b["z"][1] - b["z"][0])
            if span > 1e-6 and (hi - lo) < z_overlap * span:
                continue
            ang = float(np.degrees(np.arccos(
                np.clip(abs(float(np.dot(a["u"], b["u"]))), 0, 1))))
            ends = [(np.linalg.norm(p - q), (p + q) / 2.0)
                    for p in (a["lo"], a["hi"]) for q in (b["lo"], b["hi"])]
            gap, where = min(ends, key=lambda e: e[0])
            # along/across separation in the first body's own frame
            dmid = b["mid"] - a["mid"]
            along = abs(float(dmid @ a["u"]))
            across = abs(float(dmid @ a["n"]))
            rec = {"a": i, "b": j, "angle_deg": round(ang, 1),
                   "gap_m": round(float(gap), 1),
                   "along_m": round(along, 1), "across_m": round(across, 1)}
            if ang >= cross_angle and along < 0.5 * (a["len"] + b["len"]):
                out.append(dict(rec, kind="intersection", xy=where))
            elif splay_angle[0] <= ang <= splay_angle[1] and gap <= gap_max:
                out.append(dict(rec, kind="splay", xy=where))
            elif (ang <= relay_angle and across > 1e-6
                  and along < 0.75 * max(a["len"], b["len"], 1.0)
                  and across <= gap_max):
                out.append(dict(rec, kind="relay",
                                xy=(a["mid"] + b["mid"]) / 2.0))
    return out


def corridors(bodies, tol=20.0, least=3, most=PAIR_MAX):
    """Trends shared by several bodies, and where two trends cross.

    The geophysics MMP can read is prose — a method and a page number — so a
    deformation corridor cannot be mapped from it. The bodies themselves are
    better evidence anyway: several bodies on one strike ARE a corridor, and
    where two such corridors cross is the site. Reported with how many bodies
    back each trend, so a trend of three is not read as a trend of twelve.
    """
    bods = sorted(bodies, key=lambda b: -len(b))[:int(most)]
    G = [g for g in (_geom(b) for b in bods) if g is not None]
    if len(G) < least:
        return []
    az = np.array([float(np.degrees(np.arctan2(g["u"][0], g["u"][1])) % 180.0)
                   for g in G])
    used = np.zeros(len(G), bool)
    trends = []
    for _ in range(6):
        if used.all():
            break
        free = np.nonzero(~used)[0]
        best = None
        for k in free:
            d = np.abs((az[free] - az[k] + 90.0) % 180.0 - 90.0)
            m = free[d <= tol]
            if best is None or len(m) > len(best[1]):
                best = (az[k], m)
        if best is None or len(best[1]) < least:
            break
        trends.append({"azimuth": round(float(np.mean(az[best[1]])), 1),
                       "bodies": [int(x) for x in best[1]]})
        used[best[1]] = True
    out = []
    for i in range(len(trends)):
        for j in range(i + 1, len(trends)):
            d = abs((trends[i]["azimuth"] - trends[j]["azimuth"] + 90.0) % 180.0 - 90.0)
            if d < 25.0:
                continue
            ca = np.mean([G[k]["mid"] for k in trends[i]["bodies"]], axis=0)
            cb = np.mean([G[k]["mid"] for k in trends[j]["bodies"]], axis=0)
            out.append({"kind": "intersection", "xy": (ca + cb) / 2.0,
                        "angle_deg": round(float(d), 1),
                        "trend_a": trends[i]["azimuth"],
                        "trend_b": trends[j]["azimuth"],
                        "n_a": len(trends[i]["bodies"]),
                        "n_b": len(trends[j]["bodies"]), "from": "corridors"})
    return out


def competence(code):
    """How competent a logged rock code is, or None if it is not recognised."""
    s = str(code or "").strip().lower()
    if not s:
        return None
    for k in _COMP_KEYS:
        if k in s:
            return COMPETENCE[k]
    return None


def competency_contacts(intervals, at, min_step=3):
    """Contacts between logged rocks of differing strength.

    Needs LOGGED LITHOLOGY, which no public project carries — `rock_units`
    arrives only with an uploaded drill database — so this returns nothing on
    the public corpus by construction rather than by accident. `intervals` is
    (hole, from_m, to_m, code) down the hole and `at(hole, depth)` desurveys.

    A contact counts when the two rocks sit at least `min_step` apart on the
    competence scale: a shale against a granite is a rheological boundary that
    takes up strain, a shale against a siltstone is not.
    """
    out = []
    byhole = {}
    for h, f, t, code in intervals or ():
        byhole.setdefault(str(h), []).append((float(f), float(t), code))
    for h, runs in byhole.items():
        runs.sort()
        for (f0, t0, c0), (f1, t1, c1) in zip(runs, runs[1:]):
            if abs(t0 - f1) > max(1.0, 0.1 * (t0 - f0)):
                continue                      # a gap, not a contact
            a, b = competence(c0), competence(c1)
            if a is None or b is None or abs(a - b) < min_step:
                continue
            try:
                p = at(h, (t0 + f1) / 2.0)
            except Exception:
                continue
            out.append({"kind": "competency", "xyz": np.asarray(p, float),
                        "hole": h, "depth_m": round((t0 + f1) / 2.0, 1),
                        "from_rock": str(c0), "to_rock": str(c1),
                        "step": int(abs(a - b))})
    return out


def sites(bodies, mids=None):
    """Every structural site these bodies imply, in one list.

    `bodies` is a list of per-body contact-midpoint arrays. Each site carries a
    `kind` and a plan position `xy`; depth is left out on purpose for the
    pairwise kinds, because a relay or a crossing is a column in the ground
    rather than a point in it.
    """
    out = []
    for b in sorted(bodies, key=lambda x: -len(x))[:PAIR_MAX]:
        out.extend(bends(b))
        out.extend(hinges(b))
    out.extend(pairs(bodies))
    out.extend(corridors(bodies))
    return out
