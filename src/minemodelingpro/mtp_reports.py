"""Technical reports from the MineTerminalPro catalogue.

MMP's accuracy is limited more by how many filed technical reports it holds than by
anything in the estimator: a report supplies the block size, the density, the grade
cap, the NSR formula, the metallurgy and — where it states one — the published
resource MMP is scored against. The ceo.ca feed collects new filings as they appear,
which is the right way to stay current and the wrong way to acquire a back
catalogue.

MineTerminalPro has already done that work. Its company pages carry a SEDAR+ filings
tab backed by ``/api/tech-reports/{ticker}``, which returns, per project, the current
technical report and the earlier ones, each with a filing date, a document type, the
qualified persons, and a direct PDF link. It even reports which page the resource
table starts on.

So this walks the catalogue, takes the metadata, and hands the PDFs to the same
extractor every other report goes through. Nothing here parses a report — it only
finds them.

    python -m minemodelingpro.mtp_reports survey      # how many, and what kind
    python -m minemodelingpro.mtp_reports collect     # download and queue for extraction
"""
import os
import re
import json
import time
import hashlib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("MTP_BASE", "https://mineterminalpro.com")
UA = "mmp/1.0 (MineModelingPro report collector)"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOGUE = os.path.join(_ROOT, "data", "keep", "mmp_mtp_catalogue.json")
# be a considerate guest: a few connections, a pause between calls
WORKERS = int(os.environ.get("MTP_WORKERS", "4"))
PAUSE = float(os.environ.get("MTP_PAUSE", "0.15"))


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def tickers():
    """Every company the terminal tracks, minus the ones it marks as dead."""
    d = _get(f"{BASE}/api/companies-quotes")
    dead = set(d.get("deadList") or [])
    return sorted(t for t in (d.get("data") or {}) if t not in dead)


def for_ticker(t):
    """Every technical report the catalogue holds for one company, flattened.

    A project carries one current report and a list of earlier ones; both matter,
    because an earlier report often states parameters a later summary omits, and
    MMP already takes report parameters newest-first per key."""
    try:
        d = _get(f"{BASE}/api/tech-reports/{urllib.parse.quote(t)}")
    except Exception:
        return []
    out = []
    for proj in (d.get("projects") or []):
        for which in ("current", "earlier"):
            v = proj.get(which)
            for r in ([v] if isinstance(v, dict) else (v or [])):
                if not isinstance(r, dict) or not r.get("url"):
                    continue
                out.append({
                    "ticker": t, "exchange": d.get("exchange"),
                    "key": r.get("key"), "url": r.get("url"), "src": r.get("src"),
                    "filed": r.get("filed"), "amended": bool(r.get("amended")),
                    "type": r.get("type"), "type_name": r.get("type_name"),
                    "title": r.get("title"), "project": r.get("project") or proj.get("project"),
                    "project_kind": r.get("project_kind"),
                    "effective": r.get("effective"), "country": r.get("country"),
                    "region": r.get("region"), "qps": r.get("qps") or [],
                    "resource_page": r.get("see_resources_p"),
                    "current": which == "current",
                })
    return out


def catalogue(limit=None, verbose=True):
    """Walk the whole catalogue and return every technical report in it."""
    ts = tickers()
    if limit:
        ts = ts[:limit]
    rows, done = [], 0
    def one(t):
        time.sleep(PAUSE)
        return for_ticker(t)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for got in ex.map(one, ts):
            rows.extend(got)
            done += 1
            if verbose and done % 100 == 0:
                print(f"  [mtp] {done}/{len(ts)} companies, {len(rows)} reports", flush=True)
    # one row per document: the same report is listed under every project it covers
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda x: (x.get("filed") or "", x.get("title") or "")):
        k = r.get("key") or hashlib.sha1((r["url"] or "").encode()).hexdigest()[:16]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def save(rows, path=CATALOGUE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "n": len(rows), "reports": rows}, open(path, "w"), indent=1)
    return path


def load(path=CATALOGUE):
    try:
        return json.load(open(path)).get("reports") or []
    except Exception:
        return []


