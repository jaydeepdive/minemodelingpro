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
import re
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

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
