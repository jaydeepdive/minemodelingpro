"""What kind of deposit is this, and what does that imply about modelling it?

The single largest error in MMP came from treating every deposit as the same shape
of thing. Scored against filed statements, the six projects whose estimated envelope
exceeded ten times the filed body had a median drill intercept width of 1.7 m; the
twenty that did not had 8.5 m. Every extreme error was a narrow vein — El Cubo
(1.4 m), Cahuilla (0.9 m), Perron (0.7 m), Bellechasse (1.0 m) — modelled as though
it were a bulk body.

That error runs both ways, which is why it was hard to see. A 1 m vein at 10 g/t
averaged into a 10 m block becomes a 10 m block at 1 g/t: the tonnage is invented
and the grade is destroyed at the same time. Goldboro and Copperstone, also narrow,
fail in the opposite direction for the same reason.

So the style has to be settled before anything else is, because it sets three things
a qualified person would never leave to a default:

* **density** — a quartz vein is 2.55 t/m³ and a massive sulphide lens is 4.1, and
  tonnage is linear in it. Keying density off the metal, as MMP did, gives a gold
  vein and a gold-bearing VMS the same rock.
* **how far mineralisation carries from an intercept** — one drill spacing is right
  for a lens and absurd for a vein, where it inflates the volume by the ratio of the
  spacing to the vein width.
* **block size** — a block wider than the body it sits in cannot represent it.

Evidence, in order of weight: what the technical report calls the deposit, then the
geometry of the drilling itself. Reports usually say outright ("a low-sulphidation
epithermal vein system", "volcanogenic massive sulphide"), and that is worth more
than any inference. Where no report says, the intercept widths and grade variability
are read instead — a style inferred this way is marked as inferred, and the model
page says which it was.
"""
import re
from collections import Counter

import numpy as np

# ---------------------------------------------------------------- the styles
#
# density            t/m³ for ordinary rock of this style
# density_per_pct    added t/m³ per 1% of the dense phase (sulphide, magnetite):
#                    a massive sulphide is dense BECAUSE it is massive sulphide, so
#                    density has to follow grade or every high-grade block is light
# density_drivers    which grades drive that
# halo_k             how far mineralisation is assumed to carry from an intercept,
#                    as a multiple of the intercept's own width
# halo_max_m         and the ceiling on that, whatever the drill spacing says
# block              parent block when no report states one
# anisotropy         search reach along the body's own three axes, longest first:
#                    (major, intermediate, across-thickness). PCA orders the axes by
#                    extent, so the third is the one across a tabular body — which is
#                    the number that stops a vein model bridging to the next vein.
STYLES = {
    "vein": {
        "label": "narrow vein system",
        "density": 2.75, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 3.0, "halo_max_m": 12.0, "block": [5, 5, 5],
        "anisotropy": (1.0, 0.90, 0.22),
        "note": "Narrow, high-contrast bodies. Mineralisation is not assumed to "
                "carry far from an intercept, because across strike it does not.",
    },
    "vms": {
        "label": "volcanogenic massive sulphide",
        "density": 2.95, "density_per_pct": 0.045, "density_drivers": ("Cu", "Pb", "Zn", "Fe"),
        "halo_k": 2.0, "halo_max_m": 45.0, "block": [5, 5, 5],
        "anisotropy": (1.0, 0.92, 0.65),
        "note": "Stratabound sulphide lenses: laterally continuous, vertically "
                "restricted, and dense in proportion to how much sulphide is in them.",
    },
    "porphyry": {
        "label": "porphyry",
        "density": 2.65, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 4.0, "halo_max_m": 90.0, "block": [20, 20, 15],
        "anisotropy": (1.0, 0.96, 0.90),
        "note": "Large, low-grade and broadly continuous — the one style where a "
                "wide search and a big block are the honest choice.",
    },
    "orogenic": {
        "label": "orogenic / shear-hosted gold",
        "density": 2.78, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 3.0, "halo_max_m": 30.0, "block": [10, 10, 5],
        "anisotropy": (1.0, 0.88, 0.50),
        "note": "Grade follows a structure, so continuity along and down it is far "
                "better than across it.",
    },
    "skarn": {
        "label": "skarn / replacement",
        "density": 2.90, "density_per_pct": 0.03, "density_drivers": ("Cu", "Pb", "Zn", "Fe"),
        "halo_k": 2.5, "halo_max_m": 40.0, "block": [10, 10, 10],
        "anisotropy": (1.0, 0.92, 0.75),
    },
    "sedex": {
        "label": "sediment-hosted massive sulphide",
        "density": 2.95, "density_per_pct": 0.04, "density_drivers": ("Pb", "Zn"),
        "halo_k": 3.0, "halo_max_m": 60.0, "block": [10, 10, 5],
        "anisotropy": (1.0, 0.95, 0.45),
    },
    "iron": {
        "label": "iron formation",
        "density": 3.00, "density_per_pct": 0.028, "density_drivers": ("Fe",),
        "halo_k": 3.0, "halo_max_m": 80.0, "block": [20, 20, 15],
        "anisotropy": (1.0, 0.92, 0.70),
        "note": "Density tracks magnetite content directly, and the bodies are large "
                "and bedded.",
    },
    "pegmatite": {
        "label": "pegmatite",
        "density": 2.70, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 2.5, "halo_max_m": 35.0, "block": [10, 10, 10],
        "anisotropy": (1.0, 0.88, 0.45),
        "note": "Dyke-like: continuous along and down dip, sharply bounded across it.",
    },
    "laterite": {
        "label": "laterite / saprolite",
        "density": 2.45, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 4.0, "halo_max_m": 80.0, "block": [25, 25, 2],
        "anisotropy": (1.0, 1.00, 0.15),
        "note": "A weathering blanket — wide, flat and lighter than fresh rock. The "
                "density is not a rounding matter: it is most of the tonnage.",
    },
    "phosphate": {
        "label": "sedimentary phosphate",
        "density": 2.95, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 3.5, "halo_max_m": 90.0, "block": [25, 25, 10],
        "anisotropy": (1.0, 1.00, 0.35),
    },
    "bulk": {
        "label": "bulk-tonnage / disseminated",
        "density": 2.70, "density_per_pct": 0.0, "density_drivers": (),
        "halo_k": 3.5, "halo_max_m": 60.0, "block": [10, 10, 10],
        "anisotropy": (1.0, 0.96, 0.92),
        "note": "The fallback when neither the report nor the drilling says otherwise.",
    },
}
DEFAULT = "bulk"

