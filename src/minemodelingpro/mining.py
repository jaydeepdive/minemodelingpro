"""What would actually be mined, as opposed to what is in the ground.

Jordan: "is it possible for us to visualize what would actually be mined in a
mining scenario (in this case an underground mining scenario, but sometimes open
pit in other models)".

A resource is rock above a cut-off. A mine is a set of shapes a machine can
physically extract: stopes that are big enough to drill and blast and that carry
the waste trapped inside them, or a pit whose walls stand up. The difference is
usually large and always in the same direction — a mining shape holds LESS metal
at a LOWER grade than the resource it is cut from, because it takes dilution it
cannot avoid and leaves ore it cannot reach.

Two deliberate limits, because the alternative is a number that looks like an
engineering study and is not one:

- **The pit here is not an optimised pit.** A Lerchs-Grossmann shell maximises
  value against metal prices, mining and processing costs, recoveries and
  haulage. MMP knows none of those. What this computes is the SMALLEST pit at a
  given wall angle that contains the ore above a cut-off — a geometric lower
  bound on the excavation, which is a real and checkable quantity, and it is
  reported as that and never as a pit design.
- **Stopes here are contiguous volumes of mineable size, not a stope layout.**
  No one has chosen a mining method, a level spacing, a pillar or an access. What
  this answers is "how much of this deposit sits in pieces big enough to mine at
  all", which is the question a reader asking about a mining scenario is usually
  asking first, and the one that separates a deposit from a scatter of intercepts.
"""
import numpy as np

# A stope has to be big enough to drill, blast and muck. These are deliberately
# permissive — the point is to exclude a scatter of isolated blocks, not to
# impose someone's mine design.
MIN_STOPE_M3 = 750.0        # about 2,000 t of rock
MIN_STOPE_SPAN_M = 10.0     # in each of its two longest directions
PIT_SLOPE_DEG = 50.0        # overall wall angle; rock-dependent, stated not assumed


def _grid(ijk, shape=None):
    """A dense boolean lattice from sparse block indices."""
    ijk = np.asarray(ijk, np.int64)
    if shape is None:
        shape = tuple(ijk.max(0) + 1)
    return shape


def stopes(ijk, grade, block, cutoff, min_m3=MIN_STOPE_M3,
           min_span_m=MIN_STOPE_SPAN_M, dilute=True):
    """Contiguous volumes of ore big enough to be mined, with their own dilution.

    Returns ``(label_per_block, stats)``. ``label_per_block`` is 0 where the block
    is in no stope and 1..n for the stope it belongs to; ``stats`` carries, per
    stope, the ore and diluted tonnage-relevant block counts and the mean grade.

    `dilute` fills holes: a below-cut-off block completely enclosed by one stope is
    mined whether anyone wants it or not, so it is carried, and it is what makes
    the stope grade lower than the resource grade it came from. That is the whole
    point of the exercise and must not be optimised away.
    """
    from scipy import ndimage
    ijk = np.asarray(ijk, np.int64)
    g = np.asarray(grade, float)
    bx, by, bz = [float(v) for v in block]
    vol = bx * by * bz
    if not len(ijk):
        return np.zeros(0, np.int32), []
    dims = tuple(int(v) for v in (ijk.max(0) + 1))
    ore = np.zeros(dims, bool)
    gg = np.zeros(dims, np.float32)
    sel = np.isfinite(g) & (g >= float(cutoff))
    I, J, K = ijk[:, 0], ijk[:, 1], ijk[:, 2]
    ore[I[sel], J[sel], K[sel]] = True
    gg[I, J, K] = np.where(np.isfinite(g), g, 0.0)
    present = np.zeros(dims, bool)
    present[I, J, K] = True

    body = ore
    if dilute:
        # waste wholly inside the ore is mined with it
        body = ndimage.binary_fill_holes(ore)

    lab, n = ndimage.label(body, structure=np.ones((3, 3, 3), bool))
    if not n:
        return np.zeros(len(ijk), np.int32), []
    keep = {}
    out_lab = np.zeros(dims, np.int32)
    nxt = 0
    objs = ndimage.find_objects(lab)
    for li in range(1, n + 1):
        sl = objs[li - 1]
        if sl is None:
            continue
        m = (lab[sl] == li)
        cnt = int(m.sum())
        if cnt * vol < float(min_m3):
            continue
        # span: the two longest directions must both reach the minimum, so a
        # single string of blocks down one hole is not a stope
        spans = sorted([(sl[0].stop - sl[0].start) * bx,
                        (sl[1].stop - sl[1].start) * by,
                        (sl[2].stop - sl[2].start) * bz], reverse=True)
        if spans[1] < float(min_span_m):
            continue
        nxt += 1
        out_lab[sl][m] = nxt
        sub_ore = m & ore[sl]
        sub_est = m & present[sl]
        gv = gg[sl][sub_est]
        keep[nxt] = {
            "id": nxt,
            "blocks": cnt,
            "ore_blocks": int(sub_ore.sum()),
            "volume_m3": round(cnt * vol, 1),
            "dilution": round(1.0 - (sub_ore.sum() / cnt), 4) if cnt else 0.0,
            "grade": round(float(gv.mean()), 4) if gv.size else None,
            "ore_grade": round(float(gg[sl][sub_ore].mean()), 4) if sub_ore.any() else None,
        }
    per_block = out_lab[I, J, K].astype(np.int32)
    return per_block, [keep[k] for k in sorted(keep)]


