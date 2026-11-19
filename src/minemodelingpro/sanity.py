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
import os

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

# A ceiling whose job is to say "this cannot be a grade at all" has to be set
# where the rock actually stops, and for a precious metal reported in g/t that is
# pure metal: 1,000,000 g/t. The tables above are NOT that — they are what a whole
# column, or a deposit's average, plausibly reaches, which is the right test for a
# unit mistake and the wrong one for a single sample. Read as a per-sample limit
# they destroyed real data: Langis assays native silver at 89,125 g/t in
# LM-26-377, with the laboratory escalating its own method to Ag-CON01
# (concentrate grade) to measure it — 8.9% silver, which is ordinary bonanza for
# the Cobalt camp — and a 30,000 g/t ceiling divided it by a thousand and
# published 89.1 g/t. For a per-cent column the two questions already have the
# same answer, because 100% IS the rock, so nothing about those columns changes.
PURE_GPT = 1_000_000.0
# And an assay is an average over an interval of ROCK, half a metre or more of it.
# Native silver and visible gold occur as masses a centimetre or two across, so a
# sample can be a few per cent metal and occasionally more; it cannot be mostly
# metal, because then the interval is not rock. Half of pure is the line, and it is
# the loosest line that still refuses what the corpus actually contains — 869,390
# g/t of silver (87%) and 740,627 g/t of palladium, both of which are export
# artefacts rather than assays.
SAMPLE_FRACTION = 0.5


def impossible(el):
    """The grade above which a sample cannot be this element at all, in MMP's unit.

    Half of pure metal for the precious metals; for everything else the per-cent
    ceiling above, which is already a statement about rock rather than about
    deposits (72% iron is magnetite, and nothing is more than 100% of itself)."""
    base = str(el or "").replace("Eq", "")
    if base in PRECIOUS:
        return PURE_GPT * SAMPLE_FRACTION
    return MAX_PLAUSIBLE.get(base, 100.0)


# A numbering is detected by the SHAPE of the group, not by how big its values are.
NUMBERING_MIN = 5          # enough of them to be a sequence rather than a coincidence
NUMBERING_SPREAD = 1.05    # packed into a few parts in a hundred
NUMBERING_LIFT = 100.0     # and detached from the rock around them


def _numbering(vals, median):
    """Whether a column's high tail is a numbering rather than a population of grades.

    An export that puts a sample or certificate number in the grade column leaves a
    signature nothing geological produces: dozens of values packed into a span of a
    few parts in a thousand, orders of magnitude above the rock around them. Jojay's
    gold arrives as 159643, 159645, 159646, 159647, 159648, 159650 … — 42 values
    inside a span of 54, in a deposit whose median assay is 3 g/t. Gold Springs' is
    68 values between 41,436 and 41,678: a span of 242 sitting squarely inside the
    range where Brucejack's real 41,582 g/t gold lives, so no ceiling can separate
    those two and only the shape of the group can.

    A real bonanza tail is continuous and spreads over orders of magnitude — Langis
    runs 190, 880, 3,270, 8,050, 54,652, 89,125 — and this refuses to call that a
    numbering. Every group it accepts on the corpus is an identifier column; every
    bonanza population it is shown is refused."""
    v = sorted(float(x) for x in vals if x is not None)
    if len(v) < NUMBERING_MIN or v[0] <= 0:
        return False
    # a measurement carries decimals; a serial number does not
    if not all(float(x).is_integer() for x in v):
        return False
    # spread over a real range means it behaves like a population of grades
    if v[-1] / v[0] > NUMBERING_SPREAD:
        return False
    # and it has to be detached from the rock around it, or it is just high grade
    if median and v[0] / float(median) < NUMBERING_LIFT:
        return False
    return True


def _candidates(el):
    base = str(el or "").replace("Eq", "")
    return _FACTORS_GPT if base in PRECIOUS else _FACTORS_PCT


def _limits(el):
    base = str(el or "").replace("Eq", "")
    return (MAX_PLAUSIBLE.get(base, 100.0), MAX_MEDIAN.get(base))


