"""How far off is MMP? Scored against every filed statement we hold.

A model of a deposit that has no published resource is only worth as much as the
track record of the method that made it. So this measures the method where the
answer is known: for every project whose filings carry a resource statement, it
reports MMP's tonnage, grade and contained metal **at that statement's own
cut-off**, and the split by confidence tier against the filed
measured/indicated/inferred.

The point is not to score well. It is to know the error, publish it, and let a
reader of a model with no resource statement know what the number is worth.

    PYTHONPATH=src python3 scripts/validate.py [site] [--json out.json]

Reads the built model pages (they carry the whole model), so it needs no rebuild.
Writes ``data/keep/mmp_validation.json``: the per-project rows and the headline
accuracy the model pages quote.
"""
import os
import re
import sys
import json
import glob
import math
import statistics

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_ROOT, "data", "keep", "mmp_validation.json")

TIERS = ("well drilled", "moderately drilled", "thinly drilled")
FILED = ("Measured", "Indicated", "Inferred")


def model_of(path):
    """The model JSON a built page carries."""
    s = open(path, encoding="utf-8").read()
    m = re.search(r'<script id="model"[^>]*>(.*?)</script>', s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1).replace("<\\/", "</"))
    except Exception:
        return None


def _is_value_cut(unit):
    return bool(re.search(r"/t", str(unit or "")))


# A filed statement smaller than this is far more likely to be a mis-read table
# than a real deposit, and one bad extraction swamps a median taken over ten
# projects. They are reported separately rather than silently dropped.
MIN_FILED_T = 100_000
# How much of the body a report describes a model must reach before its tonnage is
# compared like for like with the filed statement.
COVERED_MIN = 0.5


def published(m):
    """The filed statement to score against — the fullest one on offer.

    A stated cut-off is preferred, because reproducing it is the cleanest test, but
    a statement without one is still worth scoring: the grade it reports implies a
    cut-off, and asking "at the cut-off that reproduces their grade, do we find
    their tonnage?" isolates the volume question from the cut-off question."""
    best = None
    for r in (m.get("resources") or []):
        if not r.get("tonnes"):
            continue
        cats = r.get("categories") or {}
        score = ((2 if r.get("cutoff") is not None else 0)
                 + (1 if _is_value_cut(r.get("cutoff_unit")) else 0) + len(cats))
        if best is None or score > best[0]:
            best = (score, r)
    return best[1] if best else None


def _series(V, cutoff):
    """Interpolate a cut-off series at an arbitrary cut-off."""
    cuts = V["cuts"]
    if cutoff <= cuts[0]:
        return 0, 0.0
    if cutoff >= cuts[-1]:
        return len(cuts) - 1, 0.0
    i = max(j for j, c in enumerate(cuts) if c <= cutoff)
    if i + 1 >= len(cuts) or cuts[i + 1] == cuts[i]:
        return i, 0.0
    return i, (cutoff - cuts[i]) / (cuts[i + 1] - cuts[i])


