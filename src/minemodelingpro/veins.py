"""A vein, built the way vein-modelling software builds one.

What this replaces, and why. The wireframe triangulated the MIDPOINTS of the
mineralised intercepts in the body's own plane and kept the triangles whose
circumcircle fitted inside the drill pattern. Measured over three public vein
systems, that keeps about a fifth of the intercepts:

    lawyers-tau            258 intercepts ->  25 triangles,  54 used (21%)
    enchi-ncau             478 intercepts ->  44 triangles,  91 used (19%)
    el-dorado-monserrat    202 intercepts ->  36 triangles,  59 used (29%)

Everything else is outside the domain, and blocks only exist inside the domain
— so on a property with six hundred holes the model covered 16% of the silver
the assays reported, and the veins were not there to look at. Three points are
needed to make a triangle, and most trios of intercepts on a vein system fail
the circumradius test, so most of the drilling simply never arrives.

The method here is the one Leapfrog uses, and it has none of that structure:

  * every mineralised interval gives a HANGINGWALL contact at its start and a
    FOOTWALL contact at its end, rather than one midpoint;
  * a REFERENCE SURFACE is fitted through the midpoints — here as a field
    w(u,v) over the vein's own plane, interpolated from the intercepts;
  * THICKNESS is a second field t(u,v), interpolated from the true widths, with
    a minimum and a maximum, so the vein pinches and swells with the data
    instead of being one number per triangle; and
  * the vein PINCHES OUT at the edge of its support rather than ending in a
    wall, which is what closes it.

Two intercepts make a ribbon and one makes a lens, because an interpolated
field needs no quorum. The boundary is still honest: a node of the lattice is
part of the vein only while an intercept is within reach of it, in the
deposit's own anisotropic frame, so the vein stops where the drilling stops and
follows itself along strike rather than bridging to the vein next door.

TRUE WIDTH, not downhole length. A hole crossing a vein at forty degrees
measures half as much vein as it does core, and the old thickness was the core
length. Each contact pair is projected onto the surface normal, which is the
width a geologist means and the one that belongs in a volume.
"""
import numpy as np


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.array([0.0, 0.0, 1.0])


def contacts(segments, at, threshold, max_gap=12.0):
    """Hangingwall and footwall contacts, one pair per mineralised intercept.

    `segments` are (hole, from_m, to_m, grade) and `at(hole, m)` desurveys a
    depth to a point, so a curved hole gives curved contacts. Consecutive
    intervals in a hole are one intercept while the gap between them is under
    `max_gap`, which is the same rule the old intercept finder used — a hole
    that cuts the vein twice contributes two."""
    by = {}
    for h, f, t, g in segments:
        if g is None or g < threshold or t is None or f is None or t <= f:
            continue
        by.setdefault(id(h), (h, []))[1].append((float(f), float(t), float(g)))
    HW, FW, MID, GR, HOLE, LEN = [], [], [], [], [], []
    for _k, (h, ivs) in by.items():
        ivs.sort()
        run = [ivs[0]]
        for iv in ivs[1:]:
            if iv[0] - run[-1][1] <= max_gap:
                run.append(iv)
            else:
                _emit(h, run, at, HW, FW, MID, GR, HOLE, LEN)
                run = [iv]
        _emit(h, run, at, HW, FW, MID, GR, HOLE, LEN)
    if not HW:
        z = np.zeros((0, 3))
        return {"hw": z, "fw": z, "mid": z, "grade": np.zeros(0),
                "hole": np.zeros(0, object), "down_m": np.zeros(0)}
    return {"hw": np.asarray(HW, float), "fw": np.asarray(FW, float),
            "mid": np.asarray(MID, float), "grade": np.asarray(GR, float),
            "hole": np.asarray(HOLE, object), "down_m": np.asarray(LEN, float)}


def _emit(h, run, at, HW, FW, MID, GR, HOLE, LEN):
    f, t = run[0][0], run[-1][1]
    wsum = sum((b - a) for a, b, _g in run) or 1.0
    g = sum(_g * (b - a) for a, b, _g in run) / wsum
    a3, b3 = np.asarray(at(h, f), float), np.asarray(at(h, t), float)
    HW.append(a3); FW.append(b3); MID.append((a3 + b3) / 2.0)
    GR.append(g); HOLE.append(h); LEN.append(float(t - f))


def frame(mid, rot=None):
    """The vein's own axes: two in its plane, one across it.

    The stated attitude when a report gives one (`rot` from
    `model3d.frame_from_trend`), otherwise the principal axes of the contacts
    themselves. Rows are (along, down-dip, across)."""
    if rot is not None:
        return np.asarray(rot, float)
    P = np.asarray(mid, float)
    if len(P) < 3:
        return np.eye(3)
    C = P - P.mean(0)
    try:
        _u, _s, vt = np.linalg.svd(C, full_matrices=False)
        return vt
    except Exception:
        return np.eye(3)


