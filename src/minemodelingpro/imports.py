"""Import drill data from spreadsheets and databases into a model.

Takes whatever a geologist actually has — CSV/TSV, Excel (.xlsx/.xls), Access
(.mdb/.accdb), a zip of any of those, or Parquet — works out which table is
collars / surveys / assays, maps the columns however they are spelled, converts
units and coordinates, and writes ONE dataset JSON in the same shape the 43-101
extractor produces, so ``projects.assemble`` merges it with everything else.

Design points that matter for real data:
  * columns are matched by meaning, not position ("HOLEID", "Hole_No", "BHID",
    "DDH" all map to the hole id; "EAST"/"X"/"UTM_E" to easting, …);
  * assays may be WIDE (a column per element) or LONG (element + value columns);
  * intervals may give TO or LENGTH; depths may be in feet;
  * grades may be %, g/t, ppm, ppb or oz/t — each converted to the standard unit;
  * coordinates may be lat/long, a stated EPSG code, or a UTM zone; with none of
    those the data stays in its own local grid (still modelled, no terrain);
  * every row that is dropped is counted and explained, so the upload page can
    show the user exactly what was and was not understood.

Attach to an existing project (merged by hole id) with ``attach_to="<slug>"``,
or leave it out to create a project of its own.

CLI:
    python -m minemodelingpro.imports path/to/files/* --project "North Zone" \\
        --company "Example Mining" --epsg 26911 [--attach-to murray-brook] [--private]
"""
import os
import re
import io
import csv
import json
import math
import glob
import shutil
import zipfile
import datetime
import subprocess
from collections import Counter, defaultdict

from minemodelingpro import holeid
from minemodelingpro import params as PARAMS

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMPORT_DIR = os.path.join(_ROOT, "data", "keep", "mmp_imports")          # public/committed
PRIVATE_DIR = os.path.join(_ROOT, "data", "private", "imports")          # gitignored
RAW_DIR = os.path.join(_ROOT, "data", "private", "uploads")

TABULAR = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".parquet", ".json",
           ".mdb", ".accdb", ".zip", ".dbf")

# --------------------------------------------------------------- column matching
def _norm(h):
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


_PATTERNS = [
    ("hole", r"^(holeid|hole|holeno|holenumber|holename|bhid|ddh|dhid|borehole|boreholeid|"
             r"drillhole|drillholeid|hole_?id|collarid|id|name|well|wellid)$"),
    ("east", r"^(east|easting|utme|utmeast|xutm|x|xcoord|xcoordinate|coordx|east_?m|locationx|gridx|e)$"),
    ("north", r"^(north|northing|utmn|utmnorth|yutm|y|ycoord|ycoordinate|coordy|north_?m|locationy|gridy|n)$"),
    ("elev", r"^(elev|elevation|rl|z|zcoord|masl|collarelev|collarelevation|elevm|altitude|height|topo|zm)$"),
    ("lat", r"^(lat|latitude|ddlat|latdd|ycoordlat)$"),
    ("lon", r"^(lon|long|longitude|ddlon|londd|lng|xcoordlong)$"),
    ("az", r"^(az|azi|azimuth|bearing|brg|dipdir|dipdirection|collarazimuth|azideg|azimuthdeg|grid_?azimuth)$"),
    ("dip", r"^(dip|inclination|incl|plunge|collardip|dipdeg|angle)$"),
    ("depth", r"^(depth|totaldepth|eoh|holelength|holedepth|finaldepth|td|length|maxdepth|endofhole|depthm)$"),
    ("from", r"^(from|fromm|fromdepth|depthfrom|start|startdepth|top|intervalfrom|from_?ft|dfrom)$"),
    ("to", r"^(to|tom|todepth|depthto|end|enddepth|bottom|intervalto|to_?ft|dto)$"),
    ("length", r"^(length|interval|width|thickness|corelength|lengthm|intervallength|sampledlength|intlen)$"),
    ("element", r"^(element|analyte|metal|assaytype|param|parameter|variable)$"),
    ("value", r"^(value|grade|result|assay|conc|concentration|val|ppm|gpt)$"),
    ("unit", r"^(unit|units|uom|gradeunit)$"),
    ("survey_depth", r"^(depth|md|measureddepth|surveydepth|at|distance|station|depthm)$"),
    ("zone", r"^(zone|utmzone|gridzone)$"),
    ("sample", r"^(sample|sampleid|sampleno|sampnum|labno|certificate)$"),
]

