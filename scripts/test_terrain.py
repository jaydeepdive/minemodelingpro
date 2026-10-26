#!/usr/bin/env python3
"""The ground surface interpolated from collar elevations.

A collar is a surveyed point ON the topography, and it is the only elevation
data a project in a local mine grid has. Without a surface every intercept is
drawn against a flat datum, which misplaces the whole deposit in depth — so this
checks that the surface passes through the collars, that it declines to invent
one where there is nothing to fit, and that a wrong collar elevation does not
bend the ground around it.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from minemodelingpro import terrain

P = F = 0
def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print(f"  ok   {name}" + (f"  ({extra})" if extra else ""))
    else:
        F += 1; print(f"  FAIL {name}" + (f"  ({extra})" if extra else ""))

def sample(t, x, y):
    """Bilinear, so a residual measures the surface and not the lattice."""
    z = t["z"]
    fx = np.clip((np.asarray(x, float) - t["x0"]) / t["dx"], 0, t["nx"] - 1)
    fy = np.clip((np.asarray(y, float) - t["y0"]) / t["dy"], 0, t["ny"] - 1)
    i0 = fx.astype(int); j0 = fy.astype(int)
    i1 = np.minimum(i0 + 1, t["nx"] - 1); j1 = np.minimum(j0 + 1, t["ny"] - 1)
    tx = fx - i0; ty = fy - j0
    return (z[j0, i0] * (1 - tx) * (1 - ty) + z[j0, i1] * tx * (1 - ty)
            + z[j1, i0] * (1 - tx) * ty + z[j1, i1] * tx * ty)

print("\nthe surface follows the ground the collars describe")
rng = np.random.default_rng(7)
n = 80
x = rng.uniform(0, 800, n); y = rng.uniform(0, 600, n)
true = lambda a, b: 300 + 0.08 * a - 0.05 * b + 30 * np.sin(a / 220.0)
z = true(x, y)
t = terrain.from_collars(x, y, z)
ok("a tilted, folded hillside is fitted", t is not None)
r = np.abs(sample(t, x, y) - z)
ok("it passes through its own collars", float(np.median(r)) < 2.0,
   f"median {np.median(r):.2f} m, worst {r.max():.2f} m")
ok("it says where it came from", t.get("from") == "collars")
ok("it reports how many collars it used", t.get("n") == n, str(t.get("n")))
ok("relief is within the collars' own span",
   t["z"].max() - t["z"].min() <= (z.max() - z.min()) + 1e-6,
   f"{t['relief_m']} m of {z.max()-z.min():.0f} m")
gx = t["x0"] + np.arange(t["nx"]) * t["dx"]
gy = t["y0"] + np.arange(t["ny"]) * t["dy"]
ok("the lattice covers the drilling", gx[0] <= x.min() and gx[-1] >= x.max()
   and gy[0] <= y.min() and gy[-1] >= y.max())
ok("the payload is a browser's worth", t["nx"] * t["ny"] * 4 < 400_000,
   f"{t['nx']}x{t['ny']} = {t['nx']*t['ny']*4/1024:.0f} KB")

print("\na wrong collar elevation does not bend the ground")
x2 = np.r_[x, 400.0]; y2 = np.r_[y, 300.0]
z2 = np.r_[z, true(400.0, 300.0) + 400.0]      # one collar 400 m above the hill
t2 = terrain.from_collars(x2, y2, z2)
ok("the impossible collar is dropped", t2.get("dropped", 0) >= 1, str(t2.get("dropped")))
ok("the surface near it is unmoved",
   abs(float(sample(t2, [400.0], [300.0])[0]) - true(400.0, 300.0)) < 25.0,
   f"{float(sample(t2,[400.0],[300.0])[0]) - true(400.0,300.0):+.1f} m")
ok("the good collars are still honoured",
   float(np.median(np.abs(sample(t2, x, y) - z))) < 2.5,
   f"median {np.median(np.abs(sample(t2, x, y) - z)):.2f} m")
# and it must not throw away a genuine population
t3 = terrain.from_collars(x, y, z + rng.normal(0, 1.5, n))
ok("ordinary survey scatter is not called an outlier",
   t3.get("dropped", 0) <= n // 20, f"{t3.get('dropped')} of {n}")

print("\nit declines where there is nothing to fit")
ok("a flat datum gets no surface",
   terrain.from_collars(x, y, np.zeros(n)) is None)
ok("four collars get no surface",
   terrain.from_collars(x[:4], y[:4], z[:4]) is None)
ok("one collar gets no surface",
   terrain.from_collars(x[:1], y[:1], z[:1]) is None)
ok("non-finite collars are ignored, not fatal",
   terrain.from_collars(np.r_[x, np.nan], np.r_[y, 0.0], np.r_[z, 5.0]) is not None)

print("\nthe same shape the DEM path returns")
need = {"nx", "ny", "x0", "y0", "dx", "dy", "z"}
ok("every key the viewer reads is present", need <= set(t), str(sorted(need - set(t))))
ok("z is a float32 grid of ny by nx",
   t["z"].dtype == np.float32 and t["z"].shape == (t["ny"], t["nx"]))

print("\na single drill fence still gets a surface")
xf = np.linspace(0, 500, 20); yf = np.full(20, 100.0)
tf = terrain.from_collars(xf, yf, 200 + 0.1 * xf)
ok("collars on one line are fitted", tf is not None)
if tf is not None:
    rf = np.abs(sample(tf, xf, yf) - (200 + 0.1 * xf))
    ok("and the fence is honoured", float(np.median(rf)) < 3.0,
       f"median {np.median(rf):.2f} m")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
