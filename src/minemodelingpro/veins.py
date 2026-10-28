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
import os
import numpy as np

# A surface needs three points. Below that there is no plane to fit, no strike
# to follow and no thickness trend to carry — only a position, which the
# contacts themselves already show.
MIN_CONTACTS = 3
# How far off a vein's own surface a stray intercept may be and still be taken
# as part of it, in multiples of that vein's measured true width. A vein that
# undulates by a couple of its own widths between holes is a vein; one that is
# ten widths away is a different vein, or nothing.
ACROSS_WIDTHS = 3.0
# Which deposits are modelled as sheets. A plane fitted through a point cloud
# always finds SOMETHING — on a formless cloud RANSAC carves out slabs that
# pass every intrinsic test you can cheaply write, because a thin wide slice
# of a blob really is thin and wide. The thing that distinguishes a vein from
# a slice is not in the geometry, it is in the deposit, and MMP already reads
# that from the report's own Item 8. So the plane split is used where the
# filings say the body is tabular, and the old proximity split everywhere else.
TABULAR = ("vein", "orogenic", "vms", "sedex", "pegmatite")
# How many times more contacts a candidate plane must hold than slicing the
# cloud at random in that band would give. Below this it is a slice, not a vein.
CHANCE_MULT = 3.0


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


def plane_frame(nrm):
    """A vein's own axes from its normal: (along strike, down dip, across).

    Strike is horizontal and in the plane, down-dip is in the plane and points
    down, across is the normal. It has to be this and not any two perpendicular
    vectors, because the anisotropy weights are applied per axis — generous
    along strike, less down dip, tight across — and on arbitrary in-plane axes
    those weights would describe nothing.
    """
    n = _unit(np.asarray(nrm, float))
    z = np.array([0.0, 0.0, 1.0])
    if abs(float(n @ z)) > 0.999:                 # flat-lying: strike is free
        strike = np.array([1.0, 0.0, 0.0])
    else:
        strike = _unit(np.cross(z, n))
    down = _unit(np.cross(n, strike))
    if down[2] > 0:
        down = -down
    return np.vstack([strike, down, n])


def dip_of(nrm):
    """(dip, dip direction) in degrees, the way a geologist writes an attitude.

    The dip direction is where the plane goes DOWN, so it is read off the
    down-dip vector itself. Taking it from the normal instead gives the right
    answer or the back bearing depending on which way the normal happens to
    point, and a vein reported as dipping 070 when it dips 250 is worse than
    no attitude at all.
    """
    R = plane_frame(nrm)
    down = R[1]
    dip = float(np.degrees(np.arcsin(min(1.0, abs(float(down[2]))))))
    ddir = float(np.degrees(np.arctan2(float(down[0]), float(down[1]))) % 360.0)
    return round(dip, 1), round(ddir, 1)


