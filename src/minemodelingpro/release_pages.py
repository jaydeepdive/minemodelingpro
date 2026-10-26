"""Reading the release itself, not just the table somebody pulled out of it.

The drill bank carries one row per release — id, company, title, url — and the
holes and intervals an upstream parser found in it. For 722 of the 4,787
releases in the bank that parser found nothing, and for every release it found
something in, it found only assays: the collar table it could not line up, the
strike length the geologist quoted, the geophysical survey that defined the
target, the orientation of the vein, all of it stayed in the prose and never
reached a model.

None of that can be fixed upstream, and it does not need to be. The bank keeps
the release's URL. So MMP fetches the release, reads it with the same
extractors it already points at technical reports, and stores the result beside
the bank under its own reader version. The upstream feed is untouched; what MMP
knows about each release stops being whatever one parser happened to produce.

What comes back per release:

  * collars and intervals, in the importer's own shapes, so a recovered hole
    merges by normalised hole id like any other source;
  * the body's stated size and attitude (``extent``), which bounds how far the
    domain may carry a vein and orients it by the rock rather than by the drill
    pattern;
  * the geophysics, including any anomaly the release calls untested — which is
    the company's own statement of where it thinks the deposit continues; and
  * anything the release says about the rock itself.

Nothing here fetches on import. ``run()`` does the fetching, once per release,
and everything it reads is cached on disk, so a reader bump re-reads from the
cache rather than from the web.
"""
import os
import re
import json
import time
import html
import sqlite3

READER = "rp1"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEEP = os.path.join(_ROOT, "data", "keep")
DRILLBANK = os.path.join(KEEP, "drillbank.sqlite")
OUT_DIR = os.path.join(KEEP, "mmp_release_text")
RAW_DIR = os.path.join(KEEP, "mmp_release_raw")

