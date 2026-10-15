"""Technical reports from the MineTerminalPro partner API.

MMP's accuracy is limited more by how many filed technical reports it holds than by
anything in the estimator. A report supplies the block size, the density, the grade
cap, the NSR formula, the metallurgy, and — where it states one — the published
resource MMP is scored against. The ceo.ca feed collects new filings as they appear,
which is the right way to stay current and the wrong way to acquire a back
catalogue.

The partner API is that back catalogue: 4,687 reports from 920 companies, filed
since 2010, read nightly. ``/tech-reports`` lists them with type, project, filing
and effective dates and qualified persons; ``/tech-reports/{id}/text`` returns the
whole report page by page, already run through ``pdftotext -layout``.

That last part is what makes this cheap. Layout text keeps table columns aligned as
runs of spaces, which is enough for every extractor MMP has — drill tables included
— so a report is read without downloading its PDF at all. Four and a half thousand
reports is a different proposition at 70 KB of gzipped text each than at a hundred
megabytes of PDF, and it is the difference between a job measured in days and one
measured in hours.

The key is read from MTP_API_KEY and never written anywhere: this repository is
public, and a leaked key is revoked rather than recovered.

    export MTP_API_KEY=...
    python -m minemodelingpro.mtp_reports survey
    python -m minemodelingpro.mtp_reports ingest [--limit N] [--max-seconds S]
    python -m minemodelingpro.mtp_reports since 2026-10-01    # only what is new
"""
import os
import re
import json
import time
import gzip
import threading
import urllib.parse
import urllib.request

API = os.environ.get("MTP_API", "https://mineterminalpro.com/api/partner/v1")
# Cloudflare rejects urllib's default agent outright, so send a real one.
UA = "mmp/1.0 (MineModelingPro report collector)"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOGUE = os.path.join(_ROOT, "data", "keep", "mmp_mtp_catalogue.json")
# Layout text, kept on disk. Extraction improves far more often than a filed report
# changes, so the expensive part of a re-read is the download, not the parse — and
# the daily request limit makes re-downloading four thousand reports a decision
# rather than a reflex. Gzipped text is about 70 KB a report; the cache is not
# committed.
TEXT_CACHE = os.environ.get("MTP_TEXT_CACHE",
                            os.path.join(_ROOT, "data", "cache", "mtp_text"))
# 120 requests a minute is the published limit; sit under it rather than on it.
# Reading a report is a second of network and a few of parsing, and the parsing is
# pure Python, so threads share one core and buy nothing — the work goes in
# processes. Each has its own pacer, so the share is divided between them.
WORKERS = int(os.environ.get("MTP_WORKERS", "0")) or max(1, (os.cpu_count() or 2))
RATE = float(os.environ.get("MTP_RATE", "95")) / 60.0
_last = [0.0]
_lock = threading.Lock()

# Worth-first: a resource estimate states a tonnage MMP can be scored against, and a
# feasibility study states the fullest modelling parameters. A property report is
# usually an early-stage description with neither.
RANK = {"Resource estimate": 0, "Feasibility study": 1, "Pre-feasibility study": 2,
        "PEA": 3, "Technical report": 4, "Property report": 5}


def _key():
    k = os.environ.get("MTP_API_KEY") or os.environ.get("MTP_TR_KEY")
    if not k:
        raise RuntimeError("set MTP_API_KEY in the environment — never in a file, "
                           "this repository is public")
    return k


