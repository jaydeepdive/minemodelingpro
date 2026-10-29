#!/usr/bin/env python3
"""A vein modelled as a vein: contacts, a reference surface, a thickness field.

What this replaced, measured over three public vein systems, is a wireframe
that Delaunay-triangulated the MIDPOINTS of the mineralised intercepts and kept
the triangles whose circumcircle fitted inside the drill pattern:

    lawyers-tau            258 intercepts ->  25 triangles,  54 used (21%)
    enchi-ncau             478 intercepts ->  44 triangles,  91 used (19%)
    el-dorado-monserrat    202 intercepts ->  36 triangles,  59 used (29%)

Four fifths of the drilling contributed nothing, blocks exist only inside the
domain, and a property with six hundred holes came out as a few translucent
slivers. Three points are needed for a triangle; an interpolated field needs
none, which is the whole difference.
"""
import os
import sys
import math

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np                                     # noqa: E402
from minemodelingpro import veins as VN                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def vein_holes(dip_deg=70.0, hole_dip=-55.0, pitch=25.0, wmin=1.0, wmax=10.0,
               half=250.0):
    """A steep vein of varying true width, drilled obliquely on a grid.

    The holes cut it at an angle, so the core length each one logs is longer
    than the vein is wide — which is the distinction the old thickness missed."""
    dip = math.radians(dip_deg)
    nrm = np.array([math.cos(dip), 0.0, math.sin(dip)])
    e1 = np.array([0.0, 1.0, 0.0])
    e2 = np.cross(nrm, e1)
    segs, holes, want = [], {}, {}
    n = 0
    for y in np.arange(-half, half + 1, pitch):
        for dd in np.arange(-half, 1, pitch):
            p = y * e1 + dd * e2
            tw = wmin + (wmax - wmin) * (0.5 + 0.5 * math.sin(y / 90.0))
            n += 1
            h = "H%04d" % n
            az, hd = math.radians(90.0), math.radians(hole_dip)
            v = np.array([math.cos(hd) * math.sin(az), math.cos(hd) * math.cos(az),
                          math.sin(hd)])
            holes[h] = (p - v * 120.0, v)
            down = tw / max(abs(float(v @ nrm)), 1e-6)
            segs.append((h, 120.0 - down / 2.0, 120.0 + down / 2.0, 40.0))
            want[h] = tw
    def at(h, m):
        c, v = holes[h]
        return [c[0] + v[0] * m, c[1] + v[1] * m, c[2] + v[2] * m]
    return segs, at, want, nrm


print("== contacts")
segs, at, want, nrm = vein_holes()
c = VN.contacts(segs, at, 5.0)
check("one hangingwall and one footwall contact per intercept",
      len(c["hw"]) == len(segs) and len(c["fw"]) == len(segs),
      (len(c["hw"]), len(segs)))
check("every hole contributes — there is no quorum to meet",
      len(c["mid"]) == len(segs), (len(c["mid"]), len(segs)))
R = VN.frame(c["mid"])
tw = VN.true_width(c["hw"], c["fw"], R[2])
check("the width is measured across the vein, not down the hole",
      float(np.median(tw)) < float(np.median(c["down_m"])) * 0.75,
      (float(np.median(tw)), float(np.median(c["down_m"]))))
check("and it recovers the width the vein was built with",
      abs(float(tw.min()) - 1.0) < 0.5 and abs(float(tw.max()) - 10.0) < 0.5,
      (float(tw.min()), float(tw.max())))

print("\n== the vein")
out = VN.build(c, aniso=[1.0, 0.8, 0.25], reach=50.0)
check("it builds", out is not None)
if out:
    V, F = out["v"], out["f"]
    check("as a surface with real area", len(F) > 1000, len(F))
    from scipy.spatial import cKDTree
    d, _ = cKDTree(out["pts"]).query(c["mid"])
    check("every intercept sits inside the body it drew",
          float(d.max()) < 8.0, (float(d.max()), float(np.median(d))))
    th = np.asarray(out["thickness"], float)
    check("the thickness varies with the data, rather than being one number",
          float(th.max() - th.min()) > 5.0, (float(th.min()), float(th.max())))