# element spellings -> (element, default unit)
_ELEMENTS = {
    "au": ("Au", "g/t"), "gold": ("Au", "g/t"), "augpt": ("Au", "g/t"), "au_gpt": ("Au", "g/t"),
    "ag": ("Ag", "g/t"), "silver": ("Ag", "g/t"), "cu": ("Cu", "%"), "copper": ("Cu", "%"),
    "pb": ("Pb", "%"), "lead": ("Pb", "%"), "zn": ("Zn", "%"), "zinc": ("Zn", "%"),
    "ni": ("Ni", "%"), "nickel": ("Ni", "%"), "co": ("Co", "%"), "cobalt": ("Co", "%"),
    "mo": ("Mo", "%"), "moly": ("Mo", "%"), "molybdenum": ("Mo", "%"), "sn": ("Sn", "%"),
    "w": ("W", "%"), "wo3": ("WO3", "%"), "u3o8": ("U3O8", "%"), "u": ("U", "%"),
    "li": ("Li", "%"), "li2o": ("Li2O", "%"), "sb": ("Sb", "%"), "antimony": ("Sb", "%"),
    "v2o5": ("V2O5", "%"), "fe": ("Fe", "%"), "iron": ("Fe", "%"), "mn": ("Mn", "%"),
    "pt": ("Pt", "g/t"), "pd": ("Pd", "g/t"), "rh": ("Rh", "g/t"), "pge": ("PGE", "g/t"),
    "3e": ("3E", "g/t"), "2e": ("PGE", "g/t"), "aueq": ("AuEq", "g/t"), "ageq": ("AgEq", "g/t"),
    "cueq": ("CuEq", "%"), "zneq": ("ZnEq", "%"), "nieq": ("NiEq", "%"), "treo": ("TREO", "%"),
    "reo": ("REO", "%"), "p2o5": ("P2O5", "%"), "cg": ("Cg", "%"), "graphite": ("Cg", "%"),
    "tgc": ("Cg", "%"), "ta2o5": ("Ta2O5", "%"), "nb2o5": ("Nb2O5", "%"), "bi": ("Bi", "%"),
    "te": ("Te", "g/t"), "ga": ("Ga", "g/t"), "ge": ("Ge", "g/t"), "in": ("In", "g/t"),
    "cr2o3": ("Cr2O3", "%"), "tio2": ("TiO2", "%"), "s": ("S", "%"), "as": ("As", "%"),
}
PRECIOUS_STD = {"Au", "Ag", "Pt", "Pd", "Rh", "AuEq", "AgEq", "PGE", "3E", "Te", "Ga", "Ge", "In"}

_UNIT_IN_HEADER = [
    (r"\b(g/?t|gpt|g/tonne|grammes?/t|gramspert)\b", "g/t"),
    (r"\b(ppm|mg/kg)\b", "ppm"), (r"\b(ppb|ug/kg|µg/kg)\b", "ppb"),
    (r"\b(oz/t|opt|oz/ton|ozpt)\b", "oz/t"), (r"(%|pct|percent)", "%"),
    (r"\b(lb/t|lbs/t)\b", "lb/t"), (r"\b(kg/t)\b", "kg/t"),
]
_FT_HEADER = re.compile(r"\(\s*(ft|feet|foot)\s*\)|\b(ft|feet)\b", re.I)


_UNIT_SUFFIX = re.compile(r"(ft|feet|foot|m|metres|meters|metre|meter|masl|msl|mts)$")


def _role(col):
    """Column role, tolerating unit suffixes ('FROM_FT', 'Depth_m', 'Elev (masl)')."""
    n = _norm(col)
    for cand in (n, _UNIT_SUFFIX.sub("", n)):
        if len(cand) < 1:
            continue
        for role, rx in _PATTERNS:
            if re.match(rx, cand):
                return role
    return None