# What a report calls itself, in its own words. Ordered: the first match wins, so the
# more specific phrases come first.
_WORDS = [
    ("vms", r"volcanogenic massive sulphide|\bVMS\b|volcanic[- ]hosted massive sulphide|\bVHMS\b"),
    ("sedex", r"\bSEDEX\b|sediment[- ]hosted (?:massive sulphide|lead[- ]zinc)|Mississippi Valley|\bMVT\b"),
    ("laterite", r"(?:nickel|ni|cobalt|co)[- ]laterit|laterit\w*\s+(?:nickel|ni\b|deposit\s+of\s+nickel)|"
                 r"\bbauxit|limonite\s+zone|garnierite"),
    ("iron", r"iron formation|\bBIF\b|banded iron|taconite|magnetite deposit"),
    ("pegmatite", r"pegmatit|\bLCT\b|spodumene"),
    ("phosphate", r"phosphorit|sedimentary phosphate|\bphosphate (?:deposit|horizon)"),
    ("porphyry", r"porphyry (?:copper|cu|gold|molybdenum|deposit)|\bporphyry[- ]style"),
    ("skarn", r"\bskarn\b|replacement (?:body|deposit|manto)|\bmanto\b|carbonate replacement"),
    ("vein", r"epitherm|low[- ]sulphidation|high[- ]sulphidation|vein (?:system|set|field|deposit)|"
             r"quartz[- ]vein|narrow[- ]vein|\bvein[- ]hosted|bonanza"),
    ("orogenic", r"orogenic gold|shear[- ]hosted|greenstone[- ]hosted|\blode gold|"
                 r"mesotherm|archean lode"),
    ("bulk", r"disseminated|bulk[- ]tonnage|carlin[- ]type|heap leach"),
]


def from_text(pages_text):
    """What the report calls the deposit. The strongest evidence there is, and free.

    Counted across the whole document rather than taken from the first mention, so a
    passing reference to a neighbouring porphyry does not rename the project."""
    if not pages_text:
        return None, None
    blob = " ".join(pages_text if isinstance(pages_text, (list, tuple)) else [pages_text])
    blob = re.sub(r"\s+", " ", blob).lower()
    hits = Counter()
    for style, pat in _WORDS:
        n = len(re.findall(pat, blob, re.I))
        if n:
            hits[style] += n
    if not hits:
        return None, None
    style, n = hits.most_common(1)[0]
    runner = hits.most_common(2)[1][1] if len(hits) > 1 else 0
    if n < 2 or n < 1.4 * runner:
        return None, None                 # the report is not clear enough to act on
    return style, f"the technical report describes a {STYLES[style]['label']}"


