"""What would be mined, as opposed to what is in the ground.

A mining shape is only worth showing if it behaves like one: a stope has to be
big enough to mine and must carry the waste trapped inside it; a pit has to have
walls that stand up and must count the rock moved to expose the ore. These check
the behaviours, with planted geometry where the right answer is known by
construction.
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import numpy as np
from minemodelingpro import mining as MN

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print("  ok   %s" % name)
    else: fail += 1; print("  FAIL %s %s" % (name, detail))

BLK = (5.0, 5.0, 5.0)

def lattice(cells, grade):
    ijk = np.array(cells, np.int64)
    return ijk, np.full(len(ijk), float(grade))

# ---------------------------------------------------------------- stopes
# a solid 6x6x6 block of ore: 216 cells x 125 m3 = 27,000 m3, spans 30 m
cells = [(i, j, k) for i in range(6) for j in range(6) for k in range(6)]
ijk, g = lattice(cells, 10.0)
lab, st = MN.stopes(ijk, g, BLK, cutoff=1.0)
check("a mineable block of ore is one stope", len(st) == 1, str(len(st)))
check("and every cell of it belongs to that stope", (lab > 0).all())
check("its volume is the volume of the rock", st and st[0]["volume_m3"] == 27000.0,
      str(st and st[0]["volume_m3"]))
check("undiluted ore reports no dilution", st and st[0]["dilution"] == 0.0)

# a single isolated block is not a stope
ijk2, g2 = lattice([(0, 0, 0)], 50.0)
lab2, st2 = MN.stopes(ijk2, g2, BLK, cutoff=1.0)
check("one isolated block is not a stope", st2 == [], str(st2))

# a one-cell-wide string down a hole is not a stope either: it is long in one
# direction only, and no machine mines a 5 m pencil
ijk3, g3 = lattice([(0, 0, k) for k in range(40)], 50.0)
lab3, st3 = MN.stopes(ijk3, g3, BLK, cutoff=1.0)
check("a single string of blocks down one hole is not a stope", st3 == [],
      str(st3))

# two bodies far apart stay two stopes
cells_a = [(i, j, k) for i in range(6) for j in range(6) for k in range(6)]
cells_b = [(i + 40, j, k) for i in range(6) for j in range(6) for k in range(6)]
ijk4, g4 = lattice(cells_a + cells_b, 10.0)
lab4, st4 = MN.stopes(ijk4, g4, BLK, cutoff=1.0)
check("two bodies 200 m apart are two stopes", len(st4) == 2, str(len(st4)))

# ------------------------------------------------- dilution is carried
# a 6x6x6 ore block with a 2x2x2 waste core inside it
core = {(i, j, k) for i in range(2, 4) for j in range(2, 4) for k in range(2, 4)}
gg = np.array([0.0 if tuple(c) in core else 10.0 for c in cells], float)
ijk5 = np.array(cells, np.int64)
lab5, st5 = MN.stopes(ijk5, gg, BLK, cutoff=1.0, dilute=True)
check("waste trapped inside a stope is mined with it",
      st5 and st5[0]["blocks"] == 216, str(st5 and st5[0]["blocks"]))
check("it is reported as dilution",
      st5 and abs(st5[0]["dilution"] - 8 / 216) < 1e-3,
      str(st5 and st5[0]["dilution"]))
check("and it drags the stope grade below the ore grade",
      st5 and st5[0]["grade"] < st5[0]["ore_grade"],
      str(st5 and (st5[0]["grade"], st5[0]["ore_grade"])))
lab6, st6 = MN.stopes(ijk5, gg, BLK, cutoff=1.0, dilute=False)
check("with dilution off the hole is left out",
      st6 and st6[0]["blocks"] == 208, str(st6 and st6[0]["blocks"]))

# ------------------------------------------------------------------ pit
# flat ground at z=0, one ore block 50 m down, 45 degree walls
ni = nj = 21          # the lattice; the pit will reach well outside it
Z = np.zeros((ni, nj))
deep = [(20, 20, 0)]                       # origin z chosen so this is 50 m deep
ijk7 = np.array(deep, np.int64)
g7 = np.array([10.0])
inside, stt = MN.pit_shell(ijk7, g7, BLK, origin=(0.0, 0.0, -52.5),
                           cutoff=1.0, surface_z=Z, slope_deg=45.0)
check("the ore block is inside its own pit", bool(inside[0]), str(inside))
check("the pit reaches the ore", abs(stt["max_depth_m"] - 50.0) < 5.0,
      str(stt.get("max_depth_m")))
# at 45 degrees a 50 m pit is a cone of radius 50 m: volume = pi r^2 h / 3
want = math.pi * 50.0 ** 2 * 50.0 / 3.0
check("its excavated volume is a 45-degree cone to within 15%",
      abs(stt["excavated_m3"] - want) / want < 0.15,
      "%s vs %.0f" % (stt.get("excavated_m3"), want))
check("a pit that moves rock to reach one block has a huge strip ratio",
      stt["strip_ratio"] > 100, str(stt.get("strip_ratio")))

# a shallower wall angle must excavate MORE
_, flat = MN.pit_shell(ijk7, g7, BLK, origin=(0.0, 0.0, -52.5),
                       cutoff=1.0, surface_z=Z, slope_deg=30.0)
check("a flatter wall angle excavates more rock",
      flat["excavated_m3"] > stt["excavated_m3"],
      "%s vs %s" % (flat.get("excavated_m3"), stt.get("excavated_m3")))

# ore above the ground surface takes no part
_, above = MN.pit_shell(ijk7, g7, BLK, origin=(0.0, 0.0, 100.0),
                        cutoff=1.0, surface_z=Z, slope_deg=45.0)
check("ore above the ground is not pit ore", not above, str(above))

# no ground, no pit
_, nog = MN.pit_shell(ijk7, g7, BLK, origin=(0.0, 0.0, -52.5), cutoff=1.0,
                      surface_z=np.full((21, 21), np.nan), slope_deg=45.0)
check("a pit needs a ground surface and says nothing without one", not nog,
      str(nog))

# nothing above the cut-off is not a pit
_, none = MN.pit_shell(ijk7, g7, BLK, origin=(0.0, 0.0, -52.5), cutoff=99.0,
                       surface_z=Z, slope_deg=45.0)
check("no ore above the cut-off means no pit", not none, str(none))

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