_DROP = re.compile(r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", re.I | re.S)
_BREAK = re.compile(r"</(p|div|li|tr|h[1-6]|table|section|article)\s*>|<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
# the boilerplate every newswire wraps a release in
_CUT_AT = re.compile(r"(?:forward[- ]looking\s+statements?|cautionary\s+(?:note|statement)|"
                     r"neither\s+the\s+(?:tsx|canadian)\s+venture\s+exchange|"
                     r"this\s+news\s+release\s+(?:does\s+not|contains))", re.I)


def html_text(raw):
    """A release page as readable text.

    Deliberately crude — tags out, entities decoded, blocks separated by
    newlines — because the extractors downstream want prose and numbers, not
    structure, and anything cleverer is one site redesign from breaking."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    t = _DROP.sub(" ", raw)
    t = _BREAK.sub("\n", t)
    t = _TAG.sub(" ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n\s*\n\s*", "\n\n", t)
    return t.strip()


def pages_of(raw, content_type=None, url=None):
    """The release as a list of pages, whatever it was served as.

    A newswire serves HTML; a company's own filing is often a PDF behind a
    signed link. Both end up as the same list the report extractors take."""
    ct = (content_type or "").lower()
    looks_pdf = (isinstance(raw, bytes) and raw[:5] == b"%PDF") or "pdf" in ct \
        or (url or "").lower().endswith(".pdf")
    if looks_pdf:
        try:
            import pymupdf
            doc = pymupdf.open(stream=raw, filetype="pdf")
            out = []
            for pg in doc:
                try:
                    out.append(pg.get_text())
                except Exception:
                    out.append("")
            return out
        except Exception:
            return []
    txt = html_text(raw)
    m = _CUT_AT.search(txt)
    if m and m.start() > 400:
        txt = txt[:m.start()]           # the legal boilerplate is not the release
    return [txt] if txt else []


def read(pages, url=None):
    """Everything the extractors can say about one release's text."""
    from minemodelingpro import drill_tables as DT
    from minemodelingpro import extent as EXT
    from minemodelingpro import geophysics as GP
    out = {"reader": READER, "url": url,
           "chars": sum(len(p or "") for p in pages), "n_pages": len(pages)}
    if not pages:
        return out
    try:
        cols, ivs = DT.extract_tables(DT.TextDoc([p or "" for p in pages]))
    except Exception as e:
        cols, ivs = [], []
        out["table_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    out["collars"] = [c for c in (cols or []) if str(c.get("hole") or "").strip()][:400]
    out["intervals"] = [r for r in (ivs or []) if str(r.get("hole") or "").strip()][:4000]
    try:
        out["extent"] = EXT.stated(pages) or None
        out["attitude"] = EXT.attitude(pages) or None
    except Exception:
        out["extent"] = out["attitude"] = None
    try:
        sv, tg = GP.surveys(pages), GP.targets(pages)
        out["geophysics"] = {"surveys": sv[:40], "targets": tg[:20]} if (sv or tg) else None
        out["rock"] = GP.rock(pages) or None
    except Exception:
        out["geophysics"] = out["rock"] = None
    return out


def path_for(rid):
    return os.path.join(OUT_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", str(rid)) + ".json")


def load(rid):
    try:
        return json.load(open(path_for(rid), encoding="utf-8"))
    except Exception:
        return None


def load_all(release_ids=None):
    """Stored readings, by release id. Only those read by the CURRENT reader."""
    out = {}
    if not os.path.isdir(OUT_DIR):
        return out
    want = {str(x) for x in release_ids} if release_ids is not None else None
    for fn in os.listdir(OUT_DIR):
        if not fn.endswith(".json"):
            continue
        rid = fn[:-5]
        if want is not None and rid not in want:
            continue
        rec = load(rid)
        if rec and rec.get("reader") == READER:
            out[rid] = rec
    return out


def save(rid, rec):
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = dict(rec)
    rec["id"] = rid
    rec["read_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = path_for(rid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path_for(rid))
    return rec


def _raw_path(rid):
    return os.path.join(RAW_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", str(rid)) + ".bin")


def fetch(url, rid, timeout=60):
    """The release page, from the cache if it has been fetched before.

    Cached as fetched, so bumping the reader re-reads every release without
    asking the newswires for any of them a second time."""
    os.makedirs(RAW_DIR, exist_ok=True)
    p = _raw_path(rid)
    if os.path.exists(p):
        with open(p, "rb") as fh:
            return fh.read(), None
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "mmp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ct = resp.headers.get("Content-Type")
    with open(p, "wb") as fh:
        fh.write(raw)
    return raw, ct


def releases(limit=None, only=None, with_url=True):
    """The bank's releases, newest first."""
    if not os.path.exists(DRILLBANK):
        return []
    con = sqlite3.connect(DRILLBANK)
    try:
        q = ("SELECT id, url, title, company, published, n_holes, n_intervals "
             "FROM releases" + (" WHERE url IS NOT NULL AND url != ''" if with_url else "")
             + " ORDER BY published DESC")
        rows = [dict(zip(("id", "url", "title", "company", "published",
                          "n_holes", "n_intervals"), r))
                for r in con.execute(q)]
    finally:
        con.close()
    if only:
        want = {str(x) for x in only}
        rows = [r for r in rows if r["id"] in want]
    return rows[:limit] if limit else rows


def run(limit=None, only=None, max_seconds=None, refresh=False, verbose=True,
        empty_first=False):
    """Read releases that have not been read by this reader.

    `empty_first` takes the releases the upstream parser got nothing out of
    before the rest, because those are the ones with the most to recover."""
    t0 = time.time()
    rows = releases(only=only)
    if empty_first:
        rows.sort(key=lambda r: (int(r.get("n_holes") or 0) > 0,))
    done = fail = 0
    for r in rows:
        if limit and done >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            if verbose:
                print("[release_pages] time budget reached")
            break
        got = load(r["id"])
        if got and got.get("reader") == READER and not refresh:
            continue
        try:
            raw, ct = fetch(r["url"], r["id"])
            rec = read(pages_of(raw, ct, r["url"]), url=r["url"])
            rec["title"] = r.get("title")
            save(r["id"], rec)
            done += 1
            if verbose:
                g = rec.get("geophysics") or {}
                print("[release_pages] %s %-28s %6d chars  %3d collars %4d intervals "
                      "%2d surveys %2d targets%s"
                      % (r["id"], (r.get("company") or "")[:28], rec.get("chars", 0),
                         len(rec.get("collars") or []), len(rec.get("intervals") or []),
                         len(g.get("surveys") or []), len(g.get("targets") or []),
                         "  extent" if rec.get("extent") else ""))
        except Exception as e:
            fail += 1
            if verbose:
                print("[release_pages] FAILED %s: %s" % (r["id"], str(e)[:120]))
    if verbose:
        print("[release_pages] read %d release(s), %d failed" % (done, fail))
    return done


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    run(limit=int(a[a.index("--limit") + 1]) if "--limit" in a else None,
        max_seconds=int(a[a.index("--max-seconds") + 1]) if "--max-seconds" in a else None,
        refresh="--refresh" in a, empty_first="--empty-first" in a)