def _row_candidates(el):
    """What a repair may try on ONE row of an otherwise sound column.

    For a per-cent column a stray parts-per-million row is a real and documented
    event — Murray Brook's zinc is a correct per-cent column with a handful of ppm
    rows mixed into it — so the conversion is well founded and is kept.

    For a g/t column it is not. A laboratory does not change units for one sample
    in a batch, while an export DOES put a certificate or sample number in the
    grade column: Eagle Rock's platinum arrives as 1030301, 1030302, 1030303,
    1030304 — four consecutive integers. Dividing those by a thousand turns an
    identifier into 1,030 g/t of platinum, and Glencore Bucke's 5,013,070 into
    5,013 g/t of silver. That is worse than dropping them, because a dropped
    sample is visible in the flags and an invented one is not. So a precious-metal
    sample is kept, or dropped and named — never quietly rescaled."""
    base = str(el or "").replace("Eq", "")
    return () if base in PRECIOUS else _FACTORS_PCT


def _g(x):
    """A grade a reader can check against a certificate."""
    if x is None:
        return "dropped"
    x = float(x)
    return format(x, ",.0f") if abs(x) >= 1000 else format(x, "g")


def _sample_note(marks, limit=4):
    """Name the samples a repair touched: which hole, which depth, what changed.

    "3 converted" tells a geologist that something happened to his best assays and
    nothing about which ones, so he cannot go and read the certificate. That is how
    this fault survived: the repair was reported, honestly, in a form nobody could
    act on. The biggest values are named first, because those are the ones a reader
    came looking for."""
    if not marks:
        return ""
    ms = sorted(marks, key=lambda m: -(m[2] or 0))
    bits = []
    for hole, frm, before, after, kind in ms[:limit]:
        where = ""
        if hole:
            where = str(hole)
            if frm is not None:
                try:
                    where += " at %gm" % float(frm)
                except (TypeError, ValueError):
                    pass
            where += " "
        if kind == "dropped":
            bits.append("%s%s dropped" % (where, _g(before)))
        elif kind == "converted":
            bits.append("%s%s read as %s" % (where, _g(before), _g(after)))
        else:
            bits.append("%s%s" % (where, _g(before)))
    more = len(ms) - len(bits)
    return " — " + "; ".join(bits) + (" and %d more" % more if more > 0 else "")


def _locate_rows(xs):
    """`(hole, from)` per row, for the row-shaped caller."""
    def at(i):
        x = xs[i]
        return (x.get("hole"), x.get("from"))
    return at


def _locate_table(table, el):
    """`(hole, from)` per row of this element, for the column-shaped caller.

    Resolved BEFORE any grade is written or any row deleted, because `keep()`
    renumbers the table underneath us."""
    w = table.index().get(el)

    def at(i):
        if w is None or i >= len(w):
            return (None, None)
        r = table.row(w[i])
        return (r["hole"], r["from"])
    return at


def _named(marks, locate):
    """Turn `(index, before, after, kind)` into `(hole, from, before, after, kind)`."""
    out = []
    for i, before, after, kind in marks:
        hole, frm = (None, None)
        if locate is not None:
            try:
                hole, frm = locate(i)
            except Exception:
                hole, frm = (None, None)
        out.append((hole, frm, before, after, kind))
    return out


