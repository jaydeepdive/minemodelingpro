#!/usr/bin/env python3
"""The body's ORIENTATION, read from the words a report uses.

`dip` and `dip_direction` already orient the wireframe and stretch the search
along strike and down dip — and nothing ever filled them from a report, so a
vein took its orientation from the principal axes of the DRILL PATTERN rather
than from the rock.

The trap this must never fall into: `extent.stated`'s "dip" is a down-dip EXTENT
in metres (bounds 20–2,500 m). A dip angle is a different quantity wearing the
same word, and conflating them reads "7,710 ft down dip" as a 7,710-degree dip.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
from minemodelingpro import extent as E                      # noqa: E402
from minemodelingpro.drill_tables import _with_attitude      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({extra})" if extra else ""))


def dip(txt):
    a = E.attitude([txt])
    return (a.get("dip") or {}).get("median"), (a.get("dip_direction") or {}).get("median"), a


print("\na stated dip is read as an angle")
for txt, want in [
    ("The Patwon vein zone, dipping 75° to the north.", 75.0),
    ("The mineralised lode dips at approximately 62 degrees.", 62.0),
    ("Mineralisation has a dip of 48°.", 48.0),
    ("A 55° dipping orebody.", 55.0),
]:
    d, _dd, _a = dip(txt)
    check(f"{txt[:42]!r} -> {want}", d == want, d)

print("\na down-dip EXTENT is never read as an angle")
for txt in [
    "The deposit extends approximately 7,710 ft down dip and 2,707 ft along strike.",
    "The vein is traced 2,042 m down dip.",
    "Gold mineralisation extended 300 m down dip from the drill intersections.",
    "The zone has a dip length of 450 m.",
]:
    d, _dd, _a = dip(txt)
    check(f"no angle from {txt[:44]!r}", d is None, d)
# and the two live side by side without interfering
both = "The vein dips 70° and has been traced 450 m down dip."
d, _dd, _a = dip(both)
st = E.stated([both])
check("a sentence with both gives the angle to attitude", d == 70.0, d)
check("and the length to stated",
      (st.get("dip") or {}).get("median") == 450.0, (st.get("dip") or {}).get("median"))

print("\ndirection, as a compass word or an azimuth")
for txt, want in [
    ("The lode dips 60° to the north-east.", 45.0),
    ("Mineralisation dips steeply towards the west.", 270.0),
    ("The zone has a dip direction of 085 degrees.", 85.0),
]:
    _d, dd, _a = dip(txt)
    check(f"{txt[:40]!r} -> {want}", dd == want, dd)

print("\nwords stand in for a number, and say that they did")
d, _dd, a = dip("The mineralised zone is steeply dipping and continuous.")
check("'steeply dipping' is read as a dip", d == 70.0, d)
check("and flagged as a word, not a measurement", a.get("dip_is_qualitative") is True)
d2, _x, a2 = dip("The flat-lying dolomitic ore horizon.")
check("'flat-lying' reads shallow", d2 == 10.0, d2)
d3, _x, a3 = dip("The vein dips 72° and is steeply dipping throughout the deposit.")
check("a real number beats the word", d3 == 72.0 and not a3.get("dip_is_qualitative"), d3)

print("\nimplausible values are refused")
for txt in ["The structure dips 180 degrees.", "A dip of 0 degrees was logged."]:
    d, _dd, _a = dip(txt)
    check(f"refused: {txt[:38]!r}", d is None, d)
check("text with no mineralisation word is ignored",
      E.attitude(["The access road dips 12° to the south."]) == {},
      E.attitude(["The access road dips 12° to the south."]))

print("\nit reaches the modelling parameters")
mp = _with_attitude({}, E.attitude(["The vein dips 68° to the south-west."]))
check("dip is carried", mp.get("dip") == 68.0, mp)
check("dip direction is carried", mp.get("dip_direction") == 225.0, mp)
mp2 = _with_attitude({"dip": 11.0}, E.attitude(["The vein dips 68°."]))
check("a value already set is not overwritten", mp2["dip"] == 11.0, mp2)
mp3 = _with_attitude({}, E.attitude(["The zone is steeply dipping."]))
check("a qualitative dip does NOT become a parameter", "dip" not in mp3, mp3)
check("and the parameters stay valid", set(mp) <= {"dip", "dip_direction"}, mp)
from minemodelingpro import params as P                      # noqa: E402
clean, errs = P.validate(mp)
check("the validator accepts them", not errs and clean.get("dip") == 68.0, (clean, errs))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
