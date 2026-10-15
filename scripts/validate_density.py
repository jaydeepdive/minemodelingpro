"""Score MMP's density model against every bulk density the corpus states.

The CIM guidelines allow density to come from "a formula based on grade or
mineralogy" on one condition: the formula must be validated against measured values.
This is that validation. Every technical report that states a bulk density is a
measurement MMP can be checked against — the report also says what kind of deposit
it is and what it grades, which is everything the formula takes.

    PYTHONPATH=src python3 scripts/validate_density.py
"""
import os, sys, json, glob, statistics as st
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from minemodelingpro import deposit_style as DS     # noqa: E402

rows = []
for fn in sorted(glob.glob(os.path.join("data", "keep", "mmp_reports", "*.json"))):
    try:
        r = json.load(open(fn))
    except Exception:
        continue
    mp = r.get("model_params") or {}
    stated = mp.get("density") or r.get("density")
    style = mp.get("deposit_style")
    if not stated or not (1.2 <= float(stated) <= 7.0) or not style:
        continue
    # grades the report itself reports, for the grade-linked part of the formula
    grades = {}
    res = r.get("resource") or {}
    el = res.get("element")
    if el and (res.get("categories") or {}):
        g = next((v.get("grade") for v in res["categories"].values() if v.get("grade")), None)
        if g:
            grades[el] = float(g)
    pred, why = DS.density_for(style, grades, stated=None)
    rows.append({"id": r.get("id"), "company": r.get("company"), "style": style,
                 "stated": float(stated), "pred": pred, "err": pred - float(stated),
                 "grades": grades})

if not rows:
    print("no reports state both a density and a deposit style")
    raise SystemExit(0)

err = [abs(x["err"]) for x in rows]
sign = [x["err"] for x in rows]
print(f"{len(rows)} reports state a bulk density and a deposit style\n")
print(f"  median absolute error   {st.median(err):.3f} t/m3")
print(f"  median signed error     {st.median(sign):+.3f} t/m3  (positive = MMP too heavy)")
print(f"  within 0.15 t/m3        {sum(1 for e in err if e <= 0.15)}/{len(rows)}")
print(f"  within 0.30 t/m3        {sum(1 for e in err if e <= 0.30)}/{len(rows)}")
print(f"\n{'style':11} {'n':>3} {'stated med':>11} {'MMP med':>9} {'median err':>11}")
by = {}
for x in rows:
    by.setdefault(x["style"], []).append(x)
for s, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
    print(f"{s:11} {len(xs):3} {st.median([x['stated'] for x in xs]):11.2f} "
          f"{st.median([x['pred'] for x in xs]):9.2f} {st.median([x['err'] for x in xs]):+11.3f}")
print("\nworst:")
for x in sorted(rows, key=lambda z: -abs(z["err"]))[:10]:
    print(f"  {str(x['company'])[:30]:30} {x['style']:10} stated {x['stated']:5.2f}  MMP {x['pred']:5.2f}  {x['err']:+.2f}")
out = os.path.join("data", "keep", "mmp_density_validation.json")
json.dump({"n": len(rows), "median_abs_error": round(st.median(err), 3),
           "median_signed_error": round(st.median(sign), 3),
           "by_style": {s: {"n": len(xs), "stated_median": round(st.median([x['stated'] for x in xs]), 2),
                            "mmp_median": round(st.median([x['pred'] for x in xs]), 2),
                            "median_error": round(st.median([x['err'] for x in xs]), 3)}
                        for s, xs in by.items()}}, open(out, "w"), indent=1)
print("\nwrote", out)