def _norm_title(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def already_held(rows, reports_dir=None):
    """Which of these MMP already has, matched on title and filing date.

    The corpus is keyed by its own ids, so the comparison has to be on what the
    documents say rather than on where they came from."""
    from minemodelingpro import drill_tables as D
    reports_dir = reports_dir or D.OUT_DIR
    have = set()
    for fn in os.listdir(reports_dir) if os.path.isdir(reports_dir) else []:
        if not fn.endswith(".json"):
            continue
        try:
            r = json.load(open(os.path.join(reports_dir, fn)))
        except Exception:
            continue
        for key in (r.get("title_project"), r.get("project"), r.get("company")):
            if key:
                have.add(_norm_title(key) + "|" + str(r.get("date") or "")[:10])
        have.add(_norm_title(r.get("cover_company")) + "|" + str(r.get("date") or "")[:10])
    new = []
    for r in rows:
        k = _norm_title(r.get("title"))[:60] + "|" + str(r.get("filed") or "")[:10]
        if k in have:
            continue
        new.append(r)
    return new


def fetch_pdf(row, dest_dir, timeout=240):
    """Download one report. Returns the local path, or None."""
    os.makedirs(dest_dir, exist_ok=True)
    key = row.get("key") or hashlib.sha1(row["url"].encode()).hexdigest()[:16]
    path = os.path.join(dest_dir, f"mtp_{key}.pdf")
    if os.path.exists(path) and os.path.getsize(path) > 50_000:
        return path
    req = urllib.request.Request(row["url"], headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except Exception:
        return None
    if len(body) < 50_000 or not body[:5].startswith(b"%PDF"):
        return None                      # an error page, not a technical report
    with open(path, "wb") as f:
        f.write(body)
    return path


def meta_for(row):
    """The metadata MMP's extractor expects, from the catalogue entry."""
    key = row.get("key") or hashlib.sha1((row.get("url") or "").encode()).hexdigest()[:16]
    return {"id": f"mtp:{key}", "archive_url": row.get("url"),
            "report_url": row.get("url"),
            "company": row.get("ticker"), "project": row.get("project"),
            "commodity": None, "jurisdiction": row.get("region") or row.get("country"),
            "date": row.get("filed"), "source": "mineterminalpro"}


def _summary(rows):
    from collections import Counter
    by_type = Counter(r.get("type_name") or r.get("type") or "?" for r in rows)
    by_year = Counter(str(r.get("filed") or "")[:4] for r in rows)
    return by_type, by_year


def collect(rows=None, pdf_dir=None, limit=None, max_seconds=None, prefer=True,
            verbose=True):
    """Download reports MMP does not have and run them through the extractor.

    Ordered by what is worth most first: a resource estimate states a published
    tonnage MMP can be scored against, a feasibility or pre-feasibility study
    states the fullest modelling parameters, and the newest reports describe the
    deposits as they are now understood. Everything is extracted by the same
    ``drill_tables`` path as every other report, so nothing downstream needs to know
    where a report came from."""
    from minemodelingpro import drill_tables as D
    rows = rows if rows is not None else load()
    rows = already_held(rows)
    if prefer:
        rank = {"Resource estimate": 0, "Feasibility study": 1,
                "Pre-feasibility study": 2, "PEA": 3, "Technical report": 4,
                "Property report": 5}
        rows.sort(key=lambda r: (rank.get(r.get("type_name"), 9),
                                 -int(str(r.get("filed") or "0")[:4] or 0)))
    pdf_dir = pdf_dir or os.environ.get("MMP_PDF_DIR", "/tmp/mmp_pdfs")
    t0 = time.time()
    got = failed = 0
    for r in rows:
        if limit and got >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            if verbose:
                print("[mtp] time budget reached", flush=True)
            break
        path = fetch_pdf(r, pdf_dir)
        if not path:
            failed += 1
            continue
        try:
            rec = D.extract_report(path, meta_for(r))
            rec["mtp"] = {k: r.get(k) for k in
                          ("ticker", "project", "type_name", "filed", "qps",
                           "resource_page", "country", "region")}
            D.save(rec)
            got += 1
            if verbose:
                c = rec["counts"]
                print(f"[mtp] {r.get('ticker')} {str(r.get('project'))[:26]:26} "
                      f"{str(r.get('type_name'))[:18]:18} {c['collars']:4}c {c['intervals']:5}iv "
                      f"res={'y' if rec.get('resource') else 'n'} "
                      f"nsr={'y' if (rec.get('model_params') or {}).get('nsr') else 'n'}",
                      flush=True)
        except Exception as e:
            failed += 1
            if verbose:
                print(f"[mtp] FAILED {r.get('ticker')} {str(e)[:90]}", flush=True)
    if verbose:
        print(f"[mtp] extracted {got}, failed {failed}", flush=True)
    return got


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "survey"
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    if cmd == "survey":
        rows = catalogue(limit=lim)
        print(f"\n{len(rows)} unique technical reports in the catalogue")
        bt, by = _summary(rows)
        print("\nby document type:")
        for k, n in bt.most_common(12):
            print(f"  {n:5}  {k}")
        print("\nby filing year:")
        for k, n in sorted(by.items(), reverse=True)[:12]:
            print(f"  {n:5}  {k}")
        new = already_held(rows)
        print(f"\n{len(new)} of them are not already in MMP's corpus")
        print("wrote", save(rows))
    elif cmd == "collect":
        secs = int(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else None
        collect(limit=lim, max_seconds=secs)