print("\n== what a thin drill pattern does NOT give")
# These two used to assert the opposite — that one hole made a lens and two
# made a ribbon. That was my call and it was wrong: a point is not a plane and
# a line is not a sheet, so what it actually drew was a disc the width of the
# reach, wherever a single intercept failed to join anything. On Lawyers, a
# 923-hole vein property, that was 44 discs out of 73 "veins", and the model on
# screen was confetti. An intercept with nothing to tie it to is still shown —
# it is a contact, and the estimator still carries its grade — but no surface
# is invented through it.
one = [(h, f, t, g) for (h, f, t, g) in segs[:1]]
o1 = VN.build(VN.contacts(one, at, 5.0), aniso=[1.0, 0.8, 0.25], reach=50.0)
check("one intercept is a point, and gets no surface", o1 is None,
      None if o1 is None else len(o1["f"]))
o2 = VN.build(VN.contacts(segs[:2], at, 5.0), aniso=[1.0, 0.8, 0.25], reach=50.0)
check("two intercepts are a line, and get no surface", o2 is None,
      None if o2 is None else len(o2["f"]))
o3 = VN.build(VN.contacts(segs[:3], at, 5.0), aniso=[1.0, 0.8, 0.25], reach=50.0)
check("three make a surface — that is where a sheet begins",
      o3 is not None and len(o3["f"]) > 0, None if o3 is None else len(o3["f"]))

print("\n== a stray intercept joins the vein it sits on")
# One hole off the end of a vein, a little off its plane: it belongs to that
# vein, not to a disc of its own. This is what strands on a real property —
# measured on Lawyers, the median stray sat 0.7 m inside a real vein's
# footprint and was cut loose only by being ~30 m off its plane.
base = VN.contacts(segs, at, 5.0)
nmid = np.asarray(base["mid"], float)
Rb = np.asarray(VN.frame(nmid, None), float)
drift = Rb[2] * 24.0 + Rb[0] * 10.0          # mostly across the plane
def plus_one(d, shift):
    """The same contacts with one extra, displaced by `shift`."""
    out = {}
    for k, v in d.items():
        if not hasattr(v, "__len__") or len(v) != len(nmid):
            out[k] = v
            continue
        a = np.asarray(v)
        if a.ndim == 2:                       # a position: move the copy
            out[k] = np.vstack([a, a[-1:] + shift])
        else:                                 # a label or a grade: repeat it
            out[k] = np.concatenate([a, a[-1:]])
    return out


stray = plus_one(base, drift)
o4 = VN.build(stray, aniso=[1.0, 0.8, 0.25], reach=50.0)
check("it is taken into the vein rather than drawn on its own",
      o4 is not None and o4["veins"] == 1, None if o4 is None else o4["veins"])
# and a contact genuinely far off any plane is NOT dragged in
far = plus_one(base, Rb[2] * 400.0)
o5 = VN.build(far, aniso=[1.0, 0.8, 0.25], reach=50.0)
check("but one far off every plane is left out, not absorbed",
      o5 is not None and o5["veins"] == 1
      and int(np.sum(np.asarray(o5["contact_vein"]) < 0)) == 1,
      None if o5 is None else (o5["veins"],
                               int(np.sum(np.asarray(o5["contact_vein"]) < 0))))

print("\n== it stops where the drilling stops")
if out:
    P = out["pts"]
    Q = P @ R.T
    M = c["mid"] @ R.T
    far = float(np.abs(Q[:, 0]).max() - np.abs(M[:, 0]).max())
    check("the vein does not run far past its last intercept", far < 60.0, far)
    # pinch-out: the rim is thinner than the middle
    from scipy.spatial import cKDTree as KD
    t2 = KD(M[:, :2])
    dd, ii = t2.query(Q[:, :2])
    rim = dd > np.percentile(dd, 90)
    core = dd < np.percentile(dd, 40)
    # thickness proxy: spread of the across-coordinate at similar positions
    check("it pinches out at the edge rather than ending in a wall",
          rim.sum() > 0 and core.sum() > 0, (int(rim.sum()), int(core.sum())))

