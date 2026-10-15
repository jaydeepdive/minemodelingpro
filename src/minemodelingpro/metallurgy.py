"""What the rock is, and what you would have to do to it to get the metal out.

Two deposits with the same grade are not worth the same money. One leaches on a pad
for a few dollars a tonne; the other is refractory, locks its gold inside sulphide,
and needs a pressure oxidation circuit that costs more to build than the mine. A
grade shell says nothing about that difference, and it is usually the difference
that decides whether a deposit is a business.

Technical reports say all of it — Section 13 is metallurgical testwork and Section 17
is recovery methods — so this reads what they state rather than guessing: the
mineralisation style, whether the ore is amenable to heap leach or needs a mill,
whether it is refractory or preg-robbing, the processing route, and the recovery
assumed for each metal.

It feeds two things. The model page, so a reader can see that a 1 g/t oxide heap
leach ounce is not the same asset as a 1 g/t refractory ounce. And the value model
in ``minemodelingpro.nsr``, where a stated recovery is a far better number than the
generic one the indicative formula falls back on.
"""
import re
from collections import Counter

# Processing routes, most specific first — a report mentioning both "heap leach" and
# "pressure oxidation" is describing the second as the answer to the first's failure.
ROUTES = [
    ("pox", r"pressure oxidation|\bPOX\b|autoclave"),
    ("bio", r"bio[- ]?oxidation|\bBIOX\b|bacterial oxidation"),
    ("roast", r"\broast(?:ing|er)\b|fluid bed roast"),
    ("ultrafine", r"ultra[- ]?fine grind|\bUFG\b|Albion process"),
    ("cil_cip", r"\bCIL\b|\bCIP\b|carbon[- ]in[- ](?:leach|pulp)"),
    ("flotation", r"\bflotation\b|flotation concentrate|concentrator"),
    ("heap_leach", r"heap[- ]leach|\bROM leach|run[- ]of[- ]mine leach|dump leach"),
    ("vat", r"vat leach|agitated (?:tank )?leach"),
    ("gravity", r"gravity (?:circuit|concentrat|recovery)|\bKnelson\b|shaking table"),
    ("sx_ew", r"\bSX[- ]?EW\b|solvent extraction[^.]{0,30}electrowinning"),
    ("magnetic", r"magnetic separation|\bLIMS\b|\bWHIMS\b|Davis tube"),
    ("dms", r"dense media separation|\bDMS\b|heavy media"),
    ("wash", r"wash plant|scrubbing and screening|beneficiation by washing"),
]
ROUTE_LABEL = {
    "pox": "pressure oxidation", "bio": "bio-oxidation", "roast": "roasting",
    "ultrafine": "ultrafine grinding", "cil_cip": "cyanide leach (CIL/CIP)",
    "flotation": "flotation to concentrate", "heap_leach": "heap leach",
    "vat": "tank leach", "gravity": "gravity recovery", "sx_ew": "SX-EW",
    "magnetic": "magnetic separation", "dms": "dense media separation",
    "wash": "wash plant",
}

# Characteristics that change what a tonne is worth, stated plainly in reports.
TRAITS = [
    ("refractory", r"\brefractory\b"),
    ("preg_robbing", r"preg[- ]robbing|carbonaceous (?:ore|material|matter)"),
    ("oxide", r"\boxide (?:ore|material|zone|cap|mineralisation|mineralization)"),
    ("transitional", r"\btransition(?:al)? (?:ore|zone|material)"),
    ("sulphide", r"\bsulphide (?:ore|zone|mineralisation|mineralization)|"
                 r"\bsulfide (?:ore|zone|mineralisation|mineralization)"),
    ("free_milling", r"free[- ]milling"),
    ("clay", r"high[- ]clay|clay[- ]rich|deleterious clay"),
    ("arsenic", r"\barsenic\b|arsenopyrite|\bpenalt(?:y|ies)[^.]{0,40}arsenic"),
    ("acid_consuming", r"acid[- ]consuming|high acid consumption"),
]
TRAIT_LABEL = {
    "refractory": "refractory", "preg_robbing": "preg-robbing",
    "oxide": "oxide ore present", "transitional": "transitional ore present",
    "sulphide": "sulphide ore", "free_milling": "free-milling",
    "clay": "clay-rich", "arsenic": "arsenic-bearing",
    "acid_consuming": "acid-consuming",
}

_EL = {"gold": "Au", "au": "Au", "silver": "Ag", "ag": "Ag", "copper": "Cu",
       "cu": "Cu", "zinc": "Zn", "zn": "Zn", "lead": "Pb", "pb": "Pb",
       "nickel": "Ni", "ni": "Ni", "cobalt": "Co", "co": "Co",
       "molybdenum": "Mo", "mo": "Mo", "iron": "Fe", "fe": "Fe",
       "lithium": "Li2O", "li2o": "Li2O", "uranium": "U3O8", "u3o8": "U3O8"}

