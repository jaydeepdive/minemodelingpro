"""Safe, resumable re-extraction of banked SEDAR 43-101 PDFs under the current
extractor (v8: robust AISC/cash-cost + memory-safe large-PDF text).

WHY A DEDICATED DRIVER (not `python -m minemodelingpro.sedar`):
  sedar.ingest_folder() ends with shards.export_shards(), which RMTREEs and
  rewrites the WHOLE shard store from the working sqlite. Run against anything
  but the full store that would delete the 800k gov collars / 14M gov assays.
  This driver instead:
    * extracts TEXT-derived tables only (deposit_model / model_method /
      metallurgy / economics) with drill_tables=False — AISC/cash cost live in
      text, so camelot (slow, OOM-prone in a 4 GB box) is unnecessary here, and
      the existing appendix collar/assay shards are left untouched;
    * writes into a SEPARATE temp sqlite (resumable across short runs);
    * merges ONLY the re-extracted sources' text shards into the manifest,
      touching nothing else;
    * upserts those sources into the report index (never rebuilding it).

Source-id parity: each disk PDF maps to the SAME `sedar:<stable-key>` id the
first extraction used (issuer slug + submitted datetime), so shards overwrite
in place rather than duplicating.

Phases:
  python scripts/reextract_econ.py ingest [--limit N] [--max-seconds S]   # resumable
  python scripts/reextract_econ.py shard                                  # merge shards + index
  python scripts/reextract_econ.py status                                 # progress
"""
import os
import re
import sys
import glob
import json
import time
import sqlite3
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from minemodelingpro import pdf_reports, shards, store           # noqa: E402
from minemodelingpro import sedar_collect as sc                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = os.path.join(ROOT, "data", "keep")
PDFDIR = os.path.join(KEEP, "sedar_pdfs")
LEDGER = os.path.join(KEEP, "sedar_manifest.json")
INDEX = os.path.join(KEEP, "mmp_reports_index.json")
EXTRACTED = os.path.join(KEEP, "mmp_extracted.json")   # {source_id: extractor_version} — durable, committed
# Scratch sqlite lives on LOCAL disk, not the bridged mount: sqlite's locking /
# fsync is unreliable over the device bridge (disk I/O errors), whereas plain
# parquet/JSON writes to the mount are fine. Shards + OCR cache stay on the mount.
TEMPDB = os.environ.get("MMP_TEMPDB", "/tmp/mmp_reextract.sqlite")
TEXT_TABLES = ["deposit_model", "model_method", "metallurgy", "economics"]
DRILL_TABLES = ["collars", "assays", "survey", "lithology"]   # merged with max-preserve


def _pdf_sources():
    """Yield (pdf_path, source_id, meta) for every banked PDF, assigning the same
    stable-key source id the first extraction used."""
    man = json.load(open(LEDGER)) if os.path.exists(LEDGER) else []
    by_node = {r.get("node"): r for r in man if r.get("node")}
    for p in sorted(glob.glob(os.path.join(PDFDIR, "*.pdf"))):
        stem = os.path.basename(p)[:-4]
        ref = stem[len("sedar_"):] if stem.startswith("sedar_") else stem
        r = by_node.get(ref) if re.fullmatch(r"W\d+", ref) else None
        if not r:                       # stable-key-named file: match a row by key
            r = next((rr for rr in man
                      if sc._stable_key(rr.get("company"), rr.get("submitted")) == ref), None)
            sid = "sedar:" + ref
        else:
            sk = sc._stable_key(r.get("company"), r.get("submitted"))
            sid = "sedar:" + (sk or ref)
        company = (r or {}).get("company") or ref.rsplit("_", 1)[0].replace("-", " ").title()
        node = (r or {}).get("node") or ref
        meta = {"company": company, "project": (r or {}).get("project") or company,
                "commodity": (r or {}).get("commodity"),
                "jurisdiction": (r or {}).get("jurisdiction"),
                "submitted": (r or {}).get("submitted"),
                "url": (r or {}).get("sedar_url") or f"sedarplus.ca/filing/{node}"}
        yield p, sid, meta


def _done(con, sid):
    r = con.execute("SELECT note FROM sources WHERE id=?", (sid,)).fetchone()
    return bool(r) and f"ev{pdf_reports.EXTRACTOR_VERSION}" in (r[0] or "")