def _throttle():
    with _lock:
        wait = _last[0] + 1.0 / max(RATE, 0.01) - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def api(path, timeout=180, tries=3):
    """One partner-API call: paced, gzipped, and retried on a hiccup."""
    for attempt in range(tries):
        _throttle()
        req = urllib.request.Request(
            API + path, headers={"X-API-Key": _key(), "User-Agent": UA,
                                 "Accept": "application/json",
                                 "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            return json.loads(body.decode("utf-8", "replace"))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))


def _norm(r):
    """One row shape, whichever source it came from.

    The catalogue was first built by reading MineTerminalPro's own pages, which
    named the stable report id ``key`` and flattened the project and location.
    The partner API calls the same id ``id`` and nests both. Normalising here
    means the reports MMP already holds are recognised as held rather than read
    a second time under a new name."""
    proj = r.get("project")
    if isinstance(proj, dict):
        pname, pkey, pkind = proj.get("name"), proj.get("key"), proj.get("kind")
    else:
        pname, pkey, pkind = proj, r.get("project_key"), r.get("project_kind")
    loc = r.get("location") if isinstance(r.get("location"), dict) else {}
    src = r.get("source") if isinstance(r.get("source"), dict) else {}
    return {"id": r.get("id") or r.get("key"),
            "ticker": r.get("ticker"), "exchange": r.get("exchange"),
            "project": pname, "project_key": pkey, "project_kind": pkind,
            "type": r.get("type"), "type_name": r.get("type_name"),
            "title": r.get("title"),
            "filed": r.get("filed"), "effective": r.get("effective"),
            "amended": r.get("amended"),
            "is_current": r.get("is_current", r.get("current")),
            "qps": r.get("qps") or [],
            "country": loc.get("country") or r.get("country"),
            "region": loc.get("region") or r.get("region"),
            "url": src.get("url") or r.get("url") or r.get("source_url"),
            "page_refs": r.get("page_refs") or ({"resources": r["resource_page"]}
                                                if r.get("resource_page") else None),
            "pages": r.get("pages")}


def meta():
    return api("/meta")


def list_reports(limit=200, verbose=False, **filters):
    """Every report matching the filters, newest filing first."""
    out, page = [], 1
    while True:
        q = "&".join([f"limit={limit}", f"page={page}"] +
                     [f"{k}={urllib.parse.quote(str(v))}" for k, v in filters.items() if v])
        d = api("/tech-reports?" + q)
        items = d.get("items") or []
        out.extend(_norm(i) for i in items)
        if verbose:
            print(f"  [mtp] listed {len(out)}/{d.get('total')}", flush=True)
        if page >= int(d.get("pages") or 1) or not items:
            break
        page += 1
    return out


def changes_since(stamp):
    """Reports the catalogue read after a UTC timestamp — the incremental path."""
    items = (api(f"/tech-reports/changes?since={urllib.parse.quote(str(stamp))}")
             or {}).get("items") or []
    return [_norm(i) for i in items]


def report_text(rid, cache=True):
    """A report's pages, as layout-preserved text, from the cache when it is there."""
    fn = os.path.join(TEXT_CACHE, f"{rid}.json.gz")
    if cache and os.path.exists(fn):
        try:
            with gzip.open(fn, "rt", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    d = api(f"/tech-reports/{urllib.parse.quote(str(rid))}/text")
    pages = [p.get("text") or "" for p in (d.get("pages") or [])]
    if cache and pages:
        try:
            os.makedirs(TEXT_CACHE, exist_ok=True)
            tmp = fn + f".{os.getpid()}.tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(pages, f)
            os.replace(tmp, fn)
        except Exception:
            pass
    return pages


def cached_ids():
    """Reports whose text is already on disk — re-readable without a request."""
    if not os.path.isdir(TEXT_CACHE):
        return set()
    return {fn[:-len(".json.gz")] for fn in os.listdir(TEXT_CACHE)
            if fn.endswith(".json.gz")}


def save(rows, path=CATALOGUE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "n": len(rows), "reports": rows}, open(path, "w"), indent=1)
    return path


def load(path=CATALOGUE):
    try:
        return [_norm(r) for r in (json.load(open(path)).get("reports") or [])]
    except Exception:
        return []


def local_id(rid):
    return f"mtp:{rid}"


def held_ids(reports_dir=None):
    """Which MTP reports MMP has already read, by their stable catalogue id."""
    from minemodelingpro import drill_tables as D
    d = reports_dir or D.OUT_DIR
    out = set()
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if fn.startswith("mtp__") and fn.endswith(".json"):
            out.add("mtp:" + fn[len("mtp__"):-len(".json")])
    return out


def meta_for(r):
    """The metadata MMP's extractor expects, from a catalogue entry."""
    return {"id": local_id(r.get("id")),
            "report_url": r.get("url"),
            "archive_url": r.get("url"),
            "company": r.get("ticker"),
            "project": r.get("project"),
            "commodity": None,
            "jurisdiction": r.get("region") or r.get("country"),
            "date": r.get("filed"), "source": "mineterminalpro"}


def _read_one(r, verbose=True):
    """Read one report end to end. Returns True if MMP now holds it."""
    from minemodelingpro import drill_tables as D
    pages = report_text(r.get("id"))
    if not pages:
        return False
    rec = D.extract_report_text(pages, meta_for(r))
    rec["mtp"] = {k: r.get(k) for k in
                  ("id", "ticker", "exchange", "project", "project_key",
                   "type_name", "title", "filed", "effective", "qps",
                   "country", "region", "page_refs", "is_current", "pages")}
    # the catalogue's own effective date beats anything read from the prose
    if r.get("effective") and not rec.get("effective_date"):
        rec["effective_date"] = r["effective"]
    with _save_lock:
        D.save(rec)
    if verbose:
        c = rec["counts"]
        print(f"[mtp] {str(r.get('ticker'))[:6]:6} {str(r.get('project'))[:22]:22} "
              f"{str(r.get('type_name'))[:18]:18} {c['collars']:4}c {c['intervals']:5}iv "
              f"res={'y' if rec.get('resource') else 'n'} "
              f"nsr={'y' if (rec.get('model_params') or {}).get('nsr') else 'n'}", flush=True)
    return True


_save_lock = threading.Lock()


# A report that cannot be read in two minutes is a defect, not a big report: the
# whole catalogue averages two seconds. One such report used to hold a worker for as
# long as it liked — a single expression backtracking on a page full of degree
# symbols stalled the run for an hour — so a worker gives up and says so instead.
JOB_SECONDS = int(os.environ.get("MTP_JOB_SECONDS", "150"))


def _job(r):
    """One report, in a worker process. Returns (ok, id, line)."""
    import signal

    def _late(sig, frm):
        raise TimeoutError(f"gave up after {JOB_SECONDS}s")

    try:
        signal.signal(signal.SIGALRM, _late)
        signal.alarm(JOB_SECONDS)
    except Exception:
        pass
    try:
        from minemodelingpro import drill_tables as D
        pages = report_text(r.get("id"))
        if not pages:
            return False, r.get("id"), "no text"
        rec = D.extract_report_text(pages, meta_for(r))
        rec["mtp"] = {k: r.get(k) for k in
                      ("id", "ticker", "exchange", "project", "project_key",
                       "type_name", "title", "filed", "effective", "qps",
                       "country", "region", "page_refs", "is_current", "pages")}
        if r.get("effective") and not rec.get("effective_date"):
            rec["effective_date"] = r["effective"]
        D.save(rec)
        c = rec["counts"]
        res = rec.get("resource") or {}
        return True, r.get("id"), (
            f"{str(r.get('ticker'))[:6]:6} {str(r.get('project'))[:22]:22} "
            f"{str(r.get('type_name'))[:18]:18} {c['collars']:4}c {c['intervals']:5}iv "
            f"res={(str(round((res.get('tonnes') or 0) / 1e6, 1)) + 'Mt') if res else '-':>8} "
            f"nsr={'y' if (rec.get('model_params') or {}).get('nsr') else 'n'}")
    except Exception as e:
        return False, r.get("id"), (f"{r.get('ticker')} {str(r.get('project'))[:20]} "
                                    f"{type(e).__name__}: {str(e)[:90]}")
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass


def ingest(rows=None, limit=None, max_seconds=None, verbose=True, skip_held=True,
           workers=None):
    """Read reports MMP does not hold, from text, through the usual extractor.

    Worth-first: a resource estimate states a tonnage MMP can be scored against,
    a feasibility study states the fullest modelling parameters, and a property
    report is usually an early-stage description with neither."""
    import concurrent.futures as cf
    rows = rows if rows is not None else load()
    if not rows:
        rows = list_reports(verbose=verbose)
        save(rows)
    rows = [_norm(r) for r in rows]
    if skip_held:
        have = held_ids()
        rows = [r for r in rows if local_id(r.get("id")) not in have]
    rows.sort(key=lambda r: (RANK.get(r.get("type_name"), 9),
                             -int(str(r.get("filed") or "0")[:4] or 0)))
    if limit:
        rows = rows[:limit]
    n_w = int(workers or WORKERS)
    t0, got, failed, n = time.time(), 0, 0, 0
    pool = cf.ProcessPoolExecutor(max_workers=n_w)
    try:
        futs = {}
        it = iter(rows)
        for r in it:                                  # prime
            futs[pool.submit(_job, r)] = r
            if len(futs) >= n_w * 3:
                break
        while futs:
            done, _ = cf.wait(futs, return_when=cf.FIRST_COMPLETED)
            for f in done:
                futs.pop(f)
                ok, rid, line = f.result()
                n += 1
                if ok:
                    got += 1
                else:
                    failed += 1
                if verbose:
                    print(f"[mtp] {'' if ok else 'FAILED '}{line}", flush=True)
                if verbose and n % 100 == 0:
                    rate = n / max(time.time() - t0, 1.0)
                    print(f"[mtp] {n}/{len(rows)} read={got} failed={failed} "
                          f"{rate * 60:.0f}/min, ~{(len(rows) - n) / max(rate, 1e-9) / 60:.0f}"
                          f" min left", flush=True)
            if max_seconds and time.time() - t0 > max_seconds:
                if verbose:
                    print("[mtp] time budget reached", flush=True)
                break
            for r in it:
                futs[pool.submit(_job, r)] = r
                if len(futs) >= n_w * 3:
                    break
    finally:
        # a limit or a time budget leaves work in flight; waiting for it would make
        # "--limit 12" take as long as the longest report left in the queue
        pool.shutdown(wait=False, cancel_futures=True)
    if verbose:
        print(f"[mtp] read {got}, failed {failed}, {time.time() - t0:.0f}s", flush=True)
    return got


def fetch(rows=None, limit=None, verbose=True, workers=12):
    """Download report text into the cache and extract nothing.

    Downloading is network-bound and extraction is CPU-bound, and the extractor
    changes far more often than the catalogue does. Separating them means a change
    to how a table is read costs a pass over local files rather than another four
    thousand requests against a daily limit."""
    rows = [_norm(r) for r in (rows if rows is not None else load())]
    have = cached_ids()
    rows = [r for r in rows if r.get("id") not in have]
    rows.sort(key=lambda r: (RANK.get(r.get("type_name"), 9),
                             -int(str(r.get("filed") or "0")[:4] or 0)))
    if limit:
        rows = rows[:limit]
    t0, got, failed = time.time(), [0], [0]
    lock = threading.Lock()

    def one(r):
        try:
            ok = bool(report_text(r.get("id")))
        except Exception:
            ok = False
        with lock:
            got[0] += 1 if ok else 0
            failed[0] += 0 if ok else 1
            n = got[0] + failed[0]
        if verbose and n % 200 == 0:
            rate = n / max(time.time() - t0, 1.0)
            print(f"[mtp] fetched {n}/{len(rows)} failed={failed[0]} {rate * 60:.0f}/min, "
                  f"~{(len(rows) - n) / max(rate, 1e-9) / 60:.0f} min left", flush=True)

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, rows))
    if verbose:
        print(f"[mtp] fetched {got[0]}, failed {failed[0]}, {time.time() - t0:.0f}s", flush=True)
    return got[0]


