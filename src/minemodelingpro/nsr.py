"""What a tonne of rock is worth — the cut-off a polymetallic deposit is reported on.

A grade cut-off answers "how much of this metal is in the rock". That is the right
question for a gold deposit and the wrong one for a volcanogenic massive sulphide,
where copper may be a third of the value and the zinc pays for the mine. Murray Brook
is the lesson: P&E report 23.1 Mt at a CAD$23/t NSR cut-off; MMP reported 4.8 Mt
because it cut the same block model at 0.1% Cu and threw away every zinc-rich,
copper-poor block. Estimated on the same blocks and cut on NSR instead, MMP reports
21.3 Mt — the model was never wrong, the question put to it was.

So: when a deposit carries more than one metal of value, the cut-off belongs in
dollars per tonne.

Two ways to get there, in order of preference:

1. **The report's own formula.** Technical reports state it outright —
   ``NSR $/t = (Cu % x 81) + (Pb % x 12) + (Zn % x 13) + (Ag g/t x 0.90)`` — and it
   already nets out recoveries, payables, treatment charges and freight. Using it
   makes MMP's tonnage directly comparable with the published statement, which is the
   whole point of the cross-check.
2. **An indicative formula**, from metal prices and typical net-of-charge
   realisations. This is an approximation and is labelled as one: smelter terms vary
   by concentrate, and zinc in particular loses over half its gross value to
   treatment charges.
"""
import re

import numpy as np

# Grades are carried in the model's own units: per cent for base metals, grams per
# tonne for precious. A factor is therefore "dollars per tonne of rock, per unit of
# that grade".
PCT = ("Cu", "Pb", "Zn", "Ni", "Co", "Mo", "Sn", "WO3", "U3O8", "Li2O")
GPT = ("Au", "Ag", "Pt", "Pd", "Rh")

LB_PER_T = 2204.62262
G_PER_OZ = 31.1034768

# Fallback metal prices (US$/lb for base, US$/oz for precious). Only used when no
# report states a formula and no live price is supplied.
PRICE = {"Cu": 4.00, "Pb": 0.95, "Zn": 1.25, "Ni": 7.50, "Co": 15.00, "Mo": 20.00,
         "Sn": 13.00, "Au": 2400.0, "Ag": 28.0, "Pt": 950.0, "Pd": 1000.0}

# What a mine actually banks, as a share of the gross metal value: recovery, payable
# share, treatment and refining charges and freight, all together. Back-calculated
# from P&E's own Murray Brook factors (Cu 81, Pb 12, Zn 13, Ag 0.90 CAD/t at
# US$4.00/lb Cu, US$0.95/lb Pb, US$1.25/lb Zn, US$23/oz Ag, 0.76 USD/CAD): a tonne
# of base metal in a concentrate keeps far less of its gross value than a tonne of
# gold in doré, and zinc keeps barely a third of it once the treatment charge is paid.
REALISATION = {"Cu": 0.70, "Pb": 0.44, "Zn": 0.36, "Ni": 0.55, "Co": 0.50,
               "Mo": 0.60, "Sn": 0.65, "Au": 0.95, "Ag": 0.92, "Pt": 0.80, "Pd": 0.80}

_SYM = {"cu": "Cu", "pb": "Pb", "zn": "Zn", "ni": "Ni", "co": "Co", "mo": "Mo",
        "sn": "Sn", "au": "Au", "ag": "Ag", "pt": "Pt", "pd": "Pd", "wo3": "WO3",
        "u3o8": "U3O8", "li2o": "Li2O", "gold": "Au", "silver": "Ag", "copper": "Cu",
        "lead": "Pb", "zinc": "Zn", "nickel": "Ni", "cobalt": "Co"}

# "NSR $/t = (Cu % x 81) + (Pb % x 12) + (Zn % x 13) + (Ag g/t x 0.90)"
_FORMULA = re.compile(
    r"\bNSR\b[^=\n]{0,40}=\s*((?:\(?\s*[A-Za-z][A-Za-z0-9]{0,4}\s*(?:%|g\s*/\s*t|gpt|ppm)\s*"
    r"[x*×X]\s*\$?\s*[\d.]+\s*\)?\s*[+\-]?\s*){2,})", re.I)
_TERM = re.compile(r"([A-Za-z][A-Za-z0-9]{0,4})\s*(%|g\s*/\s*t|gpt|ppm)\s*[x*×X]\s*\$?\s*([\d.]+)", re.I)
_CURRENCY = re.compile(r"\b(CAD?\$|C\$|US\$|A\$|AUD\$)", re.I)


