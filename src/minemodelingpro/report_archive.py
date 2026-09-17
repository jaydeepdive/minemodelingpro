"""Retention for the source technical reports (NI 43-101s and assessment files).

We keep the ACTUAL PDFs, not just the extracted data, so any report can be
retrieved on demand later. The PDFs are large and must not bloat the git repo,
so the archive is a GitHub Release ("report-archive") whose assets are the PDFs
(durable, ~2 GB/file, stable download URLs). A small committed index
(data/keep/mmp_reports_index.json) maps every report to its metadata + archive
download URL + extraction stats, so the catalogue is searchable in-repo.

Env: GITHUB_TOKEN (or GH_TOKEN) + GITHUB_REPOSITORY (owner/repo) enable upload;
without them, archiving is skipped and the index still records everything else.
"""
import os
import re
import json
import hashlib

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX = os.path.join(_ROOT, "data", "keep", "mmp_reports_index.json")
QUEUE = os.path.join(_ROOT, "data", "keep", "mmp_report_queue.json")
RELEASE_TAG = "report-archive"


def _repo():
    return os.environ.get("GITHUB_REPOSITORY", "jaydeepdive/closeology")


def _token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _api(token):
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}


def _ensure_release(token):
    owner_repo = _repo()
    r = requests.get(f"https://api.github.com/repos/{owner_repo}/releases/tags/{RELEASE_TAG}",
                     headers=_api(token), timeout=30)
    if r.status_code == 200:
        return r.json()
    r = requests.post(f"https://api.github.com/repos/{owner_repo}/releases", headers=_api(token),
                      json={"tag_name": RELEASE_TAG, "name": "Technical report archive",
                            "body": "Source NI 43-101 / assessment-file PDFs retained for MineModelingPro.",
                            "make_latest": "false"}, timeout=30)
    r.raise_for_status()
    return r.json()


def archive_pdf(pdf_path, asset_name, token=None):
    """Upload a PDF to the report-archive release; return its download URL.
    Idempotent: an existing asset of the same name is reused."""
    token = token or _token()
    if not token or not os.path.exists(pdf_path):
        return None
    rel = _ensure_release(token)
    asset_name = re.sub(r"[^A-Za-z0-9._-]", "_", asset_name)
    if not asset_name.endswith(".pdf"):
        asset_name += ".pdf"
    for a in rel.get("assets", []):
        if a["name"] == asset_name:
            return a["browser_download_url"]
    up = rel["upload_url"].split("{")[0] + f"?name={asset_name}"
    with open(pdf_path, "rb") as f:
        r = requests.post(up, headers={**_api(token), "Content-Type": "application/pdf"},
                          data=f.read(), timeout=180)
    if r.status_code in (200, 201):
        return r.json().get("browser_download_url")
    return None


def _queue_meta():
    try:
        return {q["url"]: q for q in json.load(open(QUEUE))}
    except Exception:
        return {}


def build_index(db=None):
    """Build the searchable report index from the MMP store's ni43101 sources +
    the queue metadata + any archive URLs already recorded. Committed to repo."""
    from minemodelingpro import store
    db = db or store.DB_PATH
    if not os.path.exists(db):
        return {"reports": 0}
    con = store.connect(db)
    qmeta = _queue_meta()
    prev = {}
    if os.path.exists(INDEX):
        try:
            prev = {e["id"]: e for e in json.load(open(INDEX)).get("reports", [])}
        except Exception:
            prev = {}
    met = {}
    try:
        for mr in con.execute("SELECT source_id, process_types, refractory, recovery_summary FROM metallurgy"):
            met[mr[0]] = {"process": mr[1], "refractory": mr[2], "recovery": mr[3]}
    except Exception:
        pass
    rows = con.execute("""SELECT id, name, url, jurisdiction, pulled_at, n_collars, n_assays, note
                          FROM sources WHERE kind IN ('ni43101','assessment')""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        note = d.get("note") or ""
        m = re.search(r"(\d+)\s+resource rows", note)
        am = re.search(r"archive=(\S+)", note)
        q = qmeta.get(d["url"], {})
        mm = met.get(d["id"], {})
        out.append({
            "id": d["id"], "company": d.get("name"), "project": q.get("project"),
            "commodity": q.get("commodity"), "jurisdiction": d.get("jurisdiction") or q.get("jurisdiction"),
            "source_url": d.get("url"),
            "archive_url": (am.group(1) if am else None) or (prev.get(d["id"], {}) or {}).get("archive_url"),
            "collected": d.get("pulled_at"),
            "collars": d.get("n_collars"), "assays": d.get("n_assays"),
            "resource_rows": int(m.group(1)) if m else 0,
            "has_method": "method=y" in note,
            "metallurgy_process": mm.get("process"),
            "refractory": mm.get("refractory"),
            "recovery": (mm.get("recovery") or "")[:160] or None})
    con.close()
    out.sort(key=lambda e: (e.get("jurisdiction") or "zz", e.get("company") or ""))
    archived = sum(1 for e in out if e.get("archive_url"))
    json.dump({"generated": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds") + "Z",
               "count": len(out), "archived": archived, "reports": out},
              open(INDEX, "w"), indent=1)
    print(f"[archive] index: {len(out)} reports ({archived} with retained PDF) -> {os.path.relpath(INDEX, _ROOT)}")
    return {"reports": len(out), "archived": archived}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] and sys.argv[1] == "index":
        build_index()
