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

/* Clicking a hole must put its assays on screen, and measuring between two holes
   must agree with the collar coordinates in the page's own model JSON. Driven
   with real mouse events so the click-versus-drag split is exercised too. */
async function interact(p) {
  const spots = await p.evaluate(() => {
    /* Sweep the viewport for points that really are over a hole. A default view
       often frames the drilling at depth with the collars out of shot, so a
       collar's projected position is not a place a user could click. */
    const r = host.getBoundingClientRect(), on = [], seen = {}, cv = document.querySelector("canvas");
    let empty = null;
    const done = () => on.length >= 2 && empty;
    for (let gx = 0.05; gx < 0.96 && !done(); gx += 0.05)
      for (let gy = 0.05; gy < 0.96 && !done(); gy += 0.05) {
        const px = gx * W(), py = gy * Hh();
        const clickable = document.elementFromPoint(px + r.left, py + r.top) === cv;
        const iv = clickable ? pickInterval(px, py, 6) : null;
        const hh = iv ? { h: iv.v.h } : (clickable ? pickHole(px, py, 5) : null);
        if (hh && on.length < 2 && !seen[hh.h.id]) {
          seen[hh.h.id] = 1;
          on.push({ id: hh.h.id, x: px + r.left, y: py + r.top }); }
        if (!hh && !empty && clickable &&
            !pickInterval(px, py, 26) && !pickHole(px, py, 26))
          empty = { x: px + r.left, y: py + r.top }; }
    return { on, empty, holes: HO.length };
  });
  const out = { holes: spots.holes, clicked: null, card: 0, pin: "", chip: false,
                dragKept: null, cleared: null, meas: null, measErr: null,
                readout: "", emptyCleared: null };
  if (!spots.on.length) return out;

  await p.mouse.click(spots.on[0].x, spots.on[0].y);
  await p.waitForTimeout(220);
  Object.assign(out, await p.evaluate(() => ({
    clicked: SEL ? SEL.id : null, wanted: null,
    card: document.getElementById("hcard").innerHTML.length,
    pin: document.getElementById("hpin").style.display,
    chip: /ff7a18/.test(document.getElementById("labels").innerHTML) })));
  out.wanted = spots.on[0].id;

  /* a drag is not a click: orbiting must not keep opening cards */
  if (spots.empty) {
    await p.mouse.move(spots.empty.x, spots.empty.y);
    await p.mouse.down();
    await p.mouse.move(spots.empty.x + 60, spots.empty.y + 25, { steps: 6 });
    await p.mouse.up();
    await p.waitForTimeout(150);
    out.dragKept = await p.evaluate(() => (SEL ? SEL.id : null));
  }

  /* measuring: the distance must match the collars in the page's own JSON */
  if (spots.on.length > 1) {
    const ids = [spots.on[0].id, spots.on[1].id];
    out.meas = await p.evaluate(ids => {
      const a = HO.find(h => h.id === ids[0]), b = HO.find(h => h.id === ids[1]);
      setMeas(a, b);
      const A = M.holes.find(r => r[0] === ids[0]), B = M.holes.find(r => r[0] === ids[1]);
      const want = Math.hypot(B[1] - A[1], B[2] - A[2], B[3] - A[3]);
      return { want, got: MEAS.collar, closest: MEAS.d,
               ma: MEAS.ma, mb: MEAS.mb, da: a.depth || 0, db: b.depth || 0,
               readout: document.getElementById("mline").className };
    }, ids).catch(e => { out.measErr = String(e.message || e); return null; });
    await p.waitForTimeout(120);
    if (out.meas) out.readout = out.meas.readout;
  }

  /* the report's geophysics and rock properties. No built page carries these
     until the report corpus is re-extracted, so they are injected and drawn
     here: the renderer must handle them, and must handle their absence. */
  out.ground = await p.evaluate(() => {
    const before = !!document.getElementById("groundbox");
    M.geophysics = {surveys: [{method: "IP/resistivity", page: 23, platform: "ground",
                               line_km: 102, year: 2018, text: "an IP survey"},
                              {method: "magnetics", page: 23, platform: "airborne",
                               line_km: 277, year: 1991, text: "an airborne magnetic survey"}],
                    targets: [{page: 31, explained: false, untested: true,
                               text: "The chargeability anomaly remains untested."}]};
    M.rock = {density_low: {median: 2.43, n: 1, text: "bulk density"},
              density_high: {median: 2.63, n: 1, text: "bulk density"},
              slope_angle_deg: {median: 45, n: 1, text: "slope angle"}};
    drawGround();
    const b = document.getElementById("groundbox");
    return {before, drawn: !!b, txt: b ? b.textContent.replace(/\s+/g, " ") : ""};
  });

  await p.keyboard.press("Escape");
  await p.keyboard.press("Escape");
  await p.waitForTimeout(150);
  out.cleared = await p.evaluate(() => (SEL ? SEL.id : null) + "|" + (MEAS ? "meas" : "none"));

  /* clicking empty sky puts the card away */
  if (spots.empty) {
    await p.mouse.click(spots.on[0].x, spots.on[0].y);
    await p.waitForTimeout(180);
    const reachable = await p.evaluate(e => document.elementFromPoint(e.x, e.y) ===
      document.querySelector("canvas"), spots.empty);
    if (reachable) {                       // the open card must not be in the way
      await p.mouse.click(spots.empty.x, spots.empty.y);
      await p.waitForTimeout(150);
      out.emptyCleared = await p.evaluate(() =>
        (SEL ? "still:" + SEL.id : "none") + "|" + document.getElementById("hcard").className);
    }
  }
  return out;
}

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
        /* the deposit's own geometry, where the page was built with it */
        const E = M.elements[EL] || {};
        return { canvas: !!cv, w: cv ? cv.width : 0, h: cv ? cv.height : 0,
                 panels: document.querySelectorAll('.panel').length,
                 wantSurf: !!(E.surface && E.surface.tris),
                 gotSurf: !!surfMesh,
                 surfTris: surfMesh ? surfMesh.geometry.getIndex().count / 3 : 0,
                 payloadTris: E.surface ? E.surface.tris : 0,
                 styleSwitch: !!document.getElementById('body-style') };
      });
      const act = await interact(p);
      out.push({ name, ...st, act, errors: errs });
    } catch (e) {
      out.push({ name, canvas:false, w:0, h:0, panels:0, act:null,
                 errors: errs.concat([String(e.message||e)]) });
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

    if res.get("wantSurf"):
        check("%s draws the deposit's geometry, not only its cells" % nm,
              res.get("gotSurf") and res.get("surfTris", 0) >= res.get("payloadTris", 0),
              (res.get("gotSurf"), res.get("surfTris"), res.get("payloadTris")))
        check("%s offers the shape/cells switch" % nm, res.get("styleSwitch"),
              res.get("styleSwitch"))

    a = res.get("act") or {}
    if not a.get("holes"):
        continue
    if a.get("clicked") is None and not a.get("card"):
        check("%s puts a hole card up when a hole is clicked" % nm, False,
              "nothing selected — no hole was on screen to click" if not a else a)
        continue
    check("%s selects the hole that was clicked" % nm,
          a.get("clicked") == a.get("wanted"), (a.get("clicked"), a.get("wanted")))
    check("%s fills the hole card" % nm, a.get("card", 0) > 120, a.get("card"))
    check("%s marks the selected hole on screen (collar ring + id chip)" % nm,
          a.get("pin") == "block" and a.get("chip"), (a.get("pin"), a.get("chip")))
    if a.get("dragKept") is not None or a.get("clicked"):
        check("%s keeps the selection through a drag (a drag is not a click)" % nm,
              a.get("dragKept") == a.get("clicked"), (a.get("dragKept"), a.get("clicked")))
    m = a.get("meas")
    if m:
        check("%s measures collar to collar within 0.5 m of the model JSON" % nm,
              abs(m["got"] - m["want"]) < 0.5, (m["got"], m["want"]))
        check("%s never reports a closest approach wider than the collars" % nm,
              m["closest"] <= m["got"] + 1e-6, (m["closest"], m["got"]))
        check("%s puts the closest approach on both holes" % nm,
              -1e-6 <= m["ma"] <= m["da"] + 1e-6 and -1e-6 <= m["mb"] <= m["db"] + 1e-6,
              (m["ma"], m["da"], m["mb"], m["db"]))
        check("%s shows the measurement readout" % nm, a.get("readout") == "on", a.get("readout"))
    gr = a.get("ground") or {}
    if gr:
        check("%s shows nothing about geophysics when the page carries none" % nm,
              gr.get("before") is False, gr.get("before"))
        check("%s draws the report's geophysics and rock properties when it has them" % nm,
              gr.get("drawn") and "IP/resistivity" in gr.get("txt", "")
              and "2.43" in gr.get("txt", ""), gr.get("txt", "")[:160])
        check("%s marks an untested anomaly as the report's words, not a measurement" % nm,
              "untested" in gr.get("txt", "").lower(), gr.get("txt", "")[:160])

    check("%s clears the selection and the measurement on Escape" % nm,
          a.get("cleared") == "null|none", a.get("cleared"))
    if a.get("emptyCleared") is not None:
        check("%s puts the card away when empty space is clicked" % nm,
              a["emptyCleared"] == "none|", a["emptyCleared"])

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
