#!/usr/bin/env python3
"""The body as geometry: the surface a vein actually has, not a row of boxes.

The wireframe always knew the shape — a sheet triangulated through the
mineralised intercepts and inflated to the thickness each intercept shows. It
was then sampled into points and the points were snapped to the block lattice,
so a vein that pinches to half a metre between two holes and swells to thirty in
a third arrived on screen as the same row of 5 m cubes either way, and a 1 m vein
was drawn as wide as a block.

These check the geometry that is now kept: that it is closed, that its thickness
is the intercepts' own, that it stops where the alpha criterion stopped the
triangulation, and that what the page carries decodes back to it.
"""
import os
import sys
import json
import glob
import re

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np
from minemodelingpro import domain as DOM
from minemodelingpro.model3d import anisotropy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def edge_counts(F):
    e = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    _u, c = np.unique(np.sort(e, axis=1), axis=0, return_counts=True)
    return c


def signed_volume(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def vein(n=40, seed=3):
    """A plane dipping 60 degrees whose intercepts pinch and swell 2 m to 25 m."""
    rng = np.random.default_rng(seed)
    u = rng.uniform(-200, 200, n)
    v = rng.uniform(-120, 120, n)
    nrm = np.array([np.cos(np.radians(60)), 0.0, np.sin(np.radians(60))])
    e1 = np.array([0.0, 1.0, 0.0])
    e2 = np.cross(nrm, e1)
    P = u[:, None] * e1 + v[:, None] * e2
    pts, g, hid, want = [], [], [], []
    for i in range(n):
        w = 2.0 + 23.0 * (0.5 + 0.5 * np.sin(u[i] / 60.0))
        k = max(2, int(round(w)))
        want.append(float(k - 1))                 # the intercept's own length
        for j in range(k):
            pts.append(P[i] + (j - (k - 1) / 2.0) * nrm)
            g.append(3.0)
            hid.append("H%03d" % i)
    return (np.asarray(pts), np.asarray(g), np.asarray(hid),
            np.asarray(want), nrm)


print("== a tabular vein")
pts, g, hid, want, nrm = vein()
rot, an = anisotropy(pts, g, [1.0, 0.8, 0.25])
S, info = DOM.wireframe(pts, g, hid, 1.0, rot=rot, aniso=an, reach=140.0, step=6.0)
check("a sheet is triangulated through the intercepts", info.get("mode") == "sheet", info)
mesh = info.get("_mesh")
check("the geometry is kept, not only the point cloud", mesh is not None)
if mesh is not None:
    V, F = mesh
    check("the surface is closed (every edge shared by exactly two triangles)",
          bool((edge_counts(F) == 2).all()), np.unique(edge_counts(F)))
    check("it encloses a volume", signed_volume(V, F) != 0.0)
    half = len(V) // 2
    th = np.linalg.norm(V[:half] - V[half:], axis=1)
    check("the thickness is the intercepts' own, not one number for the body",
          th.max() - th.min() > 15.0, (round(float(th.min()), 2), round(float(th.max()), 2)))
    check("the thinnest part is as thin as the thinnest intercept",
          th.min() <= want.min() + 1.5, (float(th.min()), float(want.min())))
    check("the thickest part is as thick as the thickest intercept, and no thicker",
          want.max() - 1.5 <= th.max() <= want.max() + 1.5,
          (float(th.max()), float(want.max())))
    # the sheet is the intercepts offset either way: nothing may wander off it
    mids = (V[:half] + V[half:]) / 2.0
    cent, _h, _w = DOM.intercepts_w(pts, g, hid, 1.0)
    d = np.linalg.norm(mids[:, None, :] - cent[None, :, :], axis=2).min(1)
    check("every vertex sits on an intercept, not on invented ground",
          float(d.max()) < 1e-6, float(d.max()))
    # and the alpha criterion still binds: no triangle wider than the drill pattern
    el = np.linalg.norm(V[F[:, [1, 2, 0]]] - V[F], axis=2)
    check("no triangle spans more than the link reach allows",
          float(el.max()) <= 2.0 * info["reach_m"] + 1e-6,
          (float(el.max()), 2.0 * info["reach_m"]))

print("\n== one fence of holes: no surface to triangulate")
# intercepts along a single line — a drill section, not a pattern
rng = np.random.default_rng(7)
pts2, g2, hid2 = [], [], []
for i in range(8):
    base = np.array([i * 40.0, 0.0, -i * 20.0])
    for j in range(6):
        pts2.append(base + np.array([0.0, 0.0, j * 1.0]))
        g2.append(4.0)
        hid2.append("F%02d" % i)
pts2, g2, hid2 = np.asarray(pts2), np.asarray(g2), np.asarray(hid2)
rot2, an2 = anisotropy(pts2, g2, [1.0, 0.8, 0.25])
S2, info2 = DOM.wireframe(pts2, g2, hid2, 1.0, rot=rot2, aniso=an2, reach=90.0, step=6.0)
if info2.get("mode") == "strip":
    m2 = info2.get("_mesh")
    check("a single fence still gets a body, drawn as sleeves along its links",
          m2 is not None and len(m2[1]) > 0, info2)
    if m2 is not None:
        check("those sleeves are closed too", bool((edge_counts(m2[1]) == 2).all()),
              np.unique(edge_counts(m2[1])))
else:
    check("a single fence still gets a body", info2.get("_mesh") is not None, info2)

print("\n== a body with no plane to it")
blob = rng.normal(0, 60, (900, 3))
gb = np.full(len(blob), 3.0)
hb = np.array(["B%03d" % (i // 9) for i in range(len(blob))])
rotb, anb = anisotropy(blob, gb, [1.0, 1.0, 1.0])
S3, info3 = DOM.wireframe(blob, gb, hb, 1.0, rot=rotb, aniso=anb, reach=120.0, step=20.0)
check("a solid is filled rather than a sheet", info3.get("mode") == "solid", info3.get("mode"))
m3 = info3.get("_mesh")
check("the solid's outside is kept", m3 is not None and len(m3[1]) > 0)
if m3 is not None:
    V3, F3 = m3
    check("the solid's surface is closed", bool((edge_counts(F3) == 2).all()),
          np.unique(edge_counts(F3)))
    c = V3[np.unique(F3)].mean(0)
    check("its triangles face outwards (it is not inside out)",
          signed_volume(V3 - c, F3) > 0, signed_volume(V3 - c, F3))

print("\n== two veins are not one sheet")
# The fault this pins: the alpha criterion decided whether a triangle was real
# ground by measuring it in the body's SHADOW — its own plane, where the
# across-strike distance between two parallel veins is flattened to nothing —
# and isotropically, so one number had to serve for "along strike, which is
# continuity" and "across strike, which is the next vein". Measured on this
# fixture before the fix: 147 of 192 accepted triangles welded one vein to the
# other, and 70% of the domain points landed in the barren rock between them.


def two_veins(gap=60.0, pitch=25.0, width=2.0):
    """Two parallel 2 m veins, `gap` apart across strike, on a 25 m pattern."""
    pts, g, hid, k = [], [], [], 0
    for v, xoff in enumerate((0.0, gap)):
        for y in np.arange(-150, 151, pitch):
            for z in np.arange(-200, 1, pitch):
                k += 1
                for j in range(int(width)):
                    pts.append([xoff + j * 1.0, y, z])
                    g.append(5.0)
                    hid.append("V%d_%03d" % (v, k))
    return np.asarray(pts), np.asarray(g), np.asarray(hid)


pv, gv, hv = two_veins()
rotv, anv = anisotropy(pv, gv, [1.0, 0.8, 0.25])
Sv, iv = DOM.wireframe(pv, gv, hv, 1.0, rot=rotv, aniso=anv, reach=30.0, step=8.0)
mv = iv.get("_mesh")
check("the two veins still produce a surface", mv is not None and len(mv[1]), iv.get("faces"))
if mv is not None:
    Vv, Fv = mv
    span = Vv[Fv][:, :, 0].max(1) - Vv[Fv][:, :, 0].min(1)
    check("no triangle is welded across the barren ground between them",
          int((span > 30).sum()) == 0, int((span > 30).sum()))
    half = len(Vv) // 2
    thv = np.linalg.norm(Vv[:half] - Vv[half:], axis=1)
    check("and the vein keeps the thickness the holes measured",
          abs(float(np.median(thv)) - 2.0) < 0.6, float(np.median(thv)))
inner = int(((Sv[:, 0] > 5) & (Sv[:, 0] < 55)).sum())
check("no part of the domain sits in the rock between the veins", inner == 0,
      (inner, len(Sv)))

# and the old rule is kept A/B-able, which is also how this is pinned: with it
# the same fixture welds, so the test is checking the fix and not the fixture
os.environ["MMP_ALPHA"] = "projected"
try:
    Sold, iold = DOM.wireframe(pv, gv, hv, 1.0, rot=rotv, aniso=anv, reach=30.0, step=8.0)
    old_inner = int(((Sold[:, 0] > 5) & (Sold[:, 0] < 55)).sum())
    check("MMP_ALPHA=projected still reproduces the fault it replaced",
          old_inner > len(Sold) // 2, (old_inner, len(Sold)))
finally:
    os.environ.pop("MMP_ALPHA", None)

# A single vein must be untouched: the test refuses welds, it does not refuse sheets.
p1, g1, h1 = [], [], []
for i, y in enumerate(np.arange(-150, 151, 25.0)):
    for j, z in enumerate(np.arange(-200, 1, 25.0)):
        for q in range(3):
            p1.append([q * 1.0, y, z]); g1.append(5.0); h1.append("S%03d_%03d" % (i, j))
p1, g1, h1 = np.asarray(p1), np.asarray(g1), np.asarray(h1)
r1, a1 = anisotropy(p1, g1, [1.0, 0.8, 0.25])
S1, i1 = DOM.wireframe(p1, g1, h1, 1.0, rot=r1, aniso=a1, reach=30.0, step=8.0)
check("one vein on its own is still triangulated in full", (i1.get("faces") or 0) > 100,
      i1.get("faces"))
check("and carries its own thickness to the page",
      i1.get("_thick") is not None and len(i1["_thick"]) > 0,
      None if i1.get("_thick") is None else len(i1["_thick"]))

print("\n== what the page carries")
pages = [p for p in sorted(glob.glob(os.path.join(ROOT, "site", "models", "*.html")))
         if not os.path.basename(p).startswith("_")]
seen = 0
for p in pages:
    try:
        h = open(p, encoding="utf-8").read()
        M = json.loads(re.search(r'<script id="model" type="application/json">(.*?)</script>',
                                 h, re.S).group(1))
    except Exception:
        continue
    for el, E in (M.get("elements") or {}).items():
        S = E.get("surface")
        if not S:
            continue
        seen += 1
        nm = "%s %s" % (os.path.basename(p), el)
        import base64
        V = np.frombuffer(base64.b64decode(S["v"]), np.uint16).reshape(-1, 3)
        F = np.frombuffer(base64.b64decode(S["f"]),
                          np.uint32 if S.get("wide") else np.uint16).reshape(-1, 3)
        check("%s: the vertex count matches the buffer" % nm, len(V) == S["nv"],
              (len(V), S["nv"]))
        check("%s: the triangle count matches the buffer" % nm, len(F) == S["tris"],
              (len(F), S["tris"]))
        check("%s: no triangle points past the end of the vertices" % nm,
              int(F.max()) < len(V), (int(F.max()), len(V)))
        tot = sum(gp["count"] for gp in S["groups"])
        check("%s: the bodies cover every triangle exactly once" % nm,
              tot == len(F) * 3, (tot, len(F) * 3))
        starts = [gp["start"] for gp in S["groups"]]
        check("%s: the bodies are contiguous, so one can be drawn alone" % nm,
              starts == sorted(starts) and all(
                  S["groups"][i]["start"] + S["groups"][i]["count"]
                  == S["groups"][i + 1]["start"] for i in range(len(S["groups"]) - 1)),
              starts[:6])
        # The contacts the surface is fitted through. They travel with it, so a
        # reader can tell a wall that was measured from a wall that was guessed.
        C = S.get("contacts")
        if C:
            P = np.frombuffer(base64.b64decode(C["p"]), np.uint16).reshape(-1, 3)
            check("%s: the contact count matches the buffer" % nm,
                  len(P) == C["n"], (len(P), C["n"]))
            check("%s: the contacts come in wall pairs" % nm, C["n"] % 2 == 0, C["n"])
            ctot = sum(gp["count"] for gp in C["groups"])
            check("%s: the bodies cover every contact exactly once" % nm,
                  ctot == C["n"], (ctot, C["n"]))
            cst = [gp["start"] for gp in C["groups"]]
            check("%s: the contacts are contiguous per body" % nm,
                  cst == sorted(cst) and all(
                      C["groups"][i]["start"] + C["groups"][i]["count"]
                      == C["groups"][i + 1]["start"]
                      for i in range(len(C["groups"]) - 1)), cst[:6])
            # a contact is a point on a drillhole, so it must sit inside the
            # model, not in the air beside it
            lo = np.asarray(C["origin"], float)
            hi = lo + np.asarray(C["scale"], float) * 65535.0
            sv = np.asarray(S["origin"], float)
            sh = sv + np.asarray(S["scale"], float) * 65535.0
            pad = np.maximum(sh - sv, 1.0) * 0.25
            check("%s: the contacts sit within the surface's own extent" % nm,
                  bool((lo >= sv - pad).all() and (hi <= sh + pad).all()),
                  (lo.round(1).tolist(), sv.round(1).tolist()))
        zids = {z["id"] for z in ((E.get("body") or {}).get("zones") or [])}
        if zids:
            check("%s: every body in the surface is one the envelope names" % nm,
                  all(gp["id"] in zids for gp in S["groups"]),
                  sorted({gp["id"] for gp in S["groups"]} - zids))
    if seen >= 6:
        break
if not seen:
    print("  (no built page carries a surface yet — rebuild the site to check this)")

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
