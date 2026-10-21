"""The column table must be the rows it replaces, exactly.

Run: PYTHONPATH=src python3 scripts/test_ivtable.py
The last case reads real extracted reports from data/keep/mmp_reports/ when they
are there, and is skipped when they are not.
"""
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import numpy as np                                             # noqa: E402
from minemodelingpro.ivtable import Intervals, ByElement       # noqa: E402

PASS, FAIL = [], []
FIELDS = ("key", "hole", "from", "to", "el", "grade", "unit", "sub", "src")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""))


def rowset(rows):
    return [tuple(r[f] if not isinstance(r, dict) else r.get(f) for f in FIELDS) for r in rows]


def sample_rows(n=4000, seed=1):
    random.seed(seed)
    els = ["Au", "Ag", "Cu", "Zn", "Pb", "Co", "Ni", "As"]
    out = []
    for h in range(60):
        d = 0.0
        while d < n / 60.0:
            for el in random.sample(els, random.randint(1, len(els))):
                out.append({"key": "h%02d" % h, "hole": "DDH-%02d" % h,
                            "from": round(d, 2), "to": round(d + 1.0, 2), "el": el,
                            "grade": round(random.uniform(0, 9), 5),
                            "unit": "g/t" if el in ("Au", "Ag") else "%",
                            "sub": random.choice([0, 0, 0, 1]), "src": "s1"})
            d += 1.0
    return out


rows = sample_rows()
t = Intervals.from_rows(rows)

check("every row survives the move into columns", len(t) == len(rows))
check("every value survives it", rowset(t) == rowset(rows))
check("columns are an order of magnitude smaller",
      t.nbytes() < len(rows) * 60, "%d bytes for %d rows" % (t.nbytes(), len(rows)))

check("rows come back in the order they went in",
      [r["from"] for r in t][:50] == [r["from"] for r in rows][:50])
check("an element's rows are that element's rows, in order",
      rowset(t.rows_for("Au")) == rowset([r for r in rows if r["el"] == "Au"]))
check("asking twice returns the same list object", t.rows_for("Cu") is t.rows_for("Cu"))
check("a missing element is empty, not an error", t.rows_for("Xx") == [])

check("a row answers like the dictionary it replaces",
      t[0]["grade"] == rows[0]["grade"] and t[0].get("unit") == rows[0]["unit"]
      and t[0].get("nope", "d") == "d" and "grade" in t[0])
r0 = t[0]
r0["grade"] = 1.25
check("a row can be written to", r0["grade"] == 1.25)

check("None survives as None",
      Intervals.from_rows([{"hole": "a", "from": None, "to": 2.0, "el": "Au", "grade": None,
                            "key": "a", "unit": None, "sub": 0, "src": None}])[0]["grade"] is None)

# ---- the mapping the modeller uses
be = ByElement(t)
check("by_el lists the elements", sorted(be) == sorted({r["el"] for r in rows}))
check("by_el counts without building rows", be.count("Au") == sum(1 for r in rows if r["el"] == "Au"))
check("by_el knows which holes carry a metal",
      be.keys_of("Au") == {r["key"] for r in rows if r["el"] == "Au"})
check("by_el is true when there is data and false when there is not",
      bool(be) and not bool(ByElement(Intervals())))

# ---- the operations the pipeline performs
t2 = Intervals.from_rows(rows)
t2.restrict_keys({"h00", "h01"})
check("restricting to located holes keeps exactly those",
      rowset(t2) == rowset([r for r in rows if r["key"] in ("h00", "h01")]))

dup = rows + rows[:500] + [dict(rows[7], grade=9.99)]
t3 = Intervals.from_rows(dup)
t3.dedupe()
seen, want = set(), []
for r in dup:
    k = (r["key"], round(r["from"], 1), round(r["to"], 1), r["el"])
    if k in seen:
        continue
    seen.add(k)
    want.append(r)
check("dedupe keeps the first of each hole/interval/element, in order",
      rowset(t3) == rowset(want), "%d vs %d" % (len(t3), len(want)))

t4 = Intervals.from_rows(rows)
t4.set_grades("Au", [0.0] * t4.count("Au"))
check("grades can be written back per element",
      all(r["grade"] == 0.0 for r in t4.rows_for("Au"))
      and rowset(t4.rows_for("Cu")) == rowset([r for r in rows if r["el"] == "Cu"]))
keepmask = [i % 2 == 0 for i in range(t4.count("Ag"))]
ag_before = [r for r in rows if r["el"] == "Ag"]
t4.keep("Ag", keepmask)
check("rows can be dropped per element",
      rowset(t4.rows_for("Ag")) == rowset(ag_before[::2]))
t4.drop_element("Pb")
check("an element can be dropped whole", "Pb" not in t4.elements())

a, b = Intervals.from_rows(rows[:1000]), Intervals.from_rows(rows[1000:])
a.extend(b)
check("two tables join into one", rowset(a) == rowset(rows))
check("joining remaps the string tables",
      a.rows_for("Au")[0]["el"] == "Au" and len(set(a.els)) == len(a.els))

check("the deepest assay per hole", t.max_to_by_key()["h00"] ==
      max(r["to"] for r in rows if r["key"] == "h00"))
check("the mean of a metal", abs(t.mean("Cu") -
      float(np.mean([r["grade"] for r in rows if r["el"] == "Cu"]))) < 1e-9)
check("relabelling stamps the key and the source",
      Intervals.from_rows(rows).relabel(keyfn=lambda h: h.lower(), src="S9")[0]["key"]
      == rows[0]["hole"].lower())

# ---- storage
enc = json.dumps(t.to_json())
back = Intervals.from_json(json.loads(enc))
check("a table written and read is the same table", rowset(back) == rowset(t))
as_rows = json.dumps([{f: r[f] for f in FIELDS} for r in t])
check("columns are much smaller on disk than rows",
      len(enc) < len(as_rows) * 0.55, "%d vs %d bytes" % (len(enc), len(as_rows)))
check("the old shape still reads", rowset(Intervals.from_json(rows)) == rowset(rows))

# ---- against real extracted reports
files = sorted(glob.glob(os.path.join("data", "keep", "mmp_reports", "*.json")))
if files:
    random.seed(7)
    n, bad = 0, 0
    for f in random.sample(files, min(200, len(files))):
        try:
            iv = (json.load(open(f)) or {}).get("intervals") or []
        except Exception:
            continue
        if not iv:
            continue
        n += 1
        tt = Intervals.from_rows(iv)
        if rowset(tt) != [tuple(x.get(k) for k in FIELDS) for x in iv]:
            bad += 1
    check("real extracted reports round-trip unchanged (%d of them)" % n, bad == 0, bad)
else:
    print("  skip  real reports (data/keep/mmp_reports is not here)")

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