def fix_values(el, values, locate=None):
    """Repair a column of grades, row by row where that is what is wrong.

    A whole column in the wrong unit and a column with four bad rows in it look
    similar at the maximum and nothing alike at the median, and the difference
    matters enormously: Murray Brook's zinc is a correct per-cent column with a
    handful of parts-per-million rows mixed in, and dividing all of it by ten
    thousand turned a 2.27% deposit into a 0.0003% one. So the median decides which
    repair is called for — the unit is only wrong for the whole column if the
    typical value is impossible, not merely the extreme one.

    A SINGLE row is a different question again, and the one this got wrong. The
    ceiling that decides a whole column is "what a deposit of this metal reaches";
    the ceiling that throws away one sample has to be "what the rock can be", which
    is `impossible()` — pure metal. Between the two sits the best assay on the
    property, and it is reported rather than touched, because whether 89,125 g/t of
    silver is bonanza or a typing error is a question for the certificate and the
    geologist reading it, not for this module.

    Returns ``(values, note, dropped, marks)`` with values corrected in place;
    `marks` is ``(index, before, after, kind)`` per sample this looked at, where
    kind is "converted", "dropped" or "kept". `locate(i)` -> ``(hole, from)`` lets
    the note name the samples it touched; without it they are named by value
    alone, which is still enough to find them in a certificate."""
    v = [None if x is None else float(x) for x in values]
    pos = np.asarray([x for x in v if x is not None and np.isfinite(x) and x > 0], float)
    if not len(pos):
        return v, None, 0, []
    hard, soft = _limits(el)
    # MMP_ASSAY=old restores the rule this replaced — a per-sample ceiling of
    # 1,500 g/t gold and 30,000 g/t silver, with anything above it divided by a
    # thousand — so the two can still be measured against each other.
    legacy = os.environ.get("MMP_ASSAY", "").lower() in ("old", "ceiling")
    imp = hard if legacy else impossible(el)
    med = float(np.median(pos))
    if soft is not None and med > soft:
        f, why = unit_fix(el, pos)        # the whole column is in the wrong unit
        if f is None:
            return v, why, len(pos), []
        if f != 1.0:
            return [None if x is None else x / f for x in v], why, 0, []
        return v, None, 0, []
    if float(pos.max()) <= imp:
        # Nothing here is impossible for the unit. It is either the best rock on
        # the property or a column of numbers that is not grades at all, and the
        # shape of the group is what tells those apart.
        over = [(i, x) for i, x in enumerate(v)
                if x is not None and np.isfinite(x) and x > hard]
        if not over:
            return v, None, 0, []
        if _numbering([x for _, x in over], med):
            marks = [(i, x, None, "dropped") for i, x in over]
            for i, _x in over:
                v[i] = None
            note = (f"{el}: {len(over)} value(s) in the grade column are a "
                    f"numbering, not assays — packed within a few parts in a "
                    f"hundred of each other and far above the surrounding rock, "
                    f"which is what a sample or certificate number looks like; "
                    f"dropped" + _sample_note(_named(marks, locate)))
            return v, note, len(over), marks
        kept = [(i, x, x, "kept") for i, x in over]
        note = (f"{el}: {len(kept)} sample(s) assay above what a deposit of this "
                f"metal usually reaches ({hard:g}); left exactly as measured, "
                f"worth checking against the certificate"
                + _sample_note(_named(kept, locate)))
        return v, note, 0, kept
    # the column is fine; some rows in it cannot be a grade of this element at all
    good = pos[pos <= hard]
    ref = float(np.median(good)) if len(good) else med
    bad = fixed = dropped = 0
    marks = []
    for i, x in enumerate(v):
        if x is None or not np.isfinite(x) or x <= imp:
            continue
        bad += 1
        best = None
        for f, _name in (_candidates(el) if legacy else _row_candidates(el)):
            y = x / f
            if 0 < y <= hard and (ref <= 0 or y >= ref / 1000.0):
                if best is None or abs(np.log((y + 1e-9) / (ref + 1e-9))) < best[0]:
                    best = (abs(np.log((y + 1e-9) / (ref + 1e-9))), y)
        if best is not None:
            marks.append((i, x, best[1], "converted")); v[i] = best[1]; fixed += 1
        else:
            marks.append((i, x, None, "dropped")); v[i] = None; dropped += 1
    if not bad:
        return v, None, 0, []
    note = (f"{el}: {bad} of {len(pos)} sample(s) cannot be a grade of this element "
            f"at all (above {imp:,.0f})"
            + (f"; {fixed} converted" if fixed else "")
            + (f"; {dropped} dropped" if dropped else "")
            + _sample_note(_named(marks, locate)))
    return v, note, dropped, marks


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
        new, why, gone, marks = fix_values(el, [x.get("grade") for x in xs],
                                           locate=_locate_rows(xs))
        if gone and gone >= len(xs):
            dropped.append(el)
            flags.append({"level": "dropped", "element": el, "message": why})
            by_el.pop(el, None)
            continue
        if why:
            level = "check" if all(m[3] == "kept" for m in marks) and marks else "repaired"
            keep = []
            for x, g in zip(xs, new):
                if g is None:
                    continue
                x["grade"] = g
                keep.append(x)
            by_el[el] = keep
            flags.append({"level": level, "element": el, "message": why})
            if verbose:
                print(f"  [sanity] {why}", flush=True)
    return flags, dropped


