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
import os

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


def intercepts_w(pts, g, hid, threshold):
    """Mineralised intercepts with the thickness each one actually shows.

    A wireframe is a surface through the intercepts inflated to their widths, so
    the width is not an afterthought: a hole that cuts two metres of vein and one
    that cuts forty metres of massive sulphide describe bodies of different
    thickness, and giving both the same one throws away the only measurement of it
    there is."""
    pts = np.asarray(pts, float)
    g = np.asarray(g, float)
    hid = np.asarray(hid)
    keep = g >= threshold
    if not keep.any():
        return np.zeros((0, 3)), np.zeros(0, int), np.zeros(0)
    P, H = pts[keep], hid[keep]
    out_p, out_h, out_w = [], [], []
    for h in np.unique(H):
        q = P[H == h]
        if len(q) == 1:
            out_p.append(q[0]); out_h.append(h); out_w.append(2.0)
            continue
        q = q[np.argsort(q[:, 2])[::-1]]
        gap = np.r_[0.0, np.linalg.norm(np.diff(q, axis=0), axis=1)]
        for part in np.split(np.arange(len(q)), np.where(gap > 12.0)[0]):
            if not len(part):
                continue
            seg = q[part]
            out_p.append(seg.mean(0))
            out_h.append(h)
            # the intercept's downhole length, floored so one composite still has
            # a thickness
            out_w.append(max(float(np.linalg.norm(seg[-1] - seg[0])), 2.0))
    return np.asarray(out_p, float), np.asarray(out_h), np.asarray(out_w, float)


# ---------------------------------------------------------------------------
# The body as geometry, not as a cloud
#
# The wireframe already knows the shape: a surface triangulated through the
# mineralised intercepts and inflated to the thickness each intercept actually
# shows. That shape was then sampled into a point cloud, the cloud was snapped
# to the block lattice, and the viewer drew the cells — so a vein that pinches
# to half a metre between two holes and swells to thirty in a third arrived on
# screen as the same row of boxes either way. The geometry was built and thrown
# away twice over.
#
# These keep it. The triangles are the ones the alpha criterion already
# accepted, so the surface stops where the drilling stops; the thickness is the
# intercepts' own, so it pinches and swells where they do.


def _boundary_edges(faces):
    """Edges used by exactly one triangle, in that triangle's own winding."""
    e = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    key = np.sort(e, axis=1)
    _u, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    return e[cnt[inv.ravel()] == 1]


def sheet_mesh(cent, faces, normal, thick):
    """A closed surface: the triangulated sheet offset to each half-thickness.

    Top and bottom are the same triangulation pushed either way along the body's
    across-axis by half the local thickness, and the rim is stitched between
    them, so the result is a solid that is thin where the intercepts are thin."""
    faces = np.asarray(faces, int)
    if not len(faces):
        return None
    n = len(cent)
    half = (np.asarray(thick, float) * 0.5)[:, None]
    nrm = np.asarray(normal, float)
    V = np.vstack([cent + half * nrm, cent - half * nrm])
    T = [faces, faces[:, ::-1] + n]
    be = _boundary_edges(faces)
    if len(be):
        a, b = be[:, 0], be[:, 1]
        T.append(np.stack([a, b, b + n], 1))
        T.append(np.stack([a, b + n, a + n], 1))
    return V, np.vstack(T).astype(np.int64)


def tube_mesh(p0, p1, r0, r1, k=8):
    """A prism along one link, as thick at each end as that intercept is.

    What a single fence of holes gives: no surface to triangulate, but the
    ground between two intercepts on a section is still part of the body."""
    p0 = np.asarray(p0, float); p1 = np.asarray(p1, float)
    d = p1 - p0
    L = float(np.linalg.norm(d))
    if L < 1e-6:
        return None
    d /= L
    t = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, t); u /= np.linalg.norm(u)
    v = np.cross(d, u)
    a = np.linspace(0.0, 2.0 * np.pi, k, endpoint=False)
    ring = np.cos(a)[:, None] * u + np.sin(a)[:, None] * v
    V = np.vstack([p0 + max(r0, 0.25) * ring, p1 + max(r1, 0.25) * ring])
    i = np.arange(k); j = (i + 1) % k
    T = [np.stack([i, j, j + k], 1), np.stack([i, j + k, i + k], 1)]
    f = np.arange(1, k - 1)
    T.append(np.stack([np.zeros_like(f), f + 1, f], 1))          # cap at p0
    T.append(np.stack([np.full_like(f, k), f + k, f + k + 1], 1))  # cap at p1
    return V, np.vstack(T).astype(np.int64)