def sheets(mid, hw, fw, reach, min_contacts=5, tol_widths=2.0,
           iters=600, seed=0, min_aspect=3.0):
    """The veins these contacts lie ON, found as planes rather than as clumps.

    This replaces asking "are these contacts near each other". They always are,
    on a drilled property — what makes them one VEIN is lying on one surface.
    Splitting by proximity needs a frame to be tight in, and the frame came
    from the principal axes of the mineralised cloud, which on Lawyers (923
    holes) put the vein normal straight down: a property drilled a kilometre
    across and three hundred metres deep looks flat-lying to that fit whatever
    the veins do. One "vein" came out 330 m thick where the intercepts measure
    5.4 m, and the model on screen was a field of blobs.

    So: fit a plane to three contacts, keep the ones within a couple of their
    own measured widths of it, require that they form a connected patch in that
    plane, and require the patch to be much wider than it is thick — a sheet,
    not a cloud with a plane drawn through it. Strongest first, then again on
    what is left. Contacts that lie on no sheet are returned separately rather
    than forced onto one.

    Five contacts minimum, not three: a plane fitted to three points passes
    through them exactly, so any three points whatsoever are a perfect "sheet".
    Asking for two more than the fit needs is what makes it evidence.

    Returns (list of {ix, normal, along_m, dip_m, thick_m, dip, dip_dir}, leftover).
    """
    mid = np.asarray(mid, float)
    hw = np.asarray(hw, float)
    fw = np.asarray(fw, float)
    n = len(mid)
    if n < min_contacts:
        return [], np.arange(n)
    rng = np.random.default_rng(seed)
    left = np.arange(n)
    out = []
    while len(left) >= min_contacts:
        P = mid[left]
        best = None
        for _ in range(iters):
            k = rng.choice(len(P), 3, replace=False)
            a, b, c3 = P[k]
            nrm = np.cross(b - a, c3 - a)
            if np.linalg.norm(nrm) < 1e-9:
                continue
            nrm = _unit(nrm)
            # the tolerance is the intercepts' own true width along THIS normal,
            # so a 5 m vein is not given a 30 m band to wander in
            w = np.abs((fw[left] - hw[left]) @ nrm)
            tol = max(float(np.median(w)) * tol_widths, 3.0)
            ins = np.nonzero(np.abs((P - a) @ nrm) <= tol)[0]
            if len(ins) < min_contacts:
                continue
            R = plane_frame(nrm)
            Q = P[ins] @ R.T
            lab = _link2d(Q[:, :2], reach)
            for g in np.unique(lab):
                sel = ins[lab == g]
                if len(sel) < min_contacts:
                    continue
                sp = Q[lab == g]
                along = float(sp[:, 0].max() - sp[:, 0].min())
                dipl = float(sp[:, 1].max() - sp[:, 1].min())
                thick = max(float(np.ptp((P[sel] - a) @ nrm)), 1e-6)
                if min(along, dipl) < min_aspect * thick:
                    continue                      # a cloud, not a sheet
                # And the scatter about the plane has to be explained by the
                # vein's own width. Without this, ANY cloud slices into thin
                # slabs that pass the aspect test — a blob of random points
                # gave nine "sheets" holding 95% of it. A vein's contacts sit
                # within a width or two of its surface because that is what a
                # vein is; a slab through a porphyry does not.
                wsel = float(np.median(np.abs((fw[left][sel] - hw[left][sel]) @ nrm)))
                if thick > max(2.5 * wsel, 4.0):
                    continue
                # Finally: are there MORE contacts on this plane than slicing
                # anywhere would give? A slab anywhere through a formless cloud
                # is thin and wide and passes every test above — a blob of 120
                # random points yielded ten "sheets" holding 92% of it. The
                # null model is the obvious one: a band this thick, cut through
                # a cloud this deep, catches this many by chance. A vein has to
                # beat that by a clear margin, which it does easily, because
                # its contacts are on it rather than near it.
                depth = float(np.ptp(P @ nrm))
                if depth > 1e-6:
                    chance = len(P) * min(1.0, thick / depth)
                    if len(sel) < max(min_contacts, CHANCE_MULT * chance):
                        continue
                score = (len(sel), -thick)
                if best is None or score > best[0]:
                    best = (score, sel, nrm, along, dipl, thick)
        if best is None:
            break
        _sc, sel, nrm, along, dipl, thick = best
        dip, ddir = dip_of(nrm)
        out.append({"ix": left[sel], "normal": nrm, "along_m": round(along, 1),
                    "dip_m": round(dipl, 1), "thick_m": round(thick, 1),
                    "dip": dip, "dip_dir": ddir})
        left = np.setdiff1d(left, left[sel])
    return out, left


def _link2d(xy, reach):
    """Connected patches in the plane, at `reach`."""
    from scipy.spatial import cKDTree
    t = cKDTree(xy)
    pairs = t.query_pairs(float(reach), output_type="ndarray")
    parent = np.arange(len(xy))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in pairs:
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[ri] = rj
    root = np.array([find(i) for i in range(len(xy))])
    _u, inv = np.unique(root, return_inverse=True)
    return inv


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