# A laboratory reports a sample below its detection limit as "<10", and an export
# turns that into the number 10 — or 5, half the limit. Those are not measurements
# of anything; they say "we looked and found none". Left as grades they make a
# metal look pervasive at a constant low value, which is how a silver property
# came to report 12.9 g/t gallium over 91% of its tonnage and 7.4 Moz of contained
# metal that does not exist.
DL_SHARE = 0.25          # a quarter of the assays sharing one exact value
DL_MIN_N = 40            # and enough of them for that to mean something


def detection_fill(values):
    """The value a non-detect was written as, if one is obvious.

    The signature is a single number repeated across a large share of the
    samples AND sitting at the very bottom of the distribution. A real grade
    population is continuous; a detection limit is a spike at the floor. Returns
    (value, count) or (None, 0) — deliberately conservative, because zeroing a
    real population would be worse than leaving a fake one.
    """
    import collections
    v = [float(x) for x in values if x is not None and x == x and float(x) > 0]
    if len(v) < DL_MIN_N:
        return None, 0
    counts = collections.Counter(v)
    val, n = counts.most_common(1)[0]
    if n < DL_SHARE * len(v):
        return None, 0
    # it must be at the bottom: nothing meaningfully below it
    below = sum(1 for x in v if x < val * 0.999)
    if below > 0.02 * len(v):
        return None, 0
    # and it must not BE the population — an element that is genuinely one value
    # everywhere tells us nothing either way, so leave it alone
    if n >= 0.98 * len(v):
        return None, 0
    return val, n


def strip_detection_fill(table, verbose=False):
    """Turn non-detects back into non-detections, and say so.

    They become zero rather than half the limit: MMP's question is whether a
    metal is ore here, and half a detection limit answers it with a number the
    laboratory never measured."""
    flags = []
    for el in list(table.elements()):
        g = table.grades(el)
        val, n = detection_fill(g)
        if not val:
            continue
        import numpy as _np
        arr = _np.asarray(g, float)
        hit = _np.isfinite(arr) & (_np.abs(arr - val) < val * 1e-9)
        if not hit.any():
            continue
        arr[hit] = 0.0
        table.set_grades(el, [None if x != x else float(x) for x in arr])
        share = 100.0 * int(hit.sum()) / max(1, int(_np.isfinite(_np.asarray(g, float)).sum()))
        msg = ("%s: %d of %d assays (%.0f%%) are exactly %g — a detection limit, "
               "not a measurement. Read as not detected, so they no longer carry "
               "grade into the estimate." % (el, int(hit.sum()),
                                             int(_np.isfinite(_np.asarray(g, float)).sum()),
                                             share, val))
        flags.append({"level": "repaired", "element": el, "message": msg})
        if verbose:
            print("  [sanity] " + msg, flush=True)
    return flags


def fix_table(table, verbose=False):
    """`fix_intervals` over a column table — the same test, element by element.

    The decision is `fix_values`, unchanged and shared with the row version; only
    the reading and writing of the grades differs, because here they are already
    a numpy column."""
    flags, dropped = [], []
    flags.extend(strip_detection_fill(table, verbose=verbose))
    for el in list(table.elements()):
        g = table.grades(el)
        vals = [None if v != v else float(v) for v in g]
        # the locator is built now, because set_grades/keep renumber the table
        new, why, gone, marks = fix_values(el, vals,
                                           locate=_locate_table(table, el))
        if gone and gone >= len(vals):
            dropped.append(el)
            flags.append({"level": "dropped", "element": el, "message": why})
            table.drop_element(el)
            continue
        if why:
            level = "check" if marks and all(m[3] == "kept" for m in marks) else "repaired"
            table.set_grades(el, new)
            table.keep(el, [v is not None for v in new])
            flags.append({"level": level, "element": el, "message": why})
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
        # the maximum is ONE sample, so it answers to what rock can be, not to
        # what a deposit averages — the distinction that cost Langis its three
        # best silver assays
        if st.get("max") and float(st["max"]) > impossible(el):
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
