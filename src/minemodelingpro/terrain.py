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


_ALIASES = {"newfoundland": "newfoundland and labrador", "newfoundland & labrador": "newfoundland and labrador",
            "labrador": "newfoundland and labrador", "drc": "democratic republic of the congo",
            "congo": "democratic republic of the congo", "cote d'ivoire": "ivory coast", "usa": "united states of america",
            "nwt": "northwest territories", "bc": "british columbia", "quebec": "quebec", "yukon territory": "yukon",
            "tanzania": "united republic of tanzania", "serbia": "republic of serbia"}


def _fold(s):
    import unicodedata
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()


def region_geom(name):
    """Shapely geometry for a province/state/country name (accent/alias tolerant)."""
    if not name:
        return None
    region_of(0, 0)                      # ensure polygons are loaded
    key = _ALIASES.get(_fold(name), _fold(name))
    geoms = [g for n, c, g in _REGIONS if _fold(n) == key]
    if not geoms:
        geoms = [g for n, c, g in _REGIONS if _fold(c) == key]
    if not geoms:
        return None
    from shapely.ops import unary_union
    return unary_union(geoms)


def from_collars(xs, ys, zs, n_max=200, pad_frac=0.15, k=12):
    """A ground surface interpolated from the collars themselves.

    A drill collar is a surveyed point ON the topography — the one thing every
    drill database has, whether or not its grid can be placed on a map. Where no
    DEM is reachable (a local mine grid, an ambiguous UTM zone) the collars still
    describe the ground, and without a surface every intercept is drawn against a
    flat datum, which misplaces the whole deposit in depth.

    Inverse-square distance over the `k` nearest collars, on a lattice about half
    the collar spacing, with a small smoothing floor so three holes on one pad do
    not spike the ground between them. Local rather than global on purpose: a
    weighted mean of every collar reverts to the property's average elevation
    wherever the drilling thins, which flattens exactly the relief this exists to
    show. Returns the shape `grid` returns plus from="collars", or None if there
    is nothing to fit.
    """
    import numpy as _np
    x = _np.asarray(xs, float); y = _np.asarray(ys, float); z = _np.asarray(zs, float)
    ok = _np.isfinite(x) & _np.isfinite(y) & _np.isfinite(z)
    x, y, z = x[ok], y[ok], z[ok]
    # Four collars spread over a township describe no surface — measured at
    # 4,500 m a cell, the interpolation is a guess between four points. Eight is
    # where a drill pattern starts to have a shape.
    if len(x) < 8 or float(z.max() - z.min()) <= 0.0:
        return None                      # too few collars, or a flat datum
    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(_np.c_[x, y])
    except Exception:
        tree = None
    # the collar spacing sets both the lattice and the smoothing: closer than
    # half a spacing and the surface chases individual holes, much coarser and
    # real relief is averaged away
    if tree is not None and len(x) > 1:
        d, _i = tree.query(_np.c_[x, y], k=2)
        near = float(_np.median(d[:, 1]))
    else:
        near = 0.0
    w = max(float(x.max() - x.min()), float(y.max() - y.min()), 100.0)
    if near <= 0.0:
        near = w / 20.0
    # A collar elevation can be wrong, and one wrong one bends the ground around
    # it: Goldboro has two collars 1.6 m apart whose stated elevations differ by
    # 75 m, and South Mountain two 21 m apart differing by 431 m. Neither is a
    # hillside. Judge each collar against its neighbours and drop the ones no
    # plausible surface can pass through, keeping the count so the build can say
    # so rather than quietly smoothing over a bad record.
    dropped = 0
    if tree is not None and len(x) >= 12:
        kk0 = int(min(9, len(x)))
        dd, ii = tree.query(_np.c_[x, y], k=kk0)
        dn, iN = dd[:, 1:], ii[:, 1:]                       # exclude self
        wn = 1.0 / (dn * dn + (0.3 * near) ** 2)
        loo = (wn * z[iN]).sum(1) / wn.sum(1)               # leave-one-out fit
        r = _np.abs(z - loo)
        # Judge the miss against the ground's own gradient over the baseline the
        # neighbours give it, not against a fixed number of metres: a fold on a
        # 200 m pattern is tens of metres of real relief, while 75 m between two
        # collars 1.6 m apart is a 47:1 slope and a bad record. A flat allowance
        # cannot tell those apart, and threw away real hillsides.
        base = dn.mean(1)
        lim = _np.maximum(_np.maximum(10.0, 2.0 * base), 6.0 * float(_np.median(r)))
        keep = r <= lim
        if keep.sum() >= max(3, int(0.5 * len(x))) and (~keep).any():
            dropped = int((~keep).sum())
            x, y, z = x[keep], y[keep], z[keep]
            try:
                tree = cKDTree(_np.c_[x, y])
            except Exception:
                tree = None
    # after cleaning, how well do the collars agree with each other at all? A
    # project whose holes come from several reports on different datums can have
    # elevations that no surface fits, and inventing a kilometre of relief out of
    # that disagreement is worse than the flat datum it replaces.
    misfit = 0.0
    if tree is not None and len(x) >= 12:
        kk0 = int(min(9, len(x)))
        dd, ii = tree.query(_np.c_[x, y], k=kk0)
        dn, iN = dd[:, 1:], ii[:, 1:]
        wn = 1.0 / (dn * dn + (0.3 * near) ** 2)
        misfit = float(_np.median(_np.abs(z - (wn * z[iN]).sum(1) / wn.sum(1))))
    pad = max(near, pad_frac * w)
    x0, y0 = float(x.min()) - pad, float(y.min()) - pad
    x1, y1 = float(x.max()) + pad, float(y.max()) + pad
    step = max(near / 2.0, max(x1 - x0, y1 - y0) / (n_max - 1))
    nx = min(n_max, int((x1 - x0) / step) + 2)
    ny = min(n_max, int((y1 - y0) / step) + 2)
    gx = x0 + _np.arange(nx) * step
    gy = y0 + _np.arange(ny) * step
    X, Y = _np.meshgrid(gx, gy)
    # smooth over about half a cell: at this resolution a node stands for the
    # average ground across it, not for whichever collar fell nearest the node
    soft = (0.5 * max(near, step)) ** 2
    kk = int(min(k, len(x)))
    flatX, flatY = X.ravel(), Y.ravel()
    res = _np.empty(flatX.size, float)
    CH = 20_000
    for s_ in range(0, flatX.size, CH):
        px, py = flatX[s_:s_ + CH], flatY[s_:s_ + CH]
        if tree is not None:
            dd, ii = tree.query(_np.c_[px, py], k=kk)
            if dd.ndim == 1:
                dd = dd[:, None]; ii = ii[:, None]
            wgt = 1.0 / (dd * dd + soft)
            res[s_:s_ + CH] = (wgt * z[ii]).sum(1) / wgt.sum(1)
        else:
            dx = px[:, None] - x[None, :]
            dy = py[:, None] - y[None, :]
            wgt = 1.0 / (dx * dx + dy * dy + soft)
            res[s_:s_ + CH] = (wgt * z[None, :]).sum(1) / wgt.sum(1)
    out = res.reshape(ny, nx).astype(_np.float32)
    return {"nx": nx, "ny": ny, "x0": x0, "y0": y0, "dx": step, "dy": step,
            "z": out, "from": "collars", "n": int(len(x)), "dropped": dropped,
            "misfit_m": round(misfit, 2),
            "relief_m": round(float(z.max() - z.min()), 1)}
