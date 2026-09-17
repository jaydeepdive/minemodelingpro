"""Ingest provincial/territorial government drill-hole databases (the collar
backbone) into the MMP store via their public ArcGIS REST services.

These government layers give the authoritative COLLAR record — location,
orientation, depth, year, and (for most) a reference to the assessment report
that holds the full assays. They rarely carry the assay VALUES themselves; those
are chased separately from assessment files / NI 43-101 reports
(minemodelingpro.pdf_assays). Together: this places every historical hole on the
map and tells us which ones have assays worth extracting.

Each SOURCE is a config mapping the layer's native fields to the common collar
schema, so adding a province is a dict entry, not new code. Re-running is
idempotent (replace-by-source).

Run:  python -m minemodelingpro.gov_drillholes            # all sources
      python -m minemodelingpro.gov_drillholes nb sk      # selected
"""
import sys
import re
import io
import csv
import datetime
import urllib.request

import arcgis_common
from minemodelingpro import store


def _f(props, *names):
    """First present, non-empty attribute among candidate field names."""
    for n in names:
        if n in props and props[n] not in (None, "", " "):
            return props[n]
    return None


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _year(v):
    n = _num(v)
    if n is None:
        s = str(v or "")
        for tok in s.replace("-", " ").replace("/", " ").split():
            if tok.isdigit() and len(tok) == 4:
                return int(tok)
        return None
    return int(n) if 1800 < n < 2100 else None


# --- source registry -------------------------------------------------------
# assay_flag_fields: {ELEMENT: field} where a truthy/"Y" value flags the element
# was assayed (presence only). id_fields/az_fields/etc: candidate native columns.
SOURCES = {
    "nb": {
        "name": "New Brunswick NBGS Exploration Drillholes",
        "jurisdiction": "New Brunswick",
        "layer": "https://gis-erd-der.gnb.ca/server/rest/services/OpenData/NBGS_Exploration_Drillholes/MapServer/0",
        "id_fields": ["LABEL", "GRIDSTATION", "OBJECTID"],
        "az_fields": ["AZIMUTHTRUE"], "dip_fields": ["DIP"],
        "depth_fields": ["LENGTH_M"], "year_fields": ["YEARDRILLED"],
        "company_fields": [], "project_fields": ["GRIDNAME", "LOCATIONMAP"],
        "report_fields": ["REPT_NO"], "assay_flag_fields": {},
    },
    "on": {
        "name": "Ontario OMEIS Drill Hole (ODHD)",
        "jurisdiction": "Ontario",
        "layer": "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/GeologyOntario/GeologyOntario_Map/MapServer/47",
        "id_fields": ["HOLE_IDENT", "COMPANY_HOLE_IDENT", "OBJECTID"],
        "az_fields": ["AZIMUTH"], "dip_fields": ["DIP"],
        "depth_fields": ["LENGTH"], "year_fields": ["YEAR_DRILLED"],
        "elev_fields": ["ELEVATION_M"],
        "company_fields": ["COMPANY_NAME"], "project_fields": ["PROPERTY_NAME", "TOWNSHIP"],
        "report_fields": ["TECH_ID", "INFO_LINK"],
        "assay_list_field": "ELEMENTS",   # comma/space list of assayed elements
        "assay_flag_fields": {},
    },
    "yk": {
        "name": "Yukon Geological Survey — Drillhole Locations",
        "jurisdiction": "Yukon",
        "layer": "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Geological/MapServer/6",
        "id_fields": ["DRILLHOLE_ID", "DDH_NUMBER", "OBJECTID"],
        "az_fields": ["AZIMUTH"], "dip_fields": ["DIP"],
        "depth_fields": ["TOTAL_LENGTH_M", "END_OF_HOLE_M"], "year_fields": ["YEAR_DRILLED"],
        "elev_fields": ["ELEVATION_M"],
        "company_fields": ["CORE_OWNER", "SUBMITTED_BY"], "project_fields": ["PROPERTY", "ZONE_DESC"],
        "report_fields": ["MINFILE_NUMBER"], "commodity_fields": ["COMMODITIES"],
        "assay_flag_fields": {},
    },
    "sk": {
        "name": "Saskatchewan Mineral Exploration — Drillholes",
        "jurisdiction": "Saskatchewan",
        "layer": "https://gis.saskatchewan.ca/arcgis/rest/services/Economy/Mineral_Exploration/MapServer/3",
        "id_fields": ["GOS_UNIQUE_DRILLHOLE_ID", "DRILLHOLE_NAME", "OBJECTID"],
        "az_fields": ["DH_AZIMUTH"], "dip_fields": ["DH_INCLINATION"],
        "depth_fields": ["TOTAL_DH_LENGTH_M"], "year_fields": ["DATE_DRILLED"],
        "elev_fields": ["ORIGINAL_COLLAR_ELEVATION_M", "ELEV_CORRECTED_1ARCSEC_DEM_M"],
        "company_fields": ["COMPANY"], "project_fields": ["PROJECT_OR_PROPERTY_NAME"],
        "report_fields": ["SOURCE"], "commodity_fields": ["COMMODITY_OF_INTEREST"],
        "assay_flag_fields": {},
    },
}