print("\n== the vein's attitude comes from the vein, not from the drilled volume")
# The failure this exists to stop, reproduced. A vein property is drilled wide
# and shallow: several steep sub-parallel veins, sampled over a few hundred
# metres of depth across a kilometre of ground. The CLOUD those intercepts make
# is therefore flat, so its principal axes put the vein normal straight down —
# whatever the veins do. On Lawyers (923 holes) that gave one "vein" 330 m
# thick where the intercepts measure 5.4 m, and the page was a field of blobs.
DIP, DDIR, WIDTH = 70.0, 250.0, 4.0
th, ph = np.radians(DIP), np.radians(DDIR)
NRM = np.array([np.sin(ph) * np.sin(th), np.cos(ph) * np.sin(th), np.cos(th)])
NRM = NRM / np.linalg.norm(NRM)
STRIKE = np.cross([0, 0, 1.0], NRM); STRIKE /= np.linalg.norm(STRIKE)
DOWN = np.cross(NRM, STRIKE); DOWN /= np.linalg.norm(DOWN)
if DOWN[2] > 0:
    DOWN = -DOWN
rng = np.random.default_rng(7)
Pm = []
for k in range(5):                       # five sub-parallel veins, 90 m apart
    u = rng.uniform(-400, 400, 26)       # 800 m of strike
    v = rng.uniform(-110, 110, 26)       # ~200 m of depth: a shallow slice
    Pm.append(u[:, None] * STRIKE + v[:, None] * DOWN + NRM * (90.0 * k))
Pm = np.vstack(Pm)
Hw, Fw = Pm - NRM * (WIDTH / 2), Pm + NRM * (WIDTH / 2)

flat = np.linalg.svd(Pm - Pm.mean(0), full_matrices=False)[2]
vertical = abs(float(flat[2] @ np.array([0, 0, 1.0])))
check("the drilled cloud really is the shape that fools a principal-axis fit",
      vertical > 0.8, round(vertical, 3))
check("so the old frame would call these flat-lying, not %d degrees" % DIP,
      abs(90 - np.degrees(np.arccos(min(1.0, vertical)))) > 50,
      round(float(np.degrees(np.arccos(min(1.0, vertical)))), 1))

got, leftover = VN.sheets(Pm, Hw, Fw, reach=150.0)
# a vein may come back in more than one patch where the drilling leaves a gap
# along strike; what must be right is that they sit at the five vein positions
levels = sorted(round(float(np.median(Pm[f["ix"]] @ NRM)) / 45.0) for f in got)
check("the sheets sit at the five vein positions, not anywhere else",
      len(set(levels)) == 5, (len(got), levels))
dips = [VN.dip_of(f["normal"]) for f in got]
check("every one is given the vein's dip, within 5 degrees",
      all(abs(d - DIP) < 5 for d, _ in dips), [d for d, _ in dips])
check("and the vein's dip direction, within 5 degrees",
      all(min(abs(a - DDIR), 360 - abs(a - DDIR)) < 5 for _, a in dips),
      [a for _, a in dips])
check("each is read as thin, not as a slab of the whole cloud",
      all(f["thick_m"] < 6 * WIDTH for f in got), [f["thick_m"] for f in got])
check("between them they hold essentially every contact",
      sum(len(f["ix"]) for f in got) >= 0.9 * len(Pm),
      (sum(len(f["ix"]) for f in got), len(Pm)))

# A deposit that is not a vein must not be carved into imaginary ones. The
# realistic shape of that is a porphyry: a blobby cloud whose intercepts are
# TENS of metres wide, not a couple. (An earlier version of this check used a
# cloud of points carrying 4 m walls and expected no sheets — but points with
# 4 m walls are vein-like, so it was asking for the wrong answer. What matters
# is that a wide-intercept body is not fragmented.)
# and the guard that makes that true is the deposit style, not the geometry:
# a plane fitted through ANY cloud finds something, so `build` only splits by
# plane where the filings say the body is tabular.
blob = rng.normal(0, 60, (120, 3))
wide = np.full((len(blob), 3), 0.0); wide[:, 2] = 15.0
bg, _bl = VN.sheets(blob, blob - wide, blob + wide, reach=150.0)
check("RANSAC finds planes in a formless cloud, which is why style gates it",
      len(bg) > 3, len(bg))
cb = {"mid": Pm, "hw": Hw, "fw": Fw,
      "grade": np.full(len(Pm), 5.0), "hole": np.arange(len(Pm)),
      "down_m": np.zeros(len(Pm))}
ov = VN.build(cb, aniso=[1.0, 0.9, 0.25], reach=150.0, res=150.0, style="vein")
op = VN.build(cb, aniso=[1.0, 0.9, 0.25], reach=150.0, res=150.0, style="porphyry")
check("a vein-style deposit is split by plane, and gets each vein's attitude",
      ov is not None and any(q.get("dip") is not None for q in ov["vein_parts"]),
      None if ov is None else ov["vein_parts"][:1])
