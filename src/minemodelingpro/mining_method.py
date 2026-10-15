"""Open pit or underground — because that is how the tonnage will be presented.

A company does not publish one number. It publishes a pit-constrained tonnage at one
cut-off and an underground tonnage at a higher one, because the two are mined for
different money: a tonne near surface carries the cost of stripping the waste above
it, and a tonne at depth carries the cost of developing to reach it. The CIM
Definition Standards put the mining method among the assumptions behind reasonable
prospects for eventual economic extraction for exactly this reason.

One undifferentiated tonnage is therefore not comparable with either half of how a
filed statement is written, and it is not how a reader thinks about a deposit.

MMP cannot run a pit optimiser — that needs costs, slopes and a revenue model per
block. What it can do honestly is the geometric part: decide, for each block, whether
an open pit could plausibly reach it, using the one thing that governs that question
more than any other, the stripping ratio implied by its depth. Everything below that
is reported as underground, at a higher cut-off, because that is the only way it
would ever be mined.

The split is labelled as geometric. It is not a pit shell and does not pretend to be.
"""
import numpy as np

# How deep an open pit plausibly reaches, by deposit style. A broad low-grade body
# supports a big pit; a narrow vein does not, because the waste stripped per tonne of
# ore rises with the inverse of the body's width. These are the depths at which the
# industry stops digging and starts sinking, which is a blunter instrument than a pit
# optimiser and is meant to be.
PIT_DEPTH = {
    "porphyry": 450.0, "bulk": 350.0, "laterite": 80.0, "phosphate": 200.0,
    "iron": 350.0, "vms": 250.0, "sedex": 250.0, "skarn": 250.0,
    "orogenic": 200.0, "pegmatite": 200.0, "vein": 150.0,
}
DEFAULT_DEPTH = 250.0

# Underground mining costs several times what an open pit costs per tonne, so the
# cut-off that makes a tonne worth mining is correspondingly higher. Applied to the
# value cut-off when MMP has one, and to the grade cut-off when it does not.
UG_CUTOFF_MULTIPLE = 2.5


def pit_depth(style, median_width_m=None):
    """How deep a pit could plausibly go on this deposit."""
    d = PIT_DEPTH.get(style, DEFAULT_DEPTH)
    w = float(median_width_m or 0)
    if w and w < 5.0 and style not in ("laterite",):
        # a body a couple of metres wide cannot carry a deep pit at any strip ratio
        d = min(d, 60.0 + 20.0 * w)
    return round(d, 0)


def split(centres, topo_z, style, median_width_m=None):
    """Which blocks a pit could reach. Returns a boolean array, True for open pit.

    ``topo_z`` is the surface elevation above each block; where the terrain is not
    known the collar elevations stand in for it. Depth below surface is what decides,
    not elevation, because a deposit in a mountain is not deeper for being high."""
    if centres is None or not len(centres):
        return None, None
    z = np.asarray(centres, float)[:, 2]
    surf = np.asarray(topo_z, float) if topo_z is not None else None
    if surf is None or not np.isfinite(surf).any():
        surf = np.full(len(z), float(np.nanmax(z)))
    depth = surf - z
    limit = pit_depth(style, median_width_m)
    return depth <= limit, limit


def cutoff_for(method, base_cutoff):
    """The cut-off each half is reported at."""
    if base_cutoff is None:
        return None
    return round(float(base_cutoff) * (UG_CUTOFF_MULTIPLE if method == "underground" else 1.0), 4)


def describe(limit, style):
    return (f"Blocks within {limit:,.0f} m of surface are reported as open pit and the "
            f"rest as underground, with the underground cut-off set "
            f"{UG_CUTOFF_MULTIPLE:g}x higher because underground tonnes cost more to "
            f"mine. The depth is what a pit plausibly reaches on a "
            f"{style or 'deposit of this kind'} — a geometric split, not a pit shell.")