def ingest_source(key, cfg=None):
    cfg = cfg or SOURCES[key]
    sid = f"gov:{key}"
    print(f"[gov:{key}] fetching {cfg['name']} …")
    feats = arcgis_common.fetch_layer(cfg["layer"], out_fields="*", geom=True)
    rows, seen = [], set()
    for ft in feats:
        p = ft.get("properties", {}) or {}
        g = ft.get("geometry") or {}
        lon = lat = None
        if g.get("type") == "Point" and g.get("coordinates"):
            lon, lat = g["coordinates"][0], g["coordinates"][1]
        native = _f(p, *cfg["id_fields"])
        native = str(native) if native is not None else None
        if not native:
            continue
        uid = f"{sid}:{native}"
        if uid in seen:                       # keep first; some layers repeat ids per sample
            continue
        seen.add(uid)
        flags = [el for el, fld in cfg.get("assay_flag_fields", {}).items()
                 if str(p.get(fld, "")).strip().upper() in ("Y", "YES", "1", "TRUE")]
        alf = cfg.get("assay_list_field")
        if alf and p.get(alf):
            flags += [t.strip() for t in re.split(r"[,;/ ]+", str(p[alf])) if t.strip()]
        rows.append({
            "hole_uid": uid, "source_id": sid, "native_id": native,
            "company": _f(p, *cfg.get("company_fields", [])),
            "project": _f(p, *cfg.get("project_fields", [])),
            "jurisdiction": cfg["jurisdiction"],
            "lat": lat, "lon": lon,
            "easting": None, "northing": None, "utm_zone": None, "utm_hemi": None,
            "datum": "NAD83",
            "elev_m": _num(_f(p, *(cfg.get("elev_fields") or ["ELEVATION", "ELEV", "ELEV_M", "RL"]))),
            "azimuth": _num(_f(p, *cfg.get("az_fields", []))),
            "dip": _num(_f(p, *cfg.get("dip_fields", []))),
            "depth_m": _num(_f(p, *cfg.get("depth_fields", []))),
            "year_drilled": _year(_f(p, *cfg.get("year_fields", []))),
            "has_assay": 1 if flags else 0,
            "assay_flags": ",".join(flags) if flags else None,
            "report_ref": _f(p, *cfg.get("report_fields", [])),
            "url": cfg.get("record_url"),
        })
    con = store.connect()
    store.replace_collars(con, sid, rows)
    store.record_source(con, {
        "id": sid, "kind": "gov_drillholes", "name": cfg["name"], "url": cfg["layer"],
        "jurisdiction": cfg["jurisdiction"],
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": len(rows), "n_assays": 0,
        "note": f"{sum(1 for r in rows if r['lat'] is not None)} located"})
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[gov:{key}] {len(rows)} collars ingested | store now {s['collars']} collars "
          f"({s['collars_located']} located) across {s['sources']} sources")
    return len(rows)


# --------------------------------------------------------------- Quebec (CSV)
# SIGEOM publishes the whole diamond-drill inventory as one CSV (UTM NAD83 +
# per-interval lithology). Not an ArcGIS layer, so it gets its own reader — but
# it writes to the same collar schema, plus lithology rows.
QC_CSV = ("https://gq.mines.gouv.qc.ca/documents/SIGEOM/TOUTQC/FRA/CSV/"
          "SIGEOM_QC_Sondages_CSV/Forage%20diamant.csv")