def _summary(rows):
    from collections import Counter
    return (Counter(r.get("type_name") or "?" for r in rows),
            Counter(str(r.get("filed") or "")[:4] for r in rows))


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "survey"
    arg = lambda f, d=None: (sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d)  # noqa: E731
    if cmd == "survey":
        m = meta()
        print(f"catalogue: {m.get('reports')} reports from {m.get('companies')} companies, "
              f"filed {m.get('filed_range')}")
        print(f"limits: {m.get('limits')}")
        rows = list_reports(verbose=True)
        bt, by = _summary(rows)
        print("\nby type:")
        for k, n in bt.most_common():
            print(f"  {n:5}  {k}")
        have = held_ids()
        print(f"\nMMP already holds {len(have)}; {len(rows) - len(have)} to read")
        print("wrote", save(rows))
    elif cmd == "ingest":
        ingest(limit=int(arg("--limit")) if arg("--limit") else None,
               max_seconds=int(arg("--max-seconds")) if arg("--max-seconds") else None,
               workers=int(arg("--workers", 0)) or None)
    elif cmd == "refresh":
        rows = list_reports(verbose=True)
        print(f"{len(rows)} reports; wrote", save(rows))
    elif cmd == "fetch":
        fetch(limit=int(arg("--limit")) if arg("--limit") else None,
              workers=int(arg("--workers", 12)))
    elif cmd == "reextract":
        have = cached_ids()
        rows = [r for r in load() if r.get("id") in have]
        print(f"{len(rows)} cached report(s) to re-read")
        ingest(rows=rows, skip_held=False, workers=int(arg("--workers", 0)) or None,
               max_seconds=int(arg("--max-seconds")) if arg("--max-seconds") else None)
    elif cmd == "since":
        rows = changes_since(arg("since", sys.argv[2] if len(sys.argv) > 2 else ""))
        print(f"{len(rows)} report(s) read since then")
        ingest(rows=rows)
