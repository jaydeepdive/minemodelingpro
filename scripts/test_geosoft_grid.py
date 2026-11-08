#!/usr/bin/env python3
"""The Geosoft .grd reader, and the physical invariants of what it produces.

Quebec publishes its magnetic compilation as a rendered RGB GeoTIFF and as a
Geosoft .grd. Only the .grd holds values, GDAL cannot read it, and a reader
written against an undocumented binary layout is exactly the kind of thing that
produces plausible nonsense. So the checks here are the ones that would catch a
misread rather than the ones that confirm it parsed:

  * the header identity 512 + ES*NE*NV == file size, which pins ES, NE and NV
    together — get any one wrong and it breaks;
  * georeferencing against the ministry's own rendered GeoTIFF bounds;
  * two physical invariants that a byte-order or type error would violate —
    an analytic signal is a magnitude and is never negative, and a tilt
    derivative is an arctangent and cannot leave +-pi/2;
  * and a geological one: Lac Tio, the largest ilmenite deposit in the world,
    must be the strongest magnetic anomaly among a set of Quebec deposits. A
    grid read with the rows flipped or the axes swapped puts that signal
    somewhere else.

The synthetic cases run anywhere. The ones over the real province skip cleanly
when the COGs are not in this container.
"""
import os
import struct
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro.geosoft_grid import GeosoftGrid, DUMMY   # noqa: E402

