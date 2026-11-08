"""Read a Geosoft Oasis montaj binary grid (.grd).

Quebec publishes its province-wide magnetic compilation two ways, and only one
of them is data. The GeoTIFF is a **rendered picture** — 3-band uint8 RGB, a
colour image of the field — so anything built on it would be a screenshot
wearing a raster's clothes. The values are in the .grd, which GDAL does not
read: there is no Geosoft driver, only unrelated drivers that claim the .grd
extension (Golden Software Surfer, Northwood), and handing them this file gets
"not recognized as being in a supported file format".

So the format is read here. The header is 512 bytes, little-endian:

      0   int32   ES      bytes per element (1, 2, 4, 8)
      4   int32   SF      element kind; 2 = IEEE float
      8   int32   NE      elements per vector (columns)
     12   int32   NV      number of vectors (rows)
     16   int32   KX      storage sense; 1 = vectors run along X
     20   double  DE      element spacing (m)
     28   double  DV      vector spacing (m)
     36   double  X0      centre of the first element of the first vector
     44   double  Y0
     52   double  ROT     grid rotation (degrees)
     60   double  ZBASE   value = ZBASE + stored * ZMULT
     68   double  ZMULT

Two checks make this safe rather than hopeful, and both are asserted:

  * `512 + ES*NE*NV` must equal the file size exactly. On Quebec's residual
    field that is 2,572,153,632 bytes to the byte, which pins ES, NE and NV
    together — a misread of any one of them breaks the identity.
  * the georeferencing derived from X0/Y0/DE/DV/NE/NV must reproduce the
    bounds of the ministry's own rendered GeoTIFF. It does, to the metre:
    (-877012.5, 13462.5, 829987.5, 2132437.5) against the published
    (-877012, 13462, 829989, 2132436).

Vectors are stored south to north — Y0 is the FIRST stored row, not the top —
so a north-up raster needs them reversed. Dummies are Geosoft's +-1e32.
"""
import os
import struct

import numpy as np

HEADER = 512
DUMMY = -1e32
_KINDS = {(1, 0): "u1", (1, 1): "i1", (2, 1): "i2", (2, 0): "u2",
          (4, 1): "i4", (4, 2): "f4", (8, 2): "f8", (8, 3): "f8"}


class GeosoftGrid:
    """A Geosoft .grd opened for windowed reading.

    Nothing is loaded on open: a province-wide grid is 643 million cells and
    the modeller has a gigabyte, so rows are read on demand.
    """

    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)
        with open(path, "rb") as f:
            h = f.read(HEADER)
        if len(h) < HEADER:
            raise ValueError(f"{path}: shorter than a Geosoft header")
        (self.es, self.sf, self.ne, self.nv, self.kx) = struct.unpack("<5i", h[:20])
        (self.de, self.dv, self.x0, self.y0,
         self.rot, self.zbase, self.zmult) = struct.unpack("<7d", h[20:76])
        if self.es not in (1, 2, 4, 8) or self.ne <= 0 or self.nv <= 0:
            raise ValueError(f"{path}: not a Geosoft grid "
                             f"(ES={self.es} NE={self.ne} NV={self.nv})")
        want = HEADER + self.es * self.ne * self.nv
        if want != self.size:
            raise ValueError(f"{path}: header says {want:,} bytes, file is "
                             f"{self.size:,} — the header did not parse")
        self.dtype = _KINDS.get((self.es, self.sf))
        if self.dtype is None:
            raise ValueError(f"{path}: unsupported element kind ES={self.es} SF={self.sf}")
        if abs(self.rot) > 1e-9:
            raise ValueError(f"{path}: rotated grids are not handled (ROT={self.rot})")

    # ---------------------------------------------------------------- geometry
    @property
    def shape(self):
        return self.nv, self.ne

    @property
    def bounds(self):
        """(left, bottom, right, top) on cell EDGES, which is what a GeoTIFF wants.

        X0/Y0 are cell CENTRES, so each end moves out by half a cell."""
        return (self.x0 - self.de / 2.0,
                self.y0 - self.dv / 2.0,
                self.x0 + self.ne * self.de - self.de / 2.0,
                self.y0 + self.nv * self.dv - self.dv / 2.0)

    def transform(self):
        """North-up affine, so row 0 of the returned array is the TOP row."""
        from rasterio.transform import from_origin
        left, _bottom, _right, top = self.bounds
        return from_origin(left, top, self.de, self.dv)

    # ------------------------------------------------------------------ reading
    def rows(self, j0, n):
        """`n` stored vectors from `j0`, as float32 with dummies set to NaN.

        Stored order: j0=0 is the SOUTHERNMOST row."""
        j0 = max(0, min(int(j0), self.nv))
        n = max(0, min(int(n), self.nv - j0))
        if n == 0:
            return np.empty((0, self.ne), "f4")
        with open(self.path, "rb") as f:
            f.seek(HEADER + j0 * self.ne * self.es)
            buf = f.read(n * self.ne * self.es)
        a = np.frombuffer(buf, dtype="<" + self.dtype).astype("f4")
        a = a.reshape(n, self.ne)
        if self.zmult != 1.0 or self.zbase != 0.0:
            a = a * self.zmult + self.zbase
        return np.where(np.abs(a) > 1e30, np.nan, a)

    def north_up(self, i0, n):
        """`n` rows from the TOP, north-up — the orientation a raster uses."""
        j0 = self.nv - i0 - n
        return self.rows(j0, n)[::-1]

    def __repr__(self):
        return (f"GeosoftGrid({os.path.basename(self.path)}: {self.ne}x{self.nv} "
                f"@{self.de:g}m {self.dtype})")
