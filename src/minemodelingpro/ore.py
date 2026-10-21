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
"""
import numpy as np

# standard units: g/t for the precious metals, per cent for the rest
FLOOR = {
    "Au": 0.08, "AuEq": 0.08, "Ag": 3.0, "AgEq": 3.0, "Pt": 0.05, "Pd": 0.05,
    "Rh": 0.05, "PGE": 0.05, "PGM": 0.05, "3E": 0.05,
    "Cu": 0.03, "CuEq": 0.03, "Zn": 0.1, "ZnEq": 0.1, "Pb": 0.1, "Ni": 0.03,
    "NiEq": 0.03, "Co": 0.005, "Mo": 0.003, "Sn": 0.02, "W": 0.05, "WO3": 0.02,
    "U": 0.005, "U3O8": 0.005, "Li": 0.05, "Li2O": 0.1, "Sb": 0.5, "Bi": 0.05,
    "V2O5": 0.2, "Cr2O3": 2.0, "TiO2": 2.0, "P2O5": 1.0, "Fe": 5.0, "Mn": 5.0,
    "TREO": 0.1, "REO": 0.1, "Nb2O5": 0.1, "Ta2O5": 0.01, "Cg": 1.0, "S": 10.0,
    "Te": 5.0, "As": 1.0, "Ga": 0.01, "Ge": 0.01, "In": 0.01,
}


def floor(el):
    """The ore floor for this metal, or None where there is no sensible one."""
    return FLOOR.get(el)


def significance(el, grades):
    """How far above ore grade this metal gets here, on a 0 to 1 scale.

    Not whether it crosses the floor — background copper in a silver mine crosses
    a 0.03% floor all day — but by how much. The silver in a vein reaches a
    thousand times its floor and the tungsten beside it never reaches a tenth, so
    the comparison is made on the ratio, in orders of magnitude, at the top of the
    distribution rather than the middle: a deposit is the few per cent of the
    rock that is worth mining, and an element that is ore in two per cent of the
    samples is still what the property is about.

    An element with no floor of its own is given a low constant — enough to be
    modelled when nothing else is there, never enough to outrank a metal that
    genuinely reaches ore grade.
    """
    f = FLOOR.get(el)
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