def voids_on(lat, mid_sel, traces, width, clear=None):
    """Holes that went through the VEIN and cut nothing.

    The earlier version of this tested crossings of the flat plane the vein was
    fitted on. A vein undulates, so a hole crossing that plane thirty metres
    from where the vein really is counted as a miss, and on a property with
    nine hundred holes that closed nearly every vein. Leapfrog flips the
    outside intervals relative to the nearest INTERIOR intervals — against the
    reference surface, not against a plane — so this does the same: the
    surface is built once, and only then is a hole asked whether it passed
    through it.
    """
    if traces is None or lat is None:
        return np.zeros((0, 3))
    R, gu, gv, REF, M = lat
    P, hid = traces
    P = np.asarray(P, float)
    if not len(P) or not len(mid_sel):
        return np.zeros((0, 3))
    Q = P @ R.T
    iu = np.searchsorted(gu, Q[:, 0]).clip(0, len(gu) - 1)
    iv = np.searchsorted(gv, Q[:, 1]).clip(0, len(gv) - 1)
    on = M[iu, iv]                                 # the vein exists under here
    d = Q[:, 2] - REF[iu, iv]                      # how far off the surface
    hid = np.asarray(hid)
    same = hid[1:] == hid[:-1]
    cross = same & on[:-1] & on[1:] & ((d[:-1] <= 0) != (d[1:] <= 0))
    k = np.nonzero(cross)[0]
    if not len(k):
        return np.zeros((0, 3))
    d0, d1 = d[k], d[k + 1]
    t = np.where(np.abs(d1 - d0) > 1e-9, -d0 / (d1 - d0), 0.0)[:, None]
    X = P[k] + (P[k + 1] - P[k]) * t
    from scipy.spatial import cKDTree
    near = cKDTree(mid_sel).query(X)[0]
    # a hole that DID cut the vein has its contact at the crossing; how close
    # that has to be scales with the vein, not with the lattice
    lim = float(clear if clear is not None else max(3.0 * float(width), 10.0))
    return X[near > lim]


def voids_for(mid_sel, R, traces, width, reach, clear=None):
    """Where drilling went through the vein's plane and found no vein.

    Leapfrog calls this pinching out: holes with no vein segment are added as
    "outside" points and flipped, so the hangingwall and footwall cross and the
    vein closes. MMP holds the same evidence and was not using it — a vein ran
    until the search reach gave out, which is a property of the setting rather
    than of the deposit. These are the crossing points, carried into the
    interpolation as ZERO thickness, so the walls meet there and the body ends.

    A crossing only counts when no contact of this vein sits near it: a hole
    that did cut the vein has its contact right at the crossing.
    """
    if traces is None:
        return np.zeros((0, 3))
    P, hid = traces
    P = np.asarray(P, float)
    if not len(P) or not len(mid_sel):
        return np.zeros((0, 3))
    nrm = _unit(R[2])
    Q = mid_sel @ R.T
    o = float(np.median(Q[:, 2]))                 # the plane the vein sits on
    umin, umax = Q[:, 0].min() - reach, Q[:, 0].max() + reach
    vmin, vmax = Q[:, 1].min() - reach, Q[:, 1].max() + reach
    w = P @ nrm - o
    hid = np.asarray(hid)
    # a sign change between consecutive samples of the SAME hole is a crossing
    same = hid[1:] == hid[:-1]
    cross = same & ((w[:-1] <= 0) != (w[1:] <= 0))
    k = np.nonzero(cross)[0]
    if not len(k):
        return np.zeros((0, 3))
    d0, d1 = w[k], w[k + 1]
    t = np.where(np.abs(d1 - d0) > 1e-9, -d0 / (d1 - d0), 0.0)[:, None]
    X = P[k] + (P[k + 1] - P[k]) * t
    QX = X @ R.T
    keep = ((QX[:, 0] >= umin) & (QX[:, 0] <= umax)
            & (QX[:, 1] >= vmin) & (QX[:, 1] <= vmax))
    X = X[keep]
    if not len(X):
        return X
    from scipy.spatial import cKDTree
    near = cKDTree(mid_sel).query(X)[0]
    # how close a contact has to be before the crossing is explained by it
    lim = float(clear if clear is not None else max(2.5 * float(width), 8.0))
    return X[near > lim]


