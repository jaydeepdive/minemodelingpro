"""Ingest provincial/territorial government GEOCHEMISTRY sample databases (rock,
soil, silt and drill-core samples with multi-element assays) into the MMP store.

These ArcGIS layers hold hundreds of thousands of samples, each with a location
and a wide row of element concentrations (AU_PPB, AG_PPM, CU_PPM, FE_PCT, ...).
Element columns are auto-detected from the ELEMENT_UNIT naming, so adding a
source is a config entry, not new code. Samples are point analyses (surface or
core), stored as located "collars" (no depth) plus one assay row per element —
the same schema the drill data uses, so MineModelingPro sees one unified assay
table. No OCR, and it runs headless (government ArcGIS isn't IP-throttled).

Run:  python -m minemodelingpro.gov_samples            # all sources
      python -m minemodelingpro.gov_samples yk_geochem --limit 5000
"""
import sys
import re
import datetime

import arcgis_common
from minemodelingpro import store

# element_unit column -> (element symbol, unit). Units kept native (ppb/ppm/%).
_ELEM_COL = re.compile(r"^([A-Za-z]{1,3})[_ ]?(PPB|PPM|PCT|PERCENT|GT|G_T|PPT|OZ)$", re.I)
_ELEM_OK = {"au", "ag", "cu", "pb", "zn", "ni", "co", "mo", "as", "sb", "bi", "w", "sn",
            "u", "th", "li", "be", "cr", "v", "mn", "fe", "ti", "al", "mg", "ca", "na",
            "k", "p", "s", "ba", "sr", "rb", "cs", "ga", "ge", "se", "te", "cd", "tl",
            "hg", "re", "pt", "pd", "au", "ce", "la", "nd", "y", "sc", "zr", "nb", "ta",
            "hf", "sn", "in", "b", "cl", "br", "f"}
_UNIT = {"ppb": "ppb", "ppm": "ppm", "pct": "%", "percent": "%", "gt": "g/t", "g_t": "g/t",
         "ppt": "ppt", "oz": "oz/t", "percentage": "%"}

# Full-name column style (BC RGS etc.): "<ELEMENTNAME>_<METHOD>_<UNIT>", e.g.
# GOLD_ICPMS_PPB, ARSENIC_INAA_PPM, IRON_AAS_PERCENTAGE. Same element is often
# reported by several methods; we keep one value per element per sample, in this
# method-preference order (ICPMS/FA best for trace metals, INAA for the rest).
_ELEM_NAME = {
    "aluminium": "Al", "aluminum": "Al", "antimony": "Sb", "arsenic": "As", "barium": "Ba",
    "beryllium": "Be", "bismuth": "Bi", "boron": "B", "bromine": "Br", "cadmium": "Cd",
    "calcium": "Ca", "cerium": "Ce", "cesium": "Cs", "chromium": "Cr", "cobalt": "Co",
    "copper": "Cu", "europium": "Eu", "fluorine": "F", "gallium": "Ga", "gold": "Au",
    "hafnium": "Hf", "iron": "Fe", "lanthanum": "La", "lead": "Pb", "lithium": "Li",
    "lutetium": "Lu", "magnesium": "Mg", "manganese": "Mn", "mercury": "Hg",
    "molybdenum": "Mo", "neodymium": "Nd", "nickel": "Ni", "niobium": "Nb",
    "phosphorus": "P", "potassium": "K", "rubidium": "Rb", "samarium": "Sm",
    "scandium": "Sc", "selenium": "Se", "silver": "Ag", "sodium": "Na", "strontium": "Sr",
    "sulphur": "S", "sulfur": "S", "tantalum": "Ta", "tellurium": "Te", "terbium": "Tb",
    "thallium": "Tl", "thorium": "Th", "tin": "Sn", "titanium": "Ti", "tungsten": "W",
    "uranium": "U", "vanadium": "V", "ytterbium": "Yb", "yttrium": "Y", "zinc": "Zn",
    "zirconium": "Zr", "palladium": "Pd", "platinum": "Pt", "rhenium": "Re",
    "germanium": "Ge", "indium": "In"}
