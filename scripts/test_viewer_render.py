#!/usr/bin/env python3
"""A deployed model page must still render with the CURRENT renderer.

A model page is a baked artifact: its shell is whatever the build wrote that day,
while `/models/viewer.js` is served from the package and is this deployment's
current code. The two drift every time a control is added — and a control the
page does not have is a null, so `$("L-body").onchange = fn` throws at module
level, the renderer stops before it sizes the canvas, and the whole page is
black. Every panel dead, over one optional toggle. That is what happened to the
public gallery on 2026-10-26.

So this renders real committed pages, and a deliberately impoverished one, with
the renderer as it stands now. Needs playwright; skips cleanly without it.
"""
import os
import re
import sys
import glob
import json
import shutil
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


def have_playwright():
    try:
        subprocess.run(["node", "-e", "require('playwright')"],
                       check=True, capture_output=True, timeout=60)
        return True
    except Exception:
        return False


if not have_playwright():
    print("playwright not available — skipping the render check")
    sys.exit(0)

pages = sorted(glob.glob(os.path.join(ROOT, "site", "models", "*.html")))
pages = [p for p in pages if not os.path.basename(p).startswith("_")]
if not pages:
    print("no built model pages to render — skipping")
    sys.exit(0)

# a spread of them: the first, the last and one in the middle
pick = [pages[0], pages[len(pages) // 2], pages[-1]][: max(1, min(3, len(pages)))]

tmp = tempfile.mkdtemp(prefix="mmp-render-")
shutil.copy(os.path.join(ROOT, "src", "minemodelingpro", "viewer.js"), tmp)
three = os.path.join(ROOT, "site", "models", "three.min.js")
if not os.path.exists(three):
    print("three.min.js is not in site/models — skipping")
    sys.exit(0)
shutil.copy(three, tmp)

names = []
for p in pick:
    shutil.copy(p, tmp)
    names.append(os.path.basename(p))

# And a shell poorer than any real page: every control added since the renderer
# split, removed. Built from a real page so it never goes stale, and it is the
# direct test of the rule — an optional control that is missing must cost
# nothing but that control.
OPTIONAL_IDS = ["L-body", "L-zlabels", "body-op", "body-op-v", "body-op-row",
                "body-note", "body-why", "zlab-row", "zonebox", "zones", "zone-n",
                "pZones", "ivtbl", "iv-n", "ivnote", "checkbox", "checknote",
                "reconnote"]
src = open(pick[0], encoding="utf-8").read()
stripped = src
for i in OPTIONAL_IDS:
    # drop the whole element that carries this id, however it is written
    stripped = re.sub(r'<(\w+)[^>]*\bid="%s"[^>]*>.*?</\1>' % re.escape(i),
                      "", stripped, flags=re.S)
    stripped = re.sub(r'<(\w+)[^>]*\bid="%s"[^>]*/?>' % re.escape(i), "", stripped)
left = [i for i in OPTIONAL_IDS if ('id="%s"' % i) in stripped]
open(os.path.join(tmp, "_stripped.html"), "w", encoding="utf-8").write(stripped)
check("the impoverished shell really lost its optional controls", not left, left)
names.append("_stripped.html")

runner = os.path.join(tmp, "run.js")
open(runner, "w").write(r"""
const { chromium } = require('playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const dir = process.argv[2], pages = process.argv.slice(3);
const types = {'.html':'text/html','.js':'text/javascript','.json':'application/json'};
const srv = http.createServer((q, s) => {
  const f = path.join(dir, decodeURIComponent(q.url.split('?')[0]).replace(/^\//, ''));
  fs.readFile(f, (e, d) => e ? (s.writeHead(404), s.end())
    : (s.writeHead(200, {'Content-Type': types[path.extname(f)] || 'application/octet-stream'}), s.end(d)));
});
srv.listen(8766, async () => {
  const b = await chromium.launch();
  const out = [];
  for (const name of pages) {
    const p = await b.newPage({viewport:{width:1280,height:720}});
    const errs = [];
    p.on('pageerror', e => errs.push(String(e.message || e)));
    p.on('console', m => { if (m.type() === 'error' && !/favicon|404/.test(m.text()))
                             errs.push('console: ' + m.text().slice(0,200)); });
    try {
      await p.goto('http://127.0.0.1:8766/' + name, {waitUntil:'networkidle', timeout:60000});
      await p.waitForTimeout(4000);
      const st = await p.evaluate(() => {
        const cv = document.querySelector('canvas');
        return { canvas: !!cv, w: cv ? cv.width : 0, h: cv ? cv.height : 0,
                 panels: document.querySelectorAll('.panel').length };
      });
      out.push({ name, ...st, errors: errs });
    } catch (e) {
      out.push({ name, canvas:false, w:0, h:0, panels:0, errors: errs.concat([String(e.message||e)]) });
    }
    await p.close();
  }
  await b.close(); srv.close();
  console.log('@@' + JSON.stringify(out));
});
""")
try:
    r = subprocess.run(["node", runner, tmp] + names,
                       capture_output=True, text=True, timeout=600)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    results = json.loads(line[-1][2:]) if line else []
except Exception as e:
    print("  FAIL could not run the renderer: %s" % e)
    sys.exit(1)

check("every page was rendered", len(results) == len(names), (len(results), len(names)))
for res in results:
    nm = res["name"]
    check("%s throws nothing" % nm, not res["errors"], res["errors"][:2])
    # the canvas keeps its default 300x150 when the renderer dies before sizing it
    check("%s sizes its canvas (not the 300x150 of a dead renderer)" % nm,
          res["canvas"] and res["w"] > 400 and res["h"] > 300,
          (res["w"], res["h"]))
    check("%s draws its panels" % nm, res["panels"] >= 2, res["panels"])

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