def pit_shell(ijk, grade, block, origin, cutoff, surface_z,
              slope_deg=PIT_SLOPE_DEG):
    """The smallest pit at this wall angle that contains the ore above `cutoff`.

    Not an optimised pit — see this module's docstring. `surface_z` is the ground
    elevation per block column, as an (ni, nj) array or a callable (x, y) -> z;
    columns with no ground known take no part, because a pit with no surface to
    start from is not a pit.

    Returns ``(inside_per_block, stats)`` where `inside_per_block` is a boolean
    aligned to `ijk`, and `stats` carries the excavated volume, the ore inside it
    and the strip ratio. The waste is computed GEOMETRICALLY, from the excavation
    the wall angle forces, because the block model only covers the mineralised
    envelope and counting only the blocks it happens to contain would report a
    strip ratio of nearly zero on every deposit.
    """
    from scipy import ndimage
    ijk = np.asarray(ijk, np.int64)
    g = np.asarray(grade, float)
    bx, by, bz = [float(v) for v in block]
    ox, oy, oz = [float(v) for v in origin]
    if not len(ijk):
        return np.zeros(0, bool), {}
    dims = tuple(int(v) for v in (ijk.max(0) + 1))
    ni, nj, nk = dims
    I, J, K = ijk[:, 0], ijk[:, 1], ijk[:, 2]

    # ground elevation over the BLOCK lattice
    if callable(surface_z):
        xs = ox + (np.arange(ni) + 0.5) * bx
        ys = oy + (np.arange(nj) + 0.5) * by
        Z = np.full((ni, nj), np.nan)
        for a in range(ni):
            for b in range(nj):
                v = surface_z(xs[a], ys[b])
                if v is not None and np.isfinite(v):
                    Z[a, b] = v
    else:
        Z = np.asarray(surface_z, float)
        if Z.shape != (ni, nj):
            return np.zeros(len(ijk), bool), {}
    if not np.isfinite(Z).any():
        return np.zeros(len(ijk), bool), {}

    sel = np.isfinite(g) & (g >= float(cutoff))
    zc = oz + (K + 0.5) * bz
    depth = Z[I, J] - zc                       # positive below ground
    ok = sel & np.isfinite(depth) & (depth > 0)
    if not ok.any():
        return np.zeros(len(ijk), bool), {}

    # THE PIT IS WIDER THAN THE DEPOSIT, and that is the whole cost of it.
    #
    # A 50 m deep pit with 45-degree walls reaches 50 m further out than its
    # deepest ore in every direction, which on a block lattice sized to the
    # mineralised envelope is well outside the lattice. Computing the shell only
    # where there are blocks truncates the cone at the orebody's own edge and
    # reports a fraction of the rock that has to move — the strip ratio comes
    # out near zero on every deposit, which is exactly the flattering kind of
    # wrong this module exists to avoid. So the shell is built on a padded grid.
    t = np.tan(np.radians(float(slope_deg)))
    maxd = float(depth[ok].max())
    reach = maxd / max(t, 1e-6)
    ri = max(1, min(int(np.ceil(reach / bx)) + 1, 400))
    rj = max(1, min(int(np.ceil(reach / by)) + 1, 400))
    D = np.full((ni + 2 * ri, nj + 2 * rj), -np.inf)
    np.maximum.at(D, (I[ok] + ri, J[ok] + rj), depth[ok])
    # ground outside the lattice is taken from its edge: the DEM beyond the
    # drilling is not known here, and replicating the margin is the assumption
    # that changes the answer least
    Zp = np.pad(Z, ((ri, ri), (rj, rj)), mode="edge")

    a = np.arange(-ri, ri + 1) * bx
    b = np.arange(-rj, rj + 1) * by
    cone = -np.hypot(a[:, None], b[None, :]) * t          # additive offsets
    P = ndimage.grey_dilation(np.where(np.isfinite(D), D, -1e9),
                              footprint=np.ones_like(cone, bool),
                              structure=cone, mode="constant", cval=-1e9)
    P = np.where(P > 0, P, 0.0)                            # no pit where no ore
    inside = np.isfinite(depth) & (depth > 0) & (depth <= P[I + ri, J + rj])

    cell_area = bx * by
    excavated = float(P[np.isfinite(Zp)].sum() * cell_area)
    ore_blocks = int((inside & sel).sum())
    ore_vol = ore_blocks * bx * by * bz
    waste = max(0.0, excavated - ore_vol)
    gv = g[inside & sel]
    return inside, {
        "slope_deg": float(slope_deg),
        "cutoff": float(cutoff),
        "max_depth_m": round(float(P.max()), 1),
        "excavated_m3": round(excavated),
        "ore_m3": round(ore_vol),
        "waste_m3": round(waste),
        "strip_ratio": round(waste / ore_vol, 2) if ore_vol > 0 else None,
        "ore_blocks": ore_blocks,
        "grade": round(float(gv.mean()), 4) if gv.size else None,
        "note": ("the smallest pit at this wall angle that contains the ore above "
                 "this cut-off — a geometric lower bound on the excavation, not an "
                 "optimised pit design"),
    }


