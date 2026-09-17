"""Drill-news sync for MineModelingPro.

MMP's economic + 3D modelling reads a drill-news bank at
``data/keep/drillbank.sqlite`` (tables: releases, holes, intervals). Closeology
used to produce that file in the same repo. After the split MMP owns its own
copy, populated at build time by one of two sources:

  1. MineTerminalPro API  (target)  -- set MTP_API_URL + MTP_API_KEY
  2. A local copy         (interim) -- set CLOSEOLOGY_DRILLBANK=/path/to/drillbank.sqlite

If neither is configured this is a no-op: export.py and model3d.py already skip
the news layer gracefully when the file is absent, so a build still succeeds on
the government backbone + 43-101 data alone.

Run:  PYTHONPATH=src python -m minemodelingpro.drill_sync
"""
import os
import shutil
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEEP = os.path.join(_ROOT, "data", "keep")
DRILLBANK = os.path.join(KEEP, "drillbank.sqlite")

# ---- schema MMP reads (export.py / model3d.py) --------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS releases(
  id TEXT PRIMARY KEY, company TEXT, project TEXT, country TEXT,
  source TEXT, url TEXT, published TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS holes(
  release_id TEXT, hole_id TEXT, easting REAL, northing REAL,
  utm_zone TEXT, utm_hemi TEXT, datum TEXT, lat REAL, lon REAL,
  elev_m REAL, azimuth REAL, dip REAL, depth_m REAL);
CREATE TABLE IF NOT EXISTS intervals(
  release_id TEXT, hole_id TEXT, is_subinterval INTEGER,
  from_m REAL, to_m REAL, length_m REAL, element TEXT, grade REAL, unit TEXT);
CREATE INDEX IF NOT EXISTS ix_holes_rel ON holes(release_id);
CREATE INDEX IF NOT EXISTS ix_iv_rel ON intervals(release_id);
"""


def _fresh_db(path):
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript(_DDL)
    return con, tmp


def _commit_swap(con, tmp, path):
    con.commit()
    con.close()
    os.replace(tmp, path)


# ---- source 1: MineTerminalPro API -------------------------------------------
def sync_from_api(url, key, since=None, timeout=45):
    """Pull drill-result releases from the MineTerminalPro API into drillbank.sqlite.

    Maps the response shape agreed in the integration brief:
      {results:[{id, published_at, company, ticker, exchange, title, source_url,
                 project, jurisdiction, holes:[{hole_id, lat, lon, elev_m,
                 azimuth, dip, depth_m, easting, northing, utm_zone, utm_hemi,
                 datum, intervals:[{from_m,to_m,length_m,element,grade,unit,
                 is_subinterval}]}]}], next_cursor}
    Releases whose holes are not yet structured on the API side are still stored
    (release row + source link) so nothing is lost; their assays fill in once the
    API exposes structured drill data (or an extractor is added here).
    """
    import requests  # local import so a no-op build needs no network deps

    con, tmp = _fresh_db(DRILLBANK)
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    params = {"category": "drill-results", "limit": 200}
    if since:
        params["since"] = since
    n_rel = n_hole = n_iv = 0
    cursor = None
    pages = 0
    while True:
        if cursor:
            params["cursor"] = cursor
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        results = payload.get("results") or payload.get("data") or []
        for rel in results:
            rid = str(rel.get("id"))
            con.execute(
                "INSERT OR REPLACE INTO releases(id,company,project,country,source,url,published,title)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (rid, rel.get("company"), rel.get("project"),
                 rel.get("jurisdiction") or rel.get("country"),
                 rel.get("source") or "mineterminalpro",
                 rel.get("source_url") or rel.get("url"),
                 rel.get("published_at") or rel.get("published"),
                 rel.get("title")))
            n_rel += 1
            for h in (rel.get("holes") or []):
                hid = h.get("hole_id") or h.get("id")
                con.execute(
                    "INSERT INTO holes(release_id,hole_id,easting,northing,utm_zone,utm_hemi,"
                    "datum,lat,lon,elev_m,azimuth,dip,depth_m) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, hid, h.get("easting"), h.get("northing"), h.get("utm_zone"),
                     h.get("utm_hemi"), h.get("datum"), h.get("lat"), h.get("lon"),
                     h.get("elev_m"), h.get("azimuth"), h.get("dip"), h.get("depth_m")))
                n_hole += 1
                for iv in (h.get("intervals") or []):
                    con.execute(
                        "INSERT INTO intervals(release_id,hole_id,is_subinterval,from_m,to_m,"
                        "length_m,element,grade,unit) VALUES(?,?,?,?,?,?,?,?,?)",
                        (rid, hid, 1 if iv.get("is_subinterval") else 0,
                         iv.get("from_m"), iv.get("to_m"), iv.get("length_m"),
                         iv.get("element"), iv.get("grade"), iv.get("unit")))
                    n_iv += 1
        pages += 1
        cursor = payload.get("next_cursor") or payload.get("next")
        if not cursor or pages >= 200:
            break
    _commit_swap(con, tmp, DRILLBANK)
    print(f"[drill_sync] API: {n_rel} releases, {n_hole} holes, {n_iv} intervals -> {DRILLBANK}")
    return {"releases": n_rel, "holes": n_hole, "intervals": n_iv}


# ---- source 2: interim local copy --------------------------------------------
def sync_from_copy(src_path):
    if not os.path.exists(src_path):
        print(f"[drill_sync] copy source not found: {src_path} -- skipping")
        return {"skipped": True}
    os.makedirs(KEEP, exist_ok=True)
    shutil.copy2(src_path, DRILLBANK)
    try:
        con = sqlite3.connect(DRILLBANK)
        n = con.execute("SELECT count(*) FROM releases").fetchone()[0]
        con.close()
    except Exception:
        n = "?"
    print(f"[drill_sync] copied drill bank from {src_path} ({n} releases) -> {DRILLBANK}")
    return {"copied": True}


def sync():
    url, key = os.environ.get("MTP_API_URL"), os.environ.get("MTP_API_KEY")
    if url and key:
        return sync_from_api(url, key, since=os.environ.get("MTP_SINCE"))
    cp = os.environ.get("CLOSEOLOGY_DRILLBANK")
    if cp:
        return sync_from_copy(cp)
    print("[drill_sync] no source configured (MTP_API_URL/KEY or CLOSEOLOGY_DRILLBANK); "
          "leaving drill bank as-is -- news layer will be skipped if absent")
    return {"noop": True}


if __name__ == "__main__":
    out = sync()
    sys.exit(0 if not out.get("error") else 1)