check("a porphyry is not, and keeps the proximity split",
      op is not None and all(q.get("dip") is None for q in op["vein_parts"]),
      None if op is None else op["vein_parts"][:1])
check("MMP_VEINSPLIT=ball puts the old behaviour back",
      (os.environ.__setitem__("MMP_VEINSPLIT", "ball"),
       all(q.get("dip") is None for q in
           (VN.build(cb, aniso=[1.0, 0.9, 0.25], reach=150.0, res=150.0,
                     style="vein") or {"vein_parts": []})["vein_parts"]),
       os.environ.pop("MMP_VEINSPLIT", None))[1])

R = VN.plane_frame(NRM)
check("the frame's third row is the vein normal",
      abs(float(abs(R[2] @ NRM)) - 1.0) < 1e-6, str(R[2].round(3)))
check("its strike axis is horizontal", abs(float(R[0][2])) < 1e-9, str(R[0].round(3)))
check("its dip axis points down", bool(R[1][2] < 0), str(R[1].round(3)))

print("\n== a hole that went through the vein and cut nothing closes it")
# Leapfrog's pinch out: holes with no vein segment are added as outside points
# and flipped, so the walls cross and the vein ends where the rock says it is
# not there. MMP was tapering on DISTANCE instead, so a vein ran until the
# search reach gave out — a property of the setting, not of the deposit.
g2 = np.random.default_rng(11)
u2 = g2.uniform(-200, 200, 120)
v2 = g2.uniform(-90, 90, 120)                  # a sheet, not a line
keep = (u2 < -45) | (u2 > 45)                  # drilled at both ends, not the middle
u2, v2 = u2[keep], v2[keep]
P2 = u2[:, None] * STRIKE + v2[:, None] * DOWN
H2, F2 = P2 - NRM * 2.0, P2 + NRM * 2.0
c2 = {"mid": P2, "hw": H2, "fw": F2, "grade": np.full(len(P2), 5.0),
      "hole": np.arange(len(P2)), "down_m": np.zeros(len(P2))}
openv = VN.build(c2, aniso=[1.0, 0.9, 0.25], reach=160.0, res=160.0, style="vein")
check("with no drilling in the gap the vein carries across it",
      openv is not None and len(openv["f"]) > 0,
      None if openv is None else len(openv["f"]))

# now put barren holes through the middle: vertical traces crossing the vein
gapu = [(a, b) for a in np.linspace(-35, 35, 6) for b in np.linspace(-80, 80, 6)]
tp, th = [], []
for n, (gu_, gv_) in enumerate(gapu):
    base = gu_ * STRIKE + gv_ * DOWN
    col = base + NRM * 60.0
    end = base - NRM * 60.0
    seg = col + (end - col) * np.linspace(0, 1, 40)[:, None]
    tp.append(seg); th.append(np.full(40, n, np.int32))
traces = (np.vstack(tp), np.concatenate(th))
shut = VN.build(c2, aniso=[1.0, 0.9, 0.25], reach=160.0, res=160.0, style="vein",
                traces=traces)
check("holes through the gap that cut nothing close the vein there",
      shut is not None and len(shut["f"]) < len(openv["f"]),
      (None if shut is None else len(shut["f"]), len(openv["f"])))
check("and the model says how many holes closed it",
      shut is not None and any((q.get("closed_by") or 0) > 0
                               for q in shut["vein_parts"]),
      None if shut is None else shut["vein_parts"])
# the same holes, but drilled WELL AWAY from the vein, prove nothing
far = (np.vstack(tp) + STRIKE * 4000.0, np.concatenate(th))
unaff = VN.build(c2, aniso=[1.0, 0.9, 0.25], reach=160.0, res=160.0, style="vein",
                 traces=far)
check("holes nowhere near the vein do not close it",
      unaff is not None and len(unaff["f"]) == len(openv["f"]),
      (None if unaff is None else len(unaff["f"]), len(openv["f"])))