_METHOD_RANK = {"icpms": 0, "icpes": 1, "icp": 1, "fa": 2, "inaa": 3, "naa": 3,
                "aas": 4, "nadnc": 5, "color": 6, "ion": 6}
_FULLCOL = re.compile(
    r"^([A-Za-z]+)_([A-Za-z]+)_(PPM|PPB|PERCENTAGE|PCT|PERCENT|GT|G_T)(_\d+)?$", re.I)


def _num(v):
    if v in (None, "", " "):
        return None
    s = str(v).strip().lstrip("<>~=")            # detection-limit markers
    try:
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None


SOURCES = {
    "yk_geochem": {
        "name": "Yukon Assessment Report Geochemistry",
        "jurisdiction": "Yukon",
        "layer": "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Geological/MapServer/1",
        "id_fields": ["SAMPLE_NAME", "OBJECTID"], "type_field": "SAMPLE_TYPE",
        "report_field": "REPORT_NUMBER", "method_field": "ANALYTICAL_METHOD_FAMILY",
        "lat_field": "LATITUDE_DD", "lon_field": "LONGITUDE_DD",
    },
    "yk_rgs": {
        "name": "Yukon Regional Geochemical Surveys (RGS) - All",
        "jurisdiction": "Yukon",
        "layer": "https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Geological/MapServer/41",
        "id_fields": ["SAMPLE_NUMBER", "SAMPLE_NAME", "OBJECTID"], "type_field": "SAMPLE_TYPE",
        "report_field": None, "method_field": None,
        "lat_field": "LATITUDE_DD", "lon_field": "LONGITUDE_DD",
    },
    "bc_rgs": {
        "name": "British Columbia Regional Geochemical Survey (RGS)",
        "jurisdiction": "British Columbia",
        "layer": "https://delivery.maps.gov.bc.ca/arcgis/rest/services/mpcm/bcgwpub/MapServer/564",
        "id_fields": ["MASTER_ID", "REG_GEOCHMCL_SURV_PNT_SP_ID", "OBJECTID"],
        "type_field": "MATERIAL", "report_field": None, "method_field": None,
        "lat_field": "LATITUDE", "lon_field": "LONGITUDE",
        "column_style": "fullname",
    },
}


def _element_cols(feats):
    """Detect element columns present (with data) in the returned features."""
    if not feats:
        return []
    props = feats[0].get("properties", {}) or {}
    cols = []
    for name in props:
        m = _ELEM_COL.match(name)
        if not m:
            continue
        el, unit = m.group(1).lower(), m.group(2).lower()
        if el in _ELEM_OK and unit in _UNIT:
            cols.append((name, el.title(), _UNIT[unit]))
    return cols


def _element_cols_full(feats):
    """Detect full-name element columns (GOLD_ICPMS_PPB style) across a sample of
    features (scan several rows — any one row has many nulls). Returns a dict
    element_symbol -> ordered list of (column, unit, method_rank), best first."""
    cols = {}
    for ft in feats[:200]:
        props = ft.get("properties", {}) or {}
        for name in props:
            m = _FULLCOL.match(name)
            if not m:
                continue
            el_name, method, unit = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
            sym = _ELEM_NAME.get(el_name)
            if not sym or unit not in _UNIT:
                continue
            rank = _METHOD_RANK.get(method, 9)
            entry = (name, _UNIT[unit], rank)
            cols.setdefault(sym, [])
            if entry not in cols[sym]:
                cols[sym].append(entry)
    for sym in cols:
        cols[sym].sort(key=lambda e: e[2])          # best method first
    return cols


def _emit_assays_full(p, uid, native, sid, ecols_full):
    """One assay row per element, taking the value from the best available method."""
    out = []
    for sym, choices in ecols_full.items():
        for col, unit, _rank in choices:
            val = _num(p.get(col))
            if val is not None:
                out.append({"source_id": sid, "hole_uid": uid, "native_id": native,
                            "from_m": None, "to_m": None, "length_m": None,
                            "element": sym, "grade": val, "unit": unit, "is_subinterval": 0})
                break
    return out


