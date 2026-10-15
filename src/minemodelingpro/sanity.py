"""Catch the numbers that cannot be true before they reach a model.

Seven per cent of MMP's models carried a physically impossible number: 4,029% zinc,
2,860% copper, 8,920% cobalt, a gold median of 83.5 g/t. Every one is a unit the
table did not state and the extractor had to guess — parts per million read as per
cent, parts per billion read as grams per tonne — and a single one of them poisons
everything downstream, because grade drives the cut-off, the cut-off drives the
tonnage, and the grade cap is set from the same data.

Mont Sorcier was the clearest case: a 24% iron deposit whose grade cap was matched
from a stray "15" in its report, so the model was capped below its own ore and could
never reach the deposit at all.

These are cheap to detect, because rock has limits. Nothing is more than 100% of
itself, gold does not average 83 grams a tonne across a deposit, and a grade cap
below the median of the data it caps is not a cap, it is a mistake. What cannot be
repaired with confidence is flagged rather than quietly published, because a model
that is wrong and says so is worth more than one that is wrong and does not.
"""
import numpy as np

# The most a whole column of assays can plausibly reach, in MMP's standard units
# (g/t for precious, % for everything else). These are deliberately generous: the
# point is to catch a factor of a thousand, not to second-guess a bonanza sample.
MAX_PLAUSIBLE = {
    "Au": 1500.0, "Ag": 30000.0, "Pt": 500.0, "Pd": 500.0, "Rh": 300.0,
    "Cu": 100.0, "Pb": 100.0, "Zn": 100.0, "Ni": 100.0, "Co": 100.0, "Mo": 100.0,
    "Sn": 100.0, "W": 100.0, "WO3": 100.0, "Sb": 100.0, "Bi": 100.0,
    "Fe": 72.0, "Mn": 70.0, "Cr": 70.0, "V2O5": 40.0, "U3O8": 40.0,
    "Li2O": 12.0, "Ta2O5": 10.0, "P2O5": 45.0, "Al2O3": 70.0, "TiO2": 60.0,
    "S": 55.0, "Graphite": 60.0, "C": 60.0,
}
# And the most a whole deposit can plausibly AVERAGE. A median above this is the
# signature of a wrong unit rather than an extraordinary deposit.
MAX_MEDIAN = {
    "Au": 60.0, "Ag": 2000.0, "Pt": 30.0, "Pd": 30.0,
    "Cu": 35.0, "Pb": 40.0, "Zn": 45.0, "Ni": 25.0, "Co": 15.0, "Mo": 10.0,
    "Fe": 70.0, "Li2O": 8.0, "U3O8": 25.0, "P2O5": 40.0,
}
# Which mis-reads are actually possible depends on the element, and trying factors
# in a fixed order gets it wrong: 6,140 gold divided by either 1,000 or 10,000 lands
# somewhere plausible, but gold is never reported in per cent, so only the parts-per-
# billion reading is a real explanation. So the candidates follow the metal.
PRECIOUS = {"Au", "Ag", "Pt", "Pd", "Rh", "Ir", "Os", "Ru"}
_FACTORS_GPT = ((1_000.0, "parts per billion read as grams per tonne"),)
_FACTORS_PCT = ((10_000.0, "parts per million read as per cent"),
                (1e7, "parts per billion read as per cent"))


def _candidates(el):
    base = str(el or "").replace("Eq", "")
    return _FACTORS_GPT if base in PRECIOUS else _FACTORS_PCT


def _limits(el):
    base = str(el or "").replace("Eq", "")
    return (MAX_PLAUSIBLE.get(base, 100.0), MAX_MEDIAN.get(base))


def fix_values(el, values):
    """Repair a column of grades, row by row where that is what is wrong.

    A whole column in the wrong unit and a column with four bad rows in it look
    similar at the maximum and nothing alike at the median, and the difference
    matters enormously: Murray Brook's zinc is a correct per-cent column with a
    handful of parts-per-million rows mixed in, and dividing all of it by ten
    thousand turned a 2.27% deposit into a 0.0003% one. So the median decides which
    repair is called for — the unit is only wrong for the whole column if the
    typical value is impossible, not merely the extreme one.

    Returns ``(values, note, dropped)`` with values corrected in place."""
    v = [None if x is None else float(x) for x in values]
    pos = np.asarray([x for x in v if x is not None and np.isfinite(x) and x > 0], float)
    if not len(pos):
        return v, None, 0
    hard, soft = _limits(el)
    med = float(np.median(pos))
    if soft is not None and med > soft:
        f, why = unit_fix(el, pos)        # the whole column is in the wrong unit
        if f is None:
            return v, why, len(pos)
        if f != 1.0:
            return [None if x is None else x / f for x in v], why, 0
        return v, None, 0
    if float(pos.max()) <= hard:
        return v, None, 0
    # the column is fine; some rows in it are not
    good = pos[pos <= hard]
    ref = float(np.median(good)) if len(good) else med
    bad = fixed = dropped = 0
    for i, x in enumerate(v):
        if x is None or not np.isfinite(x) or x <= hard:
            continue
        bad += 1
        best = None
        for f, _name in _candidates(el):
            y = x / f
            if 0 < y <= hard and (ref <= 0 or y >= ref / 1000.0):
                if best is None or abs(np.log((y + 1e-9) / (ref + 1e-9))) < best[0]:
                    best = (abs(np.log((y + 1e-9) / (ref + 1e-9))), y)
        if best is not None:
            v[i] = best[1]; fixed += 1
        else:
            v[i] = None; dropped += 1
    if not bad:
        return v, None, 0
    note = (f"{el}: {bad} of {len(pos)} sample(s) were impossible for the unit "
            f"(above {hard:g})" + (f"; {fixed} converted" if fixed else "")
            + (f"; {dropped} dropped" if dropped else ""))
    return v, note, dropped