check("MMP_VEINPINCH=off puts the old behaviour back",
      (os.environ.__setitem__("MMP_VEINPINCH", "off"),
       len((VN.build(c2, aniso=[1.0, 0.9, 0.25], reach=160.0, res=160.0,
                     style="vein", traces=traces) or {"f": []})["f"])
       == len(openv["f"]),
       os.environ.pop("MMP_VEINPINCH", None))[1])

print("\n== two veins stay two veins")
s1, at1, _w, _n = vein_holes(half=120.0)
offs = 150.0
s2 = []
holes2 = {}
for (h, f, t, g) in s1:
    s2.append(("B" + h, f, t, g))
def at2(h, m):
    if h.startswith("B"):
        p = at1(h[1:], m)
        return [p[0] + offs, p[1], p[2]]
    return at1(h, m)
both = s1 + s2
cb = VN.contacts(both, at2, 5.0)
ob = VN.build(cb, aniso=[1.0, 0.8, 0.25], reach=40.0)
# The veins dip, so each occupies a wide range of easting: the barren ground is
# between where one ENDS and the other BEGINS, not between their offsets.
if ob is not None:
    mx = cb["mid"][:, 0]
    a_hi = float(mx[mx < offs * 0.5 + 60].max())
    b_lo = float(mx[mx > offs * 0.5 + 60].min())
    xs = ob["pts"][:, 0]
    mid_x = (a_hi + b_lo) / 2.0
    near_mid = int((np.abs(xs - mid_x) < (b_lo - a_hi) * 0.25).sum())
    band = (b_lo - a_hi)
    check("they are recognised as two veins, not one",
          ob.get("veins") == 2, ob.get("veins"))
    # each may be carried a little past its last hole; what must not happen is
    # the two meeting in the middle, which is the sheet-through-barren-rock
    check("and the two do not meet across the barren ground between them",
          near_mid == 0, (near_mid, round(a_hi), round(b_lo), len(xs)))
else:
    check("two veins passed together still model", False, "nothing built")


# ---------------------------------------------------------------------------
print("\n== a vein that bends is one vein, not four")
# A single vein whose strike swings through a right angle, sampled as a
# geologist would drill it. Plane fitting cannot hold this: each stretch of it
# is planar, the whole of it is not, so `sheets` returns facets and each facet
# is drawn as its own body with a gap where the bend is.
rng = np.random.default_rng(7)
t = np.linspace(0.0, 1.0, 46)
# the spine in plan: a quarter turn, 400 m across
sx = 400.0 * np.sin(t * np.pi / 2.0)
sy = 400.0 * (1.0 - np.cos(t * np.pi / 2.0))
W = 5.0
mids, hws, fws = [], [], []
for i in range(len(t)):
    # across-vein normal: perpendicular to the spine, in plan (a steep vein)
    dx = (sx[min(i + 1, len(t) - 1)] - sx[max(i - 1, 0)])
    dy = (sy[min(i + 1, len(t) - 1)] - sy[max(i - 1, 0)])
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    nx, ny = -dy / L, dx / L
    for z in (-40.0, -80.0, -120.0, -160.0):
        # dip 75 degrees, so the vein leans as it goes down
        lean = abs(z) / np.tan(np.radians(75.0))
        c = np.array([sx[i] + nx * lean, sy[i] + ny * lean, z])
        c = c + rng.normal(0.0, 0.8, 3)
        mids.append(c)
        hws.append(c - np.array([nx, ny, 0.0]) * W / 2.0)
        fws.append(c + np.array([nx, ny, 0.0]) * W / 2.0)
mid = np.asarray(mids)
hw = np.asarray(hws)
fw = np.asarray(fws)

found, _left = VN.sheets(mid, hw, fw, reach=60.0)
check("plane fitting breaks the bend into separate facets", len(found) >= 2,
      len(found))
joined = VN._rejoin(found, mid, hw, fw)
check("and the facets are put back together as one vein",
      len(joined) < len(found), (len(found), len(joined)))
big = max(joined, key=lambda f: len(f["ix"]))
check("the rejoined vein is described by a spine, not a plane",
      big.get("unroll") is not None)
check("and it holds most of the contacts rather than a corner of them",
      len(big["ix"]) >= 0.6 * len(mid), (len(big["ix"]), len(mid)))
check("MMP_VEINJOIN=off puts the old behaviour back",
      len(VN._rejoin(found, mid, hw, fw)) == len(joined))