def from_geometry(widths, grades, commodities=()):
    """Read the style off the drilling when no report states one.

    Intercept width is the discriminating measurement: it is what separates a vein
    from a lens, and it is the thing MMP was getting wrong. Grade variability
    corroborates — narrow bodies are erratic — and the commodity suite rules a few
    styles in or out."""
    if widths is None or not len(widths):
        return DEFAULT, None
    w = np.asarray([x for x in widths if x and x > 0], float)
    if not len(w):
        return DEFAULT, None
    med = float(np.median(w))
    p90 = float(np.percentile(w, 90))
    g = np.asarray([x for x in (grades or []) if x and x > 0], float)
    cv = float(g.std() / g.mean()) if len(g) > 4 and g.mean() else 0.0
    com = set(commodities or ())
    if "Fe" in com and med > 20:
        return "iron", f"wide iron intercepts (median {med:.0f} m)"
    if ("Li2O" in com or "Ta2O5" in com) and med > 3:
        return "pegmatite", f"lithium/tantalum over {med:.0f} m intercepts"
    if "P2O5" in com:
        return "phosphate", "phosphate horizons"
    if med <= 3.0 and p90 <= 12.0:
        return "vein", (f"narrow intercepts (median {med:.1f} m, 90th percentile "
                        f"{p90:.1f} m) with erratic grade" if cv > 1.0
                        else f"narrow intercepts (median {med:.1f} m)")
    if med >= 30.0 and cv < 0.8:
        return "porphyry", f"wide, low-contrast intercepts (median {med:.0f} m)"
    if med >= 10.0 and {"Cu", "Zn", "Pb"} & com and cv > 0.9:
        return "vms", f"thick polymetallic sulphide intercepts (median {med:.0f} m)"
    if med <= 8.0 and cv > 1.2 and ("Au" in com or "Ag" in com):
        return "orogenic", f"structurally controlled intercepts (median {med:.1f} m)"
    return DEFAULT, f"intercepts of median {med:.1f} m with no clearer signature"


def classify(widths=None, grades=None, commodities=(), pages_text=None, stated=None):
    """Settle the style. Returns ``(key, style, why, confidence)``.

    ``stated`` is a style set by hand for the project, which always wins — this is
    where a human overrides the machine, and that is the point."""
    if stated and stated in STYLES:
        return stated, STYLES[stated], "set for this project", "stated"
    key, why = from_text(pages_text)
    if key:
        return key, STYLES[key], why, "report"
    key, why = from_geometry(widths, grades, commodities)
    return key, STYLES[key], why, "inferred from the drilling"


# ------------------------------------------------------------------- density
def density_for(style, grades=None, stated=None):
    """Tonnes per cubic metre, by what the rock actually is.

    A stated bulk density from the project's own report always wins. Otherwise the
    style sets it, and for styles whose rock is dense *because* it is mineralised —
    massive sulphide, iron formation — it rises with grade, because a block of 20%
    combined base metal is not the same rock as its barren neighbour. Murray Brook's
    2,961 measurements run from 2.04 to 6.86 t/m³ against a 4.14 average; a single
    constant cannot describe that, and tonnage is linear in the error."""
    if stated and 1.0 <= float(stated) <= 8.0:
        return float(stated), "stated in the technical report"
    S = STYLES.get(style) or STYLES[DEFAULT]
    d = S["density"]
    per = S.get("density_per_pct") or 0.0
    drivers = S.get("density_drivers") or ()
    if per and grades:
        pct = sum(float(grades.get(el) or 0.0) for el in drivers)
        if pct > 0:
            d = d + per * min(pct, 60.0)
            return round(min(d, 6.5), 2), (f"{S['label']}, raised for "
                                           f"{pct:.1f}% combined {'/'.join(drivers)}")
    return round(d, 2), f"typical for a {S['label']}"


def halo_for(style, median_width_m, drill_spacing_m):
    """How far mineralisation is carried beyond a reported intercept.

    MMP used one drill spacing for everything. For a 17 m massive sulphide lens that
    is about right; for a 1 m vein it inflates the modelled volume by the ratio of
    the spacing to the vein — which is exactly the 95x and 45x envelopes measured
    against filed statements. So the halo is set by the body's own thickness, with
    the drill spacing only as a ceiling: data cannot support reach beyond it either."""
    S = STYLES.get(style) or STYLES[DEFAULT]
    w = float(median_width_m or 0) or 5.0
    halo = w * float(S["halo_k"])
    halo = min(halo, float(S["halo_max_m"]))
    if drill_spacing_m:
        halo = min(halo, float(drill_spacing_m))
    return round(max(halo, 2.0), 1)


def block_for(style, median_width_m=None):
    """A parent block the body can actually contain.

    A block wider than the vein it sits in cannot represent it: the grade is averaged
    away and the tonnage is fabricated in the same step."""
    S = STYLES.get(style) or STYLES[DEFAULT]
    blk = [float(v) for v in S["block"]]
    w = float(median_width_m or 0)
    if w and w < blk[0]:
        side = max(1.0, min(blk[0], round(w, 1)))
        blk = [side, side, max(1.0, min(blk[2], side))]
    return blk


def describe(key, why, confidence):
    S = STYLES.get(key) or STYLES[DEFAULT]
    bits = [S["label"]]
    if why:
        bits.append(why)
    return {"key": key, "label": S["label"], "why": why, "confidence": confidence,
            "note": S.get("note"), "summary": " — ".join(bits)}
