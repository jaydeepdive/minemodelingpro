"""Structural sites: do they find what they claim, and refuse what they should?

The easy half of this feature is drawing a circle where two veins cross. These
checks pin the hard half — that a straight vein yields no bend, that two veins
a kilometre apart vertically are not a relay, and that the competency test
returns nothing when nobody logged the rock, which is its state on every
public project.

    PYTHONPATH=src python3 scripts/test_structure.py
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from minemodelingpro import structure as ST          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=None):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name,
                         "" if extra is None else "  — %s" % (extra,)))


def sheet(strike_deg, n=40, length=600.0, off=0.0, z=(-40.0, -200.0), jit=1.5,
          seed=0):
    """A straight vein of a given strike, as contact midpoints."""
    rng = np.random.default_rng(seed)
    s = np.radians(strike_deg)
    u = np.array([np.sin(s), np.cos(s)])
    nn = np.array([-u[1], u[0]])
    P = []
    for a in np.linspace(-length / 2, length / 2, n):
        for zz in np.linspace(z[0], z[1], 4):
            xy = u * a + nn * off
            P.append([xy[0], xy[1], zz] + rng.normal(0, jit, 3))
    return np.asarray(P)


print("== a bend is found, and a straight vein has none")
straight = sheet(30.0, seed=1)
check("a straight vein yields no bend", len(ST.bends(straight)) == 0,
      len(ST.bends(straight)))

rng = np.random.default_rng(2)
B = []
for a in np.linspace(-300.0, 0.0, 22):           # one limb
    for zz in np.linspace(-40.0, -200.0, 4):
        B.append([a, 0.0, zz] + rng.normal(0, 1.5, 3))
for a in np.linspace(0.0, 260.0, 20):            # and the other, turned 50 deg
    for zz in np.linspace(-40.0, -200.0, 4):
        B.append([a * np.cos(np.radians(50)), a * np.sin(np.radians(50)), zz]
                 + rng.normal(0, 1.5, 3))
bent = np.asarray(B)
bd = ST.bends(bent)
check("a vein bent through 50 degrees reports a bend", len(bd) >= 1, len(bd))
if bd:
    worst = max(bd, key=lambda d: d["turn_deg"])
    check("and reports roughly the right turn",
          30.0 <= worst["turn_deg"] <= 70.0, worst["turn_deg"])
    check("at roughly the right place",
          float(np.linalg.norm(worst["xy"])) < 160.0,
          np.round(worst["xy"], 0).tolist())

print("\n== two veins crossing at a high angle are an intersection")
a1 = sheet(10.0, seed=3)
a2 = sheet(85.0, seed=4)
pr = ST.pairs([a1, a2])
kinds = {p["kind"] for p in pr}
check("a crossing is reported", "intersection" in kinds, sorted(kinds))
check("and it is not called a relay", "relay" not in kinds, sorted(kinds))

print("\n== two en-echelon segments are a relay, not one vein")
# same strike, offset across it, overlapping along it
r1 = sheet(20.0, length=400.0, off=0.0, seed=5)
r2 = sheet(20.0, length=400.0, off=70.0, seed=6)
pr = ST.pairs([r1, r2])
check("a relay is reported", any(p["kind"] == "relay" for p in pr),
      sorted({p["kind"] for p in pr}))
rel = [p for p in pr if p["kind"] == "relay"]
if rel:
    check("with the across-strike step measured",
          40.0 <= rel[0]["across_m"] <= 100.0, rel[0]["across_m"])

print("\n== depth has to overlap for two bodies to be related")
# same geometry as the relay above, but one vein a kilometre deeper
deep = sheet(20.0, length=400.0, off=70.0, z=(-1400.0, -1600.0), seed=7)
pr = ST.pairs([r1, deep])
check("two bodies a kilometre apart in depth are not related",
      not any(p["kind"] == "relay" for p in pr),
      sorted({p["kind"] for p in pr}))

print("\n== a splay is a modest angle with the ends together")
s1 = sheet(0.0, length=500.0, seed=8)
s2 = []
rng = np.random.default_rng(9)
for a in np.linspace(0.0, 420.0, 26):     # branches AT the first vein's end
    for zz in np.linspace(-40.0, -200.0, 4):
        ang = np.radians(25.0)
        s2.append([a * np.sin(ang), 250.0 + a * np.cos(ang), zz]
                  + rng.normal(0, 1.5, 3))
pr = ST.pairs([s1, np.asarray(s2)])
check("a branching vein is reported as a splay",
      any(p["kind"] == "splay" for p in pr), sorted({p["kind"] for p in pr}))

print("\n== a fold hinge is found in section, not in plan")
rng = np.random.default_rng(10)
H = []
for x in np.linspace(-400.0, 400.0, 46):
    for y in np.linspace(-120.0, 120.0, 4):
        # a horizon that rolls over: dips one way then the other
        z = -300.0 + 90.0 * np.cos(x / 200.0)
        H.append([x, y, z] + rng.normal(0, 1.0, 3))
hz = ST.hinges(np.asarray(H))
check("a rolling horizon reports a hinge", len(hz) >= 1, len(hz))
flat = np.column_stack([
    np.repeat(np.linspace(-400.0, 400.0, 40), 4),
    np.tile(np.linspace(-120.0, 120.0, 4), 40),
    -300.0 + 0.2 * np.repeat(np.linspace(-400.0, 400.0, 40), 4)])
check("a plane-dipping horizon reports none", len(ST.hinges(flat)) == 0,
      len(ST.hinges(flat)))

print("\n== corridors come from the bodies, because geophysics is prose")
# five veins on one strike and four on another: two trends that cross
many = [sheet(15.0, length=300.0, off=o, seed=20 + k)
        for k, o in enumerate((-300.0, -150.0, 0.0, 150.0, 300.0))]
many += [sheet(75.0, length=300.0, off=o, seed=40 + k)
         for k, o in enumerate((-200.0, 0.0, 200.0, 400.0))]
cr = ST.corridors(many)
check("two trends crossing are reported", len(cr) >= 1, len(cr))
if cr:
    check("with how many bodies back each trend",
          cr[0]["n_a"] >= 3 and cr[0]["n_b"] >= 3,
          (cr[0]["n_a"], cr[0]["n_b"]))
check("one trend alone is not a crossing",
      len(ST.corridors(many[:5])) == 0, len(ST.corridors(many[:5])))

print("\n== competency contacts need logged rock, and say nothing without it")
check("no lithology yields no contacts",
      ST.competency_contacts([], lambda h, d: (0, 0, 0)) == [])
check("and unrecognised codes yield none",
      ST.competency_contacts([("H1", 0.0, 10.0, "ZZZ"),
                              ("H1", 10.0, 20.0, "QQQ")],
                             lambda h, d: (0, 0, -d)) == [])
lith = [("H1", 0.0, 40.0, "GRANITE"), ("H1", 40.0, 70.0, "BLACK SHALE"),
        ("H1", 70.0, 90.0, "siltstone")]
cc = ST.competency_contacts(lith, lambda h, d: (0.0, 0.0, -float(d)))
check("a granite against a shale is a contact", len(cc) == 1, len(cc))
if cc:
    check("placed at the contact depth", abs(cc[0]["depth_m"] - 40.0) < 1.0,
          cc[0]["depth_m"])
    check("and a shale against a siltstone is not",
          all(c["depth_m"] < 60.0 for c in cc),
          [c["depth_m"] for c in cc])

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