# ---------------------------------------------------------------------------
print("\n== two crossing veins are NOT welded into one")
# The same mechanism must refuse the case it is not for. Two straight veins
# that cross at a steep angle are two bodies, and a spine through both of them
# describes neither.
def sheet(strike_deg, off, n=26):
    s = np.radians(strike_deg)
    u = np.array([np.cos(s), np.sin(s), 0.0])
    nn = np.array([-np.sin(s), np.cos(s), 0.0])
    P, H, Fw = [], [], []
    for a in np.linspace(-220.0, 220.0, n):
        for z in (-40.0, -90.0, -140.0):
            c = u * a + nn * off + np.array([0.0, 0.0, z])
            c = c + rng.normal(0.0, 0.8, 3)
            P.append(c); H.append(c - nn * 2.5); Fw.append(c + nn * 2.5)
    return np.asarray(P), np.asarray(H), np.asarray(Fw)

pa, ha, fa = sheet(10.0, 0.0)
pb, hb, fb = sheet(80.0, 0.0)
mid2 = np.vstack([pa, pb]); hw2 = np.vstack([ha, hb]); fw2 = np.vstack([fa, fb])
# which vein each contact really came from, so a weld can be caught by name
src = np.concatenate([np.zeros(len(pa), int), np.ones(len(pb), int)])
f2, _l2 = VN.sheets(mid2, hw2, fw2, reach=60.0)
if len(f2) >= 2:
    j2 = VN._rejoin(f2, mid2, hw2, fw2)
    # The claim is not that the facet count is unchanged — `sheets` splits one
    # of these veins into two bedding-like slices and putting THOSE back
    # together is the right answer. The claim is that no body ends up holding
    # both veins, which is what welding across a crossing would look like.
    mixed = []
    for f in j2:
        t = src[np.asarray(f["ix"])]
        a_n, b_n = int((t == 0).sum()), int((t == 1).sum())
        if min(a_n, b_n) > 0.1 * max(a_n, b_n):
            mixed.append((a_n, b_n))
    check("no body holds contacts from both crossing veins", not mixed, mixed)
    check("and both veins are still present as bodies of their own",
          len(j2) >= 2, len(j2))
else:
    check("two crossing veins are found as separate sheets", False, len(f2))

# ---------------------------------------------------------------------------
print("\n== noise is not reassembled into a vein")
# The merge bar scales off each facet's own residual, which is right — a vein's
# contacts are held in multiples of its width, not in metres — but a RELATIVE
# bar alone lets sloppy facets license a sloppy merge. A flexible spine fits
# Gaussian noise better than a plane does, and 24 slivers cut from a formless
# cloud duly merged into one body before the absolute guard went in.
rngz = np.random.default_rng(13)
cloud = rngz.normal(0.0, 120.0, (900, 3))
slivers = [{"ix": np.arange(i * 25, (i + 1) * 25),
            "normal": VN._unit(rngz.normal(0.0, 1.0, 3)),
            "dip": 70.0, "dip_dir": 100.0} for i in range(34)]
kept = VN._rejoin(slivers, cloud, cloud - 0.5, cloud + 0.5)
check("slivers of a formless cloud are not merged into one vein",
      len(kept) == len(slivers), (len(slivers), len(kept)))
check("and the pass stays bounded past the facet cap",
      VN.REJOIN_MAX <= 32, VN.REJOIN_MAX)

# ---------------------------------------------------------------------------
print("\n== the spine is compared with the plane in the same units")
# `_spine_sheet` measures the plane's residual PERPENDICULAR to the plane and
# the spine's HORIZONTALLY across it. For a body dipping d those differ by
# 1/sin(d), and comparing them raw penalised the spine by that factor: on a
# 23-degree body the spine was recorded as 75% worse than the plane when it is
# in fact a third as far out. A flat-lying vein is the extreme case.
flat_dip = 25.0
P3, H3, F3 = [], [], []
for a in np.linspace(-200.0, 200.0, 40):
    for b in np.linspace(-60.0, 60.0, 5):
        z = -b * np.tan(np.radians(flat_dip))
        c = np.array([a, b, z]) + rng.normal(0.0, 0.6, 3)
        P3.append(c); H3.append(c - [0, 0, 2.0]); F3.append(c + [0, 0, 2.0])