def tet_boundary(cent, cells):
    """The outside of a solid: tetrahedron faces that only one cell owns.

    Each is wound to face away from the vertex it was cut from, so the solid
    lights correctly instead of turning inside out face by face."""
    c = np.asarray(cells, int)
    if not len(c):
        return None
    f = np.vstack([c[:, [0, 1, 2]], c[:, [0, 3, 1]],
                   c[:, [0, 2, 3]], c[:, [1, 3, 2]]])
    opp = np.concatenate([c[:, 3], c[:, 2], c[:, 1], c[:, 0]])
    key = np.sort(f, axis=1)
    _u, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    keep = cnt[inv.ravel()] == 1
    f, opp = f[keep], opp[keep]
    if not len(f):
        return None
    A, B, C = cent[f[:, 0]], cent[f[:, 1]], cent[f[:, 2]]
    nz = np.cross(B - A, C - A)
    flip = np.einsum("ij,ij->i", nz, A - cent[opp]) < 0
    f[flip] = f[flip][:, ::-1]
    return cent, f.astype(np.int64)


def _merge(parts):
    """One vertex and index buffer from several meshes."""
    parts = [p for p in parts if p is not None and len(p[1])]
    if not parts:
        return None
    V, F, off = [], [], 0
    for v, f in parts:
        V.append(np.asarray(v, float))
        F.append(np.asarray(f, np.int64) + off)
        off += len(v)
    return np.concatenate(V), np.concatenate(F)


def intercepts_g(pts, g, hid, threshold):
    """Mineralised intercepts with their thickness AND their grade.

    `intercepts_w` answers how thick the body is where each hole cut it. Asking
    where to drill next needs the other half of that: a boundary intercept of
    thirty metres at five grams is a target and one of one metre at a third of a
    gram is not, and nothing downstream can tell them apart from thickness
    alone. Returns (centroids, holes, widths, grades) — the grade being the
    length-weighted average over the intercept, which is what a release would
    quote for it."""
    pts = np.asarray(pts, float)
    g = np.asarray(g, float)
    hid = np.asarray(hid)
    keep = g >= threshold
    if not keep.any():
        return (np.zeros((0, 3)), np.zeros(0, int), np.zeros(0), np.zeros(0))
    P, G, H = pts[keep], g[keep], hid[keep]
    out_p, out_h, out_w, out_g = [], [], [], []
    for h in np.unique(H):
        sel = H == h
        q, qg = P[sel], G[sel]
        order = np.argsort(q[:, 2])[::-1]
        q, qg = q[order], qg[order]
        if len(q) == 1:
            out_p.append(q[0]); out_h.append(h); out_w.append(2.0); out_g.append(float(qg[0]))
            continue
        gap = np.r_[0.0, np.linalg.norm(np.diff(q, axis=0), axis=1)]
        for part in np.split(np.arange(len(q)), np.where(gap > 12.0)[0]):
            if not len(part):
                continue
            seg, sg = q[part], qg[part]
            out_p.append(seg.mean(0))
            out_h.append(h)
            out_w.append(max(float(np.linalg.norm(seg[-1] - seg[0])), 2.0))
            out_g.append(float(sg.mean()))
    return (np.asarray(out_p, float), np.asarray(out_h),
            np.asarray(out_w, float), np.asarray(out_g, float))


