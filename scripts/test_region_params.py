#!/usr/bin/env python3
"""The region that chooses the parameters must be the region the drilling is in.

When no technical report states a block size or a bulk density, `auto_params`
takes what is typical for the project's REGION (minemodelingpro.regional). That
region was read off the dataset at the top of `build_project`, while the real
one was recomputed from the coordinates nine hundred lines further down, at
output time — so the two could disagree, and on a real client project they did:

    region:       "Ontario"
    block_source: "typical for deposits in British Columbia (5 reports)"

A silver camp at Cobalt, Ontario modelled on a British Columbia block size and
density, and labelled Ontario on the page. The dataset's own label had come off
an attached public project's BC news releases; the coordinates said Ontario all
along, and nothing asked them until it was too late to matter.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro import model3d as M                          # noqa: E402

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


seen = {}
_real = M.auto_params


def spy(ds):
    seen["region"] = ds.get("region")
    return _real(ds)


def build(frame, ds_region):
    seen.clear()
    M.auto_params = spy
    try:
        M.build_project({"slug": "t", "name": "T", "region": ds_region, "frame": frame,
                         "holes": {}, "intervals": []})
    except Exception:
        pass                      # it stops at "no holes"; the spy has already run
    finally:
        M.auto_params = _real
    return seen.get("region")


# Cobalt, Ontario — Jordan's Langis collars, with a stale British Columbia label
ONT = {"kind": "geo", "lat0": 47.5808, "lon0": -79.5704, "mE": 75000.0, "mN": 110540.0}
got = build(ONT, "British Columbia")
check("the coordinates override a stale dataset region",
      got == "Ontario", f"auto_params was given {got!r}")

# a correct label is left alone
check("a region that already agrees is unchanged",
      build(ONT, "Ontario") == "Ontario")

# a local grid has no coordinates to consult, so the dataset's label stands
LOCAL = {"kind": "local", "lat0": None, "lon0": None}
check("with no map position the dataset's own region is used",
      build(LOCAL, "Ontario") == "Ontario")
check("and a local grid with no region at all stays None",
      build(LOCAL, None) is None)

# somewhere that really is British Columbia still gets British Columbia
BC = {"kind": "geo", "lat0": 54.50, "lon0": -126.50, "mE": 64700.0, "mN": 110540.0}
check("real British Columbia coordinates give British Columbia",
      build(BC, "Ontario") == "British Columbia", f"{build(BC, 'Ontario')!r}")

# and the resolution happens BEFORE the parameters are chosen, in the source
src = open(os.path.join(ROOT, "src", "minemodelingpro", "model3d.py")).read()
i_region = src.index('region = ds.get("region")')
i_auto = src.index("base, base_src = auto_params(ds)")
check("the region is resolved before auto_params is called", i_region < i_auto,
      f"region at {i_region}, auto_params at {i_auto}")

print(f"\n{len(OK)} passed, {len(BAD)} failed")
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