def curve_score(m, p, filed=0.0):
    """Compare MMP's whole grade-tonnage curve against a filed sensitivity table.

    A single filed tonnage tests one point; a sensitivity table tests the shape. If
    MMP has the deposit's size roughly right but the wrong grade distribution, one
    point can look fine while the curve is wrong everywhere else — and the shape is
    what a reader is actually relying on when they move the cut-off slider.

    Returns the median MMP/filed tonnage ratio across the table's cut-offs."""
    sens = p.get("sensitivity") or {}
    rows = sens.get("rows") or []
    if len(rows) < 3 or not sens.get("cutoff_unit"):
        return None          # a cut-off series with no stated unit cannot be matched
    vc = _is_value_cut(sens.get("cutoff_unit"))
    cuts = [float(r["cutoff"]) for r in rows if r.get("cutoff") is not None]
    unit = str(sens.get("cutoff_unit") or "")
    ceiling = 500.0 if vc else (100.0 if unit in ("%", "g/t") else 10.0)
    if not cuts or max(cuts) > ceiling:
        return None          # a 207% cut-off is a misread column, not a sensitivity
    # Tie the table to the statement it sits beside. A report prints sensitivity for
    # one category as readily as for the whole resource — Murray Brook's is Measured
    # only, 15.83 Mt where the statement is 23.12 Mt — and scoring MMP's whole
    # deposit against one category measures nothing. Where the table disagrees with
    # the headline at the headline's own cut-off, it is describing something else.
    if p.get("cutoff") is not None and p.get("tonnes"):
        at = [r for r in rows if abs(float(r["cutoff"]) - float(p["cutoff"]))
              <= max(0.02 * float(p["cutoff"]), 1e-6)]
        if at:
            share = float(at[0]["tonnes"]) / float(p.get("mi_tonnes") or p["tonnes"])
            if not (0.7 <= share <= 1.4):
                return None
        elif min(cuts) > float(p["cutoff"]) or max(cuts) < float(p["cutoff"]):
            return None      # the table does not even cover the stated cut-off
    else:
        # no stated cut-off to tie the table to, so the tonnages have to do it: a
        # table running to a hundredth of the filed tonnage is describing a
        # different thing entirely
        tt = [float(r["tonnes"]) for r in rows if r.get("tonnes")]
        if not tt or min(tt) > filed * 3 or max(tt) < filed / 3:
            return None
    got = []
    for r in rows:
        mm = mmp_at(m, float(r["cutoff"]), vc)
        if mm and mm.get("tonnes") and r.get("tonnes"):
            got.append(mm["tonnes"] / float(r["tonnes"]))
    if len(got) < 3:
        return None
    return {"n": len(got), "median": round(statistics.median(got), 3),
            "unit": sens.get("cutoff_unit"),
            "span": [rows[0]["cutoff"], rows[-1]["cutoff"]],
            "ratios": [round(x, 3) for x in got]}


def filed_element(m, p):
    """Which of OUR elements a filed statement is actually about.

    Pak was being scored by comparing its tantalum model against a filed lithium
    grade — a measurement bug that produced a 0.003x "error" and nothing else. The
    statement names its metal when the extractor was confident; when it did not, the
    contained-metal unit narrows the field and the grade magnitude settles it, since
    a statement of 1.16 is not describing a 0.011 model."""
    els = m.get("elements") or {}
    named = p.get("element")
    if named and named in els:
        return named
    cu = p.get("contained_unit")
    gu = p.get("grade_unit")
    cats = p.get("categories") or {}
    fg = next((v.get("grade") for v in cats.values() if v.get("grade")), None)
    cands = []
    for el, E in els.items():
        unit = E.get("unit")
        if gu and ((gu == "%") != (unit == "%")):
            continue
        st = E.get("stats") or {}
        p50 = st.get("p50") or 0
        if not p50 or not fg:
            continue
        # the right metal is the one whose own samples live on the same scale as
        # the filed grade; an order of magnitude apart is a different metal
        cands.append((abs(math.log((float(fg) + 1e-9) / (float(p50) + 1e-9))), el))
    if not cands:
        return None
    cands.sort()
    return cands[0][1] if cands[0][0] < math.log(20) else None


def mmp_scoped(m, p):
    """The part of MMP's model the filed statement is actually about.

    A pit-constrained statement set against MMP's whole deposit compares a pit to a
    deposit. Where the filing says which it is and MMP has split the model the same
    way, the matching half is used and the comparison means something."""
    scope = p.get("scope")
    S = (m.get("by_metal") or {}).get("method")
    if not scope or scope == "both" or not S:
        return None, None
    key = {"in_pit": "open_pit", "underground": "underground"}.get(scope)
    row = S.get(key) if key else None
    if not row or not row.get("tonnes"):
        return None, None
    return row, scope


