"""Forward-only NI 43-101 collector from ceo.ca's PUBLIC #sedar feed.

Why this exists: SEDAR+ Akamai/hCaptcha-gates its search and rate-limits document
downloads per IP (the browser collector nets ~1 report per session and the IP gets
clamped under load). ceo.ca mirrors every SEDAR filing and serves the raw PDF from
an OPEN endpoint -- https://ceo.ca/api/sedar-document/<slug> -- with no login, no
captcha and no per-IP download throttle. This reads ONLY the public feed at
https://ceo.ca/sedar (no ceo.ca account is touched, so nothing can put the user's
work login at risk), keeps Technical Report (NI 43-101) filings, downloads them via
that open endpoint, archives each to the report-archive release, and records them in
a manifest so the existing 43-101 extractor / 3D-model pipeline ingests them
unchanged.

Forward-only by design: the public feed is a rolling window, so this is scheduled
often and accumulates every new 43-101 as it is filed. Resumable via the manifest.

Run:  python -m minemodelingpro.ceo_sedar [--ingest] [--scrolls N] [--sync]
"""
import os
import re
import sys
import json
import time
import argparse
import datetime
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PDFS = os.path.join(_ROOT, "data", "keep", "sedar_pdfs")            # gitignored; durable copy -> release
MANIFEST = os.path.join(_ROOT, "data", "keep", "ceo_sedar_manifest.json")
FEED = "https://ceo.ca/sedar"
DOC_API = "https://ceo.ca/api/sedar-document/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ceo.ca slugs embed the document type, e.g.
#   ABC-2026-09-09-technical-report-ni-43-101-english-1a2b.pdf
# so a technical report is selectable on the slug alone.
_SLUG_RE = re.compile(r"content/sedar/([A-Za-z0-9][A-Za-z0-9._-]+?\.pdf)")
_TR_RE = re.compile(r"technical[-_]?report|43[-_]?101", re.I)
_META_RE = re.compile(r"^([A-Za-z0-9.]+)-(\d{4}-\d{2}-\d{2})-(.+?)-[0-9a-fA-F]{3,8}\.pdf$")
DOCTYPE = "Technical report (NI 43-101)"


def _load(path, default):
    try:
        return json.load(open(path)) if os.path.exists(path) else default
    except Exception:
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=1)
    os.replace(tmp, path)


def _meta(slug):
    m = _META_RE.match(slug)
    if not m:
        return {"ticker": None, "date": None, "doctype": None}
    return {"ticker": m.group(1), "date": m.group(2),
            "doctype": m.group(3).replace("-", " ")}


def feed_slugs(scrolls=10, log=print):
    """Every content/sedar/<slug>.pdf visible on the public feed right now. The feed
    renders client-side (websocket), newest at the bottom; we wait for it to hydrate
    then scroll UP to widen the history window."""
    from playwright.sync_api import sync_playwright
    html = ""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage",
                                 "--disable-blink-features=AutomationControlled"])
        pg = browser.new_page(user_agent=UA)
        try:
            pg.goto(FEED, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"[ceo] feed nav: {str(e)[:90]}")
        # wait for the feed to hydrate (message links appear)
        end = time.time() + 45
        while time.time() < end:
            pg.wait_for_timeout(2500)
            try:
                html = pg.content()
            except Exception:
                html = ""
            if "content/sedar/" in html:
                break
        # scroll up to pull older messages into the DOM
        for _ in range(max(0, scrolls)):
            try:
                pg.mouse.wheel(0, -4200)
            except Exception:
                pass
            pg.wait_for_timeout(1400)
        try:
            html = pg.content()
        except Exception:
            pass
        browser.close()
    return list(dict.fromkeys(_SLUG_RE.findall(html)))


