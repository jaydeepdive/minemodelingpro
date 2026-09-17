"""SEDAR+ batch collector — the processing half.

SEDAR+ is Akamai-protected, so the DOWNLOAD half is browser-driven (a Claude
session drives the user's Chrome: run the NI 43-101 technical-report search, and
for each result page write a manifest row {file, company, project, jurisdiction,
commodity, filing_ref, submitted, sedar_url} and let the report PDF download to
the user's Downloads). This module is the automatable other half: given that
folder + manifest, it extracts every report with the full v6 extractor
(resources, methodology, metallurgy, appendix collars/assays), retains the PDF
in the archive, and updates the searchable index. Idempotent per filing.

Manifest: data/keep/sedar_manifest.json — a list of rows; `file` is the PDF's
basename in the download folder, `filing_ref` its stable SEDAR id.

Run:  python -m minemodelingpro.sedar <download_folder> [manifest.json]
"""
import os
import sys
import json

from minemodelingpro import pdf_reports, shards, report_archive


def ingest_downloaded(pdf_path, company=None, project=None, jurisdiction=None,
                      commodity=None, filing_ref=None, submitted=None, sedar_url=None):
    """Ingest one downloaded SEDAR report PDF with its result-row metadata."""
    if not os.path.exists(pdf_path):
        print(f"[sedar] missing {pdf_path}"); return None
    ref = filing_ref or os.path.splitext(os.path.basename(pdf_path))[0]
    sid = "sedar:" + ref
    url = sedar_url or f"sedarplus.ca/filing/{ref}"
    return pdf_reports.ingest_report(
        url, project=project or company, commodity=commodity,
        jurisdiction=jurisdiction, report_date=submitted,
        source_id=sid, pdf_path=pdf_path)


def ingest_folder(folder, manifest=None):
    """Ingest every report named in the manifest from `folder`, then re-shard +
    reindex. Rows already at the current extractor version are skipped."""
    manifest = manifest or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "keep", "sedar_manifest.json")
    rows = json.load(open(manifest)) if os.path.exists(manifest) else []
    from minemodelingpro import store
    con = store.connect(); done = ok = 0
    for r in rows:
        ref = r.get("filing_ref") or os.path.splitext(r.get("file", ""))[0]
        if pdf_reports._current(con, f"sedarplus.ca/filing/{ref}") or \
           con.execute("SELECT 1 FROM sources WHERE id=?", ("sedar:" + ref,)).fetchone() and \
           f"ev{pdf_reports.EXTRACTOR_VERSION}" in (con.execute(
               "SELECT note FROM sources WHERE id=?", ("sedar:" + ref,)).fetchone()[0] or ""):
            continue
        p = os.path.join(folder, r["file"])
        if not os.path.exists(p):
            print(f"[sedar] not downloaded yet: {r['file']}"); continue
        done += 1
        try:
            ingest_downloaded(p, company=r.get("company"), project=r.get("project"),
                              jurisdiction=r.get("jurisdiction"), commodity=r.get("commodity"),
                              filing_ref=ref, submitted=r.get("submitted"),
                              sedar_url=r.get("sedar_url"))
            ok += 1
        except Exception as e:
            print(f"[sedar] {r.get('company')} FAILED: {str(e)[:120]}")
    con.close()
    shards.export_shards()
    report_archive.build_index()
    print(f"[sedar] ingested {ok}/{done} downloaded reports this run")
    return {"ingested": ok}


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    ingest_folder(a[0] if a else os.path.expanduser("~/Downloads"),
                  a[1] if len(a) > 1 else None)
