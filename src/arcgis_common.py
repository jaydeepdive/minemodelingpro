"""Generic ArcGIS REST -> pipeline-schema ingest, config-driven, shared by every
province/territory whose mineral data lives on an ArcGIS server (NL, SK, MB, NWT,
NB, NS, AB). One provincial config (see ca_provinces.py) supplies the layer URLs
and field names; this module does the paged fetch, geometry (always returned in
EPSG:4326) and the mapping to the standard columns the pipeline expects.

Standard outputs written under data/<slug>/:
  occurrences.parquet : minfile,name,status,commodities(list),commodity,deposit_type,
                        minfile_url,prod_ind,township,geometry(Point 4326)
  claims.parquet      : TENURE_NUMBER_ID,CLAIM_NAME,OWNER_NAME,ISSUE_DATE,GOOD_TO_DATE,geometry
  leases.parquet      : claim,geometry            (optional)
  reserves.parquet    : name,geometry             (no-stake polygons; optional)
"""
import os
import time
import datetime
from urllib.parse import quote
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape, Point

UA = {"User-Agent": "closeology-ca/1.0"}


def _get(url, params):
    for a in range(5):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=180)
            r.raise_for_status()
            return r.json()
        except Exception:
            if a == 4:
                raise
            time.sleep(2 * (a + 1))


def _max_record_count(layer_url, default=1000):
    """Read the layer's server-side page cap so we page correctly even on servers
    that don't set exceededTransferLimit (e.g. ArcGIS Online hosted FeatureServers)."""
    try:
        d = _get(layer_url, {"f": "json"})
        n = int(d.get("maxRecordCount") or default)
        return max(100, min(n, 2000))
    except Exception:
        return default


def fetch_layer(layer_url, out_fields="*", where="1=1", geom=True, generalize=None):
    """Page an ArcGIS layer as GeoJSON in EPSG:4326. Pages at the server's
    maxRecordCount and continues while a full page comes back (canonical ArcGIS
    pagination) — robust to servers that omit exceededTransferLimit."""
    page = _max_record_count(layer_url)
    feats, off = [], 0
    while True:
        q = {"where": where, "outFields": out_fields,
             "returnGeometry": str(geom).lower(), "outSR": "4326",
             "resultOffset": off, "resultRecordCount": page, "f": "geojson"}
        if generalize:
            q["maxAllowableOffset"] = generalize
        d = _get(layer_url + "/query", q)
        b = d.get("features", [])
        feats += b
        if len(b) < page:            # partial (or empty) page => no more records
            break
        off += len(b)
    return feats


def _norm_date(v):
    """Return YYYY-MM-DD from epoch-ms (number) or a date string in common formats."""
    if v is None or v == "":
        return ""
    # epoch milliseconds (ArcGIS date fields)
    if isinstance(v, (int, float)):
        try:
            return datetime.datetime.utcfromtimestamp(float(v) / 1000.0).strftime("%Y-%m-%d")
        except Exception:
            return ""
    s = str(v).strip()
    if s.isdigit() and len(s) >= 11:          # epoch-ms as string
        try:
            return datetime.datetime.utcfromtimestamp(int(s) / 1000.0).strftime("%Y-%m-%d")
        except Exception:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s[:len(fmt) + 2].split("T")[0]
                                              if "T" in s else s[:19], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return s[:10]


def _commlist(raw):
    parts = [c.strip() for c in str(raw or "").replace(";", ",").replace("/", ",").split(",") if c.strip()]
    # title-case unless it's a short symbol like "Au"
    out = []
    for c in parts:
        out.append(c if (len(c) <= 3 and c[:1].isupper()) else c.title())
    # de-dup preserving order
    seen, uniq = set(), []
    for c in out:
        k = c.lower()
        if k not in seen:
            seen.add(k); uniq.append(c)
    return uniq


def fetch_occurrences(cfg, out_dir):
    o = cfg["occ"]
    fs = fetch_layer(o["url"], o.get("fields", "*"), o.get("where", "1=1"), geom=True)
    rows, geoms = [], []
    for f in fs:
        g = f.get("geometry")
        if not g or g.get("type") != "Point":
            # some servers return MultiPoint; take first coord
            try:
                gm = shape(g)
                pt = gm.representative_point()
            except Exception:
                continue
        else:
            try:
                pt = Point(g["coordinates"][0], g["coordinates"][1])
            except Exception:
                continue
        if pt.x == 0 and pt.y == 0:
            continue
        p = f.get("properties", {})
        comm = _commlist(p.get(o["comm"])) if o.get("comm") else []
        if o.get("comm2") and p.get(o["comm2"]):
            for c in _commlist(p.get(o["comm2"])):
                if c not in comm:
                    comm.append(c)
        status = str(p.get(o["status"]) or "").strip() if o.get("status") else o.get("default_status", "Occurrence")
        pid = str(p.get(o["id"]) or "").strip()
        is_prod = False
        for tok in o.get("producer_tokens", []):
            if tok.lower() in status.lower():
                is_prod = True
                break
        if o.get("producer_field"):
            pv = str(p.get(o["producer_field"]) or "").strip().lower()
            if pv in ("y", "yes", "true", "1"):
                is_prod = True
                if o.get("default_status") and status == o.get("default_status"):
                    status = "Past Producer"
        if is_prod and o.get("producer_relabel", True):
            status = "Past Producer" if "past" in status.lower() or "dormant" in status.lower() \
                or "exhaust" in status.lower() else status
        url = ""
        if o.get("url_field"):
            url = str(p.get(o["url_field"]) or "").strip()
            if url and not url.lower().startswith("http"):
                url = ""       # some "reference" fields are citations, not links
        elif o.get("url_tmpl") and pid:
            url = o["url_tmpl"].format(id=quote(pid, safe=""))
        drill = ""
        tok = o.get("drill_status_token")
        if tok and tok.lower() in status.lower():
            drill = "drill-tested"
        rows.append({"minfile": pid,
                     "name": (str(p.get(o["name"]) or "").strip() or pid) if o.get("name") else pid,
                     "status": status, "commodities": comm, "commodity": ", ".join(comm),
                     "deposit_type": str(p.get(o["deptype"]) or "").strip() if o.get("deptype") else "",
                     "minfile_url": url, "prod_ind": "Y" if is_prod else "N", "township": "",
                     "drill_highlights": drill})
        geoms.append(pt)
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    gdf.to_parquet(os.path.join(out_dir, "occurrences.parquet"))
    print(f"[{cfg['slug']}] occurrences {len(gdf)} (producers {int((gdf.prod_ind=='Y').sum())})")
    return len(gdf)


