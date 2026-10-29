"""The deposit as a scalar field, the way implicit modelling software builds it.

Every other builder in MMP parameterises a surface and then fits data to it.
The alpha path triangulates intercept midpoints; the vein path fits a best-fit
PLANE and models the vein as a height-field over it. Both carry the same
limitation, and it is the one a geologist notices first: a vein that swings in
strike or rolls over cannot be a height-field over any plane, so it fragments
into planar pieces or sheds its ends. Pinch and swell such a model can do.
Twist and turn it cannot, by construction.

Implicit modelling does not parameterise anything. It interpolates a scalar f
over the ground — negative inside the body, positive outside, zero at the
contact — and takes the body to be the region where f < 0. The surface is
whatever the zero level turns out to be, so a fold costs nothing. Three kinds
of evidence, all of them already in a drill database, become constraints:

    inside      a sample inside a mineralised interval     f = -(its depth)
    contact     a hangingwall or footwall position         f =  0
    outside     a sample on a hole that cut nothing there  f = +(its distance)

What is interpolated is signed DISTANCE from the contact, not a sign. The
difference is not cosmetic: interpolating -1/0/+1 lets the labels outvote the
contacts, and the zero level settles halfway between the inside and outside
points instead of passing through the walls — measured on a synthetic vein,
21 m out whatever the grid or the smoothing, for a body 2 to 12 m thick. With
signed distance the zero level is pinned to the contacts, which is where the
rock actually changes.

That last one is what stops a body growing into ground the drilling has already
disproved, and it is the same evidence Leapfrog uses to pinch a vein out — here
it is simply one of the constraint types rather than a special case.

Continuity is anisotropic: distances are measured in the deposit's own frame,
generous along strike and down dip and tight across. That frame and the search
scale are the only things that differ between a vein, a skarn and a porphyry,
which is why one builder serves every style.
"""
import os

import numpy as np

# The field is evaluated in chunks: a 4-million-cell grid against 16 neighbours
# each is half a gigabyte of distances in one go, and the modeller has 1 GB.
CHUNK = int(os.environ.get("MMP_IMPLICIT_CHUNK", "120000"))
# Cells in the grid. Marching cubes is cheap; building the field is not.
MAX_CELLS = int(os.environ.get("MMP_IMPLICIT_CELLS", "2500000"))
# Faces per body. Marching cubes produces a face per surface cell, so a big
# body on a fine grid is hundreds of thousands of them. The page thins a mesh
# by DROPPING faces, which on a marching-cubes skin leaves holes rather than a
# coarser skin — so the mesh is built coarser instead of being cut down later.
MAX_FACES = int(os.environ.get("MMP_IMPLICIT_FACES", "60000"))
NEIGHBOURS = 16


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.array([0.0, 0.0, 1.0])


def frame_of(rot):
    R = np.asarray(rot, float) if rot is not None else np.eye(3)
    if R.shape != (3, 3):
        R = np.eye(3)
    return R


ANCHORS = 32
ANCHOR_K = 48


