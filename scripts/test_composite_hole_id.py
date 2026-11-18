#!/usr/bin/env python3
"""A composite must know which hole it came from.

`_composite_el` ran `for hi, ivs in per_hole.items()` and then, inside the
segment loop, reused `hi` for the last composite bin a segment touches:

    hi = min(len(mids), int(np.searchsorted(edges, b, "left")))

`comp_h` is appended AFTER that loop, so every hole reaching it carried a BIN
NUMBER as its hole id. `comp_h` is what tells the estimator which samples are
from the same hole, so `max_per_hole` was limiting by composite bin rather than
by hole, and anything else keyed on `hid` was reading a number that means
nothing at all.

It stayed invisible because nothing downstream checks that a hole id IS a hole
id — a wrong-but-plausible integer estimates fine, just not the way the
parameters say. It surfaced only when a filter on hole ids removed the wrong
holes and moved a client's tonnage 47%.

    PYTHONPATH=src python3 scripts/test_composite_hole_id.py
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from minemodelingpro import model3d as M3                        # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + str(detail)) if detail and not cond else ""))


def hole(i, depth=200.0):
    return {"id": f"DDH-{i:03d}", "key": f"ddh{i:03d}", "x": i * 30.0, "y": 0.0,
            "z": 300.0, "az": 0.0, "dip": -90.0, "depth": depth}


H = [hole(i) for i in range(6)]
# long holes with a mineralised run partway down: the ordinary case, and the
# one that goes through the branch where `hi` was being clobbered
per_hole = {}
for i in range(6):
    start = 40.0 + i * 7.0
    per_hole[i] = [{"from": start, "to": start + 6.0, "grade": 12.0 + i}]

segs, cp, cg, ch, cent = M3._composite_el(per_hole, H, 5.0, gap_fill=True)

check("every hole composited", len(ch) == 6, f"{len(ch)} of 6")
ok_ids = all(len(np.unique(c)) == 1 and int(c[0]) == i for i, c in enumerate(ch))
check("each hole's composites carry THAT hole's index", ok_ids,
      [int(c[0]) for c in ch])

hid = np.concatenate(ch)
check("hole ids stay inside the hole table",
      int(hid.max()) < len(H) and int(hid.min()) >= 0,
      f"{int(hid.min())}..{int(hid.max())} for {len(H)} holes")
check("every hole is represented once ids are concatenated",
      sorted(np.unique(hid).tolist()) == list(range(6)),
      np.unique(hid).tolist())

# the bin index that used to leak through is the number of composites in a
# hole — with 200 m holes at 5 m that is ~40, far outside the hole table
check("no composite carries a bin-sized id", int(hid.max()) < len(H),
      f"max id {int(hid.max())}")

# and the thing that actually depends on it: declustering. `hid_cols` is the
# hole id of each of a block's k nearest samples, in distance order.
hid_cols = np.array([[0, 0, 0, 0, 1, 1, 2, 0],
                     [3, 3, 4, 3, 3, 5, 3, 4]], np.int64)
near = np.ones(hid_cols.shape, bool)
kept = M3._limit_per_hole(hid_cols, near, 2)
per = [np.bincount(hid_cols[r][kept[r]], minlength=6).max()
       for r in range(hid_cols.shape[0])]
check("max_per_hole keeps at most N samples from one hole", max(per) <= 2, per)
check("and keeps the CLOSEST ones", bool(kept[0, 0] and kept[0, 1]
                                         and not kept[0, 2]), kept[0].tolist())
check("while other holes are untouched", bool(kept[0, 4] and kept[0, 6]),
      kept[0].tolist())

# a hole whose assays do not start at the collar still gets its own id
per_hole2 = {0: [{"from": 150.0, "to": 158.0, "grade": 20.0}],
             3: [{"from": 10.0, "to": 12.0, "grade": 5.0}]}
_s, _p, _g, ch2, _c = M3._composite_el(per_hole2, H, 5.0, gap_fill=True)
check("a deep intercept keeps its hole's id",
      int(ch2[0][0]) == 0 and int(ch2[1][0]) == 3,
      [int(c[0]) for c in ch2])

print(f"\n{len(OK)} passed, {len(BAD)} failed")
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
