"""Real topography for deposit models.

Samples the AWS Terrain Tiles DEM (Mapzen "terrarium" encoding, public, keyless,
global, ~30 m source resolution in North America and ~30-90 m elsewhere) over a
model's footprint on a regular metre grid, bilinearly interpolated, at the tile
zoom that matches the grid spacing. Returned elevations are ABSOLUTE metres above
sea level so drill collars (which carry masl elevations in reports and releases)
sit on the ground surface and holes can be seen piercing the terrain.

Tiles are cached on disk (``data/cache/dem``) so daily CI rebuilds don't refetch.
"""
import io
import os
import math
import urllib.request

import numpy as np

_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.environ.get("MMP_DEM_CACHE", os.path.join(_ROOT, "data", "cache", "dem"))
_MEM = {}


def _tile(z, x, y):
    k = (z, x, y)
    if k in _MEM:
        return _MEM[k]
    arr = None
    fn = os.path.join(CACHE, f"{z}_{x}_{y}.npy")
    if os.path.exists(fn):
        try:
            arr = np.load(fn)
        except Exception:
            arr = None
    if arr is None:
        try:
            from PIL import Image
            req = urllib.request.Request(_URL.format(z=z, x=x, y=y), headers={"User-Agent": "minemodelingpro/1.0"})
            b = urllib.request.urlopen(req, timeout=25).read()
            im = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32)
            arr = (im[..., 0] * 256.0 + im[..., 1] + im[..., 2] / 256.0 - 32768.0).astype(np.float32)
            os.makedirs(CACHE, exist_ok=True)
            np.save(fn, arr)
        except Exception:
            arr = None
    _MEM[k] = arr
    return arr


def _px(lat, lon, z):
    n = 2 ** z
    fx = (lon + 180.0) / 360.0 * n
    lr = math.radians(lat)
    fy = (1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n
    return fx * 256.0, fy * 256.0


def elevations(lats, lons, z):
    """Bilinear DEM elevation (masl) at arrays of lat/lon; NaN where unavailable."""
    lats = np.asarray(lats, float)
    lons = np.asarray(lons, float)
    out = np.full(lats.shape, np.nan, dtype=float)
    n = 2 ** z
    fx = (lons + 180.0) / 360.0 * n * 256.0
    lr = np.radians(lats)
    fy = (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * n * 256.0
    gx0 = np.floor(fx - 0.5).astype(int)
    gy0 = np.floor(fy - 0.5).astype(int)
    tx = fx - 0.5 - gx0
    ty = fy - 0.5 - gy0

    def val(gx, gy):
        v = np.full(gx.shape, np.nan)
        tiles_x = gx // 256
        tiles_y = gy // 256
        for (a, b) in set(zip(tiles_x.ravel().tolist(), tiles_y.ravel().tolist())):
            t = _tile(z, a % n, b)
            if t is None:
                continue
            m = (tiles_x == a) & (tiles_y == b)
            v[m] = t[gy[m] - b * 256, gx[m] - a * 256]
        return v
    v00 = val(gx0, gy0); v10 = val(gx0 + 1, gy0)
    v01 = val(gx0, gy0 + 1); v11 = val(gx0 + 1, gy0 + 1)
    out = (v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty)
    return out


def zoom_for(spacing_m, lat):
    """Tile zoom whose pixel size is ~half the grid spacing (capped at 15)."""
    for z in range(15, 7, -1):
        px = 156543.03 * math.cos(math.radians(lat)) / (2 ** z)
        if px >= spacing_m * 0.45:
            return z
    return 8


def grid(lat0, lon0, x0, y0, x1, y1, n_max=180):
    """DEM on a regular grid over local-metre box [x0,x1]×[y0,y1] (x east, y north,
    relative to lat0/lon0). Returns dict {nx, ny, x0, y0, dx, dy, z(list)} or None."""
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    step = max(w, h) / (n_max - 1)
    step = max(step, 8.0)
    nx = int(round(w / step)) + 1
    ny = int(round(h / step)) + 1
    xs = x0 + np.arange(nx) * step
    ys = y0 + np.arange(ny) * step
    X, Y = np.meshgrid(xs, ys)
    mE = 111320.0 * math.cos(math.radians(lat0))
    mN = 110540.0
    lat = lat0 + Y / mN
    lon = lon0 + X / mE
    z = zoom_for(step, lat0)
    try:
        el = elevations(lat, lon, z)
    except Exception:
        return None
    ok = np.isfinite(el)
    if ok.mean() < 0.6:
        return None
    if not ok.all():
        el[~ok] = np.nanmedian(el)
    try:                                   # knock down DEM stair-steps / speckle
        from scipy.ndimage import gaussian_filter
        el = gaussian_filter(el, sigma=0.7, mode="nearest")
    except Exception:
        pass
    return {"nx": nx, "ny": ny, "x0": round(float(x0), 1), "y0": round(float(y0), 1),
            "dx": round(float(step), 2), "dy": round(float(step), 2),
            "z": el.astype(np.float32), "zoom": z, "source": "AWS Terrain Tiles (SRTM/NED/GMTED)",
            "relief": round(float(np.nanmax(el) - np.nanmin(el)), 1)}


# ------------------------------------------------------------ region lookup
_REGIONS = None


def region_of(lat, lon):
    """Province/state (Canada, US, Australia, ...) or country for a lat/lon, from
    simplified Natural Earth polygons committed at data/geo/admin_regions.json."""
    global _REGIONS
    try:
        from shapely.geometry import shape, Point
    except Exception:
        return None
    if _REGIONS is None:
        fn = os.path.join(_ROOT, "data", "geo", "admin_regions.json")
        try:
            _REGIONS = [(r["n"], r["c"], shape(r["g"])) for r in __import__("json").load(open(fn))]
        except Exception:
            _REGIONS = []
    pt = Point(lon, lat)
    best, bd = None, 1e9
    for n, c, g in _REGIONS:
        if g.contains(pt):
            return _label(n, c)
    for n, c, g in _REGIONS:              # coastal / simplified-edge fallback: nearest within ~30 km
        d = g.distance(pt)
        if d < bd:
            best, bd = (n, c), d
    return _label(*best) if best and bd < 0.3 else None


def _label(n, c):
    if c == "United States of America":
        c = "USA"
    if n and c in ("Canada", "USA", "Australia"):
        return n
    return c
