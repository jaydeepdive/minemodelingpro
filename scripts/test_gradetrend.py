"""The grade search in the deposit's own orientation, where that varies.

`model3d.trend_metrics` reads a search metric per neighbourhood off the
mineralised composites, and `_knn_varying` runs the per-block search in it. The
claims worth pinning are the ones that would be silent if they broke: that the
bounded per-cell search is EXACT (it builds each cell's tree over only the
composites that cell could reach, which is an optimisation, not an
approximation), that reducing to one global frame reproduces the global search
exactly, and that no block the gate admitted comes back without a grade.

    PYTHONPATH=src python3 scripts/test_gradetrend.py
"""
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from minemodelingpro import model3d as M3            # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=None):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name,
                         "" if extra is None else "  — %s" % (extra,)))


def metric(rot, aniso):
    a = np.asarray(aniso, float)
    a = np.where(a > 1e-6, a, 1.0)
    rot = np.asarray(rot, float)
    return (rot.T / a) @ rot


def brute(ctr, pts, Wb, upper, k):
    """The honest answer: every composite measured in the block's own metric."""
    out_d = np.full((len(ctr), k), np.inf)
    out_i = np.zeros((len(ctr), k), np.int64)
    for n in range(len(ctr)):
        d = np.linalg.norm(np.einsum('ij,mj->mi', Wb[n], pts - ctr[n]), axis=1)
        o = np.argsort(d)[:k]
        d = d[o]
        ok = d <= upper
        out_d[n, :ok.sum()] = d[ok]
        out_i[n, :ok.sum()] = o[ok]
    return out_d, out_i


rng = np.random.default_rng(5)

# A folded sheet of composites, so the local orientation genuinely varies and
# the anchors do not all agree.
t = np.linspace(0.0, 1.0, 90)
sx = 600.0 * t
sy = 160.0 * np.sin(t * 3.0)
P, G = [], []
for i in range(len(t)):
    dx = sx[min(i + 1, len(t) - 1)] - sx[max(i - 1, 0)]
    dy = sy[min(i + 1, len(t) - 1)] - sy[max(i - 1, 0)]
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    nx, ny = -dy / L, dx / L
    for z in np.linspace(-40.0, -200.0, 6):
        for off in (-1.5, 0.0, 1.5):
            p = [sx[i] + nx * off, sy[i] + ny * off, z]
            P.append(np.asarray(p) + rng.normal(0.0, 0.5, 3))
            G.append(abs(float(rng.normal(3.0, 1.0))))
pts = np.asarray(P)
g = np.asarray(G)
hid = np.arange(len(pts)) // 18            # a hole per station
ANISO = np.array([1.0, 0.90, 0.22])

print("== a frame per neighbourhood")
tm = M3.trend_metrics(pts, g, None, ANISO)
check("the composites yield local metrics", tm is not None)
if tm is not None:
    A, Wl = tm
    check("one metric per anchor, 3x3", Wl.shape == (len(A), 3, 3), Wl.shape)
    check("every metric is symmetric",
          float(np.abs(Wl - np.transpose(Wl, (0, 2, 1))).max()) < 1e-9,
          round(float(np.abs(Wl - np.transpose(Wl, (0, 2, 1))).max()), 12))
    # symmetric and positive definite is what makes tensors averageable
    ev = np.linalg.eigvalsh(Wl)
    check("and positive definite", float(ev.min()) > 0, round(float(ev.min()), 6))
    # the orientations must actually differ, or there is nothing being measured
    nrm = np.linalg.eigh(Wl[0])[1][:, -1]
    angs = []
    for i in range(len(A)):
        v = np.linalg.eigh(Wl[i])[1][:, -1]
        angs.append(np.degrees(np.arccos(min(1.0, abs(float(np.dot(v, nrm)))))))
    check("the anchors do not all point the same way",
          float(np.percentile(angs, 90)) > 10.0,
          round(float(np.percentile(angs, 90)), 1))

check("a cloud too small to orient returns nothing",
      M3.trend_metrics(pts[:9], g[:9], None, ANISO) is None)