def ground_from_collars(holes, origin, block, dims, k=4, power=2.0):
    """Ground elevation over a block lattice, from the collars around each column.

    The same inverse-distance over the nearest few collars that
    `targets.surface_from_collars` does one point at a time, done for the whole
    lattice at once: a pit needs a ground surface at every column it touches, and
    a quarter of a million scalar calls is not the way to get one.

    Returns an (ni, nj) array, NaN where no collar is near enough to answer.
    """
    xy, z = [], []
    for h in holes or []:
        try:
            xy.append([float(h["x"]), float(h["y"])]); z.append(float(h["z"]))
        except (KeyError, TypeError, ValueError):
            continue
    ni, nj = int(dims[0]), int(dims[1])
    if len(xy) < 1:
        return np.full((ni, nj), np.nan)
    from scipy.spatial import cKDTree
    XY = np.asarray(xy, float); Z = np.asarray(z, float)
    bx, by = float(block[0]), float(block[1])
    ox, oy = float(origin[0]), float(origin[1])
    xs = ox + (np.arange(ni) + 0.5) * bx
    ys = oy + (np.arange(nj) + 0.5) * by
    GX, GY = np.meshgrid(xs, ys, indexing="ij")
    pts = np.column_stack([GX.ravel(), GY.ravel()])
    kk = int(min(max(1, k), len(Z)))
    d, i = cKDTree(XY).query(pts, k=kk)
    if kk == 1:
        d = d[:, None]; i = i[:, None]
    # beyond this the collars say nothing about the ground, and a pit floated on
    # an invented surface is worse than no pit
    far = d.min(1) > max(bx, by) * 40
    w = 1.0 / np.maximum(d, 1e-6) ** power
    out = (w * Z[i]).sum(1) / w.sum(1)
    out[far] = np.nan
    return out.reshape(ni, nj)
