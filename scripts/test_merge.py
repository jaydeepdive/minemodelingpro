#!/usr/bin/env python3
"""Merging a public model into a client's project, on the client's terms.

Jordan: "data uploaded from the private source should take precedence. but if
there is collar details but not assay details, then we need to defer to data
from the other sources."

      collar      the client's, always
      assays      per hole: the client's if the client assayed that hole at all;
                  otherwise the other source's, for that hole only
      a hole only the public source has: both

What this replaced simply appended every public interval, which double-counted
every hole both sides assayed — the same metre of core twice, from two sources,
in one estimate — and compared hole ids RAW, so `LM-26-101` and `LM26101` came
in as two holes standing in the same place.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))
from minemodelingpro import ivtable as IVT                  # noqa: E402
from studio.worker import _fold_public                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({extra})" if extra else ""))


def hole(hid, z=100.0):
    return {"id": hid, "key": hid, "lat": 47.5, "lon": -79.5, "z": z, "_ok": True}


def iv(hid, frm, to, el, g, unit="g/t"):
    return {"hole": hid, "key": hid, "from": frm, "to": to, "el": el,
            "grade": g, "unit": unit, "sub": 0}


def build():
    """A client project with three kinds of hole, and a public one overlapping."""
    mine = {
        # A: the client assayed it — the public source must be ignored for it
        "holes": {"LM-26-001": hole("LM-26-001"),
                  # B: collar only, no assays — the gap the other source fills
                  "LM-26-002": hole("LM-26-002"),
                  # C: written without dashes, the same hole as the public D
                  "LM26003": hole("LM26003")},
        "intervals": IVT.Intervals.from_rows([
            iv("LM-26-001", 10.0, 11.0, "Ag", 100.0),
            iv("LM-26-001", 11.0, 12.0, "Ag", 110.0)]),
        "sources": [{"kind": "imported data", "id": "import:1"}],
    }
    theirs = {
        "holes": {"LM-26-001": hole("LM-26-001", z=999.0),      # same hole, worse z
                  "LM-26-002": hole("LM-26-002", z=999.0),
                  "LM-26-003": hole("LM-26-003", z=999.0),      # == LM26003
                  "LM-26-900": hole("LM-26-900")},              # only they have it
        "intervals": IVT.Intervals.from_rows([
            iv("LM-26-001", 10.0, 11.0, "Ag", 55.0),            # must be dropped
            iv("LM-26-002", 20.0, 21.0, "Ag", 300.0),           # must be kept
            iv("LM-26-002", 21.0, 22.0, "Ag", 310.0),           # must be kept
            iv("LM-26-003", 30.0, 31.0, "Ag", 400.0),           # same as LM26003: dropped
            iv("LM-26-900", 40.0, 41.0, "Ag", 500.0)]),         # new hole: kept
        "sources": [{"kind": "news release", "id": "news:1"}],
    }
    return mine, theirs


print("\nthe client's collar always wins")
mine, theirs = build()
r = _fold_public(mine, theirs)
check("a hole both sides have keeps the client's collar",
      mine["holes"]["LM-26-001"]["z"] == 100.0, mine["holes"]["LM-26-001"]["z"])
check("a hole written differently is recognised as the same hole",
      all(h["z"] == 100.0 for k, h in mine["holes"].items()
          if k in ("LM26003", "LM-26-003")),
      {k: v["z"] for k, v in mine["holes"].items()})
check("a hole only the public source has is taken",
      "LM-26-900" in mine["holes"])
check("and the count says so", r["collars_added"] == 1, r)

print("\nassays: the client's hole keeps the client's assays")
rows = [mine["intervals"].row(j).as_dict() for j in range(len(mine["intervals"]))]
by = {}
for x in rows:
    by.setdefault(x["hole"], []).append(x["grade"])
check("the hole the client assayed shows only the client's grades",
      sorted(by.get("LM-26-001") or []) == [100.0, 110.0], by.get("LM-26-001"))
check("the public grade for that hole is gone",
      55.0 not in (by.get("LM-26-001") or []), by.get("LM-26-001"))
# LM26003 here is the public LM-26-003 — but the client has only a COLLAR for
# it, so the rule says fill it. (An earlier version of this test asserted the
# opposite, having confused "same hole" with "same hole, already assayed".)
check("a collar-only hole recognised under another spelling IS filled",
      400.0 in [g for gs in by.values() for g in gs], by)
check("a hole both sides assayed is counted as kept private",
      r["holes_kept_private"] == 1, r)

print("\nassays: a collar with no grades takes them from the other source")
check("the gap hole is filled", sorted(by.get("LM-26-002") or []) == [300.0, 310.0],
      by.get("LM-26-002"))
check("the fill is reported", r["holes_filled"] == 2, r)
check("and the hole is named, so it can be checked",
      "LM-26-002" in (r.get("filled_examples") or []), r.get("filled_examples"))
check("a hole only the public source has brings its assays",
      by.get("LM-26-900") == [500.0], by.get("LM-26-900"))

print("\nnothing is double-counted")
check("no interval appears twice", len(rows) == len({(x["hole"], x["from"], x["to"], x["el"])
                                                     for x in rows}), len(rows))
check("the total is the client's plus only what was missing",
      len(rows) == 2 + 2 + 1 + 1, len(rows))
check("the added count matches", r["intervals_added"] == 4, r)

print("\nthe degenerate cases")
m2, t2 = build()
t2["intervals"] = IVT.Intervals.from_rows([])
r2 = _fold_public(m2, t2)
check("a public source with no assays still contributes its collars",
      r2["collars_added"] == 1 and r2["intervals_added"] == 0, r2)
# the join itself, which is what every one of these rests on
from minemodelingpro import holeid as _H                     # noqa: E402
check("separators are not what makes a hole", _H.same("LM-26-003", "LM26003"))
check("leading zeros are not either", _H.same("LM-26-3", "LM-26-003"))
check("and the documented case finally holds", _H.same("DDH-26-001", "ddh26001"))
check("two genuinely different holes stay different",
      not _H.same("LM-26-001", "LM-26-002"))
m3, t3 = build()
m3["intervals"] = IVT.Intervals.from_rows([])
r3 = _fold_public(m3, t3)
check("a client with no assays at all takes everything offered",
      r3["intervals_added"] == 5, r3)
check("and every one of its holes counts as filled", r3["holes_filled"] == 3, r3)
# The cost of the loose rule, stated rather than hidden: an id whose separators
# fall differently can collide. Both spellings have to be wrong in the same way
# for it to bite, and the rule only ever LENDS assays to a hole that has none.
check("the loose rule's known collision is known",
      _H.same("LM-1-23", "LM-12-3"), "documented, not desired")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