def trend(P, anchors=ANCHORS, k=ANCHOR_K, min_pts=16, smooth=2, flat=0.45):
    """The body's own orientation WHERE IT VARIES, instead of once for all of it.

    Every style is currently searched in one ellipsoid, oriented by the
    principal axes of the whole mineralised cloud. That is right for a slab and
    wrong for everything else, and the error is not small. Measured on 14
    drilled properties, the local across-body direction sits a median of 50 to
    71 degrees away from the global one — porphyry 68, bulk 71, skarn 60, vein
    50 — while the local neighbourhoods are themselves tightly planar (third
    singular value 0 to 3 per cent of the first). So the orientation is well
    determined locally and genuinely different from place to place: the single
    ellipsoid is searching ACROSS the body in one half of the deposit and ALONG
    it in the other.

    This is the structural trend a geologist would supply by hand — a frame
    that follows the contact, the fold, or the shell — read off the data
    instead. Returns (anchor points, a frame per anchor) with rows ordered
    longest axis first, so row 2 is across the body at that anchor, or None
    where the cloud is too small or too formless to orient locally.
    """
    from scipy.spatial import cKDTree
    P = np.asarray(P, float).reshape(-1, 3)
    if len(P) < max(min_pts * 2, 40):
        return None
    n = int(np.clip(anchors, 4, max(4, len(P) // min_pts)))
    # anchors spread over the body rather than clustered where drilling is
    # dense: a decimation by index puts most of them in the busiest hole.
    A = np.empty((n, 3))
    A[0] = P[int(np.argmax(np.linalg.norm(P - P.mean(0), axis=1)))]
    d2 = ((P - A[0]) ** 2).sum(1)
    for i in range(1, n):
        A[i] = P[int(np.argmax(d2))]
        d2 = np.minimum(d2, ((P - A[i]) ** 2).sum(1))
    tree = cKDTree(P)
    kk = int(min(k, len(P)))
    R = np.zeros((n, 3, 3))
    ok = np.zeros(n, bool)
    # the global frame is the reference the local ones are signed against, so
    # that smoothing averages frames rather than cancelling them
    C = P - P.mean(0)
    try:
        G = np.linalg.svd(C, full_matrices=False)[2]
    except Exception:
        return None
    for i in range(n):
        _d, j = tree.query(A[i], k=kk)
        Q = P[np.atleast_1d(j)]
        if len(Q) < min_pts:
            continue
        Q = Q - Q.mean(0)
        try:
            _u, sv, vt = np.linalg.svd(Q, full_matrices=False)
        except Exception:
            continue
        if sv[0] <= 1e-9:
            continue
        # a neighbourhood with no shape cannot say which way is across it, and
        # a frame invented there is worse than the global one
        if float(sv[2] / sv[0]) > flat:
            continue
        for r in range(3):
            if float(np.dot(vt[r], G[r])) < 0:
                vt[r] = -vt[r]
        R[i] = vt
        ok[i] = True
    if not ok.any():
        return None
    R[~ok] = G
    # Smooth the frames against their neighbours. A frame per anchor with hard
    # assignment facets the field at the anchor boundaries; neighbouring frames
    # that agree make those seams small, and a trend is smooth in the ground.
    if n > 2 and smooth:
        atree = cKDTree(A)
        kn = int(min(4, n))
        for _ in range(int(smooth)):
            _d, nb = atree.query(A, k=kn)
            R = _orthonormal(R[np.atleast_2d(nb)].mean(1))
    return A, R


def _orthonormal(R):
    """Nearest rotation to each averaged frame, rows kept in order."""
    R = np.asarray(R, float).reshape(-1, 3, 3)
    out = np.empty_like(R)
    for i, M in enumerate(R):
        try:
            u, _s, vt = np.linalg.svd(M)
            Q = u @ vt
            if np.linalg.det(Q) < 0:
                u[:, -1] = -u[:, -1]
                Q = u @ vt
            out[i] = Q
        except Exception:
            out[i] = np.eye(3)
    return out


def field(P, S, rot, aniso, soft, grid, chunk=CHUNK, k=NEIGHBOURS, tr=None):
    """The scalar field at every node of `grid`, from constraints (P, S).

    Inverse-distance over the k nearest constraints, measured in the deposit's
    own stretched frame. Distance-weighted rather than kriged on purpose: a
    variogram fitted per body, on a few dozen intercepts, is a number invented
    with more ceremony, and the anisotropy is already carried explicitly.

    `tr` is a structural trend from `trend()` — a frame per neighbourhood. Given
    one, each node is measured in the frame of the body WHERE THAT NODE IS,
    which is what lets the same search follow a fold, a contact, or a vein that
    turns. Without one the deposit's single global frame is used, as before.
    """
    from scipy.spatial import cKDTree
    a = np.asarray(aniso, float) if aniso is not None else np.ones(3)
    a = np.where(a > 1e-6, a, 1.0)
    P = np.asarray(P, float)
    S = np.asarray(S, float)
    grid = np.asarray(grid, float)
    out = np.empty(len(grid), float)
    # the smoothing floor, so a node sitting exactly on a constraint does not
    # divide by zero and a node between two does not step between them
    soft = float(max(soft, 1e-6))
    kk = int(min(k, len(P)))
    step = max(1, int(chunk))

    def idw(d, j):
        if kk == 1:
            d, j = d[:, None], j[:, None]
        w = 1.0 / (d * d + soft * soft)
        w[~np.isfinite(w)] = 0.0
        return (w * S[j]).sum(1) / np.maximum(w.sum(1), 1e-12)

    if tr is None:
        R = frame_of(rot)
        tree = cKDTree((P @ R.T) / a)
        for i in range(0, len(grid), step):
            out[i:i + step] = idw(*tree.query((grid[i:i + step] @ R.T) / a, k=kk))
        return out

    A, RR = tr
    A = np.asarray(A, float)
    atree = cKDTree(A)
    trees = {}
    # Each node is estimated in the frames of the NEAREST FEW anchors and the
    # estimates blended by anchor distance, not taken from the nearest anchor
    # alone. Nearest-anchor is a hard switch, and a field that switches metric
    # across a boundary is discontinuous there: measured, it moved the zero
    # level 15.8 m off the wall contacts on a twisting vein, which is the
    # whole thing the surface is supposed to sit on. Blending makes the trend
    # vary smoothly, the way it does in the ground.
    m = int(min(3, len(A)))
    for i in range(0, len(grid), step):
        G = grid[i:i + step]
        ad, own = atree.query(G, k=m)
        if m == 1:
            ad, own = ad[:, None], own[:, None]
        wa = 1.0 / (ad + 1e-6)
        wa = wa / wa.sum(1, keepdims=True)
        o = np.zeros(len(G), float)
        for c in range(m):
            col = own[:, c]
            for ai in np.unique(col):
                sel = col == ai
                t = trees.get(int(ai))
                if t is None:
                    t = cKDTree((P @ RR[int(ai)].T) / a)
                    trees[int(ai)] = t
                o[sel] += wa[sel, c] * idw(
                    *t.query((G[sel] @ RR[int(ai)].T) / a, k=kk))
        out[i:i + step] = o
    return out


def _box_shell(lo, hi, step, most=4000):
    """Points on the faces of the box, standing in for ground nobody drilled."""
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    n = np.maximum(np.ceil((hi - lo) / max(step * 4.0, 1.0)).astype(int) + 1, 2)
    ax = [np.linspace(lo[d], hi[d], int(n[d])) for d in range(3)]
    G = np.stack(np.meshgrid(*ax, indexing="ij"), axis=-1).reshape(-1, 3)
    on = np.zeros(len(G), bool)
    for d in range(3):
        on |= np.isclose(G[:, d], lo[d]) | np.isclose(G[:, d], hi[d])
    G = G[on]
    if len(G) > most:
        G = G[np.linspace(0, len(G) - 1, most).astype(int)]
    return G


def components(F, n_verts):
    """Which connected piece each face belongs to.

    A field does not have to be told how many veins there are: the isosurface
    comes out in as many pieces as the data supports, and those pieces ARE the
    veins. That is the whole reason this can hold a vein that twists — the
    planar builder had to decide the split first, by fitting planes, and a vein
    that swings does not fit one.
    """
    F = np.asarray(F, np.int64)
    parent = np.arange(int(n_verts))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in F:
        ra, rb, rc = find(int(a)), find(int(b)), find(int(c))
        if ra != rb:
            parent[rb] = ra
            rb = ra
        if ra != rc:
            parent[rc] = ra
    root = np.array([find(i) for i in range(int(n_verts))])
    _u, inv = np.unique(root, return_inverse=True)
    return inv[F[:, 0]]


def attitude(V, F):
    """(dip, dip direction) of a piece of surface, from the surface itself.

    Area-weighted, because a fold has small steep panels and large gentle ones
    and counting triangles would let the small ones shout. For a body that is
    not tabular this is still defined and still means something — it is the
    plane the body most nearly lies in — so the page can state it for a skarn
    as readily as for a vein, and a reader can see for themselves how well it
    fits from the spread reported beside it.
    """
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    if not len(F):
        return None, None, None
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1)
    keep = area > 1e-12
    if not keep.any():
        return None, None, None
    n = n[keep] / area[keep][:, None]
    w = area[keep]
    # a surface has two sides; align them before averaging or they cancel
    ref = n[np.argmax(w)]
    n = np.where((n @ ref)[:, None] < 0, -n, n)
    m = (n * w[:, None]).sum(0)
    if np.linalg.norm(m) < 1e-12:
        return None, None, None
    m = _unit(m)
    # how planar it is: 1.0 is a plane, 0 is a sphere
    flat = float(np.abs(n @ m).sum() / len(n))
    R = plane_frame(m)
    down = R[1]
    dip = float(np.degrees(np.arcsin(min(1.0, abs(float(down[2]))))))
    ddir = float(np.degrees(np.arctan2(float(down[0]), float(down[1]))) % 360.0)
    return round(dip, 1), round(ddir, 1), round(flat, 3)


def plane_frame(nrm):
    """(along strike, down dip, across) from a normal — strike is horizontal."""
    n = _unit(np.asarray(nrm, float))
    z = np.array([0.0, 0.0, 1.0])
    strike = (np.array([1.0, 0.0, 0.0]) if abs(float(n @ z)) > 0.999
              else _unit(np.cross(z, n)))
    down = _unit(np.cross(n, strike))
    if down[2] > 0:
        down = -down
    return np.vstack([strike, down, n])


def lattice(lo, hi, step):
    """Grid nodes and their shape, coarsened until the grid fits MAX_CELLS."""
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    step = float(max(step, 0.25))
    while True:
        n = np.maximum(np.ceil((hi - lo) / step).astype(int) + 1, 2)
        if int(n[0]) * int(n[1]) * int(n[2]) <= MAX_CELLS or step > 1e4:
            break
        step *= 1.25
    ax = [lo[d] + np.arange(n[d]) * step for d in range(3)]
    G = np.stack(np.meshgrid(*ax, indexing="ij"), axis=-1).reshape(-1, 3)
    return G, tuple(int(x) for x in n), step, ax


def surface(inside, contacts, outside, rot=None, aniso=None, reach=60.0,
            step=None, pad=None, max_cells=None, max_faces=None,
            bound=None):
    """The body these constraints describe: vertices, faces, and the field.

    Returns None where there is not enough to model, rather than drawing
    something: three constraints inside do not make a deposit.
    """
    inside = np.asarray(inside, float).reshape(-1, 3)
    if len(inside) < 3:
        return None
    contacts = (np.asarray(contacts, float).reshape(-1, 3)
                if contacts is not None and len(contacts) else np.zeros((0, 3)))
    outside = (np.asarray(outside, float).reshape(-1, 3)
               if outside is not None and len(outside) else np.zeros((0, 3)))
    reach = float(reach)
    pad = float(pad if pad is not None else
                reach * float(os.environ.get("MMP_IMPLICIT_PAD", "0.35")))
    step = float(step if step else max(reach / 14.0, 1.0))
    lo = inside.min(0) - pad
    hi = inside.max(0) + pad
    if len(contacts):
        lo = np.minimum(lo, contacts.min(0) - pad)
        hi = np.maximum(hi, contacts.max(0) + pad)
    # outside evidence only matters where the body could be
    if len(outside):
        keep = np.all((outside >= lo - pad) & (outside <= hi + pad), axis=1)
        outside = outside[keep]
    global MAX_CELLS
    if max_cells:
        MAX_CELLS = int(max_cells)
    budget = int(max_faces or MAX_FACES)
    # How far the body may be carried from the nearest mineralised sample.
    # The continuity distance, not the grid pad: `pad` is a third of it, and
    # clamping that tight shrank every body back to nothing — measured, the
    # 293 bodies that gained a shape lost it again.
    bound = float(bound if bound is not None else
                  reach * float(os.environ.get("MMP_IMPLICIT_BOUND", "1.0")))
    # Signed distance from the nearest contact: negative in the rock that is
    # ore, positive in the rock that is not, zero where they meet.
    from scipy.spatial import cKDTree
    # Something has to be positive or the field never crosses zero and there is
    # no surface at all. Where no hole disproves the body, the edge of the
    # padded box stands in, and the zero level becomes a halo at `pad` around
    # the drilling — which is what the older builders drew explicitly.
    if not len(outside):
        outside = _box_shell(lo, hi, step)
    if len(contacts):
        ct = cKDTree(contacts)
        s_in = -ct.query(inside)[0]
        s_out = ct.query(outside)[0] if len(outside) else np.zeros(0)
    elif len(outside):
        # No walls picked out — true of every style but a vein, where there is
        # no hangingwall and footwall to speak of. The contact is then wherever
        # ore meets barren rock, so each point is signed by its distance to the
        # OTHER cloud and the zero level falls between them.
        s_in = -cKDTree(outside).query(inside)[0]
        s_out = cKDTree(inside).query(outside)[0]
    else:
        s_in = -np.full(len(inside), float(pad))
        s_out = np.full(len(outside), float(pad))
    # Just outside each logged wall, the rock is not ore — stated, not inferred.
    #
    # Inverse distance cannot extrapolate past the range of its own data. On the
    # outward side of a wall every constraint within reach is ore (negative) or
    # contact (zero), so the field has nothing positive to cross and the wall's
    # position falls to whatever barren samples happen to be pulled in from
    # elsewhere. On a twisting vein those sit 44 m across from the sheet, and
    # the surface drew itself 20 m out rather than on the 2.4 m walls.
    #
    # A logged wall is better evidence than that: it says the ore stops HERE,
    # which is a statement about the rock a short step beyond it. This is the
    # offset constraint implicit modelling adds wherever it has a contact it
    # knows the sense of, and it makes the wall's position independent of which
    # directions the search happens to favour.
    #
    # The step is read off the data, not from the grid: half the median
    # distance from an ore sample to its nearest wall is comfortably inside the
    # body's own half-width, so the constraint lands in barren rock on its own
    # side and never across the opposite wall. A grid step does not know that —
    # 6 m beyond a 4.8 m vein's wall is past its far wall, and closes the vein.
    off = np.zeros((0, 3))
    s_off = np.zeros(0)
    # MEASURED AND NOT WORTH HAVING, kept because the reasoning is right and
    # the result is counter-intuitive. On a thick folded body — 60 m between
    # walls, folded +-140 m, drilled on a grid, which is what the non-tabular
    # styles look like — the built surface sits a median 1.07 m from the true
    # margin without these constraints and 1.30 m with them, p90 8.17 against
    # 8.07: the same answer. And on a vein they cannot help, because a 4.8 m
    # vein against a 6 m smoothing length is below the lattice's resolution
    # whatever constraints it is given, which is why a vein goes through
    # `veins.py` and not through here.
    #
    # The useful finding underneath: a signed-DISTANCE field is dominated by
    # geometry, not by the search. Nothing about the wall's position is
    # recovered by stating it twice.
    if len(contacts) and len(inside) and os.environ.get("MMP_WALLSIDE") == "on":
        it = cKDTree(inside)
        d_ci, j_ci = it.query(contacts)
        away = contacts - inside[j_ci]
        n_away = np.linalg.norm(away, axis=1)
        ok = n_away > 1e-6
        if ok.any():
            delta = float(np.median(np.abs(s_in))) if len(s_in) else 0.0
            delta = max(min(delta, 0.5 * float(step)), 0.05 * float(step))
            if delta > 1e-6:
                off = contacts[ok] + (away[ok] / n_away[ok, None]) * delta
                s_off = np.full(len(off), delta)
    P = np.vstack([x for x in (inside, contacts, off, outside) if len(x)])
    S = np.concatenate([s_in, np.zeros(len(contacts)), s_off, s_out])
    # the smoothing length is the grid's own step: finer than that is detail
    # the lattice cannot carry, coarser blurs the walls away from the contacts
    from skimage import measure
    # The deposit's orientation where it varies, read off the mineralised
    # samples themselves. Built from `inside` rather than from everything,
    # because the barren points and the box shell say nothing about which way
    # the body runs.
    # NOT on by default, and the reason is measured rather than cautious. The
    # trend itself is right: the local across-body direction sits 50 to 71
    # degrees off the global one on every style with real data volume, with
    # locally planar neighbourhoods to back it. But this estimator cannot
    # carry it yet. Inverse distance cannot extrapolate past the range of its
    # own data, and on the outward side of a wall every neighbour is ore
    # (negative) or contact (zero), so the field never crosses zero there on
    # its own: what actually places the wall today is the far barren samples
    # being pulled in by a frame that does NOT suppress the across-body
    # direction. Give it the correct frame and that accident stops happening —
    # measured, the surface moved 15.7 m off the contacts it is meant to sit
    # on, with a node 20 m outside the wall seeing 3 barren samples among its
    # nearest sixteen globally and none locally.
    #
    # Stating the outward side explicitly (MMP_WALLSIDE, below) was the
    # candidate fix and was measured too: on the thick folded body this builder
    # is actually for, the trend and the global frame place the margin equally
    # well — 1.05 m against 1.07 m median, 8.9 against 8.2 at p90 — with or
    # without those constraints. All four arms agree.
    #
    # So the conclusion is not "the trend needs more support". It is that a
    # signed-DISTANCE field barely cares which way the search ellipsoid points,
    # because the values it interpolates are already geometric. The frame's
    # orientation matters to a GRADE estimate, where the value really does vary
    # along the structure — `model3d.estimate_blocks` is where a trend would
    # pay, and that is the tonnage path, so it needs its own measurement
    # against the filed statements before it goes anywhere near a default.
    tr = None
    if os.environ.get("MMP_TREND") == "on":
        tr = trend(inside)
    v = f = None
    for _try in range(4):
        G, shape, step, ax = lattice(lo, hi, step)
        F = field(P, S, rot, aniso, step, G, tr=tr).reshape(shape)
        # Nothing in an interpolated field stops it spanning ground nobody
        # drilled: given two veins 150 m apart and no hole between them, it
        # bridges. The triangulation refused that through the alpha criterion,
        # and the field needs the same bound stated its own way — beyond
        # `pad` from the nearest mineralised sample the body simply is not
        # there, whatever the interpolation would like. This is the distance
        # buffer from drilling that vein modelling software applies to a
        # boundary, and it is what keeps extrapolation to one step-out.
        din = cKDTree(inside).query(G)[0].reshape(shape)
        F = np.maximum(F, din - float(bound))
        # And the barren rock bounds it the same way, geometrically rather
        # than by interpolation weight. A hole that cut nothing is the only
        # direct evidence of where a body ENDS, and leaving that to the
        # interpolation loses it exactly where it matters: an anisotropy that
        # suppresses the across-structure direction suppresses the barren
        # samples too, because that is the direction they lie in. Measured on
        # a twisting vein, a node 20 m outside the wall had 3 barren samples
        # among its sixteen nearest in the deposit's global frame and NONE in
        # its local one, so the field stayed negative out to 25 m and the
        # surface drew itself 15.7 m off the contacts it is meant to sit on.
        #
        # Stated as a bound there is nothing to suppress: the body cannot
        # reach closer to a barren sample than that sample's own distance, so
        # the field is at least minus that distance everywhere. For a thin
        # vein this reproduces the true signed distance exactly — at the wall
        # it gives zero, at the centre it gives half the width.
        # Off by default until it has its own tonnage measurement: it can only
        # ever make a body smaller, and a change that only subtracts needs to
        # be scored against the filed statements before it ships.
        if len(outside) and os.environ.get("MMP_BARRENBOUND") == "on":
            dout = cKDTree(outside).query(G)[0].reshape(shape)
            F = np.maximum(F, -dout)
        # A shell of positive values around the grid, so a body that reaches
        # the edge closes there instead of being cut open. An open surface has
        # no inside: it is not a solid, it cannot be filled, and its volume is
        # undefined. Measured before this: 88 edges belonged to one triangle.
        F = np.pad(F, 1, mode="constant", constant_values=float(np.nanmax(F)) + 1.0)
        try:
            v, f, _n, _vals = measure.marching_cubes(F, level=0.0)
        except Exception:
            return None
        v = v - 1.0                               # undo the pad
        if not len(f):
            return None
        if len(f) <= budget:
            break
        # too fine: coarsen and rebuild rather than throw faces away
        step *= max(1.3, (len(f) / float(budget)) ** 0.5 * 0.9)
    if f is None or not len(f):
        return None
    V = np.column_stack([ax[d][0] + v[:, d] * step for d in range(3)])
    return {"v": V, "f": f.astype(np.int64), "shape": shape, "step_m": step,
            "origin": [float(x) for x in lo],
            "n_inside": int(len(inside)), "n_outside": int(len(outside)),
            "n_contacts": int(len(contacts))}