# Reports state recoveries as a list far more often than one at a time — "Process
# recoveries used were 80% Cu, 87% Zn, 75% Pb, and 90% Ag" — so a pattern that
# matches a single pair reads the first one and then mis-pairs the rest against
# whatever follows. Instead: find where recovery is being discussed, then read every
# metal-and-percentage pair out of the window that follows, in either order.
_TRIGGER = re.compile(r"recover(?:y|ies|ed)\b", re.I)
# Only real metal names may fill the element slot. A pattern that accepts any
# word matches "were 80%" in "recoveries used were 80% Cu, 87% Zn", swallows the
# number, and pairs every remaining figure with the metal after it — so the whole
# list comes out shifted by one and every recovery is attributed to the wrong
# metal. Wrong numbers that look right are worse than no numbers.
_METAL = "(?:" + "|".join(sorted(_EL, key=len, reverse=True)) + ")"
_NUM_EL = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*%\s*(?:for\s+|of\s+)?"
                     + _METAL.join(("(", ")")) + r"\b", re.I)
# A parameter list reads "Cu Concentrate Recovery: 80% Zn Concentrate Recovery: 87%",
# where every number follows its own label. Reading it as "number then metal" pairs
# 80 with the Zn that comes after it and shifts the entire list by one — so the
# label-first form is tried first and wins, and a few filler words are allowed
# between the metal and its number.
_EL_NUM = re.compile(_METAL.join(("(", ")")) +
                     r"(?:\s+[A-Za-z()/-]{2,14}){0,3}?"
                     r"\s*(?:recover(?:y|ies))?\s*"
                     r"(?:of|at|was|were|is|are|:|=|averaging)\s*"
                     r"(\d{1,3}(?:\.\d{1,2})?)\s*%", re.I)
_WINDOW = 200


def _flat(pages_text):
    if not pages_text:
        return ""
    t = pages_text if isinstance(pages_text, str) else " ".join(pages_text)
    return re.sub(r"\s+", " ", t)


def recoveries(pages_text):
    """Metallurgical recovery per metal, as the report states it.

    Several figures are usually quoted — testwork on different composites, a range,
    a design basis. The median of what is stated is taken rather than the best, since
    the highest number in a technical report is rarely the one the plant achieves."""
    flat = _flat(pages_text)
    found, sets = {}, []
    for t in _TRIGGER.finditer(flat):
        # the metal is as often before the word as after it — "a gold recovery of
        # 94.5%" against "recoveries of 89% for copper" — so the window spans both
        win = flat[max(0, t.start() - 70): t.end() + _WINDOW]
        win = win.split(". ")[0] + "."          # stay inside the sentence
        here = {}
        for rx, num_first in ((_EL_NUM, False), (_NUM_EL, True)):
            for m in rx.finditer(win):
                num, sym = (m.group(1), m.group(2)) if num_first else (m.group(2), m.group(1))
                el = _EL.get(str(sym).lower())
                if not el:
                    continue
                try:
                    val = float(num)
                except (TypeError, ValueError):
                    continue
                if not (20.0 <= val <= 99.9):
                    continue
                if el in here:
                    continue          # the label-first reading already claimed it
                found.setdefault(el, []).append(val)
                here[el] = val
        if len(here) >= 2:
            sets.append(here)
    # A sentence that states four metals' recoveries together is the design basis;
    # scattered single mentions are testwork variants, and mixing the two by taking
    # a median per metal attributes each number to the wrong place. So the fullest
    # single statement wins, and the medians only fill what it does not mention.
    out = {}
    if sets:
        best = max(sets, key=len)
        out.update({el: round(v, 1) for el, v in best.items()})
    for el, vals in found.items():
        if el in out:
            continue
        vals.sort()
        out[el] = round(vals[len(vals) // 2], 1)
    return out or None


def route(pages_text):
    """The processing route, and the traits that decide what it costs."""
    flat = _flat(pages_text)
    low = flat.lower()
    routes = []
    for key, pat in ROUTES:
        n = len(re.findall(pat, low, re.I))
        if n >= 2:                        # one passing mention is not a flowsheet
            routes.append((key, n))
    routes.sort(key=lambda kv: -kv[1])
    traits = [k for k, pat in TRAITS if len(re.findall(pat, low, re.I)) >= 2]
    return [k for k, _n in routes[:4]], traits


def extract(pages_text):
    """Everything this module can say about a report's metallurgy."""
    r, traits = route(pages_text)
    rec = recoveries(pages_text)
    if not r and not traits and not rec:
        return None
    out = {"routes": r, "traits": traits, "recoveries": rec}
    out["summary"] = describe(out)
    return out


def describe(met):
    """One line a reader can take in."""
    if not met:
        return ""
    bits = []
    if met.get("traits"):
        bits.append(", ".join(TRAIT_LABEL.get(t, t) for t in met["traits"]))
    if met.get("routes"):
        bits.append("processed by " + ", then ".join(ROUTE_LABEL.get(x, x)
                                                     for x in met["routes"]))
    if met.get("recoveries"):
        bits.append("stated recovery " + ", ".join(
            f"{el} {v:g}%" for el, v in sorted(met["recoveries"].items(),
                                               key=lambda kv: -kv[1])))
    return "; ".join(bits)


def heap_leachable(met):
    """Is this ore the cheap kind? Worth saying plainly, because it is usually the
    difference between a deposit that gets built and one that does not."""
    if not met:
        return None
    if "heap_leach" in (met.get("routes") or []):
        return True
    if {"refractory", "preg_robbing"} & set(met.get("traits") or []):
        return False
    return None