def at_their_grade(m, filed_grade, el):
    """MMP's tonnage at the cut-off where MMP's grade equals the filed grade.

    This is the test for the statements that print no cut-off, and the more
    searching test generally: it grants the filing its own grade and asks only
    whether MMP finds the same amount of rock carrying it."""
    B = m.get("by_metal") or {}
    row = (B.get("metals") or {}).get(el)
    if not row or not B.get("cuts") or not filed_grade:
        return None
    g, t = row.get("grade") or [], B["tonnes"]
    pairs = [(i, v) for i, v in enumerate(g) if v]
    if len(pairs) < 2:
        return None
    # grade rises with cut-off, so walk up until it passes theirs
    prev = None
    for i, v in pairs:
        if v >= filed_grade:
            if prev is None:
                return {"tonnes": t[i], "cut": B["cuts"][i], "mmp_grade": v, "clamped": "low"}
            (pi, pv) = prev
            f = (filed_grade - pv) / (v - pv) if v != pv else 0
            return {"tonnes": t[pi] + f * (t[i] - t[pi]),
                    "cut": B["cuts"][pi] + f * (B["cuts"][i] - B["cuts"][pi]),
                    "mmp_grade": filed_grade, "clamped": None}
        prev = (i, v)
    i, v = pairs[-1]
    return {"tonnes": t[i], "cut": B["cuts"][i], "mmp_grade": v, "clamped": "high"}


def mmp_at(m, cutoff, value_cut):
    """MMP's tonnage at a given cut-off, on the matching basis.

    Interpolates between the published cut-off series rather than snapping to the
    nearest, so a statement cut at C$23/t is not scored against our C$36/t row."""
    B = m.get("by_metal") or {}
    V = B.get("value") if value_cut else B
    if not V or not V.get("cuts"):
        return None
    cuts, tons = V["cuts"], V["tonnes"]
    if cutoff <= cuts[0]:
        t = tons[0]
    elif cutoff >= cuts[-1]:
        t = tons[-1]
    else:
        i = max(j for j, c in enumerate(cuts) if c <= cutoff)
        if i + 1 >= len(cuts) or cuts[i + 1] == cuts[i]:
            t = tons[i]
        else:
            f = (cutoff - cuts[i]) / (cuts[i + 1] - cuts[i])
            t = tons[i] + f * (tons[i + 1] - tons[i])
    out = {"tonnes": t, "basis": "value" if value_cut else "grade",
           "formula": V.get("formula"), "approximate": V.get("approximate"),
           "currency": V.get("currency")}
    K = V.get("classes")
    if K:
        split = {}
        for name in TIERS:
            series = K.get(name) or []
            if not series:
                continue
            if cutoff <= cuts[0]:
                split[name] = series[0]
            elif cutoff >= cuts[-1]:
                split[name] = series[-1]
            else:
                i = max(j for j, c in enumerate(cuts) if c <= cutoff)
                nxt = series[i + 1] if i + 1 < len(series) else series[i]
                f = ((cutoff - cuts[i]) / (cuts[i + 1] - cuts[i])
                     if i + 1 < len(cuts) and cuts[i + 1] != cuts[i] else 0)
                split[name] = series[i] + f * (nxt - series[i])
        out["tiers"] = split
    return out