def true_width(hw, fw, normal):
    """The width across the vein, not the length down the hole."""
    d = np.asarray(fw, float) - np.asarray(hw, float)
    return np.abs(d @ np.asarray(normal, float))


def components(mid, rot=None, aniso=None, reach=60.0):
    """Contacts split into the separate veins they belong to.

    A reference surface is ONE surface. Given two parallel veins it interpolates
    a sheet halfway between them, in barren rock — the same invention the
    triangulation made, by a different road. Two veins are two sets of contacts
    that cannot be walked between in steps shorter than the reach, measured in
    the deposit's own frame, so they are separated here before any surface is
    fitted. Callers normally zone first; this makes the builder right whether
    they did or not."""
    P = np.asarray(mid, float)
    n = len(P)
    if n < 2:
        return [np.arange(n)]
    R = frame(P, rot)
    a = np.asarray(aniso, float) if aniso is not None else np.ones(3)
    a = np.where(a > 1e-6, a, 1.0)
    Q = (P @ R.T) / a
    from scipy.spatial import cKDTree
    tree = cKDTree(Q)
    pairs = tree.query_pairs(float(reach), output_type="ndarray")
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in pairs:
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[ri] = rj
    root = np.array([find(i) for i in range(n)])
    return [np.nonzero(root == r)[0] for r in np.unique(root)]


def build(c, rot=None, aniso=None, reach=60.0, step=None, min_thick=0.3,
          max_thick=None, taper=0.35, max_nodes=240_000, fill_step=None,
          res=None):
    """The veins these contacts describe: surfaces, and the points inside them.

    Contacts that cannot be walked between within the reach are separate veins
    and get separate surfaces, which is what keeps a reference surface from
    being fitted halfway between two of them."""
    mid_all = np.asarray(c["mid"], float)
    if not len(mid_all):
        return None

    def _sub(ix):
        return {k: (np.asarray(v)[ix] if hasattr(v, "__len__") and len(v) == len(mid_all)
                    else v) for k, v in c.items()}

    def _try(ix, budget):
        """One component, with a shorter leash if it will not hold together.

        Reaching further merges contacts that may disagree about where the wall
        is, and `_one` refuses a node where they do — correctly, since the mean
        of two competing walls is a sheet in barren rock. But refusing every
        node means the body comes back as nothing, and a body that has gone
        dark is worse than a body drawn conservatively: measured over the
        corpus, the longer reach alone put twelve more bodies on the page with
        no shape at all. So a component that fails whole is split again at the
        data's own spacing and each piece drawn separately — the old behaviour,
        applied only where the new one does not hold."""
        got = _one(_sub(ix), rot, aniso, reach, step, min_thick, max_thick, taper,
                   budget, fill_step, res=res)
        tight = float(res or 0.0)
        # A surface fitted through N contacts that comes back with fewer than N
        # triangles has collapsed, not been drawn: the agreement test threw away
        # all but a corner of the lattice. That reads on the page exactly like
        # the body going dark — one project came back with two triangles where
        # it had had five hundred and sixty-two — so a collapse is retried on
        # the shorter leash too, and whichever draws more of the body is kept.
        ok = got is not None and len(got["f"]) >= len(ix)
        if ok or not tight or tight >= reach:
            return [got] if got is not None else []
        out = []
        pieces = components(np.asarray(mid_all)[ix], rot, aniso, tight)
        for q in pieces:
            if not len(q):
                continue
            g = _one(_sub(np.asarray(ix)[q]), rot, aniso, tight, step, min_thick,
                     max_thick, taper, max(int(budget / max(1, len(pieces))), 2_000),
                     fill_step, res=res)
            if g is not None:
                out.append(g)
        if got is not None and sum(len(g["f"]) for g in out) <= len(got["f"]):
            return [got]
        return out

    parts = components(mid_all, rot, aniso, reach)
    if len(parts) > 1:
        built = []
        per = max(int(max_nodes / max(1, len(parts))), 2_000)
        for ix in parts:
            if len(ix):
                built.extend(_try(ix, per))
        return _merge_veins(built, mid_all, c) if built else None
    built = _try(np.arange(len(mid_all)), max_nodes)
    return _merge_veins(built, mid_all, c) if built else None


