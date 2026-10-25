"""The grade at which a metal starts to be ore, and what that is for.

A modern laboratory reports thirty elements on every sample. A silver-cobalt
property therefore arrives carrying copper, zinc, tungsten, uranium and antimony
at crustal background, assayed on exactly the same holes as the silver — and any
ranking that counts holes finds every element tied and falls back on an arbitrary
order. That is how a silver mine came to be reported in copper.

What separates the deposit's metals from the rest is not coverage, it is grade:
the silver reaches hundreds of grams a tonne, the tungsten never leaves the
decimal places. These floors are coarse on purpose — they only have to tell ore
from background — and a project's own `elements` setting overrides the lot.

**Every floor carries its own unit.** The first version of this table wrote them
all as bare numbers under a comment saying which unit was meant, and gallium —
assayed in g/t, floored at 0.01 as though it were per cent — scored a perfect
1.0 on plain crustal background (17 g/t) and took a silver property's primary
metal. A floor 10,000 times too low is not a tuning problem, it is a unit the
code could not see. Units are declared here and converted at the comparison.
"""
import numpy as np

PCT_IN_GT = 10_000.0                 # 1 % = 10,000 g/t = 10,000 ppm
_PER_GT = {"%": PCT_IN_GT, "pct": PCT_IN_GT, "percent": PCT_IN_GT,
           "g/t": 1.0, "gpt": 1.0, "g/mt": 1.0, "ppm": 1.0,
           "ppb": 0.001, "oz/t": 34285.7, "opt": 34285.7}

# (grade at which this metal starts to be ore, the unit that grade is in)
FLOOR = {
    # precious, in grams a tonne
    "Au": (0.08, "g/t"), "AuEq": (0.08, "g/t"), "Ag": (3.0, "g/t"),
    "AgEq": (3.0, "g/t"), "Pt": (0.05, "g/t"), "Pd": (0.05, "g/t"),
    "Rh": (0.05, "g/t"), "PGE": (0.05, "g/t"), "PGM": (0.05, "g/t"),
    "3E": (0.05, "g/t"),
    # base and industrial, in per cent
    "Cu": (0.03, "%"), "CuEq": (0.03, "%"), "Zn": (0.1, "%"), "ZnEq": (0.1, "%"),
    "Pb": (0.1, "%"), "Ni": (0.03, "%"), "NiEq": (0.03, "%"), "Co": (0.005, "%"),
    "Mo": (0.003, "%"), "Sn": (0.02, "%"), "W": (0.05, "%"), "WO3": (0.02, "%"),
    "U": (0.005, "%"), "U3O8": (0.005, "%"), "Li": (0.05, "%"), "Li2O": (0.1, "%"),
    "Sb": (0.5, "%"), "Bi": (0.05, "%"), "V2O5": (0.2, "%"), "Cr2O3": (2.0, "%"),
    "TiO2": (2.0, "%"), "P2O5": (1.0, "%"), "Fe": (5.0, "%"), "Mn": (5.0, "%"),
    "TREO": (0.1, "%"), "REO": (0.1, "%"), "Nb2O5": (0.1, "%"), "Ta2O5": (0.01, "%"),
    "Cg": (1.0, "%"), "S": (10.0, "%"), "As": (1.0, "%"),
    # trace metals recovered as by-products, in grams a tonne. These sit at tens
    # of g/t in the deposits that actually produce them and at single figures in
    # ordinary rock, so the floor has to be the producing grade — gallium's
    # crustal background is 17 g/t and must not read as ore.
    "Te": (5.0, "g/t"), "Ga": (50.0, "g/t"), "Ge": (50.0, "g/t"), "In": (20.0, "g/t"),
    "Re": (0.5, "g/t"), "Se": (20.0, "g/t"), "Cd": (50.0, "g/t"),
}

# what a grade for this element is in when nothing says otherwise; anything not
# named here is a per-cent metal
STD_UNIT = {el: u for el, (_v, u) in FLOOR.items() if u == "g/t"}


def per_gt(unit):
    """How many g/t one unit of `unit` is, or None if it is not a grade unit."""
    return _PER_GT.get(str(unit or "").strip().lower())


def floor(el, unit=None):
    """The ore floor for this metal in `unit`, or None where there is no sensible one.

    With no unit given, the floor comes back in the unit it is declared in."""
    f = FLOOR.get(el)
    if not f:
        return None
    v, fu = f
    if unit is None:
        return v
    a, b = per_gt(fu), per_gt(unit)
    if not a or not b:
        return v if (unit or "") == fu else None
    return v * a / b


def significance(el, grades, unit=None):
    """How far above ore grade this metal gets here, on a 0 to 1 scale.

    Not whether it crosses the floor — background copper in a silver mine crosses
    a 0.03% floor all day — but by how much. The silver in a vein reaches a
    thousand times its floor and the tungsten beside it never reaches a tenth, so
    the comparison is made on the ratio, in orders of magnitude, at the top of the
    distribution rather than the middle: a deposit is the few per cent of the
    rock that is worth mining, and an element that is ore in two per cent of the
    samples is still what the property is about.

    `unit` is the unit the grades are in. Omitted, the element's own standard unit
    is assumed — which is what the importer normalises to — because comparing a
    g/t grade against a per-cent floor is how a background trace element wins.

    An element with no floor of its own is given a low constant — enough to be
    modelled when nothing else is there, never enough to outrank a metal that
    genuinely reaches ore grade.
    """
    f = floor(el, unit if unit is not None else STD_UNIT.get(el, "%"))
    g = np.asarray(grades, float)
    g = g[np.isfinite(g) & (g > 0)]
    if not len(g):
        return 0.0
    if f is None or f <= 0:
        return 0.15
    top = float(np.percentile(g, 99)) if len(g) >= 100 else float(g.max())
    if top <= 0:
        return 0.0
    return float(np.clip(np.log10(top / f), 0.0, 3.0) / 3.0)
