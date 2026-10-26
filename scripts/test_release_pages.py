#!/usr/bin/env python3
"""Reading the release itself, rather than only the table somebody pulled out.

The drill bank keeps one parser's output per release and 722 of its 4,787
releases produced nothing at all — including, for example, a release whose
entire subject is a geophysical survey that defined a new drill target. The
bank does keep each release's URL, so none of that needs fixing upstream.

These check the reading, not the fetching: the HTML stripper, where a release
stops and its legal boilerplate starts, what the extractors get out of release
prose, and the rule that a release may add assays to a hole the project already
has but may never invent a hole it gave no coordinates for.
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from minemodelingpro import release_pages as RP     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


BODY = ("Zonge International Geophysics completed induced polarization (IP) and "
        "magnetotelluric (MT) surveys over the project. A total of 6 lines of "
        "IP/Resistivity and MT acquisition totalling approximately 20 line-km of IP "
        "and about 24 line-km of MT coverage. Transmitter electrode positions were "
        "spaced at 300 m intervals along each line. The very prominent low resistivity "
        "anomaly extending southward from the target remains untested by drilling and "
        "represents an immediate drill target. The zone has been traced over a strike "
        "length of 1,200 m and dips 68 degrees to the south-west.")

PAGE = ("<html><head><title>x</title><style>.a{color:red}</style></head><body>"
        "<script>var a=1;</script><div class='nav'>Home</div>"
        "<h1>Company Reports New Geophysical Targets</h1>"
        "<p>" + BODY + "</p>"
        "<p>Forward-looking statements: this news release contains forward-looking "
        "information including the strike length of 9,999 m of nothing at all.</p>"
        "</body></html>")

print("== the page")
t = RP.html_text(PAGE)
check("script and style are gone", "var a=1" not in t and "color:red" not in t, t[:80])
check("the release text survives", "magnetotelluric" in t, t[:80])
check("entities are decoded", "&amp;" not in RP.html_text("<p>a &amp; b</p>"),
      RP.html_text("<p>a &amp; b</p>"))

pages = RP.pages_of(PAGE.encode("utf-8"), "text/html; charset=utf-8",
                    "https://example.com/release/1")
check("one page comes back", len(pages) == 1, len(pages))
check("the legal boilerplate is cut off, with the number it carries",
      "9,999" not in pages[0], pages[0][-120:])

print("\n== what the reading finds")
rec = RP.read(pages, url="https://example.com/release/1")
g = rec.get("geophysics") or {}
check("the surveys are read", {s["method"] for s in (g.get("surveys") or [])} >=
      {"IP/resistivity", "magnetotellurics"}, g.get("surveys"))
km = {s["method"]: s.get("line_km") for s in (g.get("surveys") or [])}
check("each survey keeps its own coverage",
      km.get("IP/resistivity") == 20.0 and km.get("magnetotellurics") == 24.0, km)
check("an untested anomaly is kept as the company's own words",
      any(t_["untested"] for t_ in (g.get("targets") or [])), g.get("targets"))
check("the body's stated strike length is read",
      ((rec.get("extent") or {}).get("strike") or {}).get("median") == 1200.0,
      rec.get("extent"))
check("so is its dip", ((rec.get("attitude") or {}).get("dip") or {}).get("median") == 68.0,
      rec.get("attitude"))
check("the reader stamps its version", rec.get("reader") == RP.READER, rec.get("reader"))

print("\n== a release with nothing in it")
empty = RP.read(RP.pages_of(b"<html><body><p>The company announces a financing.</p></body></html>",
                            "text/html"))
check("says nothing rather than something",
      not (empty.get("geophysics") or empty.get("extent") or empty.get("attitude")), empty)

print("\n== the store")
tmp = tempfile.mkdtemp(prefix="mmp-rel-")
old = RP.OUT_DIR
try:
    RP.OUT_DIR = tmp
    RP.save("abc123", rec)
    back = RP.load("abc123")
    check("a reading survives a round trip", back and back["id"] == "abc123", back and back.keys())
    check("it is listed for the current reader", "abc123" in RP.load_all(), list(RP.load_all()))
    check("and only for the release asked for", list(RP.load_all(["zzz"])) == [],
          list(RP.load_all(["zzz"])))
    stale = dict(rec)
    stale["reader"] = "rp0"
    RP.save("old999", stale)
    check("a reading from an older reader is not served", "old999" not in RP.load_all(),
          list(RP.load_all()))
finally:
    RP.OUT_DIR = old

print("\n== what a release may add to a project")
from minemodelingpro.projects import _fold_release_geophysics   # noqa: E402
folded = _fold_release_geophysics(
    {"surveys": [{"method": "magnetics", "platform": "airborne", "year": 1991,
                  "line_km": 277.0, "page": 23}], "targets": []},
    {"rel1": rec})
meths = [s["method"] for s in folded["surveys"]]
check("the report's surveys are kept", "magnetics" in meths, meths)
check("the release's are added", "magnetotellurics" in meths, meths)
check("and each says which release it came from",
      all(s.get("release") == "rel1" for s in folded["surveys"] if s.get("from_release")),
      folded["surveys"])
same = _fold_release_geophysics({"surveys": [{"method": "IP/resistivity", "platform": None,
                                              "year": None, "line_km": 20.0}], "targets": []},
                                {"rel1": rec})
check("a survey both describe is listed once",
      sum(1 for s in same["surveys"] if s["method"] == "IP/resistivity" and
          s.get("line_km") == 20.0) == 1, same["surveys"])

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
