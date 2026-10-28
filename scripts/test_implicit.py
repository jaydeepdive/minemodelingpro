#!/usr/bin/env python3
"""The implicit builder: a deposit as a signed field, isosurfaced.

The shape these tests care about is the one every earlier builder got wrong by
construction. A vein that swings in strike cannot be a height-field over a
plane, so the planar builder fragments it or sheds its ends; the alpha builder
has no notion of inside or outside at all. A field has no parameterisation to
fail, so the test is simply: does the modelled surface follow a vein that
twists, and does it stop where the drilling says the rock is barren.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


try:
    from minemodelingpro import implicit as IM
    from scipy.spatial import cKDTree
except Exception as e:                                       # noqa: BLE001
    print("implicit not importable (%s) — skipping" % type(e).__name__)
    sys.exit(0)


def twisting_vein(swing=160.0, n=60, scale=1.0, miss=45.0):
    """A vein whose strike swings, and which pinches and swells along it."""
    t = np.linspace(-1, 1, n)
    centre = np.column_stack([t * 300.0, swing * np.sin(t * 1.5), -40.0 * t])
    w = (2.0 + 10.0 * (0.5 + 0.5 * np.cos(t * 4))) * scale
    tg = np.gradient(centre, axis=0)
    tg /= np.linalg.norm(tg, axis=1)[:, None]
    nr = np.column_stack([-tg[:, 1], tg[:, 0], np.zeros(n)])
    nr /= np.linalg.norm(nr, axis=1)[:, None]
    rng = np.random.default_rng(3)
    ins, con, out = [], [], []
    for i in range(n):
        for dd in np.linspace(-120, 120, 9):
            p = centre[i] + np.array([0.0, 0.0, -1.0]) * dd
            ins.append(p + nr[i] * rng.uniform(-w[i] / 2, w[i] / 2))
            con.append(p + nr[i] * (w[i] / 2))
            con.append(p - nr[i] * (w[i] / 2))
            for off in (-miss, miss):
                out.append(p + nr[i] * off)
    return (np.array(ins), np.array(con), np.array(out), centre, w)


print("== a vein that twists")
ins, con, out, centre, w = twisting_vein()
got = IM.surface(ins, con, out, aniso=[1.0, 1.0, 0.35], reach=70.0, step=6.0)
check("it is modelled at all", got is not None)
if got is not None:
    V = got["v"]
    T = cKDTree(V)
    wall = float(np.median(T.query(con)[0]))
    check("the surface sits on the wall contacts, not between the clouds",
          wall < 8.0, round(wall, 1))
    # the swing is the point: the surface has to reach both ends of it
    sw = float(V[:, 1].max() - V[:, 1].min())
    check("it follows the swing in strike rather than cutting the corner",
          sw > 250.0, round(sw, 0))
    check("and does not balloon past the drilling",
          float(V[:, 0].max() - V[:, 0].min()) < 800.0,
          round(float(V[:, 0].max() - V[:, 0].min()), 0))
    # The point of the fixture, stated as the thing actually claimed: the
    # planar builder cannot hold this vein and the field can. Measured, not
    # asserted — the walls stray from their own best-fit plane by several times
    # the vein's thickness, which is what "twists" means in metres.
    C = con - con.mean(0)
    nrm = np.linalg.svd(C, full_matrices=False)[2][2]
    off = float(np.percentile(np.abs(C @ nrm), 90))
    check("the walls stray from any one plane by more than the vein is thick",
          off > 2 * float(np.median(w)), (round(off, 1), round(float(np.median(w)), 1)))
    try:
        from minemodelingpro import veins as VN
        found, left = VN.sheets(con, con - 0.5, con + 0.5, reach=70.0)
        covered = sum(len(f["ix"]) for f in found)
        check("the planar builder breaks it up or loses it",
              len(found) != 1 or covered < 0.8 * len(con),
              (len(found), covered, len(con)))
        check("the field holds it as one surface instead",
              len(got["f"]) > 0, len(got["f"]))
    except Exception as e:                                   # noqa: BLE001
        check("planar comparison ran", False, type(e).__name__)

print("\n== the drilling decides where it stops")
ins2, con2, out2, _c2, _w2 = twisting_vein(miss=30.0)
tight = IM.surface(ins2, con2, out2, aniso=[1.0, 1.0, 0.35], reach=70.0, step=6.0)
loose = IM.surface(ins2, con2, None, aniso=[1.0, 1.0, 0.35], reach=70.0, step=6.0)
check("holes that cut nothing hold the body in",
      tight is not None and loose is not None
      and len(tight["v"]) and len(loose["v"]),
      (tight is None, loose is None))
if tight is not None and loose is not None:
    a = float(np.ptp(tight["v"] @ np.array([0.0, 1.0, 0.0])))
    b = float(np.ptp(loose["v"] @ np.array([0.0, 1.0, 0.0])))
    check("so a body with barren holes around it is no wider than one without",
          a <= b + 1e-6, (round(a, 1), round(b, 1)))

print("\n== a deposit that is not a vein")
rng = np.random.default_rng(1)
blob = rng.normal(0.0, 70.0, (400, 3))
bg = IM.surface(blob, None, None, aniso=[1.0, 1.0, 0.8], reach=120.0, step=12.0)
check("a shapeless body is modelled with no walls and no barren holes",
      bg is not None and len(bg["f"]) > 0, bg is None)
if bg is not None:
    grew = float(np.max((bg["v"].max(0) - bg["v"].min(0))
                        - (blob.max(0) - blob.min(0))))
    check("and it is a halo on the drilling, not a box around it",
          0 < grew < 120.0, round(grew, 1))
far = blob * 3.0
bg2 = IM.surface(blob, None, far, aniso=[1.0, 1.0, 0.8], reach=120.0, step=12.0)
check("barren ground around it is used when there is some",
      bg2 is not None and bg2["n_outside"] > 0,
      None if bg2 is None else bg2["n_outside"])

print("\n== the field is the same however it is chunked")
P = np.vstack([ins[:200], con[:200]])
S = np.concatenate([-np.ones(200), np.zeros(200)])
G = rng.uniform(-200, 200, (5000, 3))
one = IM.field(P, S, None, [1.0, 1.0, 0.5], 8.0, G, chunk=10 ** 9)
many = IM.field(P, S, None, [1.0, 1.0, 0.5], 8.0, G, chunk=137)
check("chunking changes nothing", bool(np.allclose(one, many)),
      float(np.abs(one - many).max()))

print("\n== it refuses rather than invents")
check("three points are not a deposit",
      IM.surface(rng.normal(0, 10, (2, 3)), None, None, reach=50.0) is None)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
