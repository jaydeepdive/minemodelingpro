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
# Styles that are a BED, not a vein. An iron formation, a laterite blanket and
# a sedimentary phosphate horizon are stratiform by definition — the thing
# being modelled IS a surface with a thickness on it — yet none of them reached
# the sheet branch, because that branch was gated on TABULAR and TABULAR means
# "vein-like". They took the proximity path instead and were drawn as a blob
# with one global frame, which is the wrong shape for a bed and cannot follow a
# fold at all. They are routed here so the drape can describe them.
BEDDED = ("iron", "laterite", "phosphate")
SHEETED = TABULAR + BEDDED
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


def _unit2(v):
    n = float(np.linalg.norm(v))
    return np.asarray(v, float) / n if n > 1e-12 else np.array([1.0, 0.0])


class Unrolled:
    """Coordinates that follow the vein instead of a plane.

    A vein is parameterised here by arc length along its own SPINE — the
    smooth curve its contacts trace in plan — by elevation, and by signed
    offset across. The planar frame is a special case of this (a straight
    spine), and the general case is the one a geologist keeps pointing at: a
    vein whose strike swings. Projected onto one plane that vein folds over on
    itself and cannot be a surface at all, which is why the planar builder
    broke it into pieces. Unrolled, it is as simple as a straight one.

    Elevation is the second axis rather than down-dip distance because a vein
    that swings in strike still hangs consistently in depth, and because it
    keeps the map invertible without having to track a dip that varies. That
    makes this right for a steep vein and wrong for a flat-lying one, which is
    why the caller checks the dip before asking for it.
    """

    def __init__(self, xy, bins=24, smooth=2):
        P = np.asarray(xy, float)[:, :2]
        c = P.mean(0)
        C = P - c
        # the direction the vein runs, in plan
        try:
            v0 = np.linalg.svd(C, full_matrices=False)[2][0]
        except Exception:
            v0 = np.array([1.0, 0.0])
        t = C @ v0
        lo, hi = float(t.min()), float(t.max())
        n = int(np.clip(bins, 3, max(3, len(P) // 2)))
        edges = np.linspace(lo, hi, n + 1)
        k = np.clip(np.searchsorted(edges, t) - 1, 0, n - 1)
        pts = []
        for b in range(n):
            m = k == b
            if m.any():
                pts.append(P[m].mean(0))
        S = np.asarray(pts, float)
        if len(S) < 2:                       # degenerate: a straight spine
            S = np.vstack([c - v0 * 1.0, c + v0 * 1.0])
        for _ in range(int(smooth)):         # a spine is smooth, data is not
            if len(S) >= 3:
                S[1:-1] = (S[:-2] + 2 * S[1:-1] + S[2:]) / 4.0
        # Run the spine past the data at both ends. Without that, a contact
        # beyond the last control point has its foot clamped to the end, the
        # along-track part of its offset is lost into `w`, and the coordinates
        # stop being invertible: 5% of points came back up to 15 m from where
        # they started, all of them at the two ends.
        if len(S) >= 2:
            ext = max(float(np.linalg.norm(S[1] - S[0])), 1.0)
            head = S[0] + _unit2(S[0] - S[1]) * ext
            tail = S[-1] + _unit2(S[-1] - S[-2]) * ext
            S = np.vstack([head, S, tail])
        d = np.linalg.norm(np.diff(S, axis=0), axis=1)
        self.S = S
        self.s = np.concatenate([[0.0], np.cumsum(d)])
        seg = np.diff(S, axis=0)
        L = np.linalg.norm(seg, axis=1)
        L[L < 1e-9] = 1.0
        self.t = seg / L[:, None]                       # unit tangent per segment
        self.n = np.column_stack([-self.t[:, 1], self.t[:, 0]])   # left normal
        # The vein's own LEAN, removed from the across coordinate.
        #
        # Without this the spine is a vertical curtain and the across offset
        # measures the dip, not a departure from the sheet. Measured on a vein
        # bent through a right angle: at 75 degrees the across offset reads
        # 17.9 m, of which 16.7 m is the vein hanging over as it goes down and
        # only 3.2 m is a real departure; at 60 degrees it is 38.4 m against a
        # true 11.8 m. The fitted lean matches 1/tan(dip) to two decimals, so
        # it is the dip and nothing else, and a vein was being charged for
        # having one. That is why the spine never beat a plane on real
        # drilling while beating it easily on vertical synthetic veins.
        #
        # Fitted rather than taken from an attitude so that a vein whose dip
        # changes along strike is still described, and kept as one number so
        # the coordinates stay invertible.
        self.lean = 0.0
        self.z0 = float(np.mean(np.asarray(xy, float)[:, 2])) if \
            np.asarray(xy, float).shape[1] > 2 else 0.0
        Q = np.asarray(xy, float)
        if (Q.shape[1] > 2 and len(Q) >= 4
                and os.environ.get("MMP_VEINLEAN") != "off"):
            _u0, _v0, w0 = self._uvw_raw(Q)
            dz = Q[:, 2] - self.z0
            if float(np.ptp(dz)) > 1e-6:
                A = np.column_stack([dz, np.ones(len(dz))])
                try:
                    coef, *_ = np.linalg.lstsq(A, w0, rcond=None)
                    if np.isfinite(coef).all():
                        self.lean = float(coef[0])
                except Exception:
                    self.lean = 0.0

    def _uvw_raw(self, P):
        """Spine coordinates before the vein's own lean is taken out."""
        P = np.asarray(P, float)
        A = self.S[:-1]
        d = P[:, None, :2] - A[None, :, :]
        seg = np.diff(self.S, axis=0)
        L2 = np.maximum((seg * seg).sum(1), 1e-12)
        tt = np.clip((d * seg[None, :, :]).sum(2) / L2[None, :], 0.0, 1.0)
        foot = A[None, :, :] + tt[:, :, None] * seg[None, :, :]
        off = P[:, None, :2] - foot
        dist = np.linalg.norm(off, axis=2)
        j = np.argmin(dist, axis=1)
        i = np.arange(len(P))
        u = self.s[j] + tt[i, j] * np.linalg.norm(seg[j], axis=1)
        w = (off[i, j] * self.n[j]).sum(1)
        return u, P[:, 2].copy(), w

    def to_uvw(self, P):
        """(arc length, elevation, offset across the vein's own leaning sheet).

        The third coordinate is a departure FROM THE VEIN, with the dip taken
        out, so zero means "on the sheet" at every depth rather than only at
        the reference elevation.
        """
        u, v, w = self._uvw_raw(P)
        return u, v, w - self.lean * (v - self.z0)

    def to_world(self, u, v, w):
        """Back to world from (arc length, elevation, offset)."""
        u = np.asarray(u, float).ravel()
        v = np.asarray(v, float).ravel()
        w = np.asarray(w, float).ravel() + self.lean * (v - self.z0)
        seg = np.diff(self.S, axis=0)
        L = np.maximum(np.linalg.norm(seg, axis=1), 1e-12)
        j = np.clip(np.searchsorted(self.s, u) - 1, 0, len(seg) - 1)
        tt = np.clip((u - self.s[j]) / L[j], -2.0, 3.0)
        base = self.S[j] + tt[:, None] * seg[j]
        xy = base + w[:, None] * self.n[j]
        return np.column_stack([xy[:, 0], xy[:, 1], v])

    def normals(self, u):
        """The across-vein direction at each arc length, in 3D.

        The true normal of the leaning sheet, not the horizontal one: a vein
        offset horizontally by its width is thicker than its width by
        1/sin(dip), which at 60 degrees is 15 per cent of the tonnage.
        """
        seg = np.diff(self.S, axis=0)
        L = np.maximum(np.linalg.norm(seg, axis=1), 1e-12)
        j = np.clip(np.searchsorted(self.s, np.asarray(u, float).ravel()) - 1,
                    0, len(seg) - 1)
        N = np.column_stack([self.n[j], np.full(len(j), -self.lean)])
        return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


class Draped:
    """A flat-lying body described as its own folded surface, not as a plane.

    `Unrolled` measures across a vein HORIZONTALLY. That is right for a steep
    body and meaningless for a flat-lying one, where a horizontal offset from a
    bedding-parallel horizon is a distance ALONG it, not across it — which is
    why the spine has a dip gate and why a sedex, iron-formation or laterite
    horizon was fitted to a plane however hard it folds. Measured on Lawyers,
    three of its contact groups dip 21.9, 23.3 and 14.8 degrees and every one
    of them was refused for being too flat to follow; Langis's veins dip 4, 21
    and 32 degrees and none of them could be followed either.

    Here the body is a height field: plan position along it, and the offset
    VERTICAL — how far above or below the horizon a point sits. A fold of any
    shape in plan is then as simple as a flat bed, and the construction is
    exact for a tilted bed, because the tilt is fitted as a plane and only the
    departure from it is interpolated.

    The two are a pair, and the dip chooses between them rather than one of them
    refusing: steep bodies are unrolled along a spine, flat ones are draped on a
    surface, and a body at 45 degrees is described about equally well by either.
    """

    def __init__(self, P, bins=14, k=8, smooth=1.0):
        P = np.asarray(P, float).reshape(-1, 3)
        c = P.mean(0)
        self.c = c
        # a plan frame along the body's own run, so the lattice `_one` builds
        # over (u, v) is not a diagonal sliver of a bounding box
        C = P[:, :2] - c[:2]
        try:
            v0 = _unit2(np.linalg.svd(C, full_matrices=False)[2][0])
        except Exception:
            v0 = np.array([1.0, 0.0])
        self.e0 = v0
        self.e1 = np.array([-v0[1], v0[0]])
        U = C @ v0
        V = C @ self.e1
        Z = P[:, 2] - c[2]
        # The tilt as a plane, the fold as a departure from it. Interpolating
        # the elevations directly would flatten the bed toward its own mean
        # beyond the last hole, which loses the dip exactly where the body is
        # being carried past the drilling; fitting the tilt first means a
        # planar bed is reproduced with no residual at all.
        A = np.column_stack([U, V, np.ones(len(U))])
        try:
            coef, *_ = np.linalg.lstsq(A, Z, rcond=None)
            if not np.isfinite(coef).all():
                coef = np.array([0.0, 0.0, float(np.mean(Z))])
        except Exception:
            coef = np.array([0.0, 0.0, float(np.mean(Z))])
        self.a, self.b, self.d = (float(coef[0]), float(coef[1]), float(coef[2]))
        res = Z - (A @ coef)
        self.PU = U
        self.PV = V
        self.res = res
        span = max(float(np.ptp(U)), float(np.ptp(V)), 1.0)
        self.soft = max(span / float(max(bins, 2)) * float(smooth), 1e-3)
        self.k = int(max(1, min(k, len(P))))
        from scipy.spatial import cKDTree
        self._tree = cKDTree(np.column_stack([U, V]))

    def _plan(self, P):
        P = np.asarray(P, float)
        C = P[:, :2] - self.c[:2]
        return C @ self.e0, C @ self.e1

    def _surf(self, u, v):
        """The horizon's elevation at these plan positions."""
        u = np.asarray(u, float).ravel()
        v = np.asarray(v, float).ravel()
        base = self.a * u + self.b * v + self.d
        if not len(self.res):
            return base + self.c[2]
        d, j = self._tree.query(np.column_stack([u, v]), k=self.k)
        if self.k == 1:
            d, j = d[:, None], j[:, None]
        w = 1.0 / (d * d + self.soft * self.soft)
        w[~np.isfinite(w)] = 0.0
        add = (w * self.res[j]).sum(1) / np.maximum(w.sum(1), 1e-12)
        return base + add + self.c[2]

    def to_uvw(self, P):
        """(along, across in plan, height above the horizon) for world points."""
        P = np.asarray(P, float)
        u, v = self._plan(P)
        return u, v, P[:, 2] - self._surf(u, v)

    def to_world(self, u, v, w):
        """Back to world from (along, across in plan, height)."""
        u = np.asarray(u, float).ravel()
        v = np.asarray(v, float).ravel()
        w = np.asarray(w, float).ravel()
        xy = (self.c[:2] + u[:, None] * self.e0[None, :]
              + v[:, None] * self.e1[None, :])
        return np.column_stack([xy[:, 0], xy[:, 1], self._surf(u, v) + w])

    def normals(self, u):
        """The across-body direction: the horizon's own normal, not vertical.

        A bed offset vertically by its true width is thicker than its width by
        1/cos(dip), so the tilt has to be in the normal or the body is drawn
        too thick wherever it dips.
        """
        u = np.asarray(u, float).ravel()
        g = np.column_stack([np.full(len(u), -self.a), np.full(len(u), -self.b),
                             np.ones(len(u))])
        # back to world axes: the plan gradient is expressed in (e0, e1)
        N = np.column_stack([g[:, 0] * self.e0[0] + g[:, 1] * self.e1[0],
                             g[:, 0] * self.e0[1] + g[:, 1] * self.e1[1],
                             g[:, 2]])
        return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


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
    sty = str(style or "").lower()
    tabular = sty in SHEETED if os.environ.get("MMP_VEINBED") != "off" \
        else sty in TABULAR
    if tabular and os.environ.get("MMP_VEINSPLIT") != "ball":
        # A connected group FIRST, then ask how to describe it. The other way
        # round — plane-fitting the whole property and taking the split from
        # that — cannot find a vein that swings: the planes carve it up, and
        # each piece is then trivially planar, so nothing downstream ever gets
        # the chance to notice the vein was one body all along.
        hw_a = np.asarray(c["hw"], float)
        fw_a = np.asarray(c["fw"], float)
        for grp in components(mid_all, rot, aniso, reach):
            if len(grp) < MIN_CONTACTS:
                continue
            sub = np.asarray(mid_all)[grp]
            # Steep bodies are followed along a spine, flat ones draped on
            # their own surface. The dip picks between the two rather than one
            # of them refusing the group, which is what left a folded horizon
            # fitted to a plane.
            spine = _spine_sheet(sub, hw_a[grp], fw_a[grp])
            if spine is None and os.environ.get("MMP_VEINDRAPE") != "off":
                spine = _drape_sheet(sub, hw_a[grp], fw_a[grp])
            if spine is not None:
                plan.append((np.asarray(grp), spine[0], spine[1]))
                continue
            found, _left = sheets(sub, hw_a[grp], fw_a[grp], reach)
            # and before each facet is taken for a vein of its own, ask whether
            # several of them are one vein that bends
            if len(found) > 1 and os.environ.get("MMP_VEINJOIN") != "off":
                found = _rejoin(found, sub, hw_a[grp], fw_a[grp])
            on_sheet = sum(len(f["ix"]) for f in found)
            if found and on_sheet >= 0.5 * len(grp):
                for f in found:
                    plan.append((np.asarray(grp)[f["ix"]],
                                 plane_frame(f["normal"]), f))
    if plan:
        built = []
        per = max(int(max_nodes / max(1, len(plan))), 2_000)
        for ix, R, meta in plan:
            # each vein is drawn in ITS OWN plane, not in the deposit's average,
            # and closed where holes crossed that plane and cut nothing
            sel = np.asarray(mid_all)[ix]
            wd = float(np.median(np.abs((np.asarray(c["fw"])[ix]
                                         - np.asarray(c["hw"])[ix]) @ _unit(R[2]))))
            un = meta.get("unroll") if isinstance(meta, dict) else None
            if un is None:
                un = _unroll_for(np.asarray(mid_all)[ix], R, wd, meta)
            got = _one(_sub(ix), R, aniso, reach, step, min_thick, max_thick,
                       taper, per, fill_step, res=res, unroll=un)
            nvd = 0
            if (got is not None and traces is not None
                    and os.environ.get("MMP_VEINPINCH") != "off"):
                vd = voids_on(got.get("lat"), sel, traces, wd or 1.0)
                if len(vd):
                    shut = _one(_sub(ix), R, aniso, reach, step, min_thick,
                                max_thick, taper, per, fill_step, res=res,
                                voids=vd, unroll=un)
                    # closing a vein is evidence; erasing it is not. A pass
                    # that leaves almost nothing means the surface and the
                    # drilling disagree, and the drilling is being read wrong.
                    if shut is not None and len(shut["f"]) >= 0.2 * len(got["f"]):
                        got, nvd = shut, len(vd)
            if got is not None:
                got.pop("lat", None)
                got["attitude"] = {"dip": meta["dip"], "dip_dir": meta["dip_dir"],
                                   "closed_by": int(nvd),
                                   "unrolled": bool(un is not None)}
                if isinstance(meta, dict) and meta.get("rejoined"):
                    got["attitude"]["rejoined"] = int(meta.get("parts") or 2)
                # a folded horizon and a vein followed round a bend are both
                # "not a plane", and a reader should be able to tell which
                if isinstance(un, Draped):
                    got["attitude"]["draped"] = True
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


def _spine_sheet(mid, hw, fw, swing=0.5, min_dip=40.0, min_pts=12, better=0.6):
    """One curved sheet, where a spine describes this group better than a plane.

    This is the case the planar split cannot reach. A vein whose strike swings
    is not a plane and never will be, but it IS a sheet — just a curved one —
    and unrolled along its own trace it is as simple as a straight vein. The
    test is a comparison, not a preference: the contacts have to sit closer to
    the spine than to their own best-fit plane, by a clear margin, and the body
    has to be steep enough for "across" to mean a horizontal offset at all.
    """
    mid = np.asarray(mid, float)
    if len(mid) < min_pts:
        return None
    C = mid - mid.mean(0)
    try:
        nrm = _unit(np.linalg.svd(C, full_matrices=False)[2][2])
    except Exception:
        return None
    dip, ddir = dip_of(nrm)
    if dip < min_dip:
        return None
    width = float(np.median(np.abs((np.asarray(fw, float)
                                    - np.asarray(hw, float)) @ nrm))) or 1.0
    flat = float(np.percentile(np.abs(C @ nrm), 90))
    # A vein that really is planar keeps the cheaper path. The bar is low on
    # purpose: measured across swings from 0 to 170 m, the spine's residual is
    # 8 to 13 PER CENT of the plane's at every swing, and identical to it when
    # the vein is straight — so there is nothing to protect by holding out.
    # The old bar (stray more than twice the vein's width) was leaving veins
    # that stray nearly twice their width to be chopped into four by the
    # plane-fitting split.
    if flat <= max(swing * width, 1.0):
        return None
    try:
        un = Unrolled(mid)
    except Exception:
        return None
    _u, _v, w = un.to_uvw(mid)
    # Both residuals have to mean the same thing, and they did not. `flat` is a
    # distance PERPENDICULAR to the plane; `w` is a HORIZONTAL offset from the
    # spine, and the across axis of the unrolled frame is horizontal by
    # construction. For a surface dipping d, a horizontal offset h sits
    # h*sin(d) from it, so comparing the two directly penalised the spine by
    # 1/sin(d) — on Lawyers that read as the spine being 10% WORSE than the
    # plane on a 65-degree body whose two residuals are in fact identical, and
    # 75% worse on a 23-degree one where the spine is a third as far out.
    curved = float(np.percentile(np.abs(w), 90)) * float(np.sin(np.radians(dip)))
    if curved >= better * flat:
        return None                       # the spine is no real improvement
    R = plane_frame(nrm)
    meta = {"ix": np.arange(len(mid)), "normal": nrm, "dip": dip,
            "dip_dir": ddir, "unroll": un, "curved_m": round(curved, 1),
            "planar_m": round(flat, 1)}
    return R, meta


REJOIN_MAX = 24


def _rejoin(found, mid, hw, fw, min_dip=40.0, slack=1.25, most=REJOIN_MAX):
    """Put back together the facets that are one vein whose strike swings.

    `sheets` finds planes, and a vein that bends is not one plane, so it comes
    back as several — each piece trivially planar, each drawn as its own body,
    the bend showing on the page as a gap between two sheets that in the ground
    are continuous rock. Measured over 16 drilled vein properties, 4 of the 16
    components holding two or more steep facets were one vein cut up this way:
    los-ricos-south-ggd gave 4 facets strung 459 m along strike with 19 m of
    across-strike spread and dips inside 58-69 degrees, and el-tigre gave 3
    over 463 m with none. The rest were genuine stockwork and must stay apart.

    The test is not attitude agreement. Two facets of a near-vertical vein can
    report dip directions 144 degrees apart because the dip direction of a
    90-degree body is arbitrary, so comparing attitudes rejects exactly the
    cases this is for. Instead the contacts are asked directly: pool the two
    facets, run a spine through the pool, and keep the merge only if every
    pooled contact still sits about as close to that one curved sheet as it did
    to its own separate plane. A vein that bends passes, because a spine can
    follow a bend. Two crossing veins fail, because no single sheet holds them.
    """
    if len(found) < 2:
        return found
    # Agglomerating is quadratic in the facets and each test fits a frame, so a
    # component that came back as dozens of slivers would spend more time being
    # reassembled than it is worth. Past this many, the largest are considered
    # and the rest kept as they are — a property with thirty facets in ONE
    # connected component is a stockwork, which is the case this declines.
    rest = []
    if len(found) > most:
        order = sorted(range(len(found)), key=lambda i: -len(found[i]["ix"]))
        rest = [found[i] for i in order[most:]]
        found = [found[i] for i in order[:most]]
    mid = np.asarray(mid, float)
    hw = np.asarray(hw, float)
    fw = np.asarray(fw, float)

    def resid(ix, nrm=None):
        """How far these contacts sit from the best single sheet through them,
        and the sheet, as (residual, unroll-or-None, normal)."""
        P = mid[ix]
        if nrm is None:
            C = P - P.mean(0)
            try:
                nrm = _unit(np.linalg.svd(C, full_matrices=False)[2][2])
            except Exception:
                return None
        flat = float(np.percentile(np.abs((P - P.mean(0)) @ nrm), 90))
        dip, _dd = dip_of(nrm)
        if len(P) < 6:
            return flat, None, nrm
        # the same pair as the builder: a spine for a steep body, a drape for a
        # flat one, because plane-fitting chops a folded horizon exactly as it
        # chops a vein that swings
        try:
            if dip >= min_dip:
                un = Unrolled(P)
                _u, _v, w = un.to_uvw(P)
                conv = float(np.sin(np.radians(dip)))
            else:
                un = Draped(P)
                _u, _v, w = un.to_uvw(P)
                conv = float(np.cos(np.radians(dip)))
        except Exception:
            return flat, None, nrm
        # perpendicular-equivalent, so the two numbers mean the same thing
        curved = float(np.percentile(np.abs(w), 90)) * conv
        if curved < flat:
            return curved, un, nrm
        return flat, None, nrm

    groups = [{"ix": np.asarray(f["ix"]), "f": f} for f in found]
    for g in groups:
        r = resid(g["ix"], g["f"]["normal"])
        g["r"] = r[0] if r else 0.0
        g["w"] = float(np.median(np.abs((fw[g["ix"]] - hw[g["ix"]])
                                        @ g["f"]["normal"]))) or 1.0

    for n, g in enumerate(groups):
        g["tag"] = n
    seen = {}
    merged = True
    while merged and len(groups) > 1:
        merged = False
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i], groups[j]
                # the same pair is re-offered every round until something else
                # merges, and fitting its frame again cannot change the answer
                key = (a["tag"], b["tag"])
                if key in seen:
                    got = seen[key]
                    if got is None:
                        continue
                    r, un, nrm = got
                    bar = slack * max(a["r"], b["r"]) + 0.5 * max(a["w"], b["w"])
                    if r > bar:
                        continue
                    ix = np.unique(np.concatenate([a["ix"], b["ix"]]))
                    score = (len(ix), (max(a["r"], b["r"]) - r) if bar else 0.0)
                    if best is None or score > best[0]:
                        best = (score, i, j, ix, r, un, nrm)
                    continue
                ix = np.unique(np.concatenate([a["ix"], b["ix"]]))
                got = resid(ix)
                seen[key] = got if (got is not None and got[1] is not None) else None
                if got is None or got[1] is None:
                    continue              # no spine beat the pooled plane
                r, un, nrm = got
                # the pool must be held about as tightly as the pieces were,
                # in multiples of the vein's own width rather than in metres
                bar = slack * max(a["r"], b["r"]) + 0.5 * max(a["w"], b["w"])
                # AND held tightly in absolute terms, because the bar above is
                # relative and sloppy facets would license a sloppy merge. A
                # vein's contacts sit within a width or two of its surface
                # because that is what a vein IS — the same rule `sheets` uses
                # to refuse a slab through a formless cloud. Without this, 24
                # slivers cut from Gaussian noise merged into one body, since a
                # flexible spine fits noise better than a plane does.
                wmax = max(a["w"], b["w"])
                if r > max(ACROSS_WIDTHS * wmax, 4.0):
                    continue
                if r > bar:
                    continue
                gain = (max(a["r"], b["r"]) - r) if bar else 0.0
                score = (len(ix), gain)
                if best is None or score > best[0]:
                    best = (score, i, j, ix, r, un, nrm)
        if best is not None:
            _sc, i, j, ix, r, un, nrm = best
            dip, ddir = dip_of(nrm)
            keep = {"ix": ix, "normal": nrm, "dip": dip, "dip_dir": ddir,
                    "unroll": un, "rejoined": True,
                    "parts": int(groups[i]["f"].get("parts", 1)
                                 + groups[j]["f"].get("parts", 1))}
            groups = [g for k, g in enumerate(groups) if k not in (i, j)]
            groups.append({"ix": ix, "f": keep, "r": r, "tag": len(seen) + len(groups) + 1,
                           "w": float(np.median(np.abs((fw[ix] - hw[ix]) @ nrm))) or 1.0})
            merged = True
    return [g["f"] for g in groups] + rest


def _drape_sheet(mid, hw, fw, swing=0.5, max_dip=45.0, min_pts=12, better=0.6):
    """One folded horizon, where draping describes this group better than a plane.

    The flat-lying mirror of `_spine_sheet`. A bedded body — sedex, iron
    formation, a laterite blanket, the shallow-dipping parts of a vein system —
    folds, and a fold is not a plane and never will be; but it IS a surface, and
    read as a height field it is as simple as a flat bed. Measured on a bed
    folded 90 m over 400 m, the departure from its own best-fit plane is 23.7 m
    and from the draped horizon 4.2 m.

    The residuals are brought into the same units before they are compared, the
    same trap `_spine_sheet` fell into: the plane's is measured PERPENDICULAR to
    it and the drape's VERTICALLY, and for a bed dipping d a vertical offset h
    sits h*cos(d) from the bed. Without that conversion a dipping bed's drape is
    charged 1/cos(d) for the dip it has.
    """
    mid = np.asarray(mid, float)
    if len(mid) < min_pts:
        return None
    C = mid - mid.mean(0)
    try:
        nrm = _unit(np.linalg.svd(C, full_matrices=False)[2][2])
    except Exception:
        return None
    dip, ddir = dip_of(nrm)
    if dip > max_dip:
        return None                       # steep: that is the spine's case
    width = float(np.median(np.abs((np.asarray(fw, float)
                                    - np.asarray(hw, float)) @ nrm))) or 1.0
    flat = float(np.percentile(np.abs(C @ nrm), 90))
    if flat <= max(swing * width, 1.0):
        return None                       # a plane already holds it
    try:
        dr = Draped(mid)
        _u, _v, w = dr.to_uvw(mid)
    except Exception:
        return None
    folded = float(np.percentile(np.abs(w), 90)) * float(np.cos(np.radians(dip)))
    if folded >= better * flat:
        return None                       # the drape is no real improvement
    R = plane_frame(nrm)
    meta = {"ix": np.arange(len(mid)), "normal": nrm, "dip": dip,
            "dip_dir": ddir, "unroll": dr, "draped": True,
            "curved_m": round(folded, 1), "planar_m": round(flat, 1)}
    return R, meta


def _unroll_for(mid, R, width, meta, swing=0.5, min_dip=40.0, min_pts=12):
    """Spine coordinates for this vein, or None to keep the plane.

    Asked of the data, not assumed. A vein is unrolled when it is steep enough
    for a horizontal offset to mean "across the vein" at all, and when its
    contacts stray from their own best-fit plane by more than a couple of
    times the vein's thickness — which is what it means for a vein to swing.
    A straight vein's spine is a straight line and unrolling reduces to the
    plane exactly, so this is about cost and caution, not capability.
    """
    mid = np.asarray(mid, float)
    if len(mid) < min_pts:
        return None
    if meta is not None and meta.get("dip") is not None and meta["dip"] < min_dip:
        return None                       # flat-lying: a horizontal offset is not "across"
    C = mid - mid.mean(0)
    off = np.abs(C @ _unit(np.asarray(R, float)[2]))
    if float(np.percentile(off, 90)) <= max(swing * float(width), 1.0):
        return None                       # a plane already holds it
    try:
        un = Unrolled(mid)
    except Exception:
        return None
    _u, _v, w = un.to_uvw(mid)
    # and only if the spine really is the better description. `w` is measured
    # HORIZONTALLY across the spine and `off` PERPENDICULAR to the plane, so
    # the horizontal offset is converted before the two are compared.
    dip = meta.get("dip") if isinstance(meta, dict) else None
    sd = float(np.sin(np.radians(dip))) if dip else 1.0
    if float(np.percentile(np.abs(w), 90)) * sd >= float(np.percentile(off, 90)):
        return None
    return un


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
         extrap=0.25, res=None, voids=None, unroll=None):
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

    if unroll is not None:
        # arc length along the vein's own spine, elevation, offset across
        u, v, w = unroll.to_uvw(mid)
        nloc = unroll.normals(u)
        t_true = np.abs(((np.asarray(c["fw"], float)
                          - np.asarray(c["hw"], float)) * nloc).sum(1))
    else:
        Q = mid @ R.T                               # u, v in the plane; w across
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
        QV = (np.column_stack(unroll.to_uvw(np.asarray(voids, float)))
              if unroll is not None else np.asarray(voids, float) @ R.T)
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

    V3 = _nodes(U, V, REF, THK, R, +0.5, unroll)
    V4 = _nodes(U, V, REF, THK, R, -0.5, unroll)
    verts, faces = _skin(M, V3, V4, nu, nv)
    if faces is None or not len(faces):
        return None

    pts = _fill(U, V, REF, THK, M, R, fill_step or max(step * 0.5, 1.0), unroll)
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


def _nodes(U, V, REF, THK, R, side, unroll=None):
    """Lattice nodes of one wall, back in world coordinates.

    With a plane the map is a rotation. With an unrolled spine it is not — the
    across direction turns along the vein — so the frame does the mapping.
    """
    Q = np.stack([U, V, REF + side * THK], axis=-1).reshape(-1, 3)
    W = (unroll.to_world(Q[:, 0], Q[:, 1], Q[:, 2]) if unroll is not None
         else Q @ R)
    return np.asarray(W).reshape(U.shape[0], U.shape[1], 3)


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


def _fill(U, V, REF, THK, M, R, step, unroll=None):
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
    Q = np.concatenate(out)
    if unroll is not None:
        return np.asarray(unroll.to_world(Q[:, 0], Q[:, 1], Q[:, 2]))
    return Q @ R