def _load_extracted():
    try:
        return json.load(open(EXTRACTED))
    except Exception:
        return {}


def _save_extracted(d):
    json.dump(d, open(EXTRACTED, "w"), indent=0, sort_keys=True)


def _is_image_only(path):
    """True if the PDF has no extractable text layer (scanned) — needs OCR."""
    try:
        return sum(len(t) for t in pdf_reports._pages_text_fitz(path)) < 200
    except Exception:
        return False


def ocr(max_seconds=150):
    """Resumable OCR of every scanned (image-only) banked PDF into the OCR cache,
    so the next `ingest` extracts them like any text PDF. Drops each finished
    source from the temp db so ingest re-processes it with the OCR text."""
    _redirect_store()
    import time
    t0 = time.time()
    targets = [(p, sid) for p, sid, _ in _pdf_sources() if _is_image_only(p)]
    print(f"[reextract] {len(targets)} image-only PDF(s) to OCR")
    for p, sid in targets:
        if pdf_reports._load_ocr_cache(sid) is not None:
            print(f"[ocr] {sid}: already cached"); continue
        if max_seconds and time.time() - t0 > max_seconds:
            print("[ocr] time budget reached — run `ocr` again to continue"); break
        remaining = max_seconds - (time.time() - t0) if max_seconds else None
        done, cached, npages = pdf_reports.ocr_pages_cached(p, sid, max_seconds=remaining)
        print(f"[ocr] {sid}: {cached}/{npages} pages cached{' (COMPLETE)' if done else ''}")
        if done:
            con = store.connect(TEMPDB)
            con.execute("DELETE FROM sources WHERE id=?", (sid,)); con.commit(); con.close()
            print(f"[ocr] {sid}: dropped from temp db — will re-extract on next ingest")


def _redirect_store():
    """ingest_report calls store.connect() with no arg, whose default is BOUND at
    def-time to the real mmp.sqlite — so setting store.DB_PATH alone doesn't
    redirect it. Rebind the default too, so all writes land in the temp db."""
    store.DB_PATH = TEMPDB
    store.connect.__defaults__ = (TEMPDB,)


def ingest(limit=None, max_seconds=150):
    _redirect_store()                          # redirect the whole store to the temp db
    con = store.connect(TEMPDB)
    todo = [(p, sid, m) for p, sid, m in _pdf_sources() if not _done(con, sid)]
    con.close()
    print(f"[reextract] {len(todo)} PDF(s) to (re)extract under ev{pdf_reports.EXTRACTOR_VERSION}")
    t0 = time.time(); done = ok = 0
    for p, sid, m in todo:
        if limit and done >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            print("[reextract] time budget reached — resume with another `ingest` run"); break
        done += 1
        mb = os.path.getsize(p) // (1024 * 1024)
        print(f"[reextract] ({done}) {sid}  [{mb} MB]")
        try:
            pdf_reports.ingest_report(m["url"], project=m["project"], commodity=m["commodity"],
                                      jurisdiction=m["jurisdiction"], report_date=m["submitted"],
                                      source_id=sid, pdf_path=p, drill_tables=True)
            ok += 1
        except Exception as e:
            print(f"[reextract] FAILED {sid}: {str(e)[:160]}")
    print(f"[reextract] ingested {ok}/{done} this run")
    return ok


def _replace_source_shards(tables, t, sid, df):
    """Delete a source's existing shard files+manifest entries for table t and
    write df in their place."""
    safe = shards._safe(sid)
    tdir = os.path.join(shards.SHARD_DIR, t)
    for f in (glob.glob(os.path.join(tdir, safe + ".parquet"))
              + glob.glob(os.path.join(tdir, safe + ".[0-9]*.parquet"))):
        os.remove(f)
    tables[t] = [x for x in tables.get(t, []) if x["source"] != sid]
    if not df.empty:
        shards._write_shards(t, sid, df, tables)