COG = os.environ.get("MMP_QC_COG", "/home/claude/qcgeo/cog")
OK, BAD, SKIP = [], [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


def write_grd(path, a, de=75.0, dv=75.0, x0=0.0, y0=0.0, rot=0.0,
              es=4, sf=2, zbase=0.0, zmult=1.0, dtype="<f4", trunc=0):
    """A Geosoft grid, as the format says to write one. `a` is south-to-north."""
    nv, ne = a.shape
    h = struct.pack("<5i", es, sf, ne, nv, 1) + \
        struct.pack("<7d", de, dv, x0, y0, rot, zbase, zmult)
    h = h + b"\x00" * (512 - len(h))
    with open(path, "wb") as f:
        f.write(h)
        f.write(a.astype(dtype).tobytes())
        if trunc:
            pass
    if trunc:
        with open(path, "r+b") as f:
            f.truncate(os.path.getsize(path) - trunc)
    return path


tmp = tempfile.mkdtemp(prefix="grdtest-")

# ------------------------------------------------------------------- the basics
a = np.arange(12, dtype="f4").reshape(3, 4)        # row 0 is SOUTH
p = write_grd(os.path.join(tmp, "a.grd"), a, x0=1000.0, y0=2000.0)
g = GeosoftGrid(p)
check("shape is (vectors, elements)", g.shape == (3, 4), str(g.shape))
check("spacing read", (g.de, g.dv) == (75.0, 75.0))
check("dtype inferred from ES/SF", g.dtype == "f4", g.dtype)
check("stored rows come back as stored",
      np.array_equal(g.rows(0, 3), a), str(g.rows(0, 3)))
check("north_up reverses them (row 0 becomes the TOP)",
      np.array_equal(g.north_up(0, 3), a[::-1]))
check("north_up of one row takes the northernmost",
      np.array_equal(g.north_up(0, 1), a[2:3]))
check("north_up of the last row takes the southernmost",
      np.array_equal(g.north_up(2, 1), a[0:1]))
# bounds: X0/Y0 are cell CENTRES, bounds are EDGES
check("bounds put X0/Y0 at a cell centre, not a corner",
      g.bounds == (1000 - 37.5, 2000 - 37.5, 1000 + 4 * 75 - 37.5, 2000 + 3 * 75 - 37.5),
      str(g.bounds))
t = g.transform()
check("transform is north-up (negative y step)", t.e < 0, str(t.e))
check("transform origin is the top-left edge",
      (t.c, t.f) == (1000 - 37.5, 2000 + 3 * 75 - 37.5), f"{t.c},{t.f}")

# ------------------------------------------------------------------- the guards
def refused(fn, want):
    """The call must fail, and fail for the stated reason."""
    try:
        fn()
        return False
    except Exception as e:                                        # noqa: BLE001
        return want in str(e).lower() or want in type(e).__name__.lower()


check("a truncated file is refused (the header identity fails)",
      refused(lambda: GeosoftGrid(write_grd(os.path.join(tmp, "t.grd"), a, trunc=8)), "header"))
check("a rotated grid is refused rather than silently mis-georeferenced",
      refused(lambda: GeosoftGrid(write_grd(os.path.join(tmp, "r.grd"), a, rot=12.0)), "rotat"))
check("nonsense dimensions are refused",
      refused(lambda: GeosoftGrid(write_grd(os.path.join(tmp, "z.grd"),
                                            np.zeros((1, 1), "f4"), es=3)), "geosoft"))
check("a file too short for a header is refused",
      refused(lambda: (open(os.path.join(tmp, "s.grd"), "wb").write(b"x" * 20),
                       GeosoftGrid(os.path.join(tmp, "s.grd")))[1], "header"))

# ------------------------------------------------------------------- the dummies
d = np.array([[1.0, -1e32, 3.0], [1e32, 5.0, 6.0]], dtype="f4")
g = GeosoftGrid(write_grd(os.path.join(tmp, "d.grd"), d))
r = g.rows(0, 2)
check("Geosoft dummies become NaN", int(np.isnan(r).sum()) == 2, str(r))
check("real values survive beside dummies",
      np.array_equal(r[np.isfinite(r)], np.array([1., 3., 5., 6.], "f4")))

# -------------------------------------------------------------- ZBASE and ZMULT
s = np.array([[1.0, 2.0]], dtype="f4")
g = GeosoftGrid(write_grd(os.path.join(tmp, "m.grd"), s, zbase=10.0, zmult=2.0))
check("value = ZBASE + stored * ZMULT is applied",
      np.array_equal(g.rows(0, 1), np.array([[12.0, 14.0]], "f4")), str(g.rows(0, 1)))

# ----------------------------------------------------------- a read past the end
g = GeosoftGrid(write_grd(os.path.join(tmp, "b.grd"), a))
check("a read past the last row returns what exists, not an error",
      g.rows(2, 99).shape == (1, 4), str(g.rows(2, 99).shape))
check("a read entirely past the end is empty", g.rows(9, 2).shape == (0, 4))

# =================================================== the real province, if present
prods = {n: os.path.join(COG, f"qc_mag_{n}.tif")
         for n in ("residual", "dv1", "analytic_signal", "tilt")}
have = all(os.path.exists(p) for p in prods.values())
if not have:
    SKIP.append("the province-wide checks (COGs not in this container)")
    print(f"\n  skip  province-wide checks — no COGs under {COG}")
else:
    import rasterio
    from rasterio.warp import transform as warp

    def window(path, lat, lon, half=20):
        with rasterio.open(path) as ds:
            x, y = warp("EPSG:4326", ds.crs, [lon], [lat])
            r, c = ds.index(x[0], y[0])
            w = rasterio.windows.Window(c - half, r - half, 2 * half + 1, 2 * half + 1)
            v = ds.read(1, window=w, boundless=True, fill_value=np.nan)
        v = np.where(v < -1e30, np.nan, v)
        return v[np.isfinite(v)]

    with rasterio.open(prods["residual"]) as ds:
        check("the province grid is 22760 x 28253 at 75 m",
              (ds.width, ds.height) == (22760, 28253) and round(ds.res[0]) == 75,
              f"{ds.width}x{ds.height}@{ds.res}")
        check("it carries the Quebec Lambert CRS (EPSG:32198)",
              ds.crs.to_epsg() == 32198, str(ds.crs))
        check("it is tiled, so a window is not a whole-file read",
              bool(ds.profile.get("tiled")) and ds.block_shapes[0][0] <= 512,
              str(ds.block_shapes[0]))
        b = [round(v, 1) for v in ds.bounds]
        check("bounds reproduce the ministry's own rendered GeoTIFF",
              b == [-877012.5, 13462.5, 829987.5, 2132437.5], str(b))

    # physical invariants: a byte-order or type error breaks these
    aset = window(prods["analytic_signal"], 49.60, -78.70, half=60)
    check("analytic signal is never negative (it is a magnitude)",
          aset.size > 0 and aset.min() >= 0.0,
          f"min {aset.min() if aset.size else 'n/a'}")
    tset = window(prods["tilt"], 49.60, -78.70, half=60)
    check("tilt derivative stays inside +-pi/2 (it is an arctangent)",
          tset.size > 0 and np.abs(tset).max() <= np.pi / 2 + 1e-3,
          f"max |tilt| {np.abs(tset).max() if tset.size else 'n/a'}")

    # geological: the largest ilmenite deposit on earth is the strongest anomaly
    sites = {"Lac Tio": (51.330, -63.400), "Casa Berardi": (49.600, -78.700),
             "Eleonore": (52.700, -76.500), "Renard": (52.800, -72.650)}
    relief = {}
    for nm, (la, lo) in sites.items():
        v = window(prods["residual"], la, lo)
        relief[nm] = float(v.max() - v.min()) if v.size else 0.0
    top = max(relief, key=relief.get)
    check("Lac Tio is the strongest magnetic anomaly of the four",
          top == "Lac Tio", f"strongest was {top}: {relief}")
    check("Lac Tio's relief is an order of magnitude above the gold camps",
          relief["Lac Tio"] > 10 * relief["Casa Berardi"],
          f"{relief['Lac Tio']:.0f} vs {relief['Casa Berardi']:.0f} nT")
    print(f"\n  magnetic relief over a 3 km window: "
          + ", ".join(f"{k} {v:.0f} nT" for k, v in sorted(relief.items(), key=lambda x: -x[1])))

print(f"\n{len(OK)} passed, {len(BAD)} failed" + (f", {len(SKIP)} skipped" if SKIP else ""))
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