def score(site="site"):
    rows = []
    for path in sorted(glob.glob(os.path.join(site, "models", "*.html"))):
        if os.path.basename(path).startswith("_"):
            continue
        m = model_of(path)
        if not m:
            continue
        p = published(m)
        if not p:
            continue
        filed = float(p.get("mi_tonnes") or p.get("tonnes") or 0)
        if not filed:
            continue
        vc = _is_value_cut(p.get("cutoff_unit"))
        got = mmp_at(m, float(p["cutoff"]), vc) if p.get("cutoff") is not None else None
        cats = p.get("categories") or {}
        # the grade-matched score, which needs no stated cut-off — against the
        # element the statement is actually about, not whichever we happened to model
        prim = filed_element(m, p) or m.get("primary")
        fg = None
        for k in ("M+I", "Total", "Measured", "Indicated", "Inferred"):
            v = (cats.get(k) or {}).get("grade")
            if v:
                fg = float(v); break
        gm = at_their_grade(m, fg, prim) if fg else None
        scoped, scope = mmp_scoped(m, p)
        curve = curve_score(m, p, filed)
        if not got and not gm:
            continue
        row = {"slug": m.get("slug"), "project": m.get("project"),
               "company": m.get("company"), "region": m.get("region"),
               "primary": m.get("primary"),
               "holes": (m.get("counts") or {}).get("holes"),
               "assayed_holes": (m.get("counts") or {}).get("assayed_holes"),
               "intervals": (m.get("counts") or {}).get("intervals"),
               "cutoff": p.get("cutoff"), "cutoff_unit": p.get("cutoff_unit"),
               "value_cut": vc, "basis": (got or {}).get("basis"),
               "formula_from_report": bool((got or {}).get("formula"))
               and not (got or {}).get("approximate"),
               "filed_tonnes": filed,
               "filed_grade": fg, "filed_grade_unit": (m.get("elements") or {}).get(prim, {}).get("unit"),
               "mmp_tonnes": round(got["tonnes"]) if got else None,
               "ratio": round(got["tonnes"] / filed, 3) if got and got["tonnes"] else None,
               "grade_matched_tonnes": round(gm["tonnes"]) if gm else None,
               "grade_matched_cut": round(gm["cut"], 4) if gm else None,
               "grade_matched_ratio": round(gm["tonnes"] / filed, 3) if gm and gm["tonnes"] else None,
               "grade_matched_clamped": (gm or {}).get("clamped"),
               "element_scored": prim, "element_named": p.get("element"),
               "scope": scope,
               "coverage_ratio": (m.get("coverage") or {}).get("volume_ratio"),
               "stated_extent": (m.get("coverage") or {}).get("stated_text"),
               "curve_n": (curve or {}).get("n"),
               "curve_median": (curve or {}).get("median"),
               "curve_span": (curve or {}).get("span"),
               "scoped_tonnes": round(scoped["tonnes"]) if scoped else None,
               "scoped_ratio": round(scoped["tonnes"] / filed, 3) if scoped else None,
               "suspect": filed < MIN_FILED_T,
               "filed_cats": {k: (cats.get(k) or {}).get("tonnes") for k in FILED
                              if cats.get(k)},
               "tiers": {k: round(v) for k, v in ((got or {}).get("tiers") or {}).items()}}
        rows.append(row)
    rows.sort(key=lambda r: abs(math.log(max(r.get("ratio") or r.get("grade_matched_ratio") or 1e-6, 1e-6))))
    return rows