def shard():
    """Merge the re-extracted sources' shards into the manifest, leaving every
    OTHER shard (gov collars/assays, orphan node-keyed reports, untouched
    sources) exactly as-is. TEXT tables are always replaced (deterministic from
    text). DRILL tables use max-preserve: only replace a source's collars/assays
    if the fresh camelot pass yields at least as many rows as are already banked,
    so a page-detection change can never silently drop drill data."""
    import pandas as pd
    if not os.path.exists(TEMPDB):
        print("[reextract] no temp db — run `ingest` first"); return
    meta = json.load(open(shards.MANIFEST))
    tables = meta["tables"]
    con = sqlite3.connect(TEMPDB)
    srcs = [r[0] for r in con.execute("SELECT id FROM sources").fetchall()]
    kept = 0
    for sid in srcs:
        for t in TEXT_TABLES:
            df = pd.read_sql_query(f"SELECT * FROM {t} WHERE source_id=?", con, params=[sid])
            _replace_source_shards(tables, t, sid, df)
        for t in DRILL_TABLES:
            df = pd.read_sql_query(f"SELECT * FROM {t} WHERE source_id=?", con, params=[sid])
            existing = sum(x["rows"] for x in tables.get(t, []) if x["source"] == sid)
            if len(df) >= existing:                      # >= keeps parity, gains new data
                _replace_source_shards(tables, t, sid, df)
            else:
                print(f"[reextract] keep existing {t} for {sid} "
                      f"({existing} banked > {len(df)} re-extracted)")
        kept += 1
    con.close()
    meta["tables"] = tables
    meta["generated"] = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    meta["totals"] = {t: sum(x["rows"] for x in tables.get(t, [])) for t in shards.TABLES}
    meta["shard_count"] = sum(len(v) for v in tables.values())
    json.dump(meta, open(shards.MANIFEST, "w"), indent=2)
    print(f"[reextract] merged shards for {kept} sources; totals now {meta['totals']}")
    _reindex(srcs)


def _reindex(srcs):
    """Upsert the re-extracted sources into the report index (never rebuild)."""
    con = sqlite3.connect(TEMPDB); con.row_factory = sqlite3.Row
    met = {r["source_id"]: r for r in con.execute(
        "SELECT source_id, process_types, refractory, recovery_summary FROM metallurgy")}
    prev = {}
    if os.path.exists(INDEX):
        try:
            prev = {e["id"]: e for e in json.load(open(INDEX)).get("reports", [])}
        except Exception:
            prev = {}
    for r in con.execute("""SELECT id, name, url, jurisdiction, pulled_at, n_collars, n_assays, note
                            FROM sources""").fetchall():
        note = r["note"] or ""
        m = re.search(r"(\d+)\s+resource rows", note)
        am = re.search(r"archive=(\S+)", note)
        mm = met.get(r["id"])
        prev[r["id"]] = {
            "id": r["id"], "company": r["name"],
            "project": (prev.get(r["id"]) or {}).get("project"),
            "commodity": (prev.get(r["id"]) or {}).get("commodity"),
            "jurisdiction": r["jurisdiction"] or (prev.get(r["id"]) or {}).get("jurisdiction"),
            "source_url": r["url"],
            "archive_url": (am.group(1) if am else None) or (prev.get(r["id"]) or {}).get("archive_url"),
            "collected": r["pulled_at"],
            "collars": r["n_collars"], "assays": r["n_assays"],
            "resource_rows": int(m.group(1)) if m else 0,
            "has_method": "method=y" in note,
            "has_economics": "econ=y" in note,
            "metallurgy_process": mm["process_types"] if mm else (prev.get(r["id"]) or {}).get("metallurgy_process"),
            "refractory": mm["refractory"] if mm else (prev.get(r["id"]) or {}).get("refractory"),
            "recovery": ((mm["recovery_summary"] if mm else "") or "")[:160] or (prev.get(r["id"]) or {}).get("recovery")}
    con.close()
    out = sorted(prev.values(), key=lambda e: (e.get("jurisdiction") or "zz", e.get("company") or ""))
    archived = sum(1 for e in out if e.get("archive_url"))
    json.dump({"generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
               "count": len(out), "archived": archived, "reports": out},
              open(INDEX, "w"), indent=1)
    print(f"[reextract] report index -> {len(out)} reports ({archived} archived)")