def unit_fix(el, values):
    """The correction a column of grades needs, if any.

    Returns ``(factor, why)`` — divide every value by ``factor`` — or ``(1.0, None)``
    when the column is already plausible, or ``(None, why)`` when nothing sensible
    fixes it and the element should be dropped rather than published."""
    v = np.asarray([x for x in values if x is not None and np.isfinite(x) and x > 0], float)
    if not len(v):
        return 1.0, None
    hard, soft = _limits(el)
    mx, med = float(v.max()), float(np.median(v))
    bad_max = mx > hard
    bad_med = soft is not None and med > soft
    if not bad_max and not bad_med:
        return 1.0, None
    for f, name in _candidates(el):
        m2, d2 = mx / f, med / f
        if m2 <= hard and (soft is None or d2 <= soft) and m2 > 0:
            # don't "fix" a column into implausible smallness either
            if soft is not None and d2 < soft / 1e6:
                continue
            return f, (f"{el}: {name} — max {mx:,.1f} corrected to {m2:,.3f}")
    return None, (f"{el}: grades impossible for the unit (max {mx:,.1f}, median "
                  f"{med:,.1f}) and no unit conversion repairs them")


def fix_intervals(by_el, verbose=False):
    """Repair, or drop, every element whose grades cannot be true.

    Mutates the interval dicts in place and returns ``(flags, dropped)``."""
    flags, dropped = [], []
    for el in list(by_el):
        xs = by_el[el]
        new, why, gone = fix_values(el, [x.get("grade") for x in xs])
        if gone and gone >= len(xs):
            dropped.append(el)
            flags.append({"level": "dropped", "element": el, "message": why})
            by_el.pop(el, None)
            continue
        if why:
            keep = []
            for x, g in zip(xs, new):
                if g is None:
                    continue
                x["grade"] = g
                keep.append(x)
            by_el[el] = keep
            flags.append({"level": "repaired", "element": el, "message": why})
            if verbose:
                print(f"  [sanity] {why}", flush=True)
    return flags, dropped


def top_cut_floor(values, stated, pct=90.0):
    """A grade cap may not sit below the data it caps.

    A cap under the median is not a cap, it is a wrong number lifted from a report —
    it flattens the whole model to that value and the deposit disappears. Where a
    stated cap falls below the 90th percentile of the composites, the percentile is
    used instead and the page says so."""
    v = np.asarray([x for x in values if x is not None and x > 0], float)
    if not len(v) or stated is None:
        return stated, None
    floor = float(np.percentile(v, pct))
    if float(stated) >= floor:
        return float(stated), None
    return floor, (f"stated grade cap {float(stated):g} sits below the 90th percentile "
                   f"of this project's own composites ({floor:,.2f}); capping there "
                   f"instead, because a cap under the data flattens the deposit")


def check_model(model):
    """A last look at a finished model, for anything that should stop it being

    presented as though it were sound. Returns a list of flags; an empty list means
    nothing implausible was found, not that the model is right."""
    out = []
    els = (model or {}).get("elements") or {}
    for el, E in els.items():
        st = E.get("stats") or {}
        hard, soft = _limits(el)
        unit = E.get("unit")
        if st.get("max") and float(st["max"]) > hard:
            out.append({"level": "bad", "element": el,
                        "message": f"{el} reaches {st['max']:,} {unit}, which is not possible"})
        if soft and st.get("p50") and float(st["p50"]) > soft:
            out.append({"level": "bad", "element": el,
                        "message": f"{el} averages {st['p50']:,} {unit} across the deposit"})
        B = E.get("blocks") or {}
        q = B.get("params") or {}
        if q.get("top_cut") and st.get("p50") and float(q["top_cut"]) < float(st["p50"]):
            out.append({"level": "bad", "element": el,
                        "message": f"{el} grade cap {q['top_cut']} is below the median "
                                   f"sample grade {st['p50']}"})
        d = B.get("density")
        if d and not (1.3 <= float(d) <= 6.5):
            out.append({"level": "bad", "element": el,
                        "message": f"bulk density {d} t/m3 is outside anything plausible"})
    return out
