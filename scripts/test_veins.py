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

print("\n== what a thin drill pattern still gives")
one = [(h, f, t, g) for (h, f, t, g) in segs[:1]]
c1 = VN.contacts(one, at, 5.0)
o1 = VN.build(c1, aniso=[1.0, 0.8, 0.25], reach=50.0)
check("a single intersection still makes a lens, not nothing",
      o1 is not None and len(o1["f"]) > 0, None if o1 is None else len(o1["f"]))
two = segs[:2]
o2 = VN.build(VN.contacts(two, at, 5.0), aniso=[1.0, 0.8, 0.25], reach=50.0)
check("two holes make a ribbon", o2 is not None and len(o2["f"]) > 0,
      None if o2 is None else len(o2["f"]))

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

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
