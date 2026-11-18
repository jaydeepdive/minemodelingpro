#!/usr/bin/env python3
"""A fitted surface is only a vein if it holds its own contacts.

`sheets()` finds the veins a set of contacts lie ON — RANSAC planes, membership
at two of each contact's own measured widths, and an aspect test so the result
is a sheet rather than a cloud with a plane drawn through it. It works. It was
almost never reached.

`build()` tried a single spine, then a single drape, across the WHOLE connected
component first, and took either if it merely beat a PLANE — which a diffuse
cloud's plane makes easy. So the drape claimed whole components and `sheets()`
never ran. Measured on a real 602-hole silver property (Langis, Cobalt): one
"vein" came back holding 470 contacts spread **99.6 m** across its own surface,
p5..p95 = -28.9 to +29.6 m, on veins the core measures at **1.0 m**. It was
drawn as a 17-degree blanket; 1,584 blocks above the cut-off sat outside the
shape carrying **37% of the metal**, the high-grade ones among them.

So the spine and the drape now face the same bar as every facet `sheets`
returns and every merge `_rejoin` accepts: the contacts must sit within
`ACROSS_WIDTHS` of their own width of the surface. Better than a plane is not
the same as being a sheet.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro import veins as V                            # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


def walls(mid, nrm, w, rng):
    t = (w * rng.uniform(0.7, 1.3, len(mid)))[:, None]
    return mid - nrm * t / 2, mid + nrm * t / 2


def diffuse(n=400, scatter=29.0, w=1.0, seed=1):
    """Langis-like: contacts scattered tens of metres, veins 1 m wide."""
    r = np.random.default_rng(seed)
    nrm = np.array([0.0, 0.17, 0.985]); nrm /= np.linalg.norm(nrm)
    a = np.array([1.0, 0, 0]); b = np.cross(nrm, a)
    mid = np.array([a * r.uniform(-170, 170) + b * r.uniform(-120, 120)
                    + nrm * r.normal(0, scatter / 1.65) for _ in range(n)])
    hw, fw = walls(mid, nrm, w, r)
    return mid, hw, fw


def folded(n=160, w=5.0, seed=3):
    """A real folded horizon — one surface, genuinely, and it must stay one."""
    r = np.random.default_rng(seed)
    x = r.uniform(-200, 200, n); y = r.uniform(-150, 150, n)
    mid = np.stack([x, y, 40 * np.sin(x / 120.0) + r.normal(0, 0.6, n)], 1)
    nrm = np.array([0.0, 0.0, 1.0])
    hw, fw = walls(mid, nrm, w, r)
    return mid, hw, fw


def swinging(n=200, w=1.5, seed=5):
    """A steep vein whose strike swings — the case the spine exists for."""
    r = np.random.default_rng(seed)
    t = r.uniform(-1, 1, n)
    x = 180 * t; y = 70 * np.sin(t * 1.6); z = r.uniform(-90, 90, n)
    mid = np.stack([x, y, z], 1)
    # walls across the local strike, near-vertical
    nrm = np.array([0.0, 1.0, 0.05]); nrm /= np.linalg.norm(nrm)
    mid = mid + nrm * r.normal(0, 0.4, n)[:, None]
    hw, fw = walls(mid, nrm, w, r)
    return mid, hw, fw


# ------------------------------------------------------------------- the bar
check("a hold test exists with a floor", hasattr(V, "holds_its_contacts")
      and V.HOLD_FLOOR_M >= 1.0, f"floor {getattr(V, 'HOLD_FLOOR_M', None)}")
check("3 m of spread on a 1 m vein is not one sheet",
      not V.holds_its_contacts(29.0, 1.0))
check("1 m of spread on a 1 m vein is one sheet",
      V.holds_its_contacts(1.0, 1.0))
check("a wide body is allowed proportionally more spread",
      V.holds_its_contacts(12.0, 5.0) and not V.holds_its_contacts(40.0, 5.0))

# -------------------------------------------- the cloud must not become a vein
mid, hw, fw = diffuse()
sp = V._spine_sheet(mid, hw, fw)
dr = V._drape_sheet(mid, hw, fw)
check("a 29 m cloud of 1 m veins is refused by the spine", sp is None)
check("and refused by the drape", dr is None,
      "the drape claimed it because it merely beat a plane")
found, _left = V.sheets(mid, hw, fw, 60.0)
check("so sheets() is reached and finds many veins, not one",
      len(found) > 3, f"{len(found)} found")

# ---------------------------------------------- a real surface is still taken
mid, hw, fw = folded()
sp = V._spine_sheet(mid, hw, fw)
dr = V._drape_sheet(mid, hw, fw) if sp is None else None
got = sp or dr
check("a genuinely folded horizon is still taken as ONE surface", got is not None)
if got:
    meta = got[1]
    P = mid
    r = float(np.percentile(np.abs((P - P.mean(0)) @ meta["normal"]), 90))
    w = float(np.median(np.abs((fw - hw) @ meta["normal"]))) or 1.0
    check("and it holds its own contacts", V.holds_its_contacts(min(r, 8.0), w),
          f"spread {r:.1f} m on a {w:.1f} m bed")

mid, hw, fw = swinging()
sp = V._spine_sheet(mid, hw, fw)
check("a steep vein whose strike swings is still taken by the spine",
      sp is not None, "the case the spine exists for")

# --------------------------------------- the width the test is allowed to use
# A thickness measured PERPENDICULAR TO THE SURFACE BEING TESTED is set partly
# by that surface's own attitude. Lay a flat sheet through steep veins and the
# thickness it reports is their VERTICAL extent, and three times that then
# licenses the sheet that produced it. Measured on the real property: five
# draped surfaces held 377 of 494 contacts at dips of 37.8, 17.3, 8.4, 0.0 and
# 12.8 degrees, reporting 4.6-8.9 m thick against a 1.0 m median intercept.
def steep_swarm(n_v=6, sep=18.0, w=1.0, dip=78.0, seed=2):
    """Steep narrow veins drilled by vertical holes — the Cobalt-camp case."""
    r = np.random.default_rng(seed)
    d = np.radians(dip)
    nrm = np.array([0.0, -np.sin(d), np.cos(d)])
    a = np.array([1.0, 0, 0]); b = np.cross(nrm, a)
    mid, hw, fw = [], [], []
    t = w / np.cos(np.radians(90 - dip))      # a vertical hole reads it longer
    for k in range(n_v):
        off = (k - (n_v - 1) / 2) * sep
        for uu in np.arange(-150, 151, 20):
            for vv in np.arange(-70, 71, 20):
                m = a * uu + b * vv + nrm * off + r.normal(0, 0.4, 3)
                mid.append(m)
                hw.append(m - np.array([0, 0, t / 2]))
                fw.append(m + np.array([0, 0, t / 2]))
    return np.array(mid), np.array(hw), np.array(fw)


mid, hw, fw = steep_swarm()
mw = V.measured_width(hw, fw)
check("the drilling's own width survives a wrong surface", 0.8 <= mw <= 1.4,
      f"{mw:.2f} m from 1.0 m veins")
check("steep narrow veins are NOT flattened into one blanket",
      V._drape_sheet(mid, hw, fw) is None,
      "the drape's own inflated thickness used to license it")
found, _l = V.sheets(mid, hw, fw, 40.0)
check("and sheets() then finds the veins", len(found) > 3, f"{len(found)} found")

mid, hw, fw = folded(w=5.0)
mw = V.measured_width(hw, fw)
check("a genuinely thick bed still measures thick", 4.0 <= mw <= 6.5, f"{mw:.2f} m")
check("so the folded horizon is still taken whole",
      V._drape_sheet(mid, hw, fw) is not None)

# ------------------------------------------- a drape must be a height field
# Measured on the real property, per vein, as the share of plan cells holding
# more than one contact whose vertical spread exceeds twice what that vein's
# own dip and thickness allow: the three blankets scored 0.38, 0.50 and 0.40;
# the one genuine steep vein in the same model scored 0.00. Nothing in the
# test knows anything about the camp — only whether one elevation per plan
# position can describe the data at all.
mid, hw, fw = steep_swarm(n_v=7, sep=16.0, dip=76.0, seed=5)
check("stacked steep veins are not single-valued in plan",
      not V.single_valued(mid, 17.0, 5.0))
check("so the drape refuses them even when the residual looks tight",
      V._drape_sheet(mid, hw, fw) is None)

mid, hw, fw = folded(n=300, w=5.0)
check("a real folded horizon IS single-valued", V.single_valued(mid, 12.0, 5.0))
check("and the drape still takes it", V._drape_sheet(mid, hw, fw) is not None)

r = np.random.default_rng(11)
x = r.uniform(-200, 200, 300); y = r.uniform(-150, 150, 300)
sheet = np.stack([x, y, x * np.tan(np.radians(35)) + r.normal(0, 0.5, 300)], 1)
check("one steeply dipping sheet is single-valued at its own dip",
      V.single_valued(sheet, 35.0, 3.0), "dip must be allowed for, not charged")
check("too few contacts to judge is not evidence of stacking",
      V.single_valued(sheet[:6], 10.0, 2.0))

print(f"\n{len(OK)} passed, {len(BAD)} failed")
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