def _merge_veins(built, mid_all, c):
    V, F, P, T, off = [], [], [], [], 0
    for b in built:
        V.append(b["v"]); F.append(np.asarray(b["f"], np.int64) + off)
        off += len(b["v"]); P.append(b["pts"]); T.append(np.asarray(b["thickness"], float))
    return {"v": np.concatenate(V), "f": np.concatenate(F),
            "pts": np.concatenate(P) if P else np.zeros((0, 3)),
            "hw": c["hw"], "fw": c["fw"], "mid": mid_all,
            "thickness": np.concatenate(T) if T else np.zeros(0),
            "n_contacts": int(len(mid_all)), "veins": len(built),
            "step_m": built[0]["step_m"], "reach_m": built[0]["reach_m"],
            "area_nodes": int(sum(b["area_nodes"] for b in built))}


def _one(c, rot=None, aniso=None, reach=60.0, step=None, min_thick=0.3,
         max_thick=None, taper=0.35, max_nodes=240_000, fill_step=None,
         extrap=0.25, res=None):
    """One vein: a closed surface, and the points inside it for the estimator."""
    mid = np.asarray(c["mid"], float)
    if len(mid) < 1:
        return None
    R = frame(mid, rot)
    nrm = _unit(R[2])
    W = np.asarray(aniso, float) if aniso is not None else np.array([1.0, 1.0, 1.0])
    W = np.where(W > 1e-6, W, 1.0)

    Q = mid @ R.T                                   # u, v in the plane; w across
    u, v, w = Q[:, 0], Q[:, 1], Q[:, 2]
    t_true = true_width(c["hw"], c["fw"], nrm)
    # a hole almost parallel to the vein measures a width that is mostly core
    t_true = np.maximum(t_true, min_thick)
    if max_thick:
        t_true = np.minimum(t_true, float(max_thick))

    # a lone intercept has no span at all, so the lattice is sized on what the
    # vein is ALLOWED to reach rather than on the data's extent: otherwise the
    # grid is three nodes wide, only the middle one is in range, and a body one
    # hole has seen comes back as nothing
    out_m = float(reach) * float(extrap)
    span_u = max(float(u.max() - u.min()), 2.0 * out_m)
    span_v = max(float(v.max() - v.min()), 2.0 * out_m)
    if step is None:
        # How FAR a vein continues and how FINELY it is drawn are different
        # questions, and tying the lattice to the reach answered the second with
        # the first: raising the reach to the continuity distance coarsened the
        # step with it and halved the triangle count on most of the corpus — a
        # longer vein drawn more crudely. `res` is the distance between the data
        # instead, so the surface keeps its detail while the vein keeps its run.
        step = max(min(span_u, span_v) / 60.0, float(res or reach) / 12.0, 1.0)
    pad = out_m
    nu = int(np.clip(span_u / step + 5, 9, 1200))
    nv = int(np.clip(span_v / step + 5, 9, 1200))
    while nu * nv > max_nodes:
        step *= 1.25
        nu = int(np.clip(span_u / step + 5, 9, 1200))
        nv = int(np.clip(span_v / step + 5, 9, 1200))
    gu = np.linspace(u.min() - pad, u.max() + pad, nu)
    gv = np.linspace(v.min() - pad, v.max() + pad, nv)
    U, V = np.meshgrid(gu, gv, indexing="ij")

    # the reference surface and the thickness, interpolated in the vein's own
    # stretched frame so continuity runs along it and not across to its neighbour
    from scipy.spatial import cKDTree
    su, sv = float(W[0]), float(W[1])
    tree = cKDTree(np.c_[u / su, v / sv])
    k = int(min(12, len(mid)))
    d, i = tree.query(np.c_[U.ravel() / su, V.ravel() / sv], k=k)
    if k == 1:
        d, i = d[:, None], i[:, None]
    near = d[:, 0]
    # Carrying a vein BETWEEN two holes and extending it PAST the last one are
    # different permissions, and one distance cannot grant both: a node a full
    # reach beyond the last intercept is extrapolation, and on a dipping vein it
    # lands in the ground between this vein and the next. A node is "between"
    # when its nearest contacts surround it — the mean direction to them is
    # short — and only then does it get the whole reach.
    duv = np.stack([U.ravel()[:, None] / su - (u / su)[i],
                    V.ravel()[:, None] / sv - (v / sv)[i]], axis=-1)
    dn = np.linalg.norm(duv, axis=2, keepdims=True)
    dn[dn < 1e-9] = 1.0
    surround = np.linalg.norm((duv / dn).mean(1), axis=1)
    between = surround < 0.8
    inside = (near <= reach) & (between | (near <= reach * float(extrap)))
    wt = 1.0 / (d * d + (step * 0.5) ** 2)
    wt[~np.isfinite(wt)] = 0.0
    ref = (wt * w[i]).sum(1) / wt.sum(1)
    thk = (wt * t_true[i]).sum(1) / wt.sum(1)

    # ONE surface per vein, and the data has to agree that there is one. Where
    # the contacts near a node disagree about where the vein is by more than the
    # vein is thick, two walls are competing for the same lattice node and the
    # weighted mean of them is a sheet halfway between — in barren rock. That is
    # the failure the triangulation had in its own form, arriving by a different
    # road, so it is refused rather than averaged.
    sd = np.sqrt(np.maximum((wt * (w[i] - ref[:, None]) ** 2).sum(1) / wt.sum(1), 0.0))
    agree = sd <= np.maximum(thk, min_thick) * 1.5

    # pinch out: the walls close as the vein runs past its support, instead of
    # ending in a cliff the width of the last intercept
    edge = np.clip((reach - near) / (max(taper, 1e-6) * reach), 0.0, 1.0)
    thk = np.maximum(thk * edge, 0.0)

    M = (inside & agree).reshape(nu, nv)
    if M.sum() < 3:
        return None
    REF, THK = ref.reshape(nu, nv), thk.reshape(nu, nv)

    V3 = _nodes(U, V, REF, THK, R, +0.5)
    V4 = _nodes(U, V, REF, THK, R, -0.5)
    verts, faces = _skin(M, V3, V4, nu, nv)
    if faces is None or not len(faces):
        return None

    pts = _fill(U, V, REF, THK, M, R, fill_step or max(step * 0.5, 1.0))
    return {"v": verts, "f": faces, "pts": pts,
            "hw": c["hw"], "fw": c["fw"], "mid": mid, "veins": 1,
            "thickness": t_true, "n_contacts": int(len(mid)),
            "step_m": round(float(step), 2),
            "reach_m": round(float(reach), 1),
            "area_nodes": int(M.sum())}


