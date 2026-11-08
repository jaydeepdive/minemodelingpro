#!/usr/bin/env python3
"""Geosoft .grd -> cloud-optimised GeoTIFF.

Why a COG and not the .grd: a project is a few kilometres across, the grid is a
province, and the modeller has a gigabyte. A COG is tiled with internal
overviews, so the window over one deposit is a couple of range reads whether the
file sits on disk or in object storage — the province is never loaded.

Written in row blocks because 643 million float32 cells is 2.57 GB and this runs
beside a build that has been killed by the kernel for less.
"""
import os
import sys
import time

import numpy as np
import rasterio
import rasterio.shutil
from rasterio.enums import Resampling

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from minemodelingpro.geosoft_grid import GeosoftGrid, DUMMY   # noqa: E402

BLOCK = 512


def convert(src, dst, crs="EPSG:32198", rows=2048):
    g = GeosoftGrid(src)
    print(f"  {g}  -> {os.path.basename(dst)}", flush=True)
    prof = {
        "driver": "GTiff", "width": g.ne, "height": g.nv, "count": 1,
        "dtype": "float32", "crs": crs, "transform": g.transform(),
        "nodata": DUMMY, "tiled": True,
        "blockxsize": BLOCK, "blockysize": BLOCK,
        "compress": "DEFLATE", "predictor": 3, "zlevel": 6,
        "BIGTIFF": "YES", "num_threads": "ALL_CPUS",
    }
    tmp = dst + ".tmp.tif"
    t0 = time.time()
    seen = tot = 0
    lo, hi = np.inf, -np.inf
    with rasterio.open(tmp, "w", **prof) as out:
        for i0 in range(0, g.nv, rows):
            n = min(rows, g.nv - i0)
            a = g.north_up(i0, n)
            good = np.isfinite(a)
            seen += int(good.sum()); tot += a.size
            if good.any():
                lo = min(lo, float(a[good].min())); hi = max(hi, float(a[good].max()))
            out.write(np.where(good, a, DUMMY).astype("float32"), 1,
                      window=rasterio.windows.Window(0, i0, g.ne, n))
            if (i0 // rows) % 4 == 0:
                pc = (i0 + n) / g.nv * 100
                print(f"    {pc:5.1f}%  {time.time()-t0:5.0f}s", flush=True)
        out.build_overviews([2, 4, 8, 16, 32, 64], Resampling.average)
    print(f"    overviews built, {time.time()-t0:.0f}s; now laying out as COG", flush=True)
    rasterio.shutil.copy(tmp, dst, driver="COG", compress="DEFLATE",
                         predictor="YES", blocksize=BLOCK,
                         overview_resampling="average", BIGTIFF="YES",
                         num_threads="ALL_CPUS")
    os.remove(tmp)
    print(f"  done in {(time.time()-t0)/60:.1f} min  "
          f"{os.path.getsize(dst)/2**20:.0f} MB  "
          f"({os.path.getsize(src)/os.path.getsize(dst):.1f}x smaller than the .grd)\n"
          f"  coverage {seen/tot*100:.1f}% of the province, values {lo:.1f} to {hi:.1f}", flush=True)
    return dst


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
