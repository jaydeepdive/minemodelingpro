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