def headline(rows):
    """What the model pages may honestly claim."""
    ok = [x for x in rows if not x["suspect"]]
    r = [x["ratio"] for x in ok if x.get("ratio")]
    gm = [x["grade_matched_ratio"] for x in ok
          if x.get("grade_matched_ratio") and not x.get("grade_matched_clamped")]
    if not r and not gm:
        return {}
    within = lambda v, f: sum(1 for x in v if 1 / f <= x <= f)       # noqa: E731
    val = [x for x in ok if x["value_cut"] and x.get("ratio")]
    out = {"n": len(r), "suspect_n": sum(1 for x in rows if x["suspect"]),
           "value_cut_n": len(val),
           "value_cut_median_ratio": round(statistics.median(x["ratio"] for x in val), 3)
           if val else None}
    if r:
        out.update({"median_ratio": round(statistics.median(r), 3),
                    "within_1_5x": within(r, 1.5), "within_2x": within(r, 2.0),
                    "within_3x": within(r, 3.0),
                    "median_abs_error_pct": round(statistics.median(abs(v - 1) * 100 for v in r), 1)})
    if gm:
        out.update({"grade_matched_n": len(gm),
                    "grade_matched_median": round(statistics.median(gm), 3),
                    "grade_matched_within_1_5x": within(gm, 1.5),
                    "grade_matched_within_2x": within(gm, 2.0)})
    # The same score restricted to the projects where we actually hold the deposit.
    # A filed statement rests on the company's whole drill database; a technical
    # report publishes a summary of it. Where the report's own description of the
    # body says the model covers a twentieth of it, the gap measures how much
    # drilling we are missing, not how well the estimator interpolates — and
    # averaging the two together measures neither. Both numbers are published,
    # because the first is what a reader of a thinly published project gets and
    # the second is what the method is worth.
    cov = [x for x in ok if x.get("grade_matched_ratio")
           and not x.get("grade_matched_clamped")
           and (x.get("coverage_ratio") or 0) >= COVERED_MIN]
    if cov:
        g2 = [x["grade_matched_ratio"] for x in cov]
        out.update({"covered_n": len(cov),
                    "covered_median": round(statistics.median(g2), 3),
                    "covered_within_1_5x": within(g2, 1.5),
                    "covered_within_2x": within(g2, 2.0),
                    "covered_min_coverage": COVERED_MIN})
    out["coverage_n"] = sum(1 for x in rows if x.get("coverage_ratio") is not None)
    return out


def tier_calibration(rows):
    """Do our confidence tiers land where the filed categories do?

    Scored only where the filing publishes measured, indicated and inferred
    separately. A tier that systematically overstates confidence is worse than no
    tier at all, so the number reported is the share of filed measured+indicated
    tonnage that MMP also places in its top two tiers."""
    got = []
    for x in rows:
        f = x.get("filed_cats") or {}
        t = x.get("tiers") or {}
        if not f.get("Measured") and not f.get("Indicated"):
            continue
        if not t:
            continue
        fmi = (f.get("Measured") or 0) + (f.get("Indicated") or 0)
        ftot = fmi + (f.get("Inferred") or 0)
        mmi = (t.get("well drilled") or 0) + (t.get("moderately drilled") or 0)
        mtot = sum(t.values())
        if not ftot or not mtot:
            continue
        got.append({"slug": x["slug"], "filed_mi_share": round(fmi / ftot, 3),
                    "mmp_top2_share": round(mmi / mtot, 3)})
    if not got:
        return {"n": 0}
    return {"n": len(got),
            "filed_mi_share_median": round(statistics.median(g["filed_mi_share"] for g in got), 3),
            "mmp_top2_share_median": round(statistics.median(g["mmp_top2_share"] for g in got), 3),
            "projects": got}


def stamp(site, head, cal):
    """Write the measured record back into every built page.

    A model page claims a track record, so the claim has to be the record this
    build actually scored — not the one the previous build left behind."""
    rec = {"n": head.get("n"), "median_ratio": head.get("median_ratio"),
           "within_1_5x": head.get("within_1_5x"), "within_2x": head.get("within_2x"),
           "grade_matched_n": head.get("grade_matched_n"),
           "grade_matched_median": head.get("grade_matched_median"),
           "covered_n": head.get("covered_n"),
           "covered_median": head.get("covered_median"),
           "covered_within_1_5x": head.get("covered_within_1_5x"),
           "tiers": ({"n": cal["n"], "filed_mi_share": cal.get("filed_mi_share_median"),
                      "mmp_top2_share": cal.get("mmp_top2_share_median")}
                     if cal.get("n") else None)}
    # Round-trip the model JSON rather than pattern-matching it. A regex for
    # "track_record":{...} cannot know where the object ends — the page is one long
    # line and a non-greedy match to the next "}}" runs straight past it into the
    # rest of the model. It did, on all 120 pages, before this was written properly.
    n = bad = 0
    for path in sorted(glob.glob(os.path.join(site, "models", "*.html"))):
        if os.path.basename(path).startswith("_"):
            continue
        s = open(path, encoding="utf-8").read()
        m = re.search(r'(<script id="model"[^>]*>)(.*?)(</script>)', s, re.S)
        if not m:
            continue
        try:
            model = json.loads(m.group(2).replace("<\\/", "</"))
        except Exception:
            bad += 1
            continue
        model["track_record"] = rec
        blob = json.dumps(model, separators=(",", ":")).replace("</", "<\\/")
        open(path, "w", encoding="utf-8").write(s[:m.start(2)] + blob + s[m.end(2):])
        n += 1
    print(f"stamped the record into {n} page(s)" + (f"; {bad} unreadable" if bad else ""))