def build(c, rot=None, aniso=None, reach=60.0, step=None, min_thick=0.3,
          max_thick=None, taper=0.35, max_nodes=240_000, fill_step=None,
          res=None, style=None, traces=None):
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
        if got is not None:
            got.pop("lat", None)
        tight = float(res or 0.0)
        # A surface fitted through N contacts that comes back with fewer than N
        # triangles has collapsed, not been drawn: the agreement test threw away
        # all but a corner of the lattice. That reads on the page exactly like
        # the body going dark — one project came back with two triangles where
        # it had had five hundred and sixty-two — so a collapse is retried on
        # the shorter leash too, and whichever draws more of the body is kept.
        ok = got is not None and len(got["f"]) >= len(ix)
        if ok or not tight or tight >= reach:
            return [(got, np.asarray(ix))] if got is not None else []
        out = []
        pieces = components(np.asarray(mid_all)[ix], rot, aniso, tight)
        for q in pieces:
            if not len(q):
                continue
            sx = np.asarray(ix)[q]
            g = _one(_sub(sx), rot, aniso, tight, step, min_thick,
                     max_thick, taper, max(int(budget / max(1, len(pieces))), 2_000),
                     fill_step, res=res)
            if g is not None:
                out.append((g, sx))
        if got is not None and sum(len(g["f"]) for g, _ in out) <= len(got["f"]):
            return [(got, np.asarray(ix))]
        return out

    # Which contacts lie on which vein, and what that vein's own attitude is.
    # Found as planes; a cloud that is not sheet-like yields none and the old
    # proximity split is used instead, because not every deposit is a vein.
    plan = []
    tabular = str(style or "").lower() in TABULAR
    if tabular and os.environ.get("MMP_VEINSPLIT") != "ball":
        found, _left = sheets(mid_all, c["hw"], c["fw"], reach)
        on_sheet = sum(len(f["ix"]) for f in found)
        # only trust it when the planes account for most of the contacts;
        # otherwise it is finding a few sheets inside something else
        if found and on_sheet >= 0.5 * len(mid_all):
            plan = [(f["ix"], plane_frame(f["normal"]), f) for f in found]
    if plan:
        built = []
        per = max(int(max_nodes / max(1, len(plan))), 2_000)
        for ix, R, meta in plan:
            # each vein is drawn in ITS OWN plane, not in the deposit's average,
            # and closed where holes crossed that plane and cut nothing
            sel = np.asarray(mid_all)[ix]
            wd = float(np.median(np.abs((np.asarray(c["fw"])[ix]
                                         - np.asarray(c["hw"])[ix]) @ _unit(R[2]))))
            got = _one(_sub(ix), R, aniso, reach, step, min_thick, max_thick,
                       taper, per, fill_step, res=res)
            nvd = 0
            if (got is not None and traces is not None
                    and os.environ.get("MMP_VEINPINCH") != "off"):
                vd = voids_on(got.get("lat"), sel, traces, wd or 1.0)
                if len(vd):
                    shut = _one(_sub(ix), R, aniso, reach, step, min_thick,
                                max_thick, taper, per, fill_step, res=res,
                                voids=vd)
                    # closing a vein is evidence; erasing it is not. A pass
                    # that leaves almost nothing means the surface and the
                    # drilling disagree, and the drilling is being read wrong.
                    if shut is not None and len(shut["f"]) >= 0.2 * len(got["f"]):
                        got, nvd = shut, len(vd)
            if got is not None:
                got.pop("lat", None)
                got["attitude"] = {"dip": meta["dip"], "dip_dir": meta["dip_dir"],
                                   "closed_by": int(nvd)}
                built.append((got, np.asarray(ix)))
        if built:
            return _merge_veins(built, mid_all, c)

    parts = _absorb(components(mid_all, rot, aniso, reach), mid_all, c, rot, reach)
    if len(parts) > 1:
        built = []
        per = max(int(max_nodes / max(1, len(parts))), 2_000)
        for ix in parts:
            if len(ix):
                built.extend(_try(ix, per))
        return _merge_veins(built, mid_all, c) if built else None
    built = _try(np.arange(len(mid_all)), max_nodes)
    return _merge_veins(built, mid_all, c) if built else None


