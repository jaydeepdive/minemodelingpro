#!/usr/bin/env python3
"""Which technical report belongs to which project.

Two failures put three reports on STLR's **Tower** (Timmins) onto Thunder Gold's
**Tower Mountain** (Thunder Bay), 700 km away, and showed four unrelated filed
resources on one project page:

1. `name_key("tower")` is a substring of `name_key("towermountain")`, and a
   substring match scored exactly the attach threshold — so it attached on the
   name alone, with no company match and no location agreement. Across the
   corpus that is wrong more often than right: of 792 located report pairs whose
   names contain one another, **435 (55%) are more than 150 km apart**.
   "polaris" and "newpolaris" are 10,943 km apart.

2. The 150 km guard that would have caught it never ran. It only read a report's
   centre when `crs.kind` was utm/latlon, and all four Tower reports are
   `local` — a mine grid. But `crs.kind` describes the COLLAR coordinates, not
   the property's position: **906 of 4,973 reports carry a centre this ignored,
   and none of them is implausible as a latitude and longitude.**

The scoring is reproduced here rather than imported because the real loop needs
a full `assemble()`; the thresholds and the bonuses are read from the module so
the test fails if they drift.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro import projects as P                         # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


def score(report_name, report_co, report_center, proj_name, proj_cos, plat, plon):
    """The attach score, as projects.py computes it for name/company/location."""
    rn, pn = P.name_key(report_name or ""), P.name_key(proj_name or "")
    s = 0
    if rn and pn and rn == pn:
        s += 6
    elif rn and pn and len(rn) > 4 and len(pn) > 4 and (rn in pn or pn in rn):
        s += 3
    if report_co and any(report_co.lower() == c.lower() for c in proj_cos):
        s += 3
    c = report_center
    if c and -90 <= c[0] <= 90 and -180 <= c[1] <= 180:
        d = P._km(c[0], c[1], plat, plon)
        if d <= P.REPORT_ATTACH_KM:
            s += 5
        elif d > 150:
            s -= 20
    return s


ATTACH = 6          # projects.py: `if best is not None and best_s >= 6`
TOWER_MTN = ("Tower Mountain", ["Thunder Gold"], 48.40, -89.50)
TIMMINS = [48.4934, -80.0028]

# ------------------------------------------------- the case that caused this
s = score("Tower", "STLR", TIMMINS, *TOWER_MTN)
check("a 700 km report does not attach on a substring name", s < ATTACH, f"score {s}")
check("and the distance actually counted against it", s < 0, f"score {s}")

s = score("Tower", "STLR", None, *TOWER_MTN)
check("a substring name with no other evidence does not attach", s < ATTACH, f"score {s}")

s = score("Tower Mountain", "TGOL", None, *TOWER_MTN)
check("the project's OWN report still attaches on an exact name", s >= ATTACH, f"score {s}")

# ------------------------------------------------- corroboration still works
s = score("Tower", "Thunder Gold", None, *TOWER_MTN)
check("a substring name plus the same company does attach", s >= ATTACH, f"score {s}")
s = score("Tower", "STLR", [48.41, -89.49], *TOWER_MTN)
check("a substring name plus the right location does attach", s >= ATTACH, f"score {s}")

# ------------------------------------------------- the centre is read at all
has = "rcenter = r.get(\"center\")" in open(
    os.path.join(ROOT, "src", "minemodelingpro", "projects.py")).read()
check("a report's centre is read without consulting crs.kind", has)

# and it really is lat/lon on mine-grid reports
d = os.path.join(ROOT, "data", "keep", "mmp_reports")
if os.path.isdir(d):
    import glob
    loc, bad = 0, 0
    for f in glob.glob(os.path.join(d, "*.json"))[:4000]:
        try:
            r = json.load(open(f))
        except Exception:
            continue
        c, k = r.get("center"), (r.get("crs") or {}).get("kind")
        if c and k not in ("utm", "latlon"):
            loc += 1
            if not (-90 <= c[0] <= 90 and -180 <= c[1] <= 180):
                bad += 1
    check("mine-grid reports' centres are plausible lat/lon", loc > 100 and bad == 0,
          f"{loc} such reports, {bad} implausible")
    print(f"       ({loc} reports carry a centre the old gate ignored)")

print(f"\n{len(OK)} passed, {len(BAD)} failed")
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