def main():
    site = next((a for a in sys.argv[1:] if not a.startswith("-")), "site")
    rows = score(site)
    head = headline(rows)
    cal = tier_calibration(rows)
    out = {"generated": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds") + "Z",
           "headline": head, "tiers": cal, "projects": rows}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"scored {len(rows)} project(s) against a filed statement"
          + (f" ({head.get('suspect_n')} set aside as mis-read)" if head.get("suspect_n") else ""))
    if head.get("n"):
        print(f"  AT THEIR CUT-OFF   n={head['n']}  median {head['median_ratio']}x"
              f"  within 1.5x {head['within_1_5x']}  2x {head['within_2x']}  3x {head['within_3x']}")
        if head.get("value_cut_n"):
            print(f"    of those, value cut-offs: {head['value_cut_n']}, median {head['value_cut_median_ratio']}x")
    cv = [x for x in rows if x.get("curve_median") and not x["suspect"]]
    if cv:
        print(f"  WHOLE CURVE        n={len(cv)}  median "
              f"{statistics.median(x['curve_median'] for x in cv):.3f}x"
              f"  (MMP's grade-tonnage curve against filed sensitivity tables)")
    sc = [x for x in rows if x.get("scoped_ratio") and not x["suspect"]]
    if sc:
        print(f"  LIKE FOR LIKE      n={len(sc)}  median "
              f"{statistics.median(x['scoped_ratio'] for x in sc):.3f}x"
              f"  (filed statement's own pit/underground scope vs MMP's same half)")
    if head.get("grade_matched_n"):
        print(f"  AT THEIR GRADE     n={head['grade_matched_n']}  median {head['grade_matched_median']}x"
              f"  within 1.5x {head['grade_matched_within_1_5x']}  2x {head['grade_matched_within_2x']}")
    if head.get("covered_n"):
        print(f"  WHERE WE HOLD IT   n={head['covered_n']}  median {head['covered_median']}x"
              f"  within 1.5x {head['covered_within_1_5x']}  2x {head['covered_within_2x']}"
              f"  (>= {int(head['covered_min_coverage'] * 100)}% of the body the report describes)")
    print(f"  tier calibration           {cal.get('n', 0)} project(s)"
          + (f", filed M+I share {cal['filed_mi_share_median']} vs MMP top-two {cal['mmp_top2_share_median']}"
             if cal.get("n") else ""))
    def line(x):
        rr = x.get("ratio"); gg = x.get("grade_matched_ratio")
        return (f"    cut {rr if rr else '   –':>7}x  grade {gg if gg else '   –':>7}x"
                f"{'*' if x.get('grade_matched_clamped') else ' '} {x['slug'][:30]:30}"
                f" filed {x['filed_tonnes']/1e6:8.2f} Mt"
                + (f"  @{x['cutoff']} {x['cutoff_unit']}" if x.get("cutoff") is not None else "  (no cut-off stated)")
                + ("  SUSPECT" if x["suspect"] else ""))
    print("\n  closest:")
    for x in rows[:10]:
        print(line(x))
    print("  furthest:")
    for x in rows[-10:][::-1]:
        print(line(x))
    if "--stamp" in sys.argv:
        stamp(site, head, cal)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