def _absorb(parts, mid_all, c, rot, reach):
    """Give a stranded intercept to the vein it actually sits on.

    Connectivity is judged on a ball in a stretched frame, and that is a knife
    edge: measured on Lawyers (923 holes), the intercepts left in ones and twos
    sat a median of 0.7 m INSIDE the footprint of a real vein — directly along
    strike of it — and were cut loose only by being ~30 m off its plane against
    a 22 m tolerance. Each then became its own vein and was drawn as a disc.

    So before a component of one or two contacts is treated as a vein of its
    own, it is offered to the veins around it, judged the way a geologist would:
    is it within this vein's footprint along strike and down dip, and is it
    close to this vein's surface across the plane — in multiples of that vein's
    own true width, not in metres. If it fits, it belongs to that vein and makes
    it better supported. If it does not, it is not drawn (`_one` needs three
    contacts), and the intercept still shows as a contact.
    """
    big = [p for p in parts if len(p) >= MIN_CONTACTS]
    small = [p for p in parts if len(p) < MIN_CONTACTS]
    if not big or not small:
        return parts
    mid = np.asarray(mid_all, float)
    hw = np.asarray(c["hw"], float)
    fw = np.asarray(c["fw"], float)
    frames = []
    for bp in big:
        R = np.asarray(frame(mid[bp], rot), float)
        n = _unit(R[2])
        Q = mid[bp] @ R.T
        width = float(np.median(np.abs((fw[bp] - hw[bp]) @ n))) or 1.0
        frames.append((R, Q, max(width, 1.0)))
    out = [list(p) for p in big]
    left = []
    for sp in small:
        q = mid[np.asarray(sp)]
        best, bi = None, -1
        for k, (R, Q, width) in enumerate(frames):
            P = q @ R.T
            # inside the vein's own footprint, or how far outside it
            du = np.abs(P[:, 0] - np.clip(P[:, 0], Q[:, 0].min(), Q[:, 0].max())).max()
            dv = np.abs(P[:, 1] - np.clip(P[:, 1], Q[:, 1].min(), Q[:, 1].max())).max()
            inplane = float(np.hypot(du, dv))
            across = float(np.abs(P[:, 2] - np.median(Q[:, 2])).max())
            if inplane > reach or across > ACROSS_WIDTHS * width:
                continue
            score = (across / width, inplane)
            if best is None or score < best:
                best, bi = score, k
        if bi >= 0:
            out[bi].extend(int(i) for i in sp)
        else:
            left.append(sp)
    return [np.asarray(sorted(p), dtype=np.int64) for p in out] + left


def _merge_veins(built, mid_all, c):
    """One mesh, with the seams between the veins kept rather than welded.

    Each component was built on its own, and concatenating them threw away
    which triangles and which contacts belonged to which — so a body holding
    nine veins arrived at the page as one undifferentiated solid that could
    only be shown or hidden whole. The faces and the contacts are concatenated
    IN VEIN ORDER and carry a vein index each, which makes showing one vein a
    draw range rather than a rebuild. The contacts are reordered to match; a
    contact belonging to no built vein keeps its place at the end under -1, so
    nothing measured is dropped from the picture.
    """
    V, F, P, T, off = [], [], [], [], 0
    fv, cx, cv, parts = [], [], [], []
    for n, (b, ix) in enumerate(built):
        V.append(b["v"])
        F.append(np.asarray(b["f"], np.int64) + off)
        off += len(b["v"])
        P.append(b["pts"])
        t = np.asarray(b["thickness"], float)
        T.append(t)
        fv.append(np.full(len(b["f"]), n, np.int32))
        cx.append(np.asarray(ix, np.int64))
        cv.append(np.full(len(ix), n, np.int32))
        q = {"vein": n, "n_contacts": int(len(ix)),
             "faces": int(len(b["f"])),
             "span_m": b.get("span_m"),
             "thick_m": (round(float(np.median(t)), 2) if len(t) else None),
             "area_nodes": int(b["area_nodes"])}
        if b.get("attitude"):
            q.update(b["attitude"])      # the vein's own dip and dip direction
        parts.append(q)
    cx = np.concatenate(cx) if cx else np.zeros(0, np.int64)
    loose = np.setdiff1d(np.arange(len(mid_all)), cx, assume_unique=False)
    order = np.concatenate([cx, loose]).astype(np.int64)
    cvein = np.concatenate(cv + [np.full(len(loose), -1, np.int32)]) if cv else \
        np.full(len(loose), -1, np.int32)
    hw, fw = np.asarray(c["hw"], float), np.asarray(c["fw"], float)
    return {"v": np.concatenate(V), "f": np.concatenate(F),
            "pts": np.concatenate(P) if P else np.zeros((0, 3)),
            "hw": hw[order], "fw": fw[order], "mid": np.asarray(mid_all)[order],
            "thickness": np.concatenate(T) if T else np.zeros(0),
            "n_contacts": int(len(mid_all)), "veins": len(built),
            "face_vein": np.concatenate(fv) if fv else np.zeros(0, np.int32),
            "contact_vein": cvein,
            "vein_parts": parts,
            "step_m": built[0][0]["step_m"], "reach_m": built[0][0]["reach_m"],
            "area_nodes": int(sum(b["area_nodes"] for b, _ in built))}


