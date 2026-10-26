#!/usr/bin/env python3
"""Reading a report's geophysics and rock properties — and not reading the traps.

Every negative case here is one the extractor actually fell for when it was run
over real NI 43-101 reports: an abbreviations list that looks like a survey, a
contents page, a reference list, a "down-hole survey" that is the hole's own
azimuth and dip, a distance between two deposits read as survey line-km, a
consulting firm's initials read as the Geological Strength Index, and a page
header's date read as the year a survey was flown.

The sentences are written here rather than copied, in the shapes the reports
use, so each test isolates one pattern.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from minemodelingpro import geophysics as GP           # noqa: E402
from minemodelingpro.drill_tables import _with_stated  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def one(text):
    return GP.surveys([text])


print("== surveys")
s = one("In 1987 the company completed an airborne magnetic survey over the property, "
        "flown on flight lines at 200 m intervals.")
check("an airborne magnetic survey is read", s and s[0]["method"] == "magnetics", s)
check("so is the platform", s and s[0].get("platform") == "airborne", s)
check("so is the line spacing", s and s[0].get("line_spacing_m") == 200.0, s)
check("so is the year", s and s[0].get("year") == 1987, s)

s = one("Ground geophysical surveying was carried out, comprising induced polarisation "
        "(102-line km of dipole-dipole array) completed in 2018.")
check("a hyphenated line-km is read (102-line km, not 102 line km)",
      s and s[0].get("line_km") == 102.0, s)
check("and the method is IP", s and s[0]["method"] == "IP/resistivity", s)

s = one("A ground gravity survey was carried out in 2022 by a contractor, as summarised "
        "in a separate filing. A total of 1,168 stations were read on a 50 m grid.")
check("a station count two sentences away still belongs to its survey",
      s and s[0].get("stations") == 1168, s)

s = one("A program of rock geochemistry and 7.7 km of road-based induced polarization "
        "surveying was completed.")
check("km OF a survey counts as survey length", s and s[0].get("line_km") == 7.7, s)

print("\n== the traps")
s = one("ICP Inductively Coupled Plasma IP Induced Polarisation K Thousand K-Ar "
        "Potassium-Argon kg Kilogram m Metre")
check("an abbreviations list is not a survey", not s, s)

s = one("9.5 GROUND GRAVITY SURVEY ....................................................... 80")
check("a contents page is not a survey", not s, s)

s = one("Fritz, F.P., 2012, Mineral Gulch Project, Cassia County, Idaho, Aeromagnetic "
        "Survey Interpretation, internal report, 9 p.")
check("a reference list entry is not work done on the property", not s, s)

s = one("There is no down-hole survey data for the holes in the resource, so collar "
        "azimuth and dip were used throughout.")
check("a down-hole survey is the hole's own attitude, not borehole geophysics",
      not any(x["method"] == "borehole geophysics" for x in s), s)

s = one("Following a ground IP and magnetic survey in 2022, five holes were drilled "
        "1.5 km from the Red Dog deposit.")
check("a distance between deposits is not survey line-km",
      s and all(x.get("line_km") is None for x in s), s)

s = one("TECHNICAL REPORT 20 FEBRUARY 2026 25 An IP / resistivity survey was completed "
        "over the eastern claim block.")
check("a page header's date is not the year a survey was run",
      s and all(x.get("year") != 2026 for x in s), s)

print("\n== the rock")
r = GP.rock(["Bulk density is variable by rock type and ranges between 2.43 t/m3 and "
             "2.63 t/m3 in the mineralised domains."])
check("a stated density range is read", r.get("density_low") and r.get("density_high"), r)
check("the tonnage factor is the middle of that range", GP.density_from(r) == 2.53,
      GP.density_from(r))

r2 = GP.rock(["The specific gravity for each deposit and domain ranges from 2.62 to 2.86 "
              "depending on alteration."])
check("so is a range written with 'from ... to'", GP.density_from(r2) == 2.74,
      GP.density_from(r2))

r3 = GP.rock(["Average bulk density assigned to the modelled domain and used to populate "
              "the block model was 2.69 t/m3."])
check("a single density is read when it carries its unit", GP.density_from(r3) == 2.69,
      GP.density_from(r3))

r4 = GP.rock(["Cuttings for at least 12 holes were sent to GSI for 30 g fire assay of "
              "gold and silver."])
check("a lab's initials are not the Geological Strength Index", not r4.get("gsi"), r4)

r5 = GP.rock(["Rock mass characterisation gave a GSI of 45 across the mineralised zone."])
check("the index is read when it is the index", (r5.get("gsi") or {}).get("median") == 45.0, r5)

r6 = GP.rock(["RQDCD 0 10 1 code RQD Code added to the model for logging completeness."])
check("a variable table is not an RQD measurement", not r6.get("rqd"), r6)

r7 = GP.rock(["Core recovery was good and RQD averaged 72% through the deposit."])
check("an RQD with its per cent sign is read", (r7.get("rqd") or {}).get("median") == 72.0, r7)

r8 = GP.rock(["The Whittle shell was designed based on an overall slope angle of 45 degrees.",
              "Thick overburden of up to 10 m covers the deposit area.",
              "Uniaxial compressive strength averaged 118 MPa for the host rock."])
check("the pit slope angle is read",
      (r8.get("slope_angle_deg") or {}).get("median") == 45.0, r8.get("slope_angle_deg"))
check("the overburden thickness is read",
      (r8.get("overburden_m") or {}).get("median") == 10.0, r8.get("overburden_m"))
check("the rock strength is read",
      (r8.get("ucs_mpa") or {}).get("median") == 118.0, r8.get("ucs_mpa"))

print("\n== targets")
t = GP.targets(["The chargeability anomaly on the western grid remains untested by "
                "drilling and warrants follow-up drill testing."])
check("an untested geophysical anomaly is kept", t and t[0]["untested"], t)
t2 = GP.targets(["The sub-cropping deposit corresponds to a coincident high chargeability "
                 "and low resistivity anomaly, confirming the method over this sulphide."])
check("an anomaly the drilling explained is kept, and marked as explained",
      t2 and t2[0]["explained"], t2)
t3 = GP.targets(["The magnetic high north of the highway reflects a regional gabbro sill."])
check("an anomaly tied to neither ore nor a target is left alone", not t3, t3)

print("\n== what reaches the model")
rk = GP.rock(["Bulk density ranges between 2.43 t/m3 and 2.63 t/m3 in the mineralised "
              "domains. Thick overburden of up to 10 m covers the deposit."])
mp = _with_stated({}, None, rk)
check("the report's own density becomes the tonnage factor", mp.get("density") == 2.53, mp)
check("the overburden does NOT travel as a setting nothing applies",
      "overburden_m" not in mp, mp)
mp2 = _with_stated({"density": 3.6}, None, rk)
check("a density the resource tables already stated is not overwritten",
      mp2.get("density") == 3.6, mp2)
mp3 = _with_stated({}, None, GP.rock(["The core was logged for RQD and recovery."]))
check("a report that states no density supplies none", "density" not in mp3, mp3)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