def ingest_source(key, cfg=None, limit=None):
    cfg = cfg or SOURCES[key]
    sid = f"gov_geo:{key}"
    where = "1=1"
    print(f"[geo:{key}] fetching {cfg['name']} …")
    feats = arcgis_common.fetch_layer(cfg["layer"], out_fields="*", where=where, geom=True)
    if limit:
        feats = feats[:limit]
    style = cfg.get("column_style", "symbol")
    if style == "fullname":
        ecols_full = _element_cols_full(feats)
        ecols = []
        print(f"[geo:{key}] {len(feats)} samples, {len(ecols_full)} elements "
              f"({', '.join(list(ecols_full)[:14])}{'…' if len(ecols_full) > 14 else ''})")
    else:
        ecols_full = None
        ecols = _element_cols(feats)
        print(f"[geo:{key}] {len(feats)} samples, {len(ecols)} element columns "
              f"({', '.join(e for _, e, _ in ecols[:12])}{'…' if len(ecols) > 12 else ''})")
    collars, assays, seen = [], [], set()
    for ft in feats:
        p = ft.get("properties", {}) or {}
        g = ft.get("geometry") or {}
        lon = lat = None
        if g.get("type") == "Point" and g.get("coordinates"):
            lon, lat = g["coordinates"][0], g["coordinates"][1]
        if lat is None:
            lat = _num(p.get(cfg.get("lat_field")))
            lon = _num(p.get(cfg.get("lon_field")))
        native = None
        for f in cfg["id_fields"]:
            if p.get(f) not in (None, "", " "):
                native = str(p[f]); break
        if not native:
            continue
        uid = f"{sid}:{native}"
        if uid in seen:
            continue
        seen.add(uid)
        collars.append({
            "hole_uid": uid, "source_id": sid, "native_id": native, "company": None,
            "project": p.get(cfg.get("report_field")) or None,
            "jurisdiction": cfg["jurisdiction"], "lat": lat, "lon": lon,
            "easting": None, "northing": None, "utm_zone": None, "utm_hemi": None, "datum": None,
            "elev_m": None, "azimuth": None, "dip": None, "depth_m": None, "year_drilled": None,
            "has_assay": 1, "assay_flags": p.get(cfg.get("type_field")) or None,
            "report_ref": p.get(cfg.get("report_field")) or None, "url": cfg["layer"]})
        if ecols_full is not None:
            assays.extend(_emit_assays_full(p, uid, native, sid, ecols_full))
        else:
            for col, el, unit in ecols:
                val = _num(p.get(col))
                if val is None:
                    continue
                assays.append({"source_id": sid, "hole_uid": uid, "native_id": native,
                               "from_m": None, "to_m": None, "length_m": None,
                               "element": el, "grade": val, "unit": unit, "is_subinterval": 0})
    con = store.connect()
    store.replace_collars(con, sid, collars)
    store.replace_assays(con, sid, assays)
    store.record_source(con, {
        "id": sid, "kind": "gov_geochem", "name": cfg["name"], "url": cfg["layer"],
        "jurisdiction": cfg["jurisdiction"],
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": len(collars), "n_assays": len(assays),
        "note": f"{len(ecols_full) if ecols_full is not None else len(ecols)} elements; "
                f"point geochem samples"})
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[geo:{key}] {len(collars)} samples, {len(assays)} element assays | store now "
          f"{s['collars']} collars, {s['assays']} assays")
    return len(assays)


def run(keys=None, limit=None):
    total = 0
    for k in (keys or list(SOURCES)):
        try:
            total += ingest_source(k, limit=limit)
        except Exception as e:
            print(f"[geo:{k}] FAILED: {str(e)[:160]}")
    return total


if __name__ == "__main__":
    a = sys.argv[1:]
    lim = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    keys = [x for x in a if not x.startswith("--") and (a.index(x) == 0 or a[a.index(x) - 1] != "--limit")]
    run(keys or None, limit=lim)