def _one(c, rot=None, aniso=None, reach=60.0, step=None, min_thick=0.3,
         max_thick=None, taper=0.35, max_nodes=240_000, fill_step=None,
         extrap=0.25, res=None, voids=None):
    """One vein: a closed surface, and the points inside it for the estimator.

    Three contacts minimum. One intercept is a point and two are a line, and
    neither of those is a sheet — drawing one anyway produced a disc the width
    of the reach, which is what put forty-four circles on a 923-hole vein
    property and made the model look like spilled confetti. The intercepts
    themselves are still shown, and the estimator still carries their grade;
    what stops is inventing a surface where there is nothing to fit one to.
    """
    mid = np.asarray(c["mid"], float)
    if len(mid) < MIN_CONTACTS:
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

    # How far the sheet may run past its outermost contact. It used to also
    # SIZE the lattice for a one-contact vein, so that a lone intercept came
    # back as a disc rather than as nothing; with three contacts required there
    # is always a real extent to size on, and that circle is gone.
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
    # The contacts, plus the places drilling proved the vein absent. A void
    # carries zero thickness, so the two walls close on it and the body ends
    # there instead of running on to the edge of the search.
    uu, vv, ww, tt = u, v, w, t_true
    n_void = 0                       # NOT nv: that is the lattice's v count
    if voids is not None and len(voids):
        QV = np.asarray(voids, float) @ R.T
        n_void = len(QV)
        uu = np.concatenate([u, QV[:, 0]])
        vv = np.concatenate([v, QV[:, 1]])
        ww = np.concatenate([w, QV[:, 2]])
        tt = np.concatenate([t_true, np.zeros(n_void)])
    tree = cKDTree(np.c_[uu / su, vv / sv])
    k = int(min(12, len(uu)))
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
    duv = np.stack([U.ravel()[:, None] / su - (uu / su)[i],
                    V.ravel()[:, None] / sv - (vv / sv)[i]], axis=-1)
    dn = np.linalg.norm(duv, axis=2, keepdims=True)
    dn[dn < 1e-9] = 1.0
    surround = np.linalg.norm((duv / dn).mean(1), axis=1)
    between = surround < 0.8
    inside = (near <= reach) & (between | (near <= reach * float(extrap)))
    wt = 1.0 / (d * d + (step * 0.5) ** 2)
    wt[~np.isfinite(wt)] = 0.0
    ref = (wt * ww[i]).sum(1) / wt.sum(1)
    thk = (wt * tt[i]).sum(1) / wt.sum(1)

    # ONE surface per vein, and the data has to agree that there is one. Where
    # the contacts near a node disagree about where the vein is by more than the
    # vein is thick, two walls are competing for the same lattice node and the
    # weighted mean of them is a sheet halfway between — in barren rock. That is
    # the failure the triangulation had in its own form, arriving by a different
    # road, so it is refused rather than averaged.
    sd = np.sqrt(np.maximum((wt * (ww[i] - ref[:, None]) ** 2).sum(1) / wt.sum(1), 0.0))
    agree = sd <= np.maximum(thk, min_thick) * 1.5

    # pinch out: the walls close as the vein runs past its support, instead of
    # ending in a cliff the width of the last intercept
    edge = np.clip((reach - near) / (max(taper, 1e-6) * reach), 0.0, 1.0)
    thk = np.maximum(thk * edge, 0.0)

    # Where the walls have met there is no vein left. This is what makes a
    # body stop at the drilling that disproved it rather than at the reach.
    M = (inside & agree & (thk >= float(min_thick))).reshape(nu, nv)
    if M.sum() < 3:
        return None
    REF, THK = ref.reshape(nu, nv), thk.reshape(nu, nv)

    V3 = _nodes(U, V, REF, THK, R, +0.5)
    V4 = _nodes(U, V, REF, THK, R, -0.5)
    verts, faces = _skin(M, V3, V4, nu, nv)
    if faces is None or not len(faces):
        return None

    pts = _fill(U, V, REF, THK, M, R, fill_step or max(step * 0.5, 1.0))
    lat = (R, gu, gv, REF, M)
    return {"v": verts, "f": faces, "pts": pts,
            "hw": c["hw"], "fw": c["fw"], "mid": mid, "veins": 1,
            "thickness": t_true, "n_contacts": int(len(mid)),
            "lat": lat,
            "step_m": round(float(step), 2),
            "reach_m": round(float(reach), 1),
            # the vein's own run and height, in its own plane — what a chip
            # naming this vein has to say to be worth reading
            "span_m": round(float(max(u.max() - u.min(), v.max() - v.min())), 1),
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
