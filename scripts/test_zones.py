#!/usr/bin/env python3
"""Bodies on one property, and the mineralisation that belongs to none of them.

A property is not a deposit, so each connected body gets its own wireframe. The
remainder -- intercepts too lonely for the zone finder to call a body -- is kept
rather than dropped, because dropping it shrinks the models that are already
short. But keeping it as ONE body triangulated a single sheet 6,825 m across
White and 1,940 m across Castle, with the named zones holding two cells each:
the exact failure the zone split exists to prevent, reintroduced for the
remainder. These checks hold that line.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from minemodelingpro import zones as ZON, domain as DOM

P = F = 0
def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print(f"  ok   {name}" + (f"  ({extra})" if extra else ""))
    else:
        F += 1; print(f"  FAIL {name}" + (f"  ({extra})" if extra else ""))

def lens(cx, cy, cz, n, hole_prefix, spread=12.0, seed=0):
    """A small lens: n composites on a few holes around one point."""
    r = np.random.default_rng(seed)
    pts = np.c_[r.normal(cx, spread, n), r.normal(cy, spread, n), r.normal(cz, spread/2, n)]
    hid = np.array([f"{hole_prefix}-{i % 4}" for i in range(n)])
    return pts, hid

print("\ntwo lenses a kilometre apart are two bodies")
a_p, a_h = lens(0, 0, 0, 40, "A", seed=1)
b_p, b_h = lens(1200, 0, 0, 40, "B", seed=2)
pts = np.r_[a_p, b_p]; hid = np.r_[a_h, b_h]
lab, zl = ZON.find(pts, hid, 60.0)
ok("the finder names two", len(zl) == 2, f"{len(zl)} zones")
ok("and nothing is left over", int((lab == 0).sum()) == 0, f"{int((lab==0).sum())} loose")
ok("the two are not one body",
   len(set(lab[:40].tolist())) == 1 and len(set(lab[40:].tolist())) == 1
   and lab[0] != lab[40])

print("\none wireframe per body never spans the gap between them")
w1, _i1 = DOM.wireframe(pts[lab == 1], np.ones(int((lab == 1).sum())), hid[lab == 1],
                        0.5, reach=60.0, step=4.0)
w2, _i2 = DOM.wireframe(pts[lab == 2], np.ones(int((lab == 2).sum())), hid[lab == 2],
                        0.5, reach=60.0, step=4.0)
span = lambda w: float(w[:, 0].max() - w[:, 0].min()) if w is not None and len(w) else 0.0
ok("each body stays its own size", max(span(w1), span(w2)) < 300.0,
   f"widest {max(span(w1), span(w2)):.0f} m")
whole, _iw = DOM.wireframe(pts, np.ones(len(pts)), hid, 0.5, reach=60.0, step=4.0)
ok("triangulating them together does span it", span(whole) > 900.0,
   f"{span(whole):.0f} m — this is what one leftover body did")

print("\nlone intercepts the finder will not name are still kept")
lone = np.array([[0.0, 0.0, 0.0], [700.0, 0.0, 0.0], [1400.0, 0.0, 0.0]])
lh = np.array(["X-1", "X-2", "X-3"])
lab2, zl2 = ZON.find(lone, lh, 60.0)
ok("three isolated intercepts name no zone", len(zl2) == 0, f"{len(zl2)}")
ok("and all three are left over", int((lab2 == 0).sum()) == 3)
lab3, zl3 = ZON.find(lone, lh, 60.0, min_holes=1, min_points=1)
ok("asked for every component, it returns three", len(zl3) == 3, f"{len(zl3)}")
ok("one per point", sorted(np.bincount(lab3)[1:].tolist()) == [1, 1, 1])

print("\na body of one or two holes has no surface to triangulate")
# a real hole's samples are contiguous down a line, not a cloud: intercepts_w
# breaks a hole's run where the gap exceeds 12 m, so the fixture has to be the
# shape a hole actually makes or it fragments into two dozen "intercepts"
md = np.arange(30) * 1.5
one_p = np.c_[md * 0.34, np.zeros(30), -md * 0.94]
one_h = np.array(["H-0"] * 30)
w, info = DOM.wireframe(one_p, np.ones(30), one_h, 0.5, reach=60.0, step=4.0)
ok("thirty composites down one hole are one intercept", info.get("intercepts") == 1,
   str(info.get("intercepts")))
ok("and it returns the intercept, not a sheet", w is not None and len(w) <= 2,
   f"{0 if w is None else len(w)} points")

print("\nthe leftovers are split at the same distance the zones are")
# the distance matters: clustering the leftovers at the wireframe's tighter
# reach, without first giving each body its composites, left every component
# under three points and collapsed real shapes to a handful of cells
far_p, far_h = lens(400, 0, 0, 12, "C", spread=8.0, seed=4)
mix = np.r_[a_p, far_p]; mixh = np.r_[a_h, far_h]
l_tight, z_tight = ZON.find(mix, mixh, 30.0, min_holes=1, min_points=1)
l_wide, z_wide = ZON.find(mix, mixh, 500.0, min_holes=1, min_points=1)
ok("a tight reach keeps them apart", len(z_tight) >= 2, f"{len(z_tight)}")
ok("a reach past the gap joins them", len(z_wide) == 1, f"{len(z_wide)}")

print("\nclustering happens in the deposit's own frame")
# along strike is generous, across it is tight: the same 200 m gap is one body
# along the long axis and two across it
rot = np.eye(3); an = (1.0, 0.9, 0.2)
# a 50 m step-out: inside the 60 m reach along the long axis, and five times
# that once warped across it, which is the whole point of clustering in the
# deposit's frame rather than in plain metres
along = np.r_[lens(0, 0, 0, 20, "P", seed=5)[0], lens(50, 0, 0, 20, "Q", seed=6)[0]]
across = np.r_[lens(0, 0, 0, 20, "P", seed=5)[0], lens(0, 0, 50, 20, "Q", seed=6)[0]]
h2 = np.array([f"P-{i%4}" for i in range(20)] + [f"Q-{i%4}" for i in range(20)])
la, za = ZON.find(along, h2, 60.0, rot=rot, aniso=an)
lc, zc = ZON.find(across, h2, 60.0, rot=rot, aniso=an)
ok("a 50 m step-out along strike is one body", len(za) == 1, f"{len(za)} zones")
ok("the same step-out across it is two", len(zc) == 2, f"{len(zc)} zones")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