def boundary_of(cent, rot, reach):
    """The rim of the sheet: vertices on its edge, with the way out of it.

    The alpha triangulation that draws the body already stops where the
    drilling stops, so its boundary IS the edge of what has been tested. Each
    boundary vertex comes back with a unit direction, in the body's own plane,
    pointing away from the body — which is the direction the next hole goes."""
    R = rot if rot is not None else np.eye(3)
    Q = cent @ R.T
    faces = _alpha_faces(Q[:, :2], reach, xyz=cent)
    if not len(faces):
        return np.zeros(0, int), np.zeros((0, 3))
    e = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    key = np.sort(e, axis=1)
    _u, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    rim = e[cnt[inv.ravel()] == 1]
    if not len(rim):
        return np.zeros(0, int), np.zeros((0, 3))
    uv = Q[:, :2]
    inside = uv.mean(0)
    acc = {}
    for a, b in rim:
        d = uv[b] - uv[a]
        n = np.array([d[1], -d[0]], float)        # normal to the rim edge
        ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n /= ln
        mid = (uv[a] + uv[b]) / 2.0
        if np.dot(n, mid - inside) < 0:           # point it away from the body
            n = -n
        for v in (a, b):
            acc.setdefault(int(v), []).append(n)
    idx = sorted(acc)
    dirs = []
    for v in idx:
        n = np.mean(acc[v], axis=0)
        ln = np.linalg.norm(n)
        n = n / ln if ln > 1e-9 else np.array([1.0, 0.0])
        dirs.append(R.T @ np.array([n[0], n[1], 0.0]))   # back into world axes
    return np.asarray(idx, int), np.asarray(dirs, float)


