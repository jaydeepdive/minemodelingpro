#!/usr/bin/env python3
"""Where to drill next — a reading of the drilling, not a prediction.

The rule being tested is deliberately conservative: every proposal is one drill
spacing beyond a hole that cut mineralisation, in the body's own plane, on
ground no existing hole passes through. Nothing here extrapolates a grade past
the last hole, because the block model does not go there either — that is what
the edge of it means.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np                                      # noqa: E402
from minemodelingpro import domain as DOM               # noqa: E402
from minemodelingpro import targets as TG               # noqa: E402
from minemodelingpro.model3d import anisotropy          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def vein(seed=5):
    """A vertical vein striking east-west, drilled from -200 E to 0 E only.

    The eastern half is untested, so every target should step east, and the
    richest intercept on that eastern rim should come first."""
    rng = np.random.default_rng(seed)
    pts, g, hid = [], [], []
    for i, x in enumerate(np.arange(-200.0, 1.0, 25.0)):
        for y in (-40.0, 0.0, 40.0):
            w = 4.0 + (14.0 if x > -60 else 0.0)        # it fattens to the east
            gr = 1.0 + (5.0 if x > -60 else 0.0)
            k = int(w)
            for j in range(k):
                pts.append([x, y, -50.0 - j * 1.0])
                g.append(gr + rng.normal(0, 0.05))
                hid.append("H%03d_%d" % (i, int(y)))
    return np.asarray(pts), np.asarray(g), np.asarray(hid)


pts, g, hid = vein()
rot, an = anisotropy(pts, g, [1.0, 0.8, 0.25])
cent, hole, wid, gr = DOM.intercepts_g(pts, g, hid, 0.5)
print("== the intercepts")
check("one intercept per hole", len(cent) == len({str(h) for h in hid}),
      (len(cent), len({str(h) for h in hid})))
check("each carries its own grade", gr.max() - gr.min() > 3.0,
      (round(float(gr.min()), 2), round(float(gr.max()), 2)))
check("and its own thickness", wid.max() - wid.min() > 8.0,
      (round(float(wid.min()), 1), round(float(wid.max()), 1)))

print("\n== the rim")
idx, dirs = DOM.boundary_of(cent, rot, reach=90.0)
check("the body has a rim", len(idx) > 3, len(idx))
check("every rim direction is a unit vector",
      np.allclose(np.linalg.norm(dirs, axis=1), 1.0, atol=1e-6),
      list(np.round(np.linalg.norm(dirs, axis=1)[:4], 4)))
mid = cent.mean(0)
outward = [float(np.dot(dirs[k], cent[i] - mid)) for k, i in enumerate(idx)]
check("every one of them points away from the body", min(outward) > -1e-6,
      round(min(outward), 3))

print("\n== the targets")
holes = [{"x": float(x), "y": float(y), "z": 0.0, "az": 0.0, "dip": -90.0, "depth": 120.0}
         for x in np.arange(-200.0, 1.0, 25.0) for y in (-40.0, 0.0, 40.0)]
tested = TG.hole_samples(holes)
check("a hole is sampled down its length, not just at its collar",
      len(tested) > len(holes) * 5, (len(tested), len(holes)))

ts = TG.propose(cent, gr, wid, hole, idx, dirs, tested, step=25.0, max_targets=10)
check("it proposes somewhere to drill", len(ts) >= 3, len(ts))
check("nothing is proposed where a hole already goes",
      all((t.get("nearest_drilling_m") or 99) >= 12.5 for t in ts),
      [t.get("nearest_drilling_m") for t in ts][:5])
check("no two proposals land on top of each other",
      all(np.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= 12.5 or a is b
          for a in ts for b in ts if a is not b),
      [(t["x"], t["y"]) for t in ts][:4])
check("the best one is on the rich eastern edge", ts[0]["x"] > -40.0 and ts[0]["grade"] > 4.0,
      (ts[0]["x"], ts[0]["grade"]))
check("they are ranked by the metal the hole beside them cut",
      [t["gram_metres"] for t in ts] == sorted((t["gram_metres"] for t in ts), reverse=True),
      [t["gram_metres"] for t in ts])
check("each names the hole it steps out from",
      all(str(t["from_hole"]) in set(map(str, hid)) for t in ts),
      [t["from_hole"] for t in ts][:3])
check("each steps exactly one spacing",
      all(abs(t["step_m"] - 25.0) < 1e-6 for t in ts), [t["step_m"] for t in ts][:3])

print("\n== where the rig stands")
c = TG.collar_for([100.0, 0.0, -150.0], az_deg=270.0, dip_deg=-45.0, surface_z=0.0)
check("a collar is placed up the hole's own direction", c is not None, c)
check("at the surface", c and c["z"] == 0.0, c)
check("its length is the rise over the sine of the dip",
      c and abs(c["depth_m"] - 150.0 * (2 ** 0.5)) < 0.5, c)
check("and it stands up-dip of the target", c and c["x"] > 100.0, c)
check("a flat hole is refused rather than projected to the horizon",
      TG.collar_for([0.0, 0.0, -100.0], 90.0, -1.0, 0.0) is None)
check("so is a target above the surface",
      TG.collar_for([0.0, 0.0, 50.0], 90.0, -60.0, 0.0) is None)
check("and one too deep to reach",
      TG.collar_for([0.0, 0.0, -3000.0], 90.0, -60.0, 0.0) is None)

print("\n== a body with no untested ground")
dense = TG.hole_samples([{"x": float(x), "y": float(y), "z": 0.0, "az": 0.0,
                          "dip": -90.0, "depth": 200.0}
                         for x in np.arange(-400.0, 401.0, 12.0)
                         for y in np.arange(-200.0, 201.0, 12.0)])
check("drilled out ground gets no targets",
      TG.propose(cent, gr, wid, hole, idx, dirs, dense, step=25.0) == [], "")

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
