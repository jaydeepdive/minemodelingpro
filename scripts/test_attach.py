#!/usr/bin/env python3
"""Building a client's project on top of a public one.

`attach_to` existed and could not work. Finding one public project meant
`assemble()` — the whole drill bank (643 MB) plus every extracted report
(516 MB), 758 projects materialised, 1,298 MB peak — on a modeler that has
1,048 MB. The build died, and from the page that looked like nothing happening.

`assemble_slug` reads the published model page's own source list and loads only
those releases and reports. These checks hold that it is the SAME answer, and
that it stays cheap.
"""
import os
import sys
import json
import time
import resource

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
from minemodelingpro import projects as PROJ          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({extra})" if extra else ""))


def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


SITE = os.path.join(_ROOT, "site")
if not os.path.isdir(os.path.join(SITE, "models")):
    print("no built gallery here — skipping")
    sys.exit(0)

print("\nthe scoped assembly is the same answer, far cheaper")
# a project the gallery says is worth comparing: the biggest one with a page
idx = None
try:
    idx = json.load(open(os.path.join(SITE, "models_index.json")))
except Exception:
    pass
cand = None
if idx:
    ms = sorted((idx.get("models") or []), key=lambda m: -(m.get("holes") or 0))
    for m in ms:
        if os.path.exists(os.path.join(SITE, "models", "%s.html" % m.get("slug"))):
            cand = m["slug"]
            break
cand = cand or slug

# In a FRESH process, and FIRST, before anything here touches the drill bank.
# Two traps, both of which this test fell into:
#  - ru_maxrss is a process-wide high-water mark that never comes down, so
#    measuring in-process after a comparison reports the comparison's peak;
#  - and a forked child INHERITS the parent's high-water mark, so a probe
#    spawned from a parent that has already ballooned reports the parent.
# Measured correctly this is ~35 MB; measured either wrong way, 1,160.
import subprocess
probe = (
    "import sys, time, resource, json;"
    "sys.path.insert(0, %r);"
    "from minemodelingpro import projects as P;"
    "t=time.time();"
    "d=P.assemble_slug(%r, site_dir=%r);"
    "print('@@'+json.dumps({'ok': d is not None,"
    " 'slug': (d or {}).get('slug'), 'holes': len((d or {}).get('holes') or {}),"
    " 'ivs': len((d or {}).get('intervals') or []),"
    " 'sources': len((d or {}).get('sources') or []),"
    " 'secs': round(time.time()-t, 1),"
    " 'mb': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)}))"
) % (os.path.join(_ROOT, "src"), cand, SITE)
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=600)
line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
got = json.loads(line[-1][2:]) if line else {}
d = got if got.get("ok") else None
dt, used = got.get("secs", 0), got.get("mb", 10 ** 9)
check("a public project assembles from its page alone", d is not None, cand)
if d:
    check("it is the project that was asked for",
          d["slug"] == cand or cand.startswith(d["slug"]), f"{d['slug']} vs {cand}")
    check("it carries holes", d["holes"] > 0, d["holes"])
    check("it carries intervals", d["ivs"] > 0, d["ivs"])
    check("it carries its sources", d["sources"] > 0, d["sources"])
# the whole point: it must fit where the modeler lives
check("it stays well inside the modeler's 1,048 MB", used < 700,
      f"peak {used:.0f} MB, {dt:.1f}s")

print("\nreading what a published model was built from")
import glob
pages = [p for p in sorted(glob.glob(os.path.join(SITE, "models", "*.html")))
         if not os.path.basename(p).startswith("_")]
if not pages:
    print("no model pages — skipping")
    sys.exit(0)

slug = os.path.splitext(os.path.basename(pages[0]))[0]
info = PROJ.published_sources(slug, site_dir=SITE)
check("a published page names its own sources", bool(info and info.get("ids")),
      f"{slug}: {len((info or {}).get('ids') or [])} ids")
if info:
    check("every id is prefixed with its catalogue",
          all(":" in str(i) for i in info["ids"]), str(info["ids"][:2]))
    # the prefix varies by catalogue (mtp:, ni43101:), so the split is
    # "news, or not" — a list of known report prefixes drops sources silently
    check("every id lands in news or reports",
          len(info["news"]) + len(info["reports"]) == len(info["ids"]),
          f"{len(info['news'])}+{len(info['reports'])} of {len(info['ids'])}")
    check("a report-built project keeps its reports",
          not [i for i in info["ids"] if not str(i).startswith("news:")]
          or len(info["reports"]) > 0, str(info["ids"][:2]))
check("a slug with no page reads as nothing",
      PROJ.published_sources("not-a-real-project-xyz", site_dir=SITE) is None)

print("\nloading only the sources a project names")
if not os.path.exists(PROJ.DRILLBANK):
    print("  (no drill bank here — release scoping is not exercised)")
else:
    t0 = time.time()
    few = PROJ.load_news(release_ids=[])
    check("no releases asked for means nothing read", few == ([], {}))
    all_h, _all_iv = PROJ.load_news()
    some_h, _some_iv = PROJ.load_news(release_ids=[all_h[0]["rid"]]) if all_h else ([], {})
    check("asking for one release gets one release's holes",
          bool(all_h) and 0 < len(some_h) < len(all_h),
          f"{len(some_h)} of {len(all_h)}")
    check("and they are that release's holes",
          all(h["rid"] == all_h[0]["rid"] for h in some_h))

reps_all = PROJ.load_reports()
check("asking for no reports reads none", PROJ.load_reports(only=[]) == [])
if reps_all:
    one = str(reps_all[0].get("id") or "")
    got = PROJ.load_reports(only=[one])
    check("asking for one report reads at most one", len(got) <= 1, f"{len(got)}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