def _vein(segments, at, threshold, rot, aniso, reach, thickness, info,
          vein_nodes=240_000, max_points=600_000, vein_reach=None, style=None,
          traces=None):
    """The vein-modelling path: contacts, a reference surface, a thickness.

    `reach` is the link reach — the distance at which the old triangulation was
    allowed to close a face. It is deliberately tight, because a triangulated
    sheet that bridges a 100 m gap is a sheet over untested ground. The vein
    builder does not work that way: it splits at gaps into separate veins, it
    rejects a node where the two walls disagree, and it gives a node full reach
    only where contacts surround it, falling back to a quarter of it where they
    do not. So it can be told how far a vein is believed to continue —
    `vein_reach`, the same distance the zone finder uses to decide two holes are
    in one body — without the sheet running away. At the link reach it was doing
    the opposite: splitting one 121-hole vein at Enchi into separate bodies the
    zone finder had already joined. MMP_VEINREACH=link restores the old one."""
    res = float(reach)                 # the data's own spacing: lattice resolution
    if vein_reach and os.environ.get("MMP_VEINREACH") != "link":
        reach = max(float(reach), float(vein_reach))
    if os.environ.get("MMP_VEINRES") == "reach":
        res = None                     # the old behaviour: resolution follows reach
    try:
        from minemodelingpro import veins as VEIN
        c = VEIN.contacts(segments, at, threshold)
        if not len(c["mid"]):
            return None
        out = VEIN.build(c, rot=rot, aniso=aniso, reach=reach, res=res,
                         min_thick=0.3, max_thick=(float(thickness) * 3
                                                   if thickness else None),
                         max_nodes=int(max(vein_nodes, 2_000)), style=style,
                         traces=traces)
        if out is None:
            return None
        info.update({"mode": "vein", "intercepts": out["n_contacts"],
                     "contacts": out["n_contacts"] * 2,
                     "reach_m": out["reach_m"], "step_m": out["step_m"],
                     "points": int(len(out["pts"])),
                     "triangles": int(len(out["f"]))})
        tv = np.asarray(out["thickness"], float)
        if len(tv):
            info["_thick"] = tv
        info["_mesh"] = (np.asarray(out["v"], float), np.asarray(out["f"], np.int64))
        info["_contacts"] = (np.asarray(out["hw"], float), np.asarray(out["fw"], float))
        # which vein each face and each contact belongs to. A body is often
        # several veins, and without this the page can only show or hide the
        # whole body: there was no way to look at one vein of nine.
        info["_face_vein"] = np.asarray(out.get("face_vein", ()), np.int32)
        info["_contact_vein"] = np.asarray(out.get("contact_vein", ()), np.int32)
        info["_vein_parts"] = out.get("vein_parts") or []
        info["veins"] = int(out.get("veins") or 1)
        # the same cap the triangulated path applies. Without it a client's
        # six-hundred-hole database can hand the estimator an unbounded cloud,
        # and the modeller has 1 GB — which the kernel answers by killing it
        # mid-build, with nothing on the page but a job that never finishes.
        S = np.asarray(out["pts"], float)
        if len(S) > max_points:
            S = S[np.linspace(0, len(S) - 1, max_points).astype(int)]
            info["points"] = int(len(S))
            info["thinned_to"] = int(max_points)
        return S, info
    except Exception as exc:                                   # noqa: BLE001
        info["vein_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        return None


def _alpha_faces(uv, reach, xyz=None):
    """Delaunay triangles small enough to be real ground between holes.

    This is the alpha-shape criterion, and it is what keeps a wireframe honest: a
    triangle whose circumcircle is wider than the drill pattern is not a piece of
    the body, it is the hull closing over ground nobody tested.

    The triangulation is done in the body's own plane, because that is what
    gives a sheet its topology. The TEST is done on the real coordinates, and
    that distinction was missing for as long as this function has existed: a
    criterion applied to the projection measures the body's SHADOW, where the
    across-strike separation between two parallel veins is flattened to nothing.
    On a vein system drilled 25 m apart with two veins 60 m apart across strike,
    77% of the accepted triangles joined one vein to the other — edges measuring
    35 m in projection and 70 m in the rock — and the wireframe came out as one
    crumpled sheet welded across the barren ground between them, which is also
    ground the estimator then filled with grade.

    `xyz` is measured in the DEPOSIT'S OWN FRAME, stretched by the style's
    anisotropy, which is the second half of the same fault. An isotropic reach
    cannot tell 60 m along strike — ordinary continuity — from 60 m across it,
    which is the next vein. `links()` has always compared distances in that
    warped frame; this did not."""
    from scipy.spatial import Delaunay
    if len(uv) < 3:
        return np.zeros((0, 3), int)
    try:
        tri = Delaunay(uv)
    except Exception:
        return np.zeros((0, 3), int)
    # MMP_ALPHA=projected restores the old test — measured in the body's shadow,
    # isotropically — so the change stays A/B-able against the filed statements.
    if os.environ.get("MMP_ALPHA") == "projected":
        xyz = None
    P = np.asarray(xyz if xyz is not None else uv, float)
    keep = []
    for sx in tri.simplices:
        a, b, c = P[sx[0]], P[sx[1]], P[sx[2]]
        la = np.linalg.norm(b - c); lb = np.linalg.norm(a - c); lc = np.linalg.norm(a - b)
        s = (la + lb + lc) / 2.0
        ar = max(s * (s - la) * (s - lb) * (s - lc), 1e-12) ** 0.5
        R = la * lb * lc / (4.0 * ar)              # circumradius, in the rock
        if R <= reach and max(la, lb, lc) <= 2.0 * reach:
            keep.append(sx)
    return np.asarray(keep, int) if keep else np.zeros((0, 3), int)


_LATTICE = {}


def _lattice(n):
    """The barycentric grid of a triangle at n divisions, computed once per n."""
    got = _LATTICE.get(n)
    if got is None:
        ii, jj = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        m = (ii + jj) <= n
        got = _LATTICE[n] = (ii[m] / n, jj[m] / n)
    return got


def _fill_triangle(P, W, step, layers, normal):
    """Points covering one triangle of the sheet, at its own thickness.

    Written against arrays rather than three nested Python loops: a wireframe of
    a few hundred triangles at a few thousand points each is most of a model's
    build time otherwise, and it doubled the time to render the larger deposits."""
    a, b, c = P
    L = max(float(np.linalg.norm(b - a)), float(np.linalg.norm(c - a)),
            float(np.linalg.norm(c - b)))
    n = int(np.clip(L / max(step, 1.0), 1, 90))
    u, v = _lattice(n)
    base = a + u[:, None] * (b - a) + v[:, None] * (c - a)
    if layers <= 1:
        return base
    t = W[0] + u * (W[1] - W[0]) + v * (W[2] - W[0])
    ks = np.linspace(-0.5, 0.5, layers)
    off = (ks[:, None] * t[None, :])[:, :, None] * np.asarray(normal, float)
    return (base[None, :, :] + off).reshape(-1, 3)


def _describe(info, V, F, hw, fw, IMP):
    """Per-vein metadata from the isosurface, in the shape the page expects.

    The planar builder had to find the veins before it could draw them, by
    fitting planes — which is exactly what a vein that swings in strike
    defeats. Here the drawing comes first and the veins fall out of it as the
    connected pieces of the surface, each with its own attitude measured from
    its own faces. The page keeps its per-vein chips, its attitudes and its
    contact grouping; only the way they were arrived at has changed.
    """
    try:
        lab = IMP.components(F, len(V))
        order = np.argsort(lab, kind="stable")
        F2, lab = np.asarray(F)[order], lab[order]
        info["_mesh"] = (V, F2)
        info["_face_vein"] = lab.astype(np.int32)
        parts, cmid = [], []
        for k in np.unique(lab):
            sub = F2[lab == k]
            dip, ddir, flat = IMP.attitude(V, sub)
            vid = np.unique(sub)
            pts_k = V[vid]
            span = float(np.linalg.norm(pts_k.max(0) - pts_k.min(0)))
            parts.append({"vein": int(k), "faces": int(len(sub)),
                          "span_m": round(span, 1), "dip": dip, "dip_dir": ddir,
                          "planar": flat})
            cmid.append(pts_k.mean(0))
        # each wall pair belongs to the piece of surface nearest it, and the
        # vein's thickness is what those intercepts measured, not the lattice
        if len(hw) and parts:
            from scipy.spatial import cKDTree
            mid = (hw + fw) / 2.0
            owner = np.zeros(len(mid), np.int32)
            byv = {}
            for n, q in enumerate(parts):
                byv[q["vein"]] = n
            vid_all = [np.unique(F2[lab == k]) for k in np.unique(lab)]
            trees = [cKDTree(V[v]) for v in vid_all]
            d = np.stack([t.query(mid)[0] for t in trees], axis=1)
            owner = np.argmin(d, axis=1).astype(np.int32)
            info["_contact_vein"] = owner
            for n, q in enumerate(parts):
                m = owner == n
                if not m.any():
                    continue
                R = IMP.plane_frame(np.asarray([0.0, 0.0, 1.0]) if q["dip"] is None
                                    else _normal_of(q["dip"], q["dip_dir"]))
                t = np.abs((fw[m] - hw[m]) @ R[2])
                q["n_contacts"] = int(m.sum())
                q["thick_m"] = round(float(np.median(t)), 2)
        info["_vein_parts"] = parts
        info["veins"] = len(parts)
    except Exception:
        info.pop("_face_vein", None)
        info.pop("_contact_vein", None)


def _normal_of(dip, dip_dir):
    """The upward normal of a plane with this attitude."""
    d, a = np.radians(float(dip)), np.radians(float(dip_dir))
    return np.array([np.sin(a) * np.sin(d), np.cos(a) * np.sin(d), np.cos(d)])


def _implicit(pts, g, hid, threshold, rot, aniso, reach, step, info,
              segments=None, at=None, traces=None, max_points=600_000,
              max_faces=None):
    """The deposit as a signed field, isosurfaced. One builder, every style.

    Inside is the mineralised composite positions; outside is every other
    sample on every hole, which is the evidence that a body stops. Where the
    caller can give wall contacts — the vein case — they pin the zero level
    exactly; where it cannot, the two clouds sign each other and the contact
    falls between them, which is what it means for a porphyry shell.
    """
    try:
        from minemodelingpro import implicit as IMP
        P = np.asarray(pts, float)
        gg = np.asarray(g, float)
        sel = gg >= float(threshold)
        inside = P[sel]
        # Three HOLES, not three points. Thirty composites down one hole are a
        # line, and a field fitted to a line invents a body in every direction
        # nobody drilled — the same fault as drawing a disc through a single
        # intercept, arriving by a different road.
        holes = np.unique(np.asarray(hid)[sel]) if hid is not None else []
        if len(inside) < 6 or len(holes) < 3:
            return None
        contacts = np.zeros((0, 3))
        hw = fw = np.zeros((0, 3))
        if segments is not None and at is not None:
            try:
                from minemodelingpro import veins as VEIN
                c = VEIN.contacts(segments, at, threshold)
                if len(c["mid"]):
                    hw = np.asarray(c["hw"], float)
                    fw = np.asarray(c["fw"], float)
                    contacts = np.vstack([hw, fw])
            except Exception:
                contacts = np.zeros((0, 3))
        outside = np.zeros((0, 3))
        if traces is not None:
            from scipy.spatial import cKDTree
            T = np.asarray(traces[0], float)
            lo, hi = inside.min(0) - reach, inside.max(0) + reach
            T = T[np.all((T >= lo) & (T <= hi), axis=1)]
            if len(T):
                # a sample sitting on mineralisation is not evidence against it
                near = cKDTree(inside).query(T)[0]
                outside = T[near > max(float(step), 2.0)]
        out = IMP.surface(inside, contacts, outside, rot=rot, aniso=aniso,
                          reach=float(reach), step=float(step),
                          max_faces=max_faces)
        if out is None:
            return None
        V, F = np.asarray(out["v"], float), np.asarray(out["f"], np.int64)
        info.update({"mode": "implicit", "intercepts": int(len(inside)),
                     "reach_m": round(float(reach), 1),
                     "step_m": round(float(out["step_m"]), 2),
                     "outside_samples": int(out["n_outside"]),
                     "contacts": int(out["n_contacts"]),
                     "triangles": int(len(F))})
        info["_mesh"] = (V, F)
        if len(contacts):
            h = len(contacts) // 2
            info["_contacts"] = (contacts[:h], contacts[h:])
        # The veins are the pieces the surface came out in. Nothing had to
        # decide how many there are or fit a plane to each: a body that folds
        # is one piece, and two bodies the data keeps apart are two.
        _describe(info, V, F, hw, fw, IMP)
        # the estimator wants the inside of the body, not its skin
        S = _inside_points(V, F, inside, float(out["step_m"]))
        if len(S) > max_points:
            S = S[np.linspace(0, len(S) - 1, max_points).astype(int)]
        info["points"] = int(len(S))
        return S, info
    except Exception as exc:                     # the surface is a bonus layer
        if os.environ.get("MMP_DEBUG"):
            import traceback
            traceback.print_exc()
        info["implicit_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        return None


def _inside_points(V, F, seed, step):
    """A cloud filling the modelled body, for the estimator to search.

    The surface is a skin; the estimator needs the volume. Rather than a
    point-in-mesh test per node, which is slow and fiddly for an open surface,
    this fills between the skin and the drilling that made it.
    """
    from scipy.spatial import cKDTree
    if not len(V):
        return np.asarray(seed, float)
    lo, hi = V.min(0), V.max(0)
    n = np.maximum(np.ceil((hi - lo) / max(step, 1.0)).astype(int) + 1, 2)
    if int(n[0]) * int(n[1]) * int(n[2]) > 4_000_000:
        return np.asarray(seed, float)
    ax = [lo[d] + np.arange(n[d]) * max(step, 1.0) for d in range(3)]
    G = np.stack(np.meshgrid(*ax, indexing="ij"), axis=-1).reshape(-1, 3)
    # inside means closer to the drilling that made the body than to its skin
    ds = cKDTree(V).query(G)[0]
    di = cKDTree(np.asarray(seed, float)).query(G)[0]
    keep = (di <= ds) & (di <= max(step, 1.0) * 6.0)
    S = G[keep]
    return S if len(S) else np.asarray(seed, float)


def wireframe(pts, g, hid, threshold, rot=None, aniso=None, reach=120.0, step=8.0,
              thickness=None, max_points=600_000, segments=None, at=None,
              vein_nodes=240_000, vein_reach=None, style=None, traces=None,
              face_budget=None):
    """The body a qualified person would draw, as a cloud of points inside it.

    The skeleton this replaces joined intercepts with lines, and a line has no
    area: inflating it gave a sleeve a few metres wide between two holes, which
    added almost nothing to a model whose composites already carried that halo.
    A wireframe is a SURFACE through the intercepts — three holes define a patch of
    ground, not two — inflated to the thickness the intercepts themselves show.
    That is the difference between wiring up a drill pattern and modelling a lens.

    For a tabular body the surface is triangulated in the body's own plane, found
    by principal axes, so the sheet follows the vein rather than the map grid. For
    a body with no plane to it — a porphyry, a disseminated blanket — the same
    criterion is applied in three dimensions and the tetrahedra between holes are
    filled instead.

    Either way the alpha criterion stops it at the end of the drilling: a triangle
    wider than the drill pattern is dropped rather than closed over untested
    ground. Returns (points, info)."""
    cent, hole, wid = intercepts_w(pts, g, hid, threshold)
    info = {"intercepts": int(len(cent))}
    # A vein is modelled as a vein where the caller can give the intervals
    # themselves: a reference surface through the contacts with an interpolated
    # thickness, which uses every intercept instead of the fifth of them that
    # survive a Delaunay circumradius test. See veins.py for the measurement
    # that made this necessary. MMP_DOMAIN=alpha restores the triangulation.
    mode = os.environ.get("MMP_DOMAIN", "")
    # A TABULAR body leads with the vein builder, and this is a measurement,
    # not a preference. A vein is represented there as a reference surface plus
    # a THICKNESS — a value, not a geometry — so a 5 m vein is resolved on any
    # lattice. The field has to resolve it geometrically, and on Lawyers it ran
    # at a grid step of 5 to 11.5 m against veins measuring about 5 m: one cell
    # thick, which marching cubes turns into fragments. Letting the field lead
    # there gave 54 pieces with scattered attitudes where the vein builder gave
    # 23 coherent ones dipping consistently 220 to 260. Refining the grid is no
    # answer: that body at 1 m cells is 36 million of them, and the modeller has
    # 1 GB. This is why vein modelling software keeps a dedicated vein type
    # rather than treating a vein as one more implicit surface.
    if segments is not None and at is not None and mode != "alpha":
        got = _vein(segments, at, threshold, rot, aniso, reach, thickness, info,
                    vein_nodes, max_points, vein_reach, style, traces)
        if got is not None:
            return got
    # Otherwise the body as a scalar field — the deposit that is not tabular,
    # and the tabular one the vein builder could not hold. Fitting a surface to
    # anything is what limited every earlier builder: a shape that folds is not
    # a height-field over any plane, and the triangulation it replaces had no
    # notion of inside or outside at all, so a hole that went through a body
    # and cut nothing could not shrink it. Measured over 76 filed statements,
    # this halves the bodies that get no shape drawn for them: 959 of 1,251
    # down to 668. MMP_DOMAIN=alpha restores the triangulation everywhere.
    # MMP_DOMAIN=vein stops here and falls through to the triangulation, which
    # is what shipped before the field builder existed — the control arm.
    if mode != "alpha":
        got = _implicit(pts, g, hid, threshold, rot, aniso, reach, step,
                        info, segments, at, traces, max_points,
                        max_faces=face_budget)
        if got is not None:
            return got
    if len(cent) < 3:
        return (cent if len(cent) else np.zeros((0, 3))), info
    spacing = _intercept_spacing(cent, hole)
    reach = max(float(reach), spacing * 1.6)
    info["reach_m"] = round(float(reach), 1)
    info["intercept_spacing_m"] = round(float(spacing), 1)
    R = rot if rot is not None else np.eye(3)
    across = float(np.asarray(aniso, float)[2]) if aniso is not None else 1.0
    Q = cent @ R.T                                  # body frame: axes by variance
    tabular = across < 0.6 and len(cent) >= 4
    pts_out = [cent]
    mesh = None                                     # the same shape, kept as geometry
    if tabular:
        # the triangulation is in the body's plane; the test is in its own
        # stretched frame, so a long link along strike is allowed and the same
        # distance across strike — where the next vein is — is not
        W = (R / np.asarray(aniso, float)[:, None]) if aniso is not None else R
        faces = _alpha_faces(Q[:, :2], reach, xyz=cent @ W.T)
        info["mode"] = "sheet"
        info["faces"] = int(len(faces))
        nrm = R[2]                                  # across-thickness direction
        th = [float(thickness) if thickness else 0.0] * len(cent)
        th = [max(t, w) for t, w in zip(th, wid)]
        layers = 3
        for f in faces:
            pts_out.append(_fill_triangle(cent[f], [th[i] for i in f], step, layers, nrm))
        if len(faces):
            mesh = sheet_mesh(cent, faces, nrm, th)
            # how thick the body is where the drilling actually measured it.
            # A vein two metres wide across four hundred metres of strike is a
            # few pixels on screen, so the number has to be stated rather than
            # left to the eye. Only the intercepts the surface was built from.
            used = sorted({int(i) for f in faces for i in f})
            if used:
                tv = np.asarray([th[i] for i in used], float)
                info["_thick"] = tv
        if not len(faces):
            # the intercepts lie on one line — a single drill section, or a body
            # tested down one fence of holes. There is no surface to triangulate,
            # but the ground between two holes on a section is still a strip of
            # the body, so it is filled as one at the intercepts' own thickness.
            info["mode"] = "strip"
            ed = links(cent, hole, R, aniso, reach=reach, k=4)
            info["links"] = int(len(ed))
            tubes = []
            for a, b in ed:
                P = np.stack([cent[a], cent[b], cent[b] + nrm * 1e-6])
                pts_out.append(_fill_triangle(
                    P, [th[a], th[b], th[b]], step, layers, nrm))
                tubes.append(tube_mesh(cent[a], cent[b], th[a] / 2.0, th[b] / 2.0))
            mesh = _merge(tubes)
    else:
        from scipy.spatial import Delaunay
        info["mode"] = "solid"
        try:
            tet = Delaunay(cent)
            cells = []
            for sx in tet.simplices:
                V = cent[sx]
                if max(np.linalg.norm(V[i] - V[j]) for i in range(4)
                       for j in range(i + 1, 4)) <= 2.0 * reach:
                    cells.append(sx)
            info["cells"] = len(cells)
            if cells:
                mesh = tet_boundary(cent, np.asarray(cells, int))
            for sx in cells:
                V = cent[sx]
                L = max(np.linalg.norm(V[i] - V[j]) for i in range(4)
                        for j in range(i + 1, 4))
                n = int(np.clip(L / max(step, 1.0), 1, 24))
                ii, jj, kk = np.meshgrid(*(np.arange(n + 1),) * 3, indexing="ij")
                m = (ii + jj + kk) <= n
                u, v, w = ii[m] / n, jj[m] / n, kk[m] / n
                pts_out.append(V[0] + u[:, None] * (V[1] - V[0])
                               + v[:, None] * (V[2] - V[0])
                               + w[:, None] * (V[3] - V[0]))
        except Exception:
            info["cells"] = 0
    S = (np.concatenate([np.atleast_2d(x) for x in pts_out])
         if len(pts_out) > 1 else cent)
    if len(S) > max_points:
        S = S[np.linspace(0, len(S) - 1, max_points).astype(int)]
    info["points"] = int(len(S))
    if mesh is not None and len(mesh[1]):
        # under a private key: this is geometry, not something to serialise into
        # the page's info block, and model3d lifts it out before the JSON is written
        info["_mesh"] = (np.asarray(mesh[0], float), np.asarray(mesh[1], np.int64))
        info["triangles"] = int(len(mesh[1]))
    return S, info