def _nodes(U, V, REF, THK, R, side):
    """Lattice nodes of one wall, back in world coordinates."""
    Q = np.stack([U, V, REF + side * THK], axis=-1).reshape(-1, 3)
    return (Q @ R).reshape(U.shape[0], U.shape[1], 3)


def _skin(M, A, B, nu, nv):
    """Two walls and the rim between them, as one closed mesh."""
    idx = -np.ones((nu, nv), np.int64)
    sel = np.argwhere(M)
    for n, (i, j) in enumerate(sel):
        idx[i, j] = n
    nA = len(sel)
    verts = np.concatenate([A[M], B[M]])
    tri = []
    quad = (M[:-1, :-1] & M[1:, :-1] & M[:-1, 1:] & M[1:, 1:])
    qi, qj = np.nonzero(quad)
    a = idx[qi, qj]; b = idx[qi + 1, qj]; cc = idx[qi, qj + 1]; d = idx[qi + 1, qj + 1]
    if len(a):
        tri.append(np.stack([a, b, d], 1)); tri.append(np.stack([a, d, cc], 1))
        tri.append(np.stack([a + nA, d + nA, b + nA], 1))
        tri.append(np.stack([a + nA, cc + nA, d + nA], 1))
    # the rim: every lattice edge with the vein on one side only
    rim = []
    for di, dj in ((1, 0), (0, 1)):
        p = M[:nu - di, :nv - dj] & M[di:, dj:]
        pi, pj = np.nonzero(p)
        for ii, jj in zip(pi, pj):
            i2, j2 = ii + di, jj + dj
            # an edge is on the rim when the quads either side of it are not both filled
            if di:
                n1 = jj > 0 and M[ii, jj - 1] and M[i2, jj - 1]
                n2 = jj + 1 < nv and M[ii, jj + 1] and M[i2, jj + 1]
            else:
                n1 = ii > 0 and M[ii - 1, jj] and M[ii - 1, j2]
                n2 = ii + 1 < nu and M[ii + 1, jj] and M[ii + 1, j2]
            if n1 and n2:
                continue
            x, y = idx[ii, jj], idx[i2, j2]
            rim.append((x, y, y + nA)); rim.append((x, y + nA, x + nA))
    if rim:
        tri.append(np.asarray(rim, np.int64))
    if not tri:
        return verts, None
    return verts, np.concatenate(tri).astype(np.int64)


def _fill(U, V, REF, THK, M, R, step):
    """Points inside the vein, for the estimator's domain."""
    out = []
    uu, vv = U[M], V[M]
    rr, tt = REF[M], THK[M]
    layers = np.clip((tt / max(step, 1e-6)).astype(int) + 1, 1, 9)
    for n in np.unique(layers):
        s = layers == n
        ks = np.linspace(-0.5, 0.5, int(n)) if n > 1 else np.array([0.0])
        for kk in ks:
            out.append(np.stack([uu[s], vv[s], rr[s] + kk * tt[s]], 1))
    if not out:
        return np.zeros((0, 3))
    return np.concatenate(out) @ R
