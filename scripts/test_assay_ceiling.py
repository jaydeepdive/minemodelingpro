"""A bonanza assay is a measurement; a certificate number is not.

Jordan, reading his own drill logs beside the model: "89125 ppm was translated to
just 89.1 g/t which is entirely wrong. the high grade assays within that ten metre
section dont at all match what the logs say."

He was right. `MAX_PLAUSIBLE["Ag"]` was 30,000 g/t and was being read as a
per-SAMPLE limit, so every assay above it was assumed to be a unit mistake and
divided by a thousand until it fit. Langis's three best silver samples — the ones
the laboratory escalated to Ag-CON01, its concentrate-grade method, in order to
measure at all — were published at a thousandth of their value.

These cases are the two populations that ceiling has to separate, taken from real
data: LM-26-377's bonanza silver, which must survive untouched, and the sample
numbers and certificate numbers the corpus actually contains, which must not
become ore.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from minemodelingpro import sanity as S

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   %s" % name)
    else:
        fail += 1
        print("  FAIL %s %s" % (name, detail))


# ----------------------------------------------------------------- Langis
# LM-26-377, as the import stored it and the logs state it. The two bonanza
# samples sit in a column whose ordinary values are a few g/t to a few thousand.
LANGIS = [8050.0, 89125.0, 54652.0, 880.0, 3270.0, 12.4, 3.1, 44.0, 190.0, 7.7]

v, note, dropped, marks = S.fix_values("Ag", list(LANGIS))
check("LM-26-377's 89,125 g/t silver survives exactly",
      v[1] == 89125.0, "got %r" % v[1])
check("and its 54,652 g/t neighbour survives exactly",
      v[2] == 54652.0, "got %r" % v[2])
check("nothing in the column is dropped", dropped == 0, str(dropped))
check("every ordinary value is untouched", v[3:] == LANGIS[3:])
check("the flag names the hole's own numbers so they can be checked",
      note and "89,125" in note, repr(note))
check("and reports them as kept, not repaired",
      marks and all(m[3] == "kept" for m in marks), repr(marks))

# the old rule, pinned, so this cannot come back silently
check("the ceiling that destroyed it is no longer a per-sample limit",
      S.impossible("Ag") == S.PURE_GPT * S.SAMPLE_FRACTION
      and S.MAX_PLAUSIBLE["Ag"] == 30000.0,
      "%r / %r" % (S.impossible("Ag"), S.MAX_PLAUSIBLE.get("Ag")))
check("a sample may not be mostly metal — that is not an interval of rock",
      S.impossible("Au") == 500000.0, str(S.impossible("Au")))
check("1 ppm is 1 g/t, so a ppm column needs no conversion at all",
      S.fix_values("Ag", [89125.0, 12.0, 3.0])[0][0] == 89125.0)

# ------------------------------------------------- what must still be caught
# Eagle Rock's platinum, verbatim from the corpus: four consecutive integers,
# which is a sample number sequence and not a deposit.
EAGLE = [1030301.0, 1030302.0, 1030303.0, 1030304.0, 0.4, 1.2, 0.8, 2.1, 0.3, 1.1]
v, note, dropped, marks = S.fix_values("Pt", list(EAGLE))
check("a sample-number run is not published as platinum",
      all(x is None for x in v[:4]), repr(v[:4]))
check("it is dropped rather than divided into a plausible grade",
      dropped == 4 and all(m[3] == "dropped" for m in marks[:4]), str(dropped))
check("and the flag names one so it can be traced to the certificate",
      note and "1,030,30" in note, repr(note))

# Glencore Bucke's silver: 5,013,070 g/t is 501% silver. Under the old rule this
# became 5,013 g/t — an identifier turned into the best assay on the property.
v, note, dropped, _ = S.fix_values("Ag", [5013070.0, 2081210.0, 300.0, 12.0, 45.0, 7.0])
check("5,013,070 g/t silver is more than the rock and is dropped",
      v[0] is None and v[1] is None, repr(v[:2]))
check("it is NOT rescaled to 5,013 g/t",
      5013.0 not in [x for x in v if x is not None], repr(v))

# --------------------------------------------- the per-cent path is unchanged
# Murray Brook: a correct per-cent zinc column with parts-per-million rows in it.
# This conversion is well founded and measured, and must still happen.
v, note, dropped, marks = S.fix_values("Zn", [22700.0, 2.27, 3.1, 1.8, 2.9, 0.4])
check("a stray ppm row in a per-cent column is still converted",
      v[0] is not None and abs(v[0] - 2.27) < 1e-9, repr(v[0]))
check("and is reported as converted", marks and marks[0][3] == "converted",
      repr(marks))
check("nothing is more than 100% of itself", S.impossible("Zn") == 100.0)
check("iron's ceiling stays chemistry, not pure metal",
      S.impossible("Fe") == 72.0, str(S.impossible("Fe")))

# ------------------------------------- a whole column in the wrong unit, intact
# The median is what decides a unit mistake, and that test is untouched: a gold
# column in parts per billion still gets divided by a thousand.
ppb = [5000.0, 8000.0, 12000.0, 3000.0, 20000.0, 150000.0, 6000.0, 9000.0]
v, note, dropped, _ = S.fix_values("Au", ppb)
check("a gold column in ppb is still repaired as a whole column",
      v and abs(v[0] - 5.0) < 1e-9 and abs(v[5] - 150.0) < 1e-9, repr(v[:6]))
check("and says which mis-read it was",
      note and "parts per billion" in note, repr(note))

# ------------------------------------------------- a real bonanza gold sample
# Brucejack has published individual drill assays above 40,000 g/t gold. The old
# Au ceiling of 1,500 g/t (0.15%) rewrote every one of them.
v, note, dropped, marks = S.fix_values(
    "Au", [41582.0, 6140.0, 2.1, 0.8, 14.0, 3.2, 0.5, 1.1, 0.9, 2.2])
check("41,582 g/t gold survives", v[0] == 41582.0, repr(v[0]))
check("6,140 g/t gold survives", v[1] == 6140.0, repr(v[1]))
check("neither is dropped", dropped == 0, str(dropped))

# ------------------------------------------------------------ the empty cases
check("an empty column is fine", S.fix_values("Au", [])[1] is None)
check("a column of None is fine", S.fix_values("Au", [None, None])[1] is None)
check("a column with no excess gets no note",
      S.fix_values("Ag", [12.0, 300.0, 45.0])[1] is None)

# ------------------------------------- end to end, through both plumbings
# The note is only useful if it reaches the page naming the HOLE, and there are
# two paths to it: rows (a report) and columns (a client's upload). Both must say
# the same thing, because the project page shows whichever one ran.
from minemodelingpro.ivtable import Intervals                     # noqa: E402

# A real column is overwhelmingly ordinary rock with a few bonanza samples in it —
# Langis is 3 samples above 30,000 g/t out of 48,301 — and the MEDIAN is what tells
# a bonanza column apart from a column in the wrong unit. A fixture of nothing but
# high grade has a median of 8,050 g/t, trips the column-level unit test, and gets
# divided by a thousand for a completely different and correct reason. Build the
# fixture like the data, or it tests the wrong branch.
ROWS = [
    {"hole": "LM-26-377", "from": 172.10, "to": 172.75, "el": "Ag",
     "grade": 89125.0, "unit": "g/t", "sub": 0},
    {"hole": "LM-26-377", "from": 172.75, "to": 173.25, "el": "Ag",
     "grade": 54652.0, "unit": "g/t", "sub": 0},
    {"hole": "LM-26-377", "from": 173.25, "to": 174.00, "el": "Ag",
     "grade": 8050.0, "unit": "g/t", "sub": 0},
] + [
    {"hole": "LM-26-101", "from": float(d), "to": float(d + 1), "el": "Ag",
     "grade": g, "unit": "g/t", "sub": 0}
    for d, g in enumerate([12.4, 3.1, 44.0, 190.0, 7.7, 0.5, 2.2, 18.0, 61.0,
                           4.4, 1.1, 9.9, 130.0, 25.0, 0.8, 3.3, 77.0, 6.6,
                           2.9, 15.0, 41.0, 0.4])
]
N_ROWS = len(ROWS)

by = {"Ag": [dict(r) for r in ROWS]}
flags, dropped = S.fix_intervals(by)
msg = " ".join(f["message"] for f in flags)
check("the row path keeps every assay", len(by["Ag"]) == N_ROWS, str(len(by["Ag"])))
check("the row path still reports 89,125",
      any(abs(x["grade"] - 89125.0) < 1e-9 for x in by["Ag"]), repr(by["Ag"][0]))
check("the row path names the hole", "LM-26-377" in msg, msg)
check("and the depth, so it can be found in the log", "172.1" in msg, msg)
check("and flags it for a look rather than as a repair",
      flags and flags[0]["level"] == "check", repr(flags))

tb = Intervals.from_rows([dict(r) for r in ROWS])
flags2, dropped2 = S.fix_table(tb)
msg2 = " ".join(f["message"] for f in flags2)
check("the column path keeps every assay", len(tb) == N_ROWS, str(len(tb)))
check("the column path still reports 89,125",
      max(tb.grades("Ag")) == 89125.0, repr(max(tb.grades("Ag"))))
check("the column path names the same hole", "LM-26-377" in msg2, msg2)
check("both plumbings say the same thing", msg == msg2, "%r vs %r" % (msg, msg2))

print("\nthe note a reader sees:\n   " + msg2)

# ----------------------------------------- the median still decides a unit error
# The two tests answer two different questions and both are needed. This pins the
# boundary, because a fixture of pure bonanza took the other branch and looked
# exactly like the bug being fixed here.
check("a bonanza SAMPLE in an ordinary column is kept",
      S.fix_values("Ag", [89125.0] + [12.0] * 40)[0][0] == 89125.0)
check("a whole column whose TYPICAL value is impossible is still converted",
      abs(S.fix_values("Ag", [89125.0, 8050.0, 54652.0, 12000.0,
                              9000.0])[0][0] - 89.125) < 1e-9,
      "the median test is what catches a ppb column and must keep working")
check("and that conversion says it was the whole column, not a sample",
      "parts per billion" in (S.fix_values("Ag", [89125.0, 8050.0, 54652.0,
                                                  12000.0, 9000.0])[1] or ""))

# ------------------------------------- a numbering in the grade column
# These are verbatim from the corpus, and they are the cases no ceiling can reach:
# Gold Springs' run sits INSIDE the range where real bonanza gold lives.
GOLD_SPRINGS = [41436.0 + i for i in range(68)]           # 68 integers, span 67
JOJAY = [159605.0 + i for i in range(42)]                  # 42 integers, span 41
ORDINARY_AU = [0.5, 1.2, 0.3, 2.1, 0.8, 0.4, 1.9, 0.6, 3.0, 0.2] * 36

v, note, dropped, marks = S.fix_values("Au", GOLD_SPRINGS + ORDINARY_AU)
check("68 integers packed in a span of 67 are a numbering, not gold",
      all(x is None for x in v[:68]), repr(v[:3]))
check("the numbering is dropped, not rescaled", dropped == 68, str(dropped))
check("and the flag says what it was",
      note and "numbering" in note, repr(note))
check("the ordinary assays are untouched", v[68:] == ORDINARY_AU)

v2 = S.fix_values("Au", JOJAY + [3.01] * 200)[0]
check("Jojay's consecutive sample numbers are refused",
      all(x is None for x in v2[:42]), repr(v2[:3]))

# a year in the grade column: nine identical integers
v3, n3, d3, _ = S.fix_values("Au", [2009.0] * 9 + [0.8] * 12)
check("a year repeated down the column is refused", all(x is None for x in v3[:9]),
      repr(v3[:3]))

# ------------------------------- and what the numbering test must NOT touch
check("Langis's continuous bonanza tail is not a numbering",
      not S._numbering([8050.0, 89125.0, 54652.0, 880.0, 3270.0], 24.0),
      "it spreads over orders of magnitude")
check("a real tail with decimals is not a numbering",
      not S._numbering([3430.5, 12000.2, 44574.9, 8000.1, 20000.4], 1.371))
check("too few values to be a sequence is not a numbering",
      not S._numbering([41436.0, 41437.0], 0.5))
check("high grade that is NOT detached from its rock is not a numbering",
      not S._numbering([41436.0 + i for i in range(68)], 900.0),
      "within 100x of the surrounding rock, so it reads as grade")
check("Brucejack's 41,582 g/t survives beside a numbering's own range",
      S.fix_values("Au", [41582.0] + [1.3] * 40)[0][0] == 41582.0)

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
