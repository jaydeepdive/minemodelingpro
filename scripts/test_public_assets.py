"""Everything a signed-out visitor's pages load must be reachable signed out.

minemodelingpro.com served its landing page with no menu bar and no way to sign
in, for days. Nothing was broken: `nav.js` simply was not on the gate's list of
files an anonymous visitor may fetch, so the browser got a 401, `MMPNav` was
never defined, and the empty `<div class="topbar">` rendered as nothing at all.
A missing script is silent — the page still returns 200 — so this is the check
that would have caught it.

    PYTHONPATH=src python3 scripts/test_public_assets.py
"""
import os
import time
import re
import glob
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import studio.server as S                                        # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("ok  " if ok else "FAIL", name,
                          "" if ok else ("  — " + detail)))


# the pages a visitor with no session is served, and the URL path each sits at
PUBLIC_PAGES = [("site/index.html", "/"), ("site/app/index.html", "/app/index.html")]

# src="..." on any tag, plus href on a stylesheet link: the things a page cannot
# render without. Navigation links to other pages are deliberately not included —
# the gallery is gated on purpose.
_SRC = re.compile(r'<(?:script|img|iframe)\b[^>]*\bsrc="([^"]+)"', re.I)
_CSS = re.compile(r'<link\b[^>]*\brel="stylesheet"[^>]*\bhref="([^"]+)"', re.I)


def _resolve(page_url, ref):
    if re.match(r"^(https?:)?//|^data:|^mailto:|^#", ref):
        return None                                  # off-site or not a fetch
    if ref.startswith("/"):
        return ref.split("?")[0].split("#")[0]
    base = page_url.rsplit("/", 1)[0] + "/"
    return os.path.normpath(os.path.join(base, ref)).replace(os.sep, "/").split("?")[0]


for rel, url in PUBLIC_PAGES:
    path = os.path.join(_ROOT, rel)
    if not os.path.exists(path):
        check("%s exists" % rel, False, "not found")
        continue
    html = open(path, encoding="utf-8").read()
    refs = _SRC.findall(html) + _CSS.findall(html)
    check("%s loads something" % rel, bool(refs), "no scripts or stylesheets found")
    for ref in refs:
        got = _resolve(url, ref)
        if got is None:
            continue
        check("%s may fetch %s" % (url, got), S._is_open(got),
              "the gate would answer 401; add it to _OPEN_EXACT in studio/server.py")
        on_disk = os.path.join(_ROOT, "site", got.lstrip("/"))
        check("%s exists on disk" % got, os.path.exists(on_disk), on_disk)

# and the landing page itself, which the gate must never redirect
for p in ("/", "/index.html", "/app/index.html"):
    check("%s is public" % p, S._is_open(p))

# The renderer Studio's private viewer fetches must be THIS deployment's code,
# not the copy committed into site/ by whichever nightly build last ran. That
# copy went stale for six days and every viewer improvement stopped at it.
import importlib                                                 # noqa: E402
_m3 = importlib.import_module("minemodelingpro.model3d")
_pkg = os.path.dirname(os.path.abspath(_m3.__file__))
routes = {getattr(r, "path", None) for r in S.app.routes}
for want in ("/models/viewer.js", "/models/_shell.html"):
    check("%s is served from the package" % want, want in routes,
          "no explicit route: the static mount would serve the committed copy")
for name in ("viewer.js", "model3d_viewer.html"):
    check("%s is in the package" % name, os.path.exists(os.path.join(_pkg, name)))
# the mount must not shadow them: explicit routes have to be registered first
_paths = [getattr(r, "path", "") for r in S.app.routes]
if "/models/viewer.js" in _paths:
    check("the renderer route is registered before the static mount",
          _paths.index("/models/viewer.js") < (_paths.index("/") if "/" in _paths else 10**9))

# No page may use an id twice. querySelector returns the FIRST match, so a
# duplicate id silently hands your code the wrong element — a <select id="del">
# added beside an existing <button id="del">Delete project</button> meant every
# `$("#del")` resolved to the button, and `.options.length` threw
# "Cannot read properties of undefined (reading 'length')" on page load.
_ID = re.compile(r'\sid="([A-Za-z0-9_-]+)"')
for rel in sorted(glob.glob(os.path.join(_ROOT, "site", "**", "*.html"), recursive=True)):
    if os.sep + "models" + os.sep in rel:
        continue                                   # generated model pages
    ids = _ID.findall(open(rel, encoding="utf-8").read())
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    check("%s has no duplicate id" % os.path.relpath(rel, _ROOT), not dupes, dupes)




# --------------------------------------------------------------------------
# The app must start when Postgres does not answer.
#
# It used to not: `wait_for_db` raised SystemExit after 60 seconds, App Platform
# restarted the process, and it exited again. A crash loop serves 503 on every
# route — the public gallery, which needs no database at all, and /api/health,
# which is the one page that could have said what was wrong. With one instance
# and no replica, refusing to start is a total outage rather than a safe
# failure, so the app now starts degraded and says so.
print("\n== with no database")
_saved = os.environ.pop("DATABASE_URL", None)
try:
    from fastapi.testclient import TestClient                   # noqa: E402
    from studio import store                                    # noqa: E402

    store.drop_db()
    S.DBSTATE.update(ok=False, error=None, schema=False)
    with TestClient(S.app, raise_server_exceptions=False) as c:
        # the health facts are gathered on a thread, off the request path, so
        # give that thread its first pass rather than racing it
        for _ in range(100):
            if S._FACTS.get("at"):
                break
            time.sleep(0.02)
        r = c.get("/api/health")
        check("the app starts and answers its health check", r.status_code == 200,
              r.status_code)
        body = r.json() if r.status_code == 200 else {}
        check("which reports the database as down rather than pretending",
              body.get("ok") is False and body.get("db") == "unreachable", body)
        check("and says what the matter is", bool(body.get("db_error")), body)
        for path in ("/", "/nav.js"):
            check("%s still serves — it needs no database" % path,
                  c.get(path).status_code == 200, c.get(path).status_code)
        r = c.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
        check("a route that needs the database says so, with a sentence",
              r.status_code == 503 and "database" in (r.text or ""),
              (r.status_code, (r.text or "")[:80]))
except Exception as e:                                          # noqa: BLE001
    check("the app starts with no database", False, "%s: %s" % (type(e).__name__, e))
finally:
    if _saved is not None:
        os.environ["DATABASE_URL"] = _saved
    store.drop_db()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