print("\n== the bounded per-cell search is exact, not approximate")
# Blocks through the middle of the body, where cells meet and the bounding box
# of one cell's blocks does not contain all of its composites.
bl = pts[rng.choice(len(pts), 400, replace=False)] + rng.normal(0.0, 6.0, (400, 3))
UPPER = 60.0
if tm is not None:
    A, Wl = tm
    own = cKDTree(A).query(bl, k=1)[1]
    Wb = Wl[own]
    bd, bi = brute(bl, pts, Wb, UPPER, 24)
    kd, ki = M3._knn_varying(bl, pts, cKDTree(pts), A, Wl, UPPER, 24)
    # distances first: they are what the pass tiers and the IDW weights read
    both = np.isfinite(bd) & np.isfinite(kd)
    check("it finds the same distances as measuring every composite",
          float(np.abs(bd[both] - kd[both]).max()) < 1e-6,
          round(float(np.abs(bd[both] - kd[both]).max()), 9) if both.any() else "none")
    check("and the same number of neighbours per block",
          bool((np.isfinite(bd).sum(1) == np.isfinite(kd).sum(1)).all()),
          (int(np.isfinite(bd).sum()), int(np.isfinite(kd).sum())))
    # and the same composites, which is the claim the AABB bound rests on
    same = 0
    for n in range(len(bl)):
        a = set(bi[n][np.isfinite(bd[n])].tolist())
        b = set(ki[n][np.isfinite(kd[n])].tolist())
        same += (a == b)
    check("and the same composites, block for block", same == len(bl),
          (same, len(bl)))
    check("nothing beyond the search radius is returned",
          float(np.nanmax(np.where(np.isfinite(kd), kd, 0.0))) <= UPPER + 1e-9,
          round(float(np.nanmax(np.where(np.isfinite(kd), kd, 0.0))), 3))

print("\n== one global frame reproduces the global search")
# The construction has to contain the old behaviour as a special case, or a
# comparison between the two arms is not a comparison.
rotI = np.eye(3)
W = metric(rotI, ANISO)
A1 = pts.mean(0)[None, :]
W1 = np.stack([W])
gd, gi = cKDTree(pts @ W.T).query(bl @ W.T, k=24, distance_upper_bound=UPPER)
vd, vi = M3._knn_varying(bl, pts, cKDTree(pts), A1, W1, UPPER, 24)
fin = np.isfinite(gd) & np.isfinite(vd)
check("the distances match a single global query",
      float(np.abs(gd[fin] - vd[fin]).max()) < 1e-6,
      round(float(np.abs(gd[fin] - vd[fin]).max()), 9) if fin.any() else "none")
check("and so does the neighbour count",
      bool((np.isfinite(gd).sum(1) == np.isfinite(vd).sum(1)).all()),
      (int(np.isfinite(gd).sum()), int(np.isfinite(vd).sum())))

print("\n== a block the gate admits always comes back with a grade")
# `weight` gates on the global frame and estimates from the local one, and the
# two point differently on purpose — so a block can pass the gate and have an
# empty local neighbourhood. Dividing by zero there reports NaN, which reads
# downstream as a hole in the deposit rather than as a missing estimate.
far = pts.mean(0) + np.array([[0.0, 0.0, 0.0]])
if tm is not None:
    A, Wl = tm
    d0, _i0 = M3._knn_varying(far, pts, cKDTree(pts), A, Wl, 1e-3, 24)
    check("an unreachable block returns no neighbours rather than bad ones",
          not np.isfinite(d0).any(), int(np.isfinite(d0).sum()))

# and end to end: every estimated block has a finite grade in every mode
seg = {"mid": pts}
ok_modes = []
for mode in ("", "weight", "on"):
    if mode:
        os.environ["MMP_GRADETREND"] = mode
    else:
        os.environ.pop("MMP_GRADETREND", None)
    try:
        out = M3.estimate_blocks(pts, g, hid, (10.0, 10.0, 10.0), 30.0,
                                 opts={"anisotropy": list(ANISO)})
        gr = out[3]
        ok_modes.append((mode or "off", len(gr), bool(np.isfinite(gr).all())))
    except Exception as e:                                       # noqa: BLE001
        ok_modes.append((mode or "off", -1, "%s: %s" % (type(e).__name__, e)))
os.environ.pop("MMP_GRADETREND", None)
for name, n, fin_ok in ok_modes:
    check("%s: every estimated block has a finite grade" % name,
          fin_ok is True and n > 0, (n, fin_ok))
blocks = {name: n for name, n, _f in ok_modes}
if blocks.get("off", 0) > 0 and "weight" in blocks:
    check("weight mode keeps the deposit's extent",
          blocks["weight"] == blocks["off"],
          (blocks["off"], blocks["weight"]))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
