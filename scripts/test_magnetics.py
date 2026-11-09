#!/usr/bin/env python3
"""Regional magnetics: the reader's output, and the claim it is allowed to make.

The claim, measured on 44 Québec projects against 1,008 decoy windows drawn
from nearby SURVEYED ground: a deposit sits on ground with 3.3x the magnetic
field relief, 4.1x the analytic signal and 3.7x the horizontal gradient of
ordinary ground, and the effect survives both controls for the obvious
confound (tighter flight lines over deposits) — including low-passing to 450 m,
which would have destroyed a resolution artefact and instead strengthened it.

What is pinned here is that the module keeps to that claim: it discriminates in
the measured direction, it says nothing where nothing was flown, and it never
returns a score it cannot support.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from minemodelingpro import magnetics as M                      # noqa: E402

COG = os.environ.get("MMP_MAG_DIR") or "/home/claude/qcgeo/cog"
OK, BAD, SKIP = [], [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


# ------------------------------------------------------------------ region keys
check("Québec is recognised with and without the accent",
      M.region_key("Québec") == M.region_key("Quebec") == "quebec")
check("the two-letter code works", M.region_key("QC") == "quebec")
check("a region with no grid returns no key", M.region_key("Ontario") is None)
check("a missing region is not a key", M.region_key(None) is None)

# ------------------------------------------------- nothing configured, no claims
old = os.environ.pop(M.ROOT_ENV, None)
check("without a grid directory nothing is available", M.available("Québec") is False)
check("and sample() returns None rather than zeros",
      M.sample("Québec", 49.6, -78.7) is None)
check("favourability also returns None",
      M.favourability("Québec", 49.6, -78.7) is None)
if old:
    os.environ[M.ROOT_ENV] = old

# ------------------------------------------------------------- the calibration
cal = M.CALIBRATION.get("quebec")
check("a Québec calibration is baked in", bool(cal), "missing")
if cal:
    check("it covers the three measured features", len(cal) == 3, str(sorted(cal)))
    check("every feature's deciles are non-decreasing",
          all(list(v) == sorted(v) for v in cal.values()))
    check("nine deciles per feature", all(len(v) == 9 for v in cal.values()),
          str({k: len(v) for k, v in cal.items()}))

# ====================================================== against the real grids
if not os.path.isdir(COG) or not os.path.exists(os.path.join(COG, "qc_mag_residual.tif")):
    SKIP.append("grid-backed checks (no COGs in this container)")
    print(f"\n  skip  grid-backed checks — nothing under {COG}")
else:
    os.environ[M.ROOT_ENV] = COG
    check("Québec is available with the grids in place", M.available("Québec") is True)
    check("a region without a grid stays unavailable", M.available("Ontario") is False)

    tio = M.sample("Québec", 51.330, -63.400)      # largest ilmenite deposit on earth
    berardi = M.sample("Québec", 49.600, -78.700)  # Au, Abitibi
    mtl = M.sample("Québec", 45.500, -73.570)      # a city, no deposit
    for nm, s in (("Lac Tio", tio), ("Casa Berardi", berardi), ("Montréal", mtl)):
        check(f"{nm} returns a surveyed sample", bool(s) and s.get("surveyed"), str(s))
    check("the sample reports which window it measured",
          tio.get("window_m") == 1500, str(tio.get("window_m")))
    check("it names its source", "Géologie Québec" in (tio.get("source") or ""))

    # the ordering is the physics: a massive magnetite-ilmenite body outranks a
    # gold camp, which outranks ground with no deposit
    check("Lac Tio has far more relief than a gold camp",
          tio["residual_relief_nT"] > 10 * berardi["residual_relief_nT"],
          f"{tio['residual_relief_nT']} vs {berardi['residual_relief_nT']}")
    check("Lac Tio's analytic signal outranks the gold camp's",
          tio["analytic_signal_p90"] > berardi["analytic_signal_p90"])
    check("a deposit-free city ranks below both on gradient",
          mtl["gradient_abs_median"] < berardi["gradient_abs_median"] < tio["gradient_abs_median"],
          f"{mtl['gradient_abs_median']} / {berardi['gradient_abs_median']} / {tio['gradient_abs_median']}")

    # favourability: bounded, ordered, and silent where it cannot speak
    f_tio = M.favourability("Québec", 51.330, -63.400)
    f_mtl = M.favourability("Québec", 45.500, -73.570)
    check("favourability is a score in 0..1",
          0.0 <= f_tio["score"] <= 1.0 and 0.0 <= f_mtl["score"] <= 1.0,
          f"{f_tio['score']} {f_mtl['score']}")
    check("Lac Tio scores above a deposit-free city",
          f_tio["score"] > f_mtl["score"], f"{f_tio['score']} vs {f_mtl['score']}")
    check("the score carries the sample it came from", "from" in f_tio)

    # the honest blank: unsurveyed ground gets no number
    off = M.sample("Québec", 47.100, -74.800)
    check("unsurveyed ground reports surveyed=False, not a value",
          off is not None and off.get("surveyed") is False, str(off))
    check("and gets no favourability score",
          M.favourability("Québec", 47.100, -74.800) is None)
    # far outside the grid entirely
    out = M.sample("Québec", 20.0, -100.0)
    check("a point off the grid does not raise",
          out is None or out.get("surveyed") is False, str(out))

    print(f"\n  relief / analytic p90 / gradient over a 1.5 km window:")
    for nm, s in (("Lac Tio", tio), ("Casa Berardi", berardi), ("Montréal", mtl)):
        print(f"    {nm:14s} {s['residual_relief_nT']:9.1f} nT   "
              f"{s['analytic_signal_p90']:8.4f}   {s['gradient_abs_median']:7.4f}")

print(f"\n{len(OK)} passed, {len(BAD)} failed" + (f", {len(SKIP)} skipped" if SKIP else ""))
for b in BAD:
    print("  failed:", b)
sys.exit(1 if BAD else 0)
