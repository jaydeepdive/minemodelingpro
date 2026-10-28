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


def field(P, S, rot, aniso, soft, grid, chunk=CHUNK, k=NEIGHBOURS):
    """The scalar field at every node of `grid`, from constraints (P, S).

    Inverse-distance over the k nearest constraints, measured in the deposit's
    own stretched frame. Distance-weighted rather than kriged on purpose: a
    variogram fitted per body, on a few dozen intercepts, is a number invented
    with more ceremony, and the anisotropy is already carried explicitly.
    """
    from scipy.spatial import cKDTree
    R = frame_of(rot)
    a = np.asarray(aniso, float) if aniso is not None else np.ones(3)
    a = np.where(a > 1e-6, a, 1.0)
    Q = (np.asarray(P, float) @ R.T) / a
    tree = cKDTree(Q)
    S = np.asarray(S, float)
    kk = int(min(k, len(Q)))
    out = np.empty(len(grid), float)
    # the smoothing floor, so a node sitting exactly on a constraint does not
    # divide by zero and a node between two does not step between them
    soft = float(max(soft, 1e-6))
    for i in range(0, len(grid), max(1, int(chunk))):
        G = (np.asarray(grid[i:i + chunk], float) @ R.T) / a
        d, j = tree.query(G, k=kk)
        if kk == 1:
            d, j = d[:, None], j[:, None]
        w = 1.0 / (d * d + soft * soft)
        w[~np.isfinite(w)] = 0.0
        out[i:i + chunk] = (w * S[j]).sum(1) / np.maximum(w.sum(1), 1e-12)
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
    P = np.vstack([x for x in (inside, contacts, outside) if len(x)])
    S = np.concatenate([s_in, np.zeros(len(contacts)), s_out])
    # the smoothing length is the grid's own step: finer than that is detail
    # the lattice cannot carry, coarser blurs the walls away from the contacts
    from skimage import measure
    v = f = None
    for _try in range(4):
        G, shape, step, ax = lattice(lo, hi, step)
        F = field(P, S, rot, aniso, step, G).reshape(shape)
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