def ingest_quebec(url=QC_CSV):
    from pyproj import Transformer
    sid = "gov:qc"
    print("[gov:qc] downloading SIGEOM diamond-drill CSV …")
    req = urllib.request.Request(url, headers={"User-Agent": "closeology-mmp/1.0"})
    raw = urllib.request.urlopen(req, timeout=180).read().decode("latin-1", "replace")
    rdr = csv.DictReader(io.StringIO(raw))
    tf = {}  # zone -> transformer (NAD83 UTM -> WGS84)

    def to_ll(zone, e, n):
        try:
            z = int(float(zone))
        except (TypeError, ValueError):
            return None, None
        if not (7 <= z <= 22) or e in (None, "") or n in (None, ""):
            return None, None
        if z not in tf:
            tf[z] = Transformer.from_crs(f"EPSG:{26900 + z}", "EPSG:4326", always_xy=True)
        try:
            lon, lat = tf[z].transform(float(e), float(n))
            return lat, lon
        except Exception:
            return None, None

    collars, litho, seen = [], [], set()
    for p in rdr:
        native = (p.get("NUMR_FORG") or "").strip()
        if not native:
            continue
        uid = f"{sid}:{native}"
        if uid in seen:
            continue
        seen.add(uid)
        zone = p.get("FUS_UTM"); e = p.get("ESTN"); n = p.get("NORD")
        lat, lon = to_ll(zone, e, n)
        # lithology intervals: PROF_i is the depth to the base of interval i
        prev, litho_rows, maxprof = 0.0, [], 0.0
        for i in range(1, 11):
            pr = _num(p.get(f"PROF{i}"))
            lith = (p.get(f"LITH{i}") or "").strip()
            minr = (p.get(f"MINR{i}") or "").strip()
            if pr is None:
                continue
            maxprof = max(maxprof, pr)
            if lith or minr:
                litho_rows.append({"source_id": sid, "hole_uid": uid, "from_m": prev,
                                   "to_m": pr, "rock": lith or None, "note": minr or None})
            prev = pr
        litho.extend(litho_rows)
        depth = _num(p.get("SOMR_LITH")) or (maxprof or None)
        collars.append({
            "hole_uid": uid, "source_id": sid, "native_id": native,
            "company": (p.get("NOM_COMP") or p.get("NOM_DETN") or "").strip() or None,
            "project": (p.get("CANT_SEIGN") or p.get("QUADR_1") or "").strip() or None,
            "jurisdiction": "Quebec", "lat": lat, "lon": lon,
            "easting": _num(e), "northing": _num(n),
            "utm_zone": int(float(zone)) if str(zone).strip() not in ("", "None") else None,
            "utm_hemi": "N", "datum": "NAD83", "elev_m": None,
            "azimuth": _num(p.get("AZMT_DEPR")), "dip": _num(p.get("PLON_DEPR")),
            "depth_m": depth, "year_drilled": _year(p.get("AN_FORAGE")),
            "has_assay": 0, "assay_flags": None,
            "report_ref": (p.get("NUMR_RAPR") or "").strip() or None, "url": None})
    con = store.connect()
    store.replace_collars(con, sid, collars)
    con.execute("DELETE FROM lithology WHERE source_id=?", (sid,))
    if litho:
        con.executemany("""INSERT INTO lithology(source_id,hole_uid,from_m,to_m,rock,note)
            VALUES(:source_id,:hole_uid,:from_m,:to_m,:rock,:note)""", litho)
    store.record_source(con, {
        "id": sid, "kind": "gov_drillholes", "name": "SIGEOM Quebec — Forages au diamant",
        "url": url, "jurisdiction": "Quebec",
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": len(collars), "n_assays": 0,
        "note": f"{sum(1 for c in collars if c['lat'] is not None)} located; {len(litho)} lithology rows"})
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[gov:qc] {len(collars)} collars ({sum(1 for c in collars if c['lat'] is not None)} located), "
          f"{len(litho)} lithology rows | store now {s['collars']} collars")
    return len(collars)


def run(keys=None):
    keys = keys or (list(SOURCES) + ["qc"])
    if keys and "qc" in keys:
        try:
            ingest_quebec()
        except Exception as e:
            print(f"[gov:qc] FAILED: {str(e)[:160]}")
        keys = [k for k in keys if k != "qc"]
    total = 0
    for k in keys:
        try:
            total += ingest_source(k)
        except Exception as e:
            print(f"[gov:{k}] FAILED: {str(e)[:160]}")
    return total


if __name__ == "__main__":
    run(sys.argv[1:] or None)