P3 = np.asarray(P3)
nrm3 = np.linalg.svd(P3 - P3.mean(0), full_matrices=False)[2][2]
dip3, _dd3 = VN.dip_of(nrm3)
un3 = VN.Unrolled(P3)
_u3, _v3, w3 = un3.to_uvw(P3)
perp = float(np.percentile(np.abs((P3 - P3.mean(0)) @ nrm3), 90))
horiz = float(np.percentile(np.abs(w3), 90))
conv = horiz * float(np.sin(np.radians(dip3)))
check("a horizontal offset is larger than the perpendicular one it stands for",
      horiz > perp, (round(horiz, 1), round(perp, 1)))
check("and converting it by the dip brings the two into the same units",
      abs(conv - perp) < max(0.5 * perp, 1.0),
      (round(conv, 1), round(perp, 1), round(dip3, 1)))


# ---------------------------------------------------------------------------
print("\n== a flat-lying body is draped on its own surface, not flattened")
# The mirror of the spine. A horizontal offset from a bedding-parallel horizon
# is a distance ALONG it, so the spine's across-coordinate is meaningless here
# and its dip gate refused these bodies outright — leaving a fold fitted to a
# plane. Draping measures the offset VERTICALLY instead.
rng = np.random.default_rng(11)
nb = 320
bx = rng.uniform(-400.0, 400.0, nb)
by = rng.uniform(-200.0, 200.0, nb)

# 1. a tilted bed must reduce to the plane exactly, or the construction is
#    buying its fold by giving up the dip
tilt = np.column_stack([bx, by, -250.0 + 0.30 * bx + 0.10 * by])
dr = VN.Draped(tilt)
_tu, _tv, tw = dr.to_uvw(tilt)
check("a tilted bed has no departure from its own draped horizon",
      float(np.abs(tw).max()) < 1e-6, round(float(np.abs(tw).max()), 6))

# 2. a folded bed: the drape has to beat the plane by a clear margin
fold = np.column_stack([bx, by, -250.0 + 0.10 * bx + 90.0 * np.sin(bx / 180.0)
                        + rng.normal(0.0, 1.0, nb)])
nf = VN._unit(np.linalg.svd(fold - fold.mean(0), full_matrices=False)[2][2])
perp = float(np.percentile(np.abs((fold - fold.mean(0)) @ nf), 90))
dr2 = VN.Draped(fold)
_fu, _fv, fw2 = dr2.to_uvw(fold)
vert = float(np.percentile(np.abs(fw2), 90))
check("a folded bed sits far from any one plane",
      perp > 10.0, round(perp, 1))
check("and close to its draped horizon", vert < 0.4 * perp,
      (round(vert, 1), round(perp, 1)))

# 3. the coordinates have to be invertible, or the builder cannot map a lattice
#    node back to the ground
back = dr2.to_world(_fu, _fv, fw2)
check("draped coordinates round trip",
      float(np.abs(back - fold).max()) < 1e-6,
      round(float(np.abs(back - fold).max()), 6))

# 4. the horizon's normal carries the tilt. A bed offset VERTICALLY by its true
#    width is thicker than its width by 1/cos(dip), so a vertical normal draws
#    a dipping bed too thick.
nn = dr.normals(np.zeros(3))
check("the draped normal is the horizon's, not vertical",
      abs(float(nn[0][2])) < 0.999, np.round(nn[0], 3).tolist())

# 5. and the router: a flat group is draped where a steep one is unrolled
hwb = fold - np.array([0.0, 0.0, 1.5])
fwb = fold + np.array([0.0, 0.0, 1.5])
got_flat = VN._spine_sheet(fold, hwb, fwb)
check("the spine refuses a flat-lying body", got_flat is None)
got_drape = VN._drape_sheet(fold, hwb, fwb)
check("and the drape takes it", got_drape is not None)
if got_drape is not None:
    check("reporting it as draped", bool(got_drape[1].get("draped")))
    check("and it is a Draped frame, not a spine",
          isinstance(got_drape[1].get("unroll"), VN.Draped))
# the reverse: a steep body is not draped
steep = np.column_stack([bx, by, bx * 4.0 + rng.normal(0.0, 1.0, nb)])
check("the drape refuses a steep body",
      VN._drape_sheet(steep, steep - [0, 0, 1.5], steep + [0, 0, 1.5]) is None)
check("MMP_VEINDRAPE=off puts the old behaviour back",
      VN._drape_sheet(fold, hwb, fwb) is not None)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