def _download(slug, dest, log):
    """Pull the raw PDF from ceo.ca's open document endpoint. Returns True on a
    valid PDF. Streams to disk so large (20-40 MB) technical reports are fine."""
    req = urllib.request.Request(DOC_API + slug, headers={
        "User-Agent": UA,
        "Referer": "https://ceo.ca/content/sedar/" + slug,
        "Accept": "application/pdf,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            head = r.read(5)
            if not head.startswith(b"%PDF"):
                log(f"  not a pdf (magic {head!r})")
                return False
            with open(dest, "wb") as f:
                f.write(head)
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
        return os.path.getsize(dest) > 1024
    except Exception as e:
        log(f"  fetch error: {str(e)[:100]}")
        try:
            os.path.exists(dest) and os.remove(dest)
        except Exception:
            pass
        return False


def sync_downloads(log=print):
    """Ensure every manifest PDF is present locally (re-fetch from the open endpoint
    if missing) so a fresh build runner can ingest them. Returns count present."""
    os.makedirs(PDFS, exist_ok=True)
    manifest = _load(MANIFEST, [])
    have = 0
    for r in manifest:
        dest = os.path.join(PDFS, r["file"])
        if os.path.exists(dest) and os.path.getsize(dest) > 1024:
            have += 1
            continue
        if _download(r["filing_ref"], dest, log):
            have += 1
            time.sleep(1.0)
    log(f"[ceo] synced {have}/{len(manifest)} manifest PDFs present locally")
    return have


def collect(scrolls=10, ingest=False, log=print):
    os.makedirs(PDFS, exist_ok=True)
    manifest = _load(MANIFEST, [])
    have = {r.get("filing_ref") for r in manifest}
    slugs = feed_slugs(scrolls=scrolls, log=log)
    trs = [s for s in slugs if _TR_RE.search(s)]
    log(f"[ceo] feed: {len(slugs)} filings on view, {len(trs)} technical report(s)")
    new = 0
    for s in trs:
        if s in have:
            continue
        dest = os.path.join(PDFS, "ceo_" + s)
        if not (os.path.exists(dest) and os.path.getsize(dest) > 1024):
            time.sleep(1.5)                      # gentle; ceo.ca CDN, no throttle seen
            if not _download(s, dest, log):
                continue
        meta = _meta(s)
        row = {"file": "ceo_" + s, "filing_ref": s, "company": meta["ticker"],
               "project": None, "jurisdiction": None, "commodity": None,
               "submitted": meta["date"], "doctype": DOCTYPE,
               "sedar_url": "https://ceo.ca/content/sedar/" + s, "source": "ceo.ca",
               "size_kb": os.path.getsize(dest) // 1024,
               "collected": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
        manifest.append(row)
        have.add(s)
        new += 1
        _save(MANIFEST, manifest)
        log(f"  ✓ {meta['ticker']} {meta['date']} {meta['doctype']} ({row['size_kb']} KB)  [{new}]")
        if os.environ.get("GITHUB_TOKEN"):        # durable retention in the release
            try:
                from minemodelingpro import report_archive
                url = report_archive.archive_pdf(dest, "ceo_" + os.path.splitext(s)[0])
                if url:
                    row["archive_url"] = url
                    _save(MANIFEST, manifest)
            except Exception as e:
                log(f"  archive skip: {str(e)[:70]}")
    if ingest and new:
        try:
            from minemodelingpro import sedar
            sedar.ingest_folder(PDFS, MANIFEST)
        except Exception as e:
            log(f"[ceo] ingest error: {str(e)[:120]}")
    log(f"[ceo] collected {new} new technical report(s); manifest now {len(manifest)}")
    return {"new": new, "manifest": len(manifest)}


def main():
    ap = argparse.ArgumentParser(description="Forward-only NI 43-101 collector from the public ceo.ca #sedar feed")
    ap.add_argument("--scrolls", type=int, default=10, help="history window: how many times to scroll the feed up")
    ap.add_argument("--ingest", action="store_true", help="extract downloaded reports into the model shard store")
    ap.add_argument("--sync", action="store_true", help="only re-fetch manifest PDFs missing locally, then ingest")
    a = ap.parse_args()
    if a.sync:
        sync_downloads()
        if a.ingest:
            from minemodelingpro import sedar
            sedar.ingest_folder(PDFS, MANIFEST)
        return
    collect(scrolls=a.scrolls, ingest=a.ingest)


if __name__ == "__main__":
    main()
