#!/usr/bin/env python3
"""Identifying a UTM zone from collar elevations — and the flat-ground trap.

A collar file of bare easting/northing is the common case, and MMP works the
zone out by matching the collar elevations against the terrain each candidate
zone would put them on. Two numbers come back per candidate: `mad`, how well the
SHAPE of the ground matches, and `off`, whether it is at the same HEIGHT.

Comparing shape alone is pathological on flat ground, because every flat place
on earth has the same shape. A real client upload — 602 drill collars at Langis,
Cobalt, Ontario, in perfect UTM 17N, over 42 m of relief in 3 km — matched its
true zone to a misfit of 1.9 m and a mean offset of 2.5 m, and was discarded as
"ambiguous" against zone 33N in AUSTRIA at a misfit of 2.6 m and an offset of
92 m. The data ended up in a local grid with no map position; the project was
then labelled British Columbia and given a British Columbia block size and
density, and could not merge with the public model of the same property.

So a rival must be comparable on BOTH numbers. These checks pin that, and pin
that the guard still does its job — a rival that really does fit equally well,
at the same height, still forces a refusal rather than a guess.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro import imports as IMP                        # noqa: E402

OK, BAD, SKIP = [], [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


# Jordan's own collars, as uploaded: Langis, Cobalt, Ontario, UTM 17N NAD83.
LANGIS = [
    (607331.9, 5270804.66, 214.95), (607470.47, 5270812.26, 218.39),
    (607510.83, 5270809.24, 218.98), (607494.83, 5270872.14, 220.04),
    (607507.0, 5270872.0, 220.12), (607196.37, 5270926.06, 212.94),
    (607219.86, 5270852.99, 213.29), (607746.1, 5270843.1, 218.96),
    (607559.25, 5270672.68, 217.22), (607559.57, 5270651.9, 216.68),
    (607444.33, 5270832.64, 218.16), (607571.63, 5270801.38, 218.61),
    (607371.79, 5270666.61, 214.25), (607582.6, 5270793.97, 218.42),
    (607463.38, 5270854.49, 218.72), (607320.88, 5270793.08, 214.52),
    (607057.24, 5270839.57, 210.21), (607017.66, 5270826.39, 207.88),
    (607013.68, 5270813.72, 207.56), (607014.49, 5270811.51, 207.52),
    (607024.0, 5270807.0, 208.1), (607030.57, 5270801.69, 208.37),
    (607216.39, 5270846.54, 213.17), (607200.5, 5270994.07, 210.81),
    (607518.89, 5270298.06, 213.58), (607514.62, 5270296.44, 213.64),
    (607462.48, 5270498.82, 213.84), (607464.54, 5270500.47, 213.86),
    (606414.56, 5270972.68, 187.78),
]


def collars(rows):
    return [{"hole": f"H{i}", "e": e, "n": n, "z": z} for i, (e, n, z) in enumerate(rows)]


check("an offset allowance is defined and forgives a geoid-sized shift",
      25.0 <= IMP.OFFSET_SLACK_M <= 60.0, f"{IMP.OFFSET_SLACK_M}")

# ------------------------------------------------- the rule, on controlled fits
# _dem_fit is replaced so the comparison is tested on exact numbers rather than
# on whatever the terrain service answers today.
_real_fit = IMP._dem_fit


def fits(table, default=None):
    """Return a _dem_fit stand-in keyed on the longitude it is handed."""
    def fake(lats, lons, zs, zoom, scale=1.0):
        lon = sum(lons) / len(lons)
        for west, east, val in table:
            if west <= lon <= east:
                return dict(val)
        return dict(default) if default else None
    return fake


def run(table, default=None):
    IMP._dem_fit = fits(table, default)
    try:
        notes = []
        hit = IMP.infer_utm(collars(LANGIS), notes, "NAD83")
        return hit, notes
    finally:
        IMP._dem_fit = _real_fit


# Ontario (~ -79.6) fits well and at the right height; Austria (~ +16) matches
# the shape but is 92 m too high. That is the real case, and Ontario must win.
hit, notes = run([(-85, -75, {"mad": 1.94, "off": 2.5, "slope": 1.0}),
                  (10, 20, {"mad": 2.62, "off": 92.2, "slope": 1.0})],
                 default={"mad": 40.0, "off": 500.0, "slope": 1.0})
check("a rival at the same shape but 92 m too high is not a rival",
      hit is not None and hit.get("epsg") == 26917,
      f"got {hit.get('epsg') if hit else 'REFUSED'} — {notes[:1]}")

# the guard still guards: a rival that fits as well AND sits at the same height
hit, notes = run([(-85, -75, {"mad": 1.94, "off": 2.5, "slope": 1.0}),
                  (10, 20, {"mad": 2.10, "off": 4.0, "slope": 1.0})],
                 default={"mad": 40.0, "off": 500.0, "slope": 1.0})
check("a rival that matches on BOTH numbers still forces a refusal",
      hit is None, f"got {hit.get('epsg') if hit else None}")
check("and it says the elevations fit two places equally well",
      any("two places" in n for n in notes), str(notes[:1]))

# a clearly worse rival never blocked anything, and still does not
hit, _ = run([(-85, -75, {"mad": 2.0, "off": 2.0, "slope": 1.0}),
              (10, 20, {"mad": 30.0, "off": 5.0, "slope": 1.0})],
             default={"mad": 40.0, "off": 500.0, "slope": 1.0})
check("a rival with a far worse shape fit does not block the winner",
      hit is not None and hit.get("epsg") == 26917)

# an excellent fit must not manufacture rivals out of the absolute slack
hit, _ = run([(-85, -75, {"mad": 0.8, "off": 1.0, "slope": 1.0}),
              (10, 20, {"mad": 6.0, "off": 3.0, "slope": 1.0})],
             default={"mad": 40.0, "off": 500.0, "slope": 1.0})
check("a 0.8 m fit is not called ambiguous against a 6 m one",
      hit is not None and hit.get("epsg") == 26917,
      "the flat +8 m slack used to swallow this")

# ------------------------------------------------- and against the real terrain
try:
    notes = []
    hit = IMP.infer_utm(collars(LANGIS), notes, "NAD83")
    if hit is None and any("could not" in n.lower() or "terrain" in n.lower() for n in notes):
        SKIP.append("the live terrain check")
        print("\n  skip  live terrain check — DEM not reachable from here")
    else:
        check("the real Langis collars resolve to zone 17N, Ontario",
              hit is not None and hit.get("epsg") == 26917,
              f"got {hit.get('epsg') if hit else 'REFUSED'}")
        if hit:
            from pyproj import Transformer
            from minemodelingpro import terrain as T
            tr = Transformer.from_crs(f"EPSG:{hit['epsg']}", "EPSG:4326", always_xy=True)
            lo, la = tr.transform(607500, 5270700)
            check("which places them in Ontario, not British Columbia",
                  T.region_of(la, lo) == "Ontario", f"{T.region_of(la, lo)} at {la:.3f},{lo:.3f}")
except Exception as e:                                            # noqa: BLE001
    SKIP.append(f"the live terrain check ({type(e).__name__})")
    print(f"\n  skip  live terrain check — {type(e).__name__}")

print(f"\n{len(OK)} passed, {len(BAD)} failed" + (f", {len(SKIP)} skipped" if SKIP else ""))
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
