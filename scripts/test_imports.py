"""What the importer must make of real-world drill files.

Run: PYTHONPATH=src python3 scripts/test_imports.py
No network is needed for the mapping checks; the UTM-zone-from-terrain check is
skipped when the DEM cannot be reached.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from minemodelingpro import imports as IMP          # noqa: E402

HOLES = [("DDH-26-%03d" % (i + 1), -120.3000 + (i % 4) * 0.002, 50.7100 + (i // 4) * 0.0012,
          520.0 + i * 7.0) for i in range(12)]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""))


def write(d, files):
    for n, text in files.items():
        open(os.path.join(d, n), "w").write(text)
    return sorted(os.path.join(d, n) for n in files)


def collars_degrees():
    return "HOLEID,X,Y,Z\n" + "".join("%s,%.6f,%.6f,%.1f\n" % h for h in HOLES)


def surveys_stations():
    out = ["HOLEID,DEPTH,AZIMUTH,INCLINATION,DECLINATION"]
    for h, *_ in HOLES:
        for d in (0, 50, 100, 150):
            out.append("%s,%d,135.0,55.0,14.8" % (h, d))
    return "\n".join(out) + "\n"


def surveys_sections():
    out = ["HOLEID,FROM,TO,AZIMUTH,INCLINATION,DECLINATION"]
    for h, *_ in HOLES:
        for a, b in ((0, 60), (60, 120), (120, 180)):
            out.append("%s,%d,%d,135.0,55.0,14.8" % (h, a, b))
    return "\n".join(out) + "\n"


def assays_long(value_col="GRADE_PPM"):
    out = ["HOLEID,FROM,TO,ANALYTE,%s" % value_col]
    for h, *_ in HOLES:
        d = 40.0
        while d < 100:
            for el, v in (("Au", 1.2), ("Cu", 800.0)):
                out.append("%s,%.1f,%.1f,%s,%.3f" % (h, d, d + 1.5, el, v))
            d += 1.5
    return "\n".join(out) + "\n"


def assays_wide():
    out = ["HOLEID,FROM,TO,Au_ppm,Cu_ppm,Ag (g/t)"]
    for h, *_ in HOLES:
        d = 40.0
        while d < 100:
            out.append("%s,%.1f,%.1f,1.2,800,5.0" % (h, d, d + 1.5))
            d += 1.5
    return "\n".join(out) + "\n"


with tempfile.TemporaryDirectory() as tmp:
    # 1 — three files, collars in degrees under x/y/z, stations by depth, long assays
    d1 = os.path.join(tmp, "a"); os.makedirs(d1)
    ds = IMP.build_dataset(write(d1, {"collars.csv": collars_degrees(),
                                      "surveys.csv": surveys_stations(),
                                      "assays.csv": assays_long()}),
                           project="T1", private=True)
    kinds = {t["table"]: t["kind"] for t in ds["tables"]}
    check("three files are recognised separately",
          kinds == {"collars.csv": "collar", "surveys.csv": "survey", "assays.csv": "assay"}, kinds)
    check("x/y in degrees are read as longitude/latitude",
          ds["crs"]["kind"] == "latlon" and ds["center"] and abs(ds["center"][0] - 50.71) < 0.02, ds["crs"])
    check("every hole joins across the three files", ds["counts"]["linked"] == 12, ds["counts"])
    check("survey stations are kept", ds["counts"]["survey_stations"] == 48, ds["counts"])
    check("copper in ppm becomes per cent",
          all(abs(x["grade"] - 0.08) < 1e-6 and x["unit"] == "%"
              for x in ds["intervals"] if x["el"] == "Cu"))
    check("gold in ppm becomes grams per tonne",
          all(abs(x["grade"] - 1.2) < 1e-6 and x["unit"] == "g/t"
              for x in ds["intervals"] if x["el"] == "Au"))
    check("a declination column is reported, not silently applied",
          any("declination" in n for n in ds["notes"])
          and all(abs(s["az"] - 135.0) < 0.01 for s in ds["surveys"]))
    check("the model is buildable", IMP.validate(ds) == [], IMP.validate(ds))

    # 2 — the same, with the azimuths declared magnetic
    files1 = sorted(os.path.join(d1, n) for n in os.listdir(d1))
    ds2 = IMP.build_dataset(files1, project="T1", private=True, azimuth_ref="magnetic")
    check("magnetic azimuths get the declination added",
          len(ds2["surveys"]) == 48
          and all(abs(s["az"] - 149.8) < 0.01 for s in ds2["surveys"]), ds2["surveys"][:1])

    # 3 — surveys stated as sections of the hole, wide assays with hidden units
    d2 = os.path.join(tmp, "b"); os.makedirs(d2)
    ds3 = IMP.build_dataset(write(d2, {"collars.csv": collars_degrees(),
                                       "surveys.csv": surveys_sections(),
                                       "assays.csv": assays_wide()}),
                            project="T2", private=True)
    kinds = {t["table"]: t["kind"] for t in ds3["tables"]}
    check("a survey given as from/to sections is still a survey",
          kinds.get("surveys.csv") == "survey", kinds)
    check("each section's start is a station, and the last section's end is the deepest",
          ds3["counts"]["survey_stations"] == 48
          and max(s["depth"] for s in ds3["surveys"]) == 180.0, ds3["counts"])
    check("Cu_ppm in a wide table is not read as per cent",
          all(abs(x["grade"] - 0.08) < 1e-6 for x in ds3["intervals"] if x["el"] == "Cu"))
    check("Ag (g/t) in a wide table stays g/t",
          all(abs(x["grade"] - 5.0) < 1e-6 for x in ds3["intervals"] if x["el"] == "Ag"))

    # 3b — a full ICP package is the client's data, and all of it comes through
    trace = {"As": 0.002, "Bi": 0.0008, "Cu": 0.004, "Pb": 0.003, "Zn": 0.006, "Au": 0.004,
             "S": 0.4, "Mn": 0.05, "Mo": 0.0002, "Sb": 0.004, "Sn": 0.0005, "W": 0.0003,
             "U": 0.0002, "Li": 0.002, "Ga": 0.001}
    iv = []
    for h in range(20):
        for i in range(40):
            iv.append({"hole": "L%02d" % h, "from": i, "to": i + 1, "el": "Ag",
                       "grade": 120.0, "unit": "g/t", "sub": 0})
            for el, g in trace.items():
                iv.append({"hole": "L%02d" % h, "from": i, "to": i + 1, "el": el, "grade": g,
                           "unit": "g/t" if el in IMP.PRECIOUS_STD else "%", "sub": 0})
    n = []
    kept, seen = IMP.carry_elements(iv, None, n)
    check("every assayed element is carried, pathfinders included",
          len(kept) == len(iv) and len({x["el"] for x in kept}) == 16, len(kept))
    check("the suite is reported", len(seen) == 16 and any("all carried" in x for x in n), n)
    check("naming elements does not drop the rest",
          len(IMP.carry_elements(iv, ["Ag"], [])[0]) == len(iv))

    # 3c — and they survive a round trip through the stored dataset
    import json as _json
    ds_rt = IMP.intern_rows(_json.loads(_json.dumps({"intervals": kept})))
    check("a dataset read back keeps every element",
          len({x["el"] for x in ds_rt["intervals"]}) == 16)
    check("the repeated strings are shared after reading back",
          len({id(x["el"]) for x in ds_rt["intervals"]}) == 16,
          len({id(x["el"]) for x in ds_rt["intervals"]}))

    # 4 — a flat local grid cannot be placed, and says so
    d3 = os.path.join(tmp, "c"); os.makedirs(d3)
    loc = "hole,east,north,elev\n" + "".join(
        "%s,%.1f,%.1f,100.0\n" % (h[0], 1000 + i * 40, 2000 + i * 30) for i, h in enumerate(HOLES))
    ds4 = IMP.build_dataset(write(d3, {"collars.csv": loc, "assays.csv": assays_long()}),
                            project="T3", private=True)
    check("a mine grid is modelled as a local grid, with a note",
          ds4["crs"]["kind"] == "local" and any("local grid" in n for n in ds4["notes"]), ds4["notes"])

    # 5 — the zone identified from the collar elevations (needs the DEM)
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
        import numpy as np
        from minemodelingpro import terrain
        z = terrain.elevations(np.array([h[2] for h in HOLES]), np.array([h[1] for h in HOLES]), 12)
        assert np.isfinite(np.asarray(z, float)).all()
        rows = []
        for h, zz in zip(HOLES, z):
            e, n = tr.transform(h[1], h[2])
            rows.append("%s,%.1f,%.1f,%.1f" % (h[0], e, n, float(zz)))
        d4 = os.path.join(tmp, "d"); os.makedirs(d4)
        ds5 = IMP.build_dataset(write(d4, {"collars.csv": "hole,east,north,elev\n" + "\n".join(rows) + "\n",
                                           "assays.csv": assays_long()}), project="T4", private=True)
        check("the UTM zone is identified from the collar elevations",
              ds5["crs"].get("epsg") == 32610, ds5["crs"])
    except Exception as e:
        print("  skip  UTM zone from terrain (DEM unavailable: %s)" % str(e)[:60])

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