def _polys(feats, colmap):
    rows, geoms = [], []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        try:
            gm = shape(g)
        except Exception:
            continue
        if gm.is_empty:
            continue
        p = f.get("properties", {})
        rows.append({k: p.get(v) for k, v in colmap.items()})
        geoms.append(gm)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


def fetch_claims(cfg, out_dir):
    c = cfg["claims"]
    fs = fetch_layer(c["url"], c.get("fields", "*"), c.get("where", "1=1"),
                     geom=True, generalize=c.get("generalize", 25))
    colmap = {}
    if c.get("id"):
        colmap["_id"] = c["id"]
    if c.get("name"):
        colmap["_name"] = c["name"]
    if c.get("owner"):
        colmap["_own"] = c["owner"]
    if c.get("issue"):
        colmap["_iss"] = c["issue"]
    if c.get("expiry"):
        colmap["_exp"] = c["expiry"]
    g = _polys(fs, colmap)
    g["TENURE_NUMBER_ID"] = g["_id"].astype(str) if "_id" in g else [str(i) for i in range(len(g))]
    g["CLAIM_NAME"] = g["_name"].astype(str) if "_name" in g else ""
    g["OWNER_NAME"] = g["_own"].astype(str) if "_own" in g else ""
    g["ISSUE_DATE"] = g["_iss"].map(_norm_date) if "_iss" in g else ""
    g["GOOD_TO_DATE"] = g["_exp"].map(_norm_date) if "_exp" in g else ""
    g = g.drop(columns=[x for x in ("_id", "_name", "_own", "_iss", "_exp") if x in g.columns])
    g.to_parquet(os.path.join(out_dir, "claims.parquet"))
    print(f"[{cfg['slug']}] claims {len(g)}")
    return len(g)


def fetch_leases(cfg, out_dir):
    l = cfg.get("leases")
    if not l:
        return 0
    fs = fetch_layer(l["url"], l.get("fields", "*"), l.get("where", "1=1"),
                     geom=True, generalize=l.get("generalize", 25))
    g = _polys(fs, {"claim": l.get("id", "OBJECTID")})
    g["claim"] = g["claim"].astype(str)
    g.to_parquet(os.path.join(out_dir, "leases.parquet"))
    print(f"[{cfg['slug']}] leases {len(g)}")
    return len(g)


def fetch_reserves(cfg, out_dir):
    r = cfg.get("reserves")
    if not r:
        return 0
    urls = r["url"] if isinstance(r["url"], list) else [r["url"]]
    wheres = r["where"] if isinstance(r.get("where"), list) else [r.get("where", "1=1")] * len(urls)
    frames = []
    for u, w in zip(urls, wheres):
        try:
            fs = fetch_layer(u, r.get("fields", "*"), w, geom=True, generalize=r.get("generalize", 40))
            gg = _polys(fs, {"name": r.get("name_field", "OBJECTID")})
            if len(gg):
                frames.append(gg)
        except Exception as e:
            print(f"[{cfg['slug']}] reserve layer skipped ({str(e)[:50]})")
    if not frames:
        return 0
    g = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    g["name"] = g["name"].astype(str)
    g.to_parquet(os.path.join(out_dir, "reserves.parquet"))
    print(f"[{cfg['slug']}] reserves (no-stake) {len(g)}")
    return len(g)


_CANADA = ("https://raw.githubusercontent.com/codeforgermany/click_that_hood/"
           "main/public/data/canada.geojson")


def boundary(cfg):
    """Save the province/territory boundary to data/keep/<slug>_boundary.parquet."""
    slug = cfg["slug"]
    dst = os.path.join("data", "keep", f"{slug}_boundary.parquet")
    if os.path.exists(dst):
        return
    os.makedirs(os.path.join("data", "keep"), exist_ok=True)
    d = requests.get(_CANADA, headers=UA, timeout=90).json()
    names = cfg.get("boundary_names") or [cfg["boundary_name"]]
    for f in d["features"]:
        nm = f["properties"].get("name")
        if nm in names:
            gpd.GeoDataFrame({"name": [cfg["name"]]}, geometry=[shape(f["geometry"])],
                             crs="EPSG:4326").to_parquet(dst)
            print(f"[{slug}] boundary saved ({nm})")
            return
    print(f"[{slug}] WARNING boundary not found for {names}")


def run_fetch(cfg):
    """Fetch everything for one ArcGIS-based province/territory."""
    out_dir = cfg["dir"]
    os.makedirs(out_dir, exist_ok=True)
    boundary(cfg)
    fetch_occurrences(cfg, out_dir)
    fetch_claims(cfg, out_dir)
    fetch_leases(cfg, out_dir)
    fetch_reserves(cfg, out_dir)