def auto(max_seconds=100):
    """Fully-automatic incremental extraction for the daily pipeline. Extracts
    every banked report not yet at the current extractor version: OCRs scanned
    reports (bounded + resumable — a large scan finishes over several runs),
    extracts text + drill data, shard-merges ONLY the newly-done sources, and
    records them in the durable extracted-ledger (data/keep/mmp_extracted.json).
    Idempotent and time-budgeted; run repeatedly to drain the backlog."""
    import time
    _redirect_store()
    if os.path.exists(TEMPDB):
        try:
            os.remove(TEMPDB)
        except OSError:
            pass
    extracted = _load_extracted()
    ev = pdf_reports.EXTRACTOR_VERSION
    pending = [(p, sid, m) for p, sid, m in _pdf_sources() if extracted.get(sid) != ev]
    print(f"[auto] {len(pending)} report(s) pending at ev{ev}")
    t0 = time.time()
    did, deferred = [], []
    # pass 1 — text-layer PDFs (fast, high yield); collect scanned ones for pass 2
    for p, sid, m in pending:
        if time.time() - t0 > max_seconds:
            break
        if _is_image_only(p):
            deferred.append((p, sid, m)); continue
        try:
            pdf_reports.ingest_report(m["url"], project=m["project"], commodity=m["commodity"],
                                      jurisdiction=m["jurisdiction"], report_date=m["submitted"],
                                      source_id=sid, pdf_path=p, drill_tables=True)
            did.append(sid); print(f"[auto] extracted {sid}")
        except Exception as e:
            print(f"[auto] FAILED {sid}: {str(e)[:140]}")
    # pass 2 — scanned PDFs: OCR (bounded/resumable) then extract once complete
    for p, sid, m in deferred:
        if time.time() - t0 > max_seconds:
            break
        if pdf_reports._load_ocr_cache(sid) is None:
            done, cached, npages = pdf_reports.ocr_pages_cached(
                p, sid, max_seconds=max(15, max_seconds - (time.time() - t0)))
            print(f"[auto] OCR {sid}: {cached}/{npages}{' complete' if done else ' — resumes next run'}")
            if not done:
                continue
        try:
            pdf_reports.ingest_report(m["url"], project=m["project"], commodity=m["commodity"],
                                      jurisdiction=m["jurisdiction"], report_date=m["submitted"],
                                      source_id=sid, pdf_path=p, drill_tables=True)
            did.append(sid); print(f"[auto] extracted {sid} (OCR)")
        except Exception as e:
            print(f"[auto] FAILED {sid}: {str(e)[:140]}")
    if did:
        shard()                       # merges exactly the sources now in the temp db
        for sid in did:
            extracted[sid] = ev
        _save_extracted(extracted)
        print(f"[auto] done: extracted {len(did)} report(s); ledger now {len(extracted)} at ev{ev}")
    else:
        print("[auto] nothing new extracted this run "
              f"({len(pending)} pending, {len(deferred)} scanned mid-OCR)")
    return len(did)


def status():
    ev = pdf_reports.EXTRACTOR_VERSION
    n_pdf = len(list(_pdf_sources()))
    extracted = _load_extracted()
    at_ev = sum(1 for _, sid, _ in _pdf_sources() if extracted.get(sid) == ev)
    print(f"[reextract] {at_ev}/{n_pdf} PDFs extracted at ev{ev} "
          f"(ledger {len(extracted)} entries)")
    if not os.path.exists(TEMPDB):
        print(f"[reextract] {n_pdf} PDFs, temp db not started"); return
    con = store.connect(TEMPDB)
    dn = sum(1 for _, sid, _ in _pdf_sources() if _done(con, sid))
    ne = con.execute("SELECT COUNT(*) FROM economics").fetchone()[0]
    naisc = con.execute("SELECT COUNT(*) FROM economics WHERE aisc IS NOT NULL").fetchone()[0]
    ncash = con.execute("SELECT COUNT(*) FROM economics WHERE cash_cost IS NOT NULL").fetchone()[0]
    con.close()
    print(f"[reextract] {dn}/{n_pdf} PDFs done | economics rows={ne} aisc={naisc} cash_cost={ncash}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "auto":
        secs = int(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else 100
        auto(max_seconds=secs)
    elif cmd == "ingest":
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
        secs = int(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else 150
        ingest(limit=lim, max_seconds=secs)
    elif cmd == "ocr":
        secs = int(sys.argv[sys.argv.index("--max-seconds") + 1]) if "--max-seconds" in sys.argv else 150
        ocr(max_seconds=secs)
    elif cmd == "shard":
        shard()
    else:
        status()