def stated(pages_text):
    """The NSR formula a technical report states, or None.

    Returns ``{"factors": {el: $/t per unit grade}, "units": {el: "%"|"g/t"},
    "currency": "C$", "source": "<the line as printed>"}``. Preferred over anything
    MMP can derive, because it is the formula the published tonnage was cut with."""
    best = None
    for t in (pages_text if isinstance(pages_text, (list, tuple)) else [pages_text]):
        flat = re.sub(r"\s+", " ", t or "")
        for m in _FORMULA.finditer(flat):
            factors, units = {}, {}
            for el, unit, val in _TERM.findall(m.group(1)):
                sym = _SYM.get(el.lower())
                if not sym:
                    continue
                u = "%" if unit == "%" else "g/t"
                try:
                    f = float(val)
                except ValueError:
                    continue
                if not (0.0001 < f < 100000):
                    continue
                factors[sym], units[sym] = f, u
            if len(factors) < 2:
                continue
            cur = _currency_of_cutoff(pages_text) or _currency_near(flat, m.start())
            cand = {"factors": factors, "units": units, "currency": cur,
                    "source": m.group(0)[:160].strip()}
            # the fullest formula on any page wins: a summary often quotes a
            # shortened version of the one the block model actually used
            if best is None or len(cand["factors"]) > len(best["factors"]):
                best = cand
    return best


_COG_CUR = re.compile(r"(CAD?\$|C\$|US\$|A\$)\s*[\d.,]+\s*/\s*t[^.\n]{0,60}cut[- ]?off|"
                      r"cut[- ]?off[^.\n]{0,80}?(CAD?\$|C\$|US\$|A\$)\s*[\d.,]+\s*/\s*t", re.I)


def _currency_of_cutoff(pages_text):
    """The currency the NSR cut-off itself is quoted in.

    The formula is usually printed bare ("NSR $/t = ..."), while the dollar it is in
    is fixed two pages earlier by the cost basis it was derived from — CAD$20/t
    processing plus CAD$3/t G&A giving a CAD$23/t cut-off. Labelling those factors
    US$ because a price list nearby happened to be quoted in US$ would misstate every
    block value on the page."""
    for t in (pages_text if isinstance(pages_text, (list, tuple)) else [pages_text]):
        for m in _COG_CUR.finditer(re.sub(r"\s+", " ", t or "")):
            tok = (m.group(1) or m.group(2) or "").upper().replace("AD", "").replace("$", "")
            got = {"C": "C$", "US": "US$", "A": "A$"}.get(tok)
            if got:
                return got
    return None


def _currency_near(flat, at, window=1400):
    """Which dollar the formula is in. Reports put 'CAD$' in the section heading
    above the formula rather than in the formula itself."""
    seg = flat[max(0, at - window):at + 200]
    hits = _CURRENCY.findall(seg)
    if not hits:
        return "$"
    last = hits[-1].upper().replace("AD", "").replace("$", "")
    return {"C": "C$", "US": "US$", "A": "A$", "AUD": "A$"}.get(last, "$")


def indicative(elements, units=None, prices=None, fx=1.0, currency="US$"):
    """An NSR formula from metal prices, for a deposit whose reports state none.

    This is an estimate of an estimate — smelter terms are specific to a concentrate
    — so it carries ``approximate: True`` and the model page says so. It is still far
    better than cutting a five-metal deposit on one metal's grade."""
    px = dict(PRICE)
    px.update({k: v for k, v in (prices or {}).items() if v})
    units = units or {}
    factors, used = {}, {}
    for el in elements:
        p = px.get(el)
        if not p:
            continue
        r = REALISATION.get(el, 0.75)
        u = units.get(el) or ("g/t" if el in GPT else "%")
        if u == "%":
            f = p * LB_PER_T / 100.0 * r          # $/t of rock per 1% metal
        elif u in ("g/t", "ppm"):
            f = p / G_PER_OZ * r                  # $/t of rock per 1 g/t metal
        else:
            continue
        factors[el], used[el] = round(f * fx, 4), u
    if len(factors) < 2:
        return None
    return {"factors": factors, "units": used, "currency": currency,
            "approximate": True,
            "source": "indicative: metal prices less typical recovery, payables and "
                      "treatment charges"}


def value(grades, model):
    """Dollars per tonne of rock, block by block.

    ``grades`` maps element to an array of estimated grades (NaN where that metal was
    not estimable); a metal with no data simply contributes nothing, which is the
    honest reading — the rock is worth at least this much."""
    if not model or not model.get("factors"):
        return None
    n = max((len(v) for v in grades.values()), default=0)
    if not n:
        return None
    out = np.zeros(n, np.float64)
    for el, f in model["factors"].items():
        g = grades.get(el)
        if g is None or len(g) != n:
            continue
        out += np.nan_to_num(np.asarray(g, np.float64), nan=0.0) * float(f)
    return out


def covered(grades, model):
    """The share of the formula's value that this project can actually estimate.

    A formula with four metals applied to a project that only ever assayed copper
    understates every block, and the page must say so rather than quietly reporting a
    low NSR as though the rock were poor."""
    if not model or not model.get("factors"):
        return None
    tot = sum(float(f) for f in model["factors"].values())
    if not tot:
        return None
    have = sum(float(f) for el, f in model["factors"].items()
               if el in grades and np.isfinite(np.asarray(grades[el], float)).any())
    return round(have / tot, 4)


def describe(model):
    """The formula as a line a reader can check against the report."""
    if not model or not model.get("factors"):
        return ""
    cur = model.get("currency") or "$"
    parts = [f"{el} {model['units'].get(el, '%')} × {f:g}"
             for el, f in sorted(model["factors"].items(), key=lambda kv: -kv[1])]
    return f"NSR {cur}/t = " + " + ".join(parts)