def _element_of(col):
    """(element, unit) for an assay column like 'Au_ppm', 'Cu (%)', 'AuEq g/t'."""
    raw = str(col).strip()
    low = raw.lower()
    unit = None
    for rx, u in _UNIT_IN_HEADER:
        if re.search(rx, low):
            unit = u
            break
    tok = _norm(re.split(r"[\s_\-(\[/]", raw.replace("%", " "))[0] or raw)
    tok = re.sub(r"(gpt|ppm|ppb|pct|percent|grade|assay)$", "", tok)
    hit = _ELEMENTS.get(tok)
    if not hit:
        hit = _ELEMENTS.get(_norm(raw))
    if not hit:
        return None
    el, defunit = hit
    return el, (unit or defunit)


def to_std(el, unit, v):
    """Grade to the standard unit (g/t for precious metals, % for the rest)."""
    if v is None:
        return None, None
    std = "g/t" if el in PRECIOUS_STD else "%"
    u = (unit or std).lower()
    if u in ("g/t", "gpt", "ppm", "mg/kg") and std == "g/t":
        return v, std
    if u in ("g/t", "ppm") and std == "%":
        return v / 10000.0, std
    if u == "ppb":
        return (v / 1000.0, std) if std == "g/t" else (v / 1e7, std)
    if u in ("%", "pct"):
        return (v * 10000.0, std) if std == "g/t" else (v, std)
    if u in ("oz/t", "opt"):
        return (v * 34.2857, std) if std == "g/t" else (v * 3.42857, std)
    if u == "lb/t":
        return (v, std) if std == "g/t" else (v / 20.0, std)
    if u == "kg/t":
        return (v, std) if std == "g/t" else (v / 10.0, std)
    return v, std


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if (isinstance(f, float) and math.isnan(f)) else f
    t = str(v).strip().replace(",", "").replace("−", "-")
    t = re.sub(r"^[<>~≈≤≥]\s*", "", t)
    if not t or t.lower() in ("na", "n/a", "nan", "null", "-", "--", "none", "nd", "n.d."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ------------------------------------------------------------------ file reading
def read_tables(path, max_rows=2_000_000):
    """{table name: DataFrame} from any supported file (zip/Access may hold many)."""
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    out = {}
    if ext == ".zip":
        with zipfile.ZipFile(path) as z:
            tmp = os.path.join(os.path.dirname(path), "_unz_" + re.sub(r"\W+", "_", base))
            os.makedirs(tmp, exist_ok=True)
            for n in z.namelist():
                if n.endswith("/") or os.path.splitext(n)[1].lower() not in TABULAR:
                    continue
                if ".." in n or n.startswith("/"):
                    continue
                dest = z.extract(n, tmp)
                for k, v in read_tables(dest).items():
                    out[f"{os.path.basename(n)}:{k}" if k != os.path.basename(n) else k] = v
        return out
    if ext in (".xlsx", ".xlsm", ".xls"):
        xl = pd.ExcelFile(path)
        for sh in xl.sheet_names:
            df = xl.parse(sh, dtype=object)
            if len(df):
                out[f"{base}:{sh}"] = df
        return out
    if ext in (".mdb", ".accdb"):
        return _read_access(path)
    if ext == ".parquet":
        out[base] = pd.read_parquet(path)
        return out
    if ext == ".json":
        d = json.load(open(path))
        if isinstance(d, list):
            out[base] = pd.DataFrame(d)
        elif isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    out[f"{base}:{k}"] = pd.DataFrame(v)
        return out
    # delimited text: sniff the separator, tolerate junk lines
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        head = f.read(64 * 1024)
    try:
        sep = csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except Exception:
        sep = "\t" if head.count("\t") > head.count(",") else ","
    df = pd.read_csv(path, sep=sep, dtype=object, engine="python", on_bad_lines="skip",
                     nrows=max_rows, encoding="utf-8-sig", encoding_errors="replace")
    if df.shape[1] == 1:                       # header row may sit below junk lines
        df2 = pd.read_csv(path, sep=sep, dtype=object, engine="python", on_bad_lines="skip",
                          skiprows=1, nrows=max_rows, encoding="utf-8-sig", encoding_errors="replace")
        if df2.shape[1] > 1:
            df = df2
    out[base] = df
    return out


def _read_access(path):
    """Access databases via mdbtools (mdb-tables / mdb-export), when installed."""
    import pandas as pd
    out = {}
    try:
        names = subprocess.run(["mdb-tables", "-1", path], capture_output=True, text=True,
                               timeout=120, check=True).stdout.split("\n")
    except Exception as e:
        raise RuntimeError("Access file needs mdbtools installed (brew install mdbtools / "
                           f"apt-get install mdbtools): {str(e)[:80]}")
    for t in [n.strip() for n in names if n.strip()]:
        try:
            csv_text = subprocess.run(["mdb-export", path, t], capture_output=True, text=True,
                                      timeout=300, check=True).stdout
            df = pd.read_csv(io.StringIO(csv_text), dtype=object)
            if len(df):
                out[f"{os.path.basename(path)}:{t}"] = df
        except Exception:
            continue
    return out


# ------------------------------------------------------------- table classification
def profile(df):
    """What a table looks like: roles found, element columns, and a guessed kind."""
    roles, elements = {}, []
    for c in df.columns:
        r = _role(c)
        if r and r not in roles:
            roles[r] = c
        if r in (None, "value"):
            e = _element_of(c)
            if e and c not in [v for v in roles.values()]:
                elements.append((c, e[0], e[1]))
    # in an interval table, a "length"-ish column is the interval length, not the
    # hole's total depth (the two words overlap: "Length (ft)" means both)
    if "from" in roles and "length" not in roles and "depth" in roles \
            and re.search(r"length|interval|width|thickness|core", str(roles["depth"]), re.I):
        roles["length"] = roles.pop("depth")
    has = lambda *k: all(x in roles for x in k)
    kind = None
    if has("hole") and (has("east", "north") or has("lat", "lon")):
        kind = "collar"
    elif has("hole", "from") and (elements or "value" in roles or "element" in roles or "to" in roles):
        kind = "assay"
    elif has("hole", "az", "dip") and ("survey_depth" in roles or "depth" in roles) and not has("east", "north"):
        kind = "survey"
    elif has("hole") and (elements or "element" in roles) and ("from" in roles or "length" in roles):
        kind = "assay"
    elif has("hole", "az", "dip"):
        kind = "collar" if has("depth") else "survey"
    return {"kind": kind, "roles": roles, "elements": elements, "rows": int(len(df)),
            "columns": [str(c) for c in df.columns]}


def _feet(df, col):
    return bool(_FT_HEADER.search(str(col)))


# ------------------------------------------------------------------- parse tables
def parse_collars(df, roles, notes, feet=None):
    out = []
    f = 0.3048 if (feet if feet is not None else _feet(df, roles.get("depth", ""))) else 1.0
    zf = 0.3048 if _feet(df, roles.get("elev", "")) else 1.0
    bad = Counter()
    for _, r in df.iterrows():
        hid = str(r.get(roles.get("hole"), "") or "").strip()
        if not hid or hid.lower() in ("nan", "none", "total"):
            bad["no hole id"] += 1
            continue
        rec = {"hole": hid}
        if "east" in roles:
            rec["e"], rec["n"] = _num(r.get(roles["east"])), _num(r.get(roles["north"]))
        if "lat" in roles:
            la, lo = _num(r.get(roles["lat"])), _num(r.get(roles["lon"]))
            if la is not None and lo is not None and abs(la) <= 90 and abs(lo) <= 180:
                rec["lat"], rec["lon"] = la, lo
        if rec.get("e") is None and rec.get("lat") is None:
            bad["no coordinates"] += 1
            continue
        z = _num(r.get(roles.get("elev")))
        rec["z"] = round(z * zf, 2) if z is not None else None
        az = _num(r.get(roles.get("az")))
        dip = _num(r.get(roles.get("dip")))
        if dip is not None and dip > 0:
            dip = -dip                                   # positive-down convention
        d = _num(r.get(roles.get("depth")))
        rec["az"] = az if (az is None or 0 <= az <= 360) else None
        rec["dip"] = dip if (dip is None or -90 <= dip <= 0) else None
        rec["depth"] = round(d * f, 2) if d and 0 < d * f <= 5000 else None
        out.append(rec)
    for k, v in bad.items():
        notes.append(f"collars: {v} row(s) skipped — {k}")
    return out


def parse_surveys(df, roles, notes):
    out = []
    dcol = roles.get("survey_depth") or roles.get("depth")
    f = 0.3048 if _feet(df, dcol or "") else 1.0
    last = None
    for _, r in df.iterrows():
        hid = str(r.get(roles.get("hole"), "") or "").strip()
        if hid and hid.lower() not in ("nan", "none"):
            last = hid
        hid = hid or last
        d, az, dip = _num(r.get(dcol)), _num(r.get(roles.get("az"))), _num(r.get(roles.get("dip")))
        if not hid or d is None or az is None or dip is None:
            continue
        if dip > 0:
            dip = -dip
        if not (0 <= az <= 360 and -90 <= dip <= 0 and 0 <= d * f <= 5000):
            continue
        out.append({"hole": hid, "depth": round(d * f, 2), "az": az, "dip": dip})
    return out


def parse_assays(df, prof, notes):
    """Wide (column per element) or long (element + value columns) assay tables."""
    roles, elements = prof["roles"], prof["elements"]
    out = []
    fcol, tcol, lcol = roles.get("from"), roles.get("to"), roles.get("length")
    f = 0.3048 if (_feet(df, fcol or "") or _feet(df, tcol or "")) else 1.0
    long_mode = ("element" in roles and ("value" in roles or len(elements) <= 1))
    bad = Counter()
    for _, r in df.iterrows():
        hid = str(r.get(roles.get("hole"), "") or "").strip()
        if not hid or hid.lower() in ("nan", "none"):
            bad["no hole id"] += 1
            continue
        fr = _num(r.get(fcol))
        to = _num(r.get(tcol)) if tcol else None
        if to is None and lcol is not None and fr is not None:
            ln = _num(r.get(lcol))
            to = fr + ln if ln else None
        if fr is None or to is None:
            bad["no from/to"] += 1
            continue
        fr, to = fr * f, to * f
        if to <= fr or to - fr > 1000 or fr < 0:
            bad["bad interval"] += 1
            continue
        if long_mode:
            name = str(r.get(roles["element"], "") or "")
            hit = _element_of(name) or _ELEMENTS.get(_norm(name))
            if not hit:
                bad[f"unknown element '{name[:12]}'"] += 1
                continue
            el, unit = (hit if isinstance(hit, tuple) and len(hit) == 2 else (hit[0], hit[1]))
            if "unit" in roles and r.get(roles["unit"]):
                unit = str(r.get(roles["unit"])).strip() or unit
            v = _num(r.get(roles.get("value")))
            if v is None or v < 0:
                bad["no value"] += 1
                continue
            g, u = to_std(el, unit, v)
            out.append({"hole": hid, "from": round(fr, 2), "to": round(to, 2), "el": el,
                        "grade": round(g, 5), "unit": u, "sub": 0})
        else:
            got = False
            for col, el, unit in elements:
                v = _num(r.get(col))
                if v is None or v < 0:
                    continue
                g, u = to_std(el, unit, v)
                out.append({"hole": hid, "from": round(fr, 2), "to": round(to, 2), "el": el,
                            "grade": round(g, 5), "unit": u, "sub": 0})
                got = True
            if not got:
                bad["no grades in row"] += 1
    for k, v in bad.items():
        notes.append(f"assays: {v} row(s) skipped — {k}")
    return out


# ---------------------------------------------------------------- georeferencing
def georeference(collars, epsg=None, utm_zone=None, hemisphere="N", datum="WGS84", notes=None):
    """Add lat/lon to collars that carry projected coordinates."""
    notes = notes if notes is not None else []
    have_ll = [c for c in collars if c.get("lat") is not None]
    en = [c for c in collars if c.get("e") is not None]
    crs = {"kind": "local"}
    if have_ll and len(have_ll) >= len(en):
        crs = {"kind": "latlon"}
    elif en and (epsg or utm_zone):
        code = epsg
        if not code and utm_zone:
            base = 32700 if str(hemisphere).upper().startswith("S") else 32600
            if str(datum).upper().startswith("NAD83") and not str(hemisphere).upper().startswith("S"):
                base = 26900
            elif str(datum).upper().startswith("NAD27"):
                base = 26700
            code = base + int(utm_zone)
        try:
            from pyproj import Transformer
            tr = Transformer.from_crs(f"EPSG:{int(code)}", "EPSG:4326", always_xy=True)
            ok = 0
            for c in en:
                lo, la = tr.transform(c["e"], c["n"])
                if math.isfinite(la) and math.isfinite(lo) and abs(la) <= 90:
                    c["lat"], c["lon"] = round(la, 6), round(lo, 6); ok += 1
            crs = {"kind": "projected", "epsg": int(code), "located": ok}
        except Exception as e:
            notes.append(f"coordinates: EPSG:{code} could not be used ({str(e)[:60]}) — kept as a local grid")
    elif en:
        notes.append("coordinates: no EPSG code or UTM zone given — modelled in its own local grid "
                     "(no terrain, cannot merge with other sources)")
    pts = sorted((c["lat"], c["lon"]) for c in collars if c.get("lat") is not None)
    center = [round(pts[len(pts) // 2][0], 5), round(pts[len(pts) // 2][1], 5)] if pts else None
    return crs, center


# --------------------------------------------------------------------- the import
def build_dataset(paths, project=None, company=None, epsg=None, utm_zone=None, hemisphere="N",
                  datum="WGS84", attach_to=None, settings=None, dataset_id=None, owner=None,
                  private=True, source_name=None):
    """Read every file, assemble one dataset, and return it with a report of what
    was understood. Nothing is written to disk — see `save`."""
    import pandas as pd
    notes, tables = [], []
    collars, surveys, intervals = [], [], []
    for path in paths:
        try:
            got = read_tables(path)
        except Exception as e:
            notes.append(f"{os.path.basename(path)}: could not be read — {str(e)[:120]}")
            continue
        if not got:
            notes.append(f"{os.path.basename(path)}: no tables found")
        for name, df in got.items():
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            prof = profile(df)
            tables.append({"table": name, **{k: v for k, v in prof.items() if k != "roles"},
                           "roles": {k: str(v) for k, v in prof["roles"].items()},
                           "elements": [[c, e, u] for c, e, u in prof["elements"]]})
            if prof["kind"] == "collar":
                collars += parse_collars(df, prof["roles"], notes)
            elif prof["kind"] == "survey":
                surveys += parse_surveys(df, prof["roles"], notes)
            elif prof["kind"] == "assay":
                intervals += parse_assays(df, prof, notes)
            else:
                notes.append(f"{name}: not recognised as collars, surveys or assays "
                             f"(columns: {', '.join(map(str, df.columns[:8]))})")
    # de-duplicate
    seen, c2 = set(), []
    for c in collars:
        k = holeid.key(c["hole"])
        if k in seen:
            continue
        seen.add(k); c2.append(c)
    collars = c2
    seen, i2 = set(), []
    for x in intervals:
        k = (holeid.key(x["hole"]), x["from"], x["to"], x["el"])
        if k in seen:
            continue
        seen.add(k); i2.append(x)
    intervals = i2
    crs, center = georeference(collars, epsg, utm_zone, hemisphere, datum, notes)

    ckeys = {holeid.key(c["hole"]) for c in collars}
    ikeys = {holeid.key(x["hole"]) for x in intervals}
    orphans = sorted(ikeys - ckeys)[:20]
    if orphans:
        notes.append(f"{len(ikeys - ckeys)} hole(s) have assays but no collar "
                     f"(e.g. {', '.join(orphans[:6])}) — they can only be placed if another "
                     f"source in the project has their collars")
    clean_settings, errs = PARAMS.validate(settings or {})
    notes += [f"settings: {e}" for e in errs]
    slug = re.sub(r"[^a-z0-9]+", "-", (project or company or "import").lower()).strip("-")[:48] or "import"
    did = dataset_id or f"import:{slug}-{datetime.datetime.utcnow():%Y%m%d%H%M%S}"
    ds = {
        "id": did, "source": "import", "project": project, "company": company,
        "owner": owner, "private": bool(private), "attach_to": attach_to,
        "title_project": project, "crs": crs, "center": center, "params": clean_settings,
        "date": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        "imported": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_name": source_name or ", ".join(os.path.basename(p) for p in paths)[:160],
        "files": [os.path.basename(p) for p in paths],
        "collars": collars, "surveys": surveys, "intervals": intervals,
        "counts": {"collars": len(collars), "intervals": len(intervals),
                   "surveyed_holes": len({s["hole"] for s in surveys}),
                   "holes_with_intervals": len(ikeys), "linked": len(ckeys & ikeys),
                   "elements": dict(Counter(x["el"] for x in intervals))},
        "tables": tables, "notes": notes,
    }
    return ds


def validate(ds):
    """Blocking problems (the model cannot be built) vs warnings."""
    errors = []
    c = ds["counts"]
    if c["collars"] < 3:
        errors.append(f"only {c['collars']} collar(s) with coordinates — need at least 3")
    if c["intervals"] < 8:
        errors.append(f"only {c['intervals']} assay interval(s) — need at least 8")
    if c["linked"] < 2:
        errors.append("assays and collars don't share hole ids — check the hole id columns")
    return errors


def save(ds, private=None):
    d = PRIVATE_DIR if (ds.get("private") if private is None else private) else IMPORT_DIR
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, re.sub(r"[^A-Za-z0-9_.-]", "_", ds["id"]) + ".json")
    json.dump(ds, open(fn, "w"), separators=(",", ":"))
    return fn


def load_all(include_private=False):
    out = []
    dirs = [IMPORT_DIR] + ([PRIVATE_DIR] if include_private else [])
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                out.append(json.load(open(f)))
            except Exception:
                pass
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Import drill data (CSV/Excel/Access/zip) into a model")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--project"); ap.add_argument("--company")
    ap.add_argument("--epsg", type=int); ap.add_argument("--utm-zone", type=int)
    ap.add_argument("--hemisphere", default="N"); ap.add_argument("--datum", default="WGS84")
    ap.add_argument("--attach-to", help="slug of an existing project to merge into")
    ap.add_argument("--set", nargs="*", default=[], help="model parameters, e.g. block=5,5,5 radius_m=60")
    ap.add_argument("--private", action="store_true", help="keep out of the public site")
    ap.add_argument("--build", action="store_true", help="build the model page after importing")
    a = ap.parse_args()
    files = [f for pat in a.files for f in (glob.glob(pat) or [pat])]
    settings = dict(x.split("=", 1) for x in a.set if "=" in x)
    ds = build_dataset(files, project=a.project, company=a.company, epsg=a.epsg, utm_zone=a.utm_zone,
                       hemisphere=a.hemisphere, datum=a.datum, attach_to=a.attach_to,
                       settings=settings, private=a.private)
    print(json.dumps({"id": ds["id"], "counts": ds["counts"], "crs": ds["crs"], "center": ds["center"],
                      "tables": [{k: t[k] for k in ("table", "kind", "rows")} for t in ds["tables"]],
                      "notes": ds["notes"]}, indent=1))
    errs = validate(ds)
    if errs:
        print("NOT MODELLABLE:", "; ".join(errs))
    print("saved ->", save(ds))
    if a.build and not errs:
        from minemodelingpro import model3d, projects
        for d in projects.assemble():
            if ds["id"] in [s.get("id") for s in d["sources"]]:
                m = model3d.build_project(d)
                print("built ->", model3d.write_viewer(m, os.path.join("site", "models", m["slug"] + ".html")))
