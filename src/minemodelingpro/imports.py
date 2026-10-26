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
import sys
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
from minemodelingpro import ivtable as IVT
from minemodelingpro import params as PARAMS

# the modeller's own ranking of which metal a project is about
_PRIORITY = ["Au", "Cu", "Ni", "Ag", "Zn", "U3O8", "Li2O", "Pb", "Co", "Mo", "Pt", "Pd", "PGE",
             "3E", "Sn", "W", "WO3", "Sb", "TREO", "REO", "V2O5", "Fe", "Mn", "Cg", "P2O5",
             "AuEq", "CuEq", "AgEq", "ZnEq", "NiEq"]

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
    # magnetic declination/variation, as survey files carry it. Read, reported,
    # and applied only when something says the azimuths are magnetic — a
    # declination column is as often a record of a correction already made.
    ("dec", r"^(dec|decl|declination|magdec|magdecl|magneticdeclination|magvar|magneticvariation|variation|magcorr|magneticcorrection)$"),
    ("depth", r"^(depth|totaldepth|eoh|holelength|holedepth|finaldepth|td|length|maxdepth|endofhole|depthm)$"),
    ("from", r"^(from|fromm|fromdepth|depthfrom|start|startdepth|top|intervalfrom|from_?ft|dfrom)$"),
    ("to", r"^(to|tom|todepth|depthto|end|enddepth|bottom|intervalto|to_?ft|dto)$"),
    ("length", r"^(length|interval|width|thickness|corelength|lengthm|intervallength|sampledlength|intlen)$"),
    ("element", r"^(element|analyte|metal|assaytype|param|parameter|variable)$"),
    ("value", r"^(value|grade|result|assay|conc|concentration|val|ppm|gpt|amount|"
              r"analysis|analyte?value|assayvalue|gradevalue|resultvalue|finalvalue|bestvalue)$"),
    ("unit", r"^(unit|units|uom|gradeunit)$"),
    ("survey_depth", r"^(depth|md|measureddepth|surveydepth|at|distance|station|depthm)$"),
    ("zone", r"^(zone|utmzone|gridzone)$"),
    ("sample", r"^(sample|sampleid|sampleno|sampnum|labno|certificate)$"),
    # Logged geology. Every real drill database has a column like this and MMP was
    # throwing it away, which is why an imported dataset had its deposit style
    # guessed from intercept widths — for the one person who does not need to guess.
    # "zone" alone is taken: it means the UTM zone on a collar table.
    ("lith", r"^(lith|lith1|litho|lithology|lithcode|rock|rocktype|rock_?type|"
             r"geology|geol|geolunit|unit|unitcode|formation|domain|domaincode|"
             r"minzone|minzonecode|mineralization|mineralisation|minerali[sz]ationtype|"
             r"zonecode|zone_?name|lithunit|majorlith|primarylith)$"),
    ("alteration", r"^(alt|alteration|altcode|alttype|alteration_?type)$"),
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
_VALUE_RX = dict(_PATTERNS)["value"]

# What may sit beside the element in a grade column's name. A lab export carries
# au_ppm next to au_cert_num, au_analysis_code and au_flag, and reading the
# certificate number as a gold grade is worse than missing the column: a
# six-figure "grade" survives every plausibility check the model applies.
_BESIDE_ELEMENT = {"", "ppm", "ppb", "gpt", "gt", "g", "t", "pct", "percent", "pc",
                   "opt", "ozt", "oz", "kg", "lb", "lbs", "kgt", "lbt", "mgkg", "ugkg",
                   "grade", "assay", "value", "result", "conc", "concentration",
                   "final", "best", "wt", "wtpct", "ppmw", "dl"}
_NAMES_ELEMENT_LAST = {"grade", "assay", "result", "value", "conc", "total", "tot"}


def _role(col):
    """Column role, tolerating unit suffixes ('FROM_FT', 'Depth_m', 'Elev (masl)')."""
    n = _norm(col)
    for cand in (n, _UNIT_SUFFIX.sub("", n)):
        if len(cand) < 1:
            continue
        for role, rx in _PATTERNS:
            if re.match(rx, cand):
                return role
    # a grade column often carries its unit in the name ("Grade_ppm"), so try the
    # stripped name — but against the value pattern ALONE. Stripping the unit for
    # every pattern made y_ppm, which is yttrium, into the northing.
    nu = re.sub(r"(ppm|ppb|gpt|gt|pct|percent|ozt|opt|kgt|lbt)$", "", n)
    if nu and nu != n and re.match(_VALUE_RX, nu):
        return "value"
    return None


def _unit_in(label):
    """The grade unit named in a column header or an element label, if any.

    Underscores and brackets are separators here, not letters: an underscore is a
    word character to a regular expression, so "Cu_ppm" hid its unit behind a
    missing word boundary and was read as per cent — out by ten thousand."""
    low = re.sub(r"[_\[\]()|]+", " ", str(label or "").lower())
    for rx, u in _UNIT_IN_HEADER:
        if re.search(rx, low):
            return u
    return None


def _element_of(col):
    """(element, unit) for an assay column like 'Au_ppm', 'Cu (%)', 'AuEq g/t'.

    The element has to be the column's subject, not merely a word in it: a real
    drill database names au_cert_num, au_analysis_code and au_flag beside au_ppm,
    and all four start with the element. Only a name whose remaining words are
    units or grade words is a grade."""
    raw = str(col).strip()
    unit = _unit_in(raw)
    whole = _ELEMENTS.get(_norm(raw))
    if whole:
        return whole[0], (unit or whole[1])
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", raw.replace("%", " ")) if t]
    if not toks:
        return None
    head = _norm(toks[0])
    hit = _ELEMENTS.get(head) or _ELEMENTS.get(
        re.sub(r"(gpt|ppm|ppb|pct|percent|grade|assay)$", "", head))
    if hit and all(_norm(t) in _BESIDE_ELEMENT for t in toks[1:]):
        return hit[0], (unit or hit[1])
    # the element is not always the first word: "Grade (% U3O8)" names it last
    if head in _NAMES_ELEMENT_LAST:
        for t in toks[1:]:
            h = _ELEMENTS.get(_norm(t))
            if h:
                return h[0], (unit or h[1])
    return None


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
def read_tables(path, max_rows=2_000_000, usecols=None):
    """{table name: DataFrame} from any supported file (zip/Access may hold many).

    `usecols` narrows a delimited file to the columns that mean something, which is
    what keeps a wide export inside the worker's memory: every cell is read as an
    object, so the sample numbers and lab codes nobody models cost as much as the
    grades."""
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
    def _read(**kw):
        a = dict(sep=sep, dtype=object, on_bad_lines="skip", nrows=max_rows,
                 encoding="utf-8-sig", encoding_errors="replace")
        a.update(kw)
        try:                      # the C parser is several times faster; fall back if it refuses
            return pd.read_csv(path, low_memory=False, **a)
        except Exception:
            return pd.read_csv(path, engine="python", **a)

    df = _read(usecols=usecols) if usecols else _read()
    if df.shape[1] == 1 and not usecols:       # header row may sit below junk lines
        df2 = _read(skiprows=1)
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
    nograde = not elements and "value" not in roles and "element" not in roles
    logged = "lith" in roles or "alteration" in roles
    kind = None
    if has("hole") and (has("east", "north") or has("lat", "lon")):
        kind = "collar"
    elif has("hole", "az", "dip") and nograde and not logged \
            and ("survey_depth" in roles or "depth" in roles or "from" in roles):
        # azimuth and dip with no assays is a downhole survey, whether the stations
        # are given as depths or as the sections of the hole they apply to
        kind = "survey"
    elif has("hole", "from") and logged and nograde:
        kind = "lithology"
    elif has("hole", "from") and (elements or "value" in roles or "element" in roles or "to" in roles):
        kind = "assay"
    elif has("hole") and (elements or "element" in roles) and ("from" in roles or "length" in roles):
        kind = "assay"
    elif has("hole", "az", "dip"):
        kind = "survey"
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
        if "zone" in roles:
            m = re.search(r"(\d{1,2})\s*([NSns])?", str(r.get(roles["zone"]) or ""))
            if m and 1 <= int(m.group(1)) <= 60:
                rec["zone"] = int(m.group(1))
                if m.group(2):
                    rec["hemi"] = m.group(2).upper()
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


def parse_surveys(df, roles, notes, azimuth_ref="auto"):
    """Downhole survey stations: hole, depth, azimuth, dip — and the declination
    if the file carries one.

    A survey file arrives with azimuths in one of two references and no way to
    tell them apart by value. `azimuth_ref` settles it: "magnetic" adds the
    declination to get true north, "true"/"grid" leaves the azimuths alone, and
    "auto" applies the declination only when the column names say the azimuth is
    magnetic — otherwise it reports the column and leaves the azimuths as filed,
    because a declination column is as often the record of a correction already
    applied as an instruction to apply one."""
    out = []
    dcol = roles.get("survey_depth") or roles.get("depth") or roles.get("from")
    tcol = roles.get("to") if dcol == roles.get("from") else None
    mcol = roles.get("dec")
    f = 0.3048 if _feet(df, dcol or "") else 1.0
    ref = str(azimuth_ref or "auto").lower()
    azname = str(roles.get("az") or "")
    mag_named = bool(re.search(r"mag", azname, re.I))
    apply_dec = (ref in ("mag", "magnetic")) or (ref == "auto" and mag_named and mcol is not None)
    if ref in ("true", "grid", "none"):
        apply_dec = False
    last = None
    decs = []
    ends = {}
    for _, r in df.iterrows():
        hid = str(r.get(roles.get("hole"), "") or "").strip()
        if hid and hid.lower() not in ("nan", "none"):
            last = hid
        hid = hid or last
        d, az, dip = _num(r.get(dcol)), _num(r.get(roles.get("az"))), _num(r.get(roles.get("dip")))
        dec = _num(r.get(mcol)) if mcol else None
        if dec is not None and abs(dec) > 90:
            dec = None
        if not hid or d is None or az is None or dip is None:
            continue
        if dip > 0:
            dip = -dip
        if dec is not None:
            decs.append(dec)
            if apply_dec:
                az = (az + dec) % 360.0
        if not (0 <= az <= 360 and -90 <= dip <= 0 and 0 <= d * f <= 5000):
            continue
        rec = {"hole": hid, "depth": round(d * f, 2), "az": round(az, 2), "dip": dip}
        if dec is not None:
            rec["dec"] = dec
        out.append(rec)
        if tcol:
            t = _num(r.get(tcol))
            if t is not None and 0 < t * f <= 5000 and t > d:
                end = dict(rec); end["depth"] = round(t * f, 2)
                ends[hid] = end
    if tcol and ends:
        depths = {(x["hole"], x["depth"]) for x in out}
        out += [e for h, e in ends.items() if (h, e["depth"]) not in depths]
        notes.append("surveys: the stations are given as sections of the hole — each section's "
                     "start carries its azimuth and dip, and the last section's end is the "
                     "deepest point the survey reaches")
    if decs:
        avg = sum(decs) / len(decs)
        if apply_dec:
            notes.append("surveys: a declination column averaging %+.1f degrees was added to the "
                         "azimuths (read as magnetic bearings corrected to true north)" % avg)
        else:
            notes.append("surveys: the file carries a declination column averaging %+.1f degrees, "
                         "and the azimuths were used as filed. If they are magnetic bearings, set "
                         "Survey azimuths to magnetic on the upload form and it is added." % avg)
    return out


def _cols(df, names):
    """Column name -> plain Python list, for the columns asked for.

    A row at a time through `iterrows` builds a pandas Series per row, which on a
    long assay table is most of the parse. Reading each column once and walking
    them in step is the same work without that."""
    out = {}
    for c in {c for c in names if c is not None}:
        if c in df.columns:
            out[c] = df[c].tolist()
    return out


def parse_assays(df, prof, notes):
    """Wide (column per element) or long (element + value columns) assay tables."""
    roles, elements = prof["roles"], prof["elements"]
    out = []
    fcol, tcol, lcol = roles.get("from"), roles.get("to"), roles.get("length")
    hcol, ecol, vcol, ucol = (roles.get("hole"), roles.get("element"),
                              roles.get("value"), roles.get("unit"))
    f = 0.3048 if (_feet(df, fcol or "") or _feet(df, tcol or "")) else 1.0
    long_mode = ("element" in roles and ("value" in roles or len(elements) <= 1))
    vunit = _unit_in(vcol or "")
    bad = Counter()
    A = _cols(df, [hcol, fcol, tcol, lcol, ecol, vcol, ucol] + [c for c, _, _ in elements])
    n = len(df)
    nil = [None] * n
    H, F, T, L = (A.get(hcol, nil), A.get(fcol, nil), A.get(tcol, nil), A.get(lcol, nil))
    E, V, U = A.get(ecol, nil), A.get(vcol, nil), A.get(ucol, nil)
    cols = [(A.get(c, nil), el, unit) for c, el, unit in elements]
    seen_el = {}                        # the element label is repeated thousands of times
    hole_str = {}                  # 900,000 rows name a few hundred holes between them
    for i in range(n):
        raw_h = H[i]
        hid = hole_str.get(raw_h)
        if hid is None:
            hid = hole_str[raw_h] = sys.intern(str(raw_h or "").strip())
        if not hid or hid.lower() in ("nan", "none"):
            bad["no hole id"] += 1
            continue
        fr = _num(F[i])
        to = _num(T[i]) if tcol else None
        if to is None and lcol is not None and fr is not None:
            ln = _num(L[i])
            to = fr + ln if ln else None
        if fr is None or to is None:
            bad["no from/to"] += 1
            continue
        fr, to = fr * f, to * f
        if to <= fr or to - fr > 1000 or fr < 0:
            bad["bad interval"] += 1
            continue
        fr, to = round(fr, 2), round(to, 2)
        if long_mode:
            name = str(E[i] or "")
            hit = seen_el.get(name, 0)
            if hit == 0:
                h = _element_of(name) or _ELEMENTS.get(_norm(name))
                if h:
                    el_, unit_ = (h if isinstance(h, tuple) and len(h) == 2 else (h[0], h[1]))
                    # where the element name carries no unit ("Cu", not "Cu_ppm"), the
                    # value column's header is the next best witness — copper in ppm
                    # read as per cent is out by ten thousand, so the default is last
                    if not _unit_in(name) and vunit:
                        unit_ = vunit
                    hit = (el_, unit_)
                else:
                    hit = None
                seen_el[name] = hit
            if not hit:
                bad[f"unknown element '{name[:12]}'"] += 1
                continue
            el, unit = hit
            if ucol and U[i]:
                unit = str(U[i]).strip() or unit
            v = _num(V[i])
            if v is None or v < 0:
                bad["no value"] += 1
                continue
            g, u = to_std(el, unit, v)
            out.append({"hole": hid, "from": fr, "to": to, "el": el,
                        "grade": round(g, 5), "unit": u, "sub": 0})
        else:
            got = False
            for arr, el, unit in cols:
                v = _num(arr[i])
                if v is None or v < 0:
                    continue
                g, u = to_std(el, unit, v)
                out.append({"hole": hid, "from": fr, "to": to, "el": el,
                            "grade": round(g, 5), "unit": u, "sub": 0})
                got = True
            if not got:
                bad["no grades in row"] += 1
    for k, v in bad.items():
        notes.append(f"assays: {v} row(s) skipped — {k}")
    return out


# ---------------------------------------------------------------- georeferencing

def parse_lithology(df, roles, notes=None):
    """Logged geology intervals: hole, from, to, and what the rock is.

    The same shape as an assay table without the assays. What the unit means is
    the client's business — MMP keeps the codes verbatim and lets a project say
    which of them are the mineralised body (`domain_units`), because a logging
    code only means something to the person who wrote it."""
    out = []
    notes = notes if notes is not None else []
    skipped = Counter()
    hc = roles.get("hole")
    fc, tc, lc = roles.get("from"), roles.get("to"), roles.get("length")
    rc = roles.get("lith") or roles.get("alteration")
    ac = roles.get("alteration") if roles.get("lith") else None
    if not (hc and fc and rc):
        return out
    f = 0.3048 if re.search(r"ft|feet", str(fc), re.I) else 1.0
    for _, row in df.iterrows():
        hid = str(row.get(hc, "") or "").strip()
        if not hid:
            skipped["no hole id"] += 1
            continue
        fr = _num(row.get(fc))
        to = _num(row.get(tc)) if tc else None
        if to is None and lc is not None and fr is not None:
            ln = _num(row.get(lc))
            to = fr + ln if ln is not None else None
        rock = str(row.get(rc) or "").strip()
        if fr is None or to is None or not rock or rock.lower() in ("nan", "none", "-"):
            skipped["no interval or no rock code"] += 1
            continue
        fr, to = fr * f, to * f
        if to <= fr or to > 5000:
            skipped["interval not plausible"] += 1
            continue
        rec = {"hole": hid, "from": round(fr, 2), "to": round(to, 2), "rock": rock[:40]}
        if ac:
            alt = str(row.get(ac) or "").strip()
            if alt and alt.lower() not in ("nan", "none", "-"):
                rec["alt"] = alt[:40]
        out.append(rec)
    for k, v in skipped.items():
        notes.append(f"logged geology: {v} row(s) skipped — {k}")
    return out


_LAND = None


def _spread(vals):
    v = sorted(x for x in vals if x is not None)
    return (v[-1] - v[0]) if len(v) > 1 else 0.0


def looks_degrees(en):
    """True when columns called x/y actually hold decimal degrees.

    Range alone does not settle it — a mine grid can run 0 to 2,000 — so range
    and spread are read together: no drill programme spans less than two units of
    a metric grid, so x,y inside (-180,180)/(-90,90) with a spread under two is a
    GPS reading written into columns named x and y, which is how most collar
    files arrive."""
    xs = [c["e"] for c in en if c.get("e") is not None]
    ys = [c["n"] for c in en if c.get("n") is not None]
    if len(xs) < 2 or len(ys) < 2:
        return False
    if _spread(xs) > 2.0 or _spread(ys) > 2.0:
        return False
    if not (any(x != int(x) for x in xs) or any(y != int(y) for y in ys)):
        return False                      # whole numbers that small are a grid
    return ((all(abs(x) <= 180 for x in xs) and all(abs(y) <= 90 for y in ys)) or
            (all(abs(x) <= 90 for x in xs) and all(abs(y) <= 180 for y in ys)))


def _dem_fit(lat, lon, z, zoom=11, scale=1.0):
    """How well the terrain under these points tracks the stated elevations.

    Three numbers, because one is not enough to tell a place from its lookalike:
    the deviation about the mean difference (a constant offset is forgiven — a GPS
    z is an ellipsoid height and can sit 60 m off the ground everywhere at once,
    but it cannot climb a hill the ground does not climb), that offset, and the
    slope of ground against stated elevation, which is 1 where the reference is
    right and anything at all where a different hillside happens to fit."""
    import numpy as np
    from minemodelingpro import terrain
    z = np.asarray(z, float) * scale
    try:
        dem = np.asarray(terrain.elevations(np.asarray(lat, float), np.asarray(lon, float), zoom), float)
    except Exception:
        return None
    ok = np.isfinite(dem) & np.isfinite(z)
    if ok.sum() < 3:
        return None
    dem, z = dem[ok], z[ok]
    d = dem - z
    off = float(np.median(d))
    mad = float(np.median(np.abs(d - off)))
    var = float(np.var(z))
    slope = float(np.cov(z, dem)[0, 1] / var) if var > 25 else None
    return {"mad": mad, "off": off, "slope": slope}


def _region(lat, lon):
    from minemodelingpro import terrain
    try:
        return terrain.region_of(lat, lon)
    except Exception:
        return None


def _as_latlon(en, notes):
    """Read x/y as degrees, working out which of the two is the latitude."""
    xs = [c["e"] for c in en]
    ys = [c["n"] for c in en]
    xlat = all(abs(v) <= 90 for v in xs)
    ylat = all(abs(v) <= 90 for v in ys)
    swap = None
    if ylat and not xlat:
        swap = False                                   # x = longitude (the usual)
    elif xlat and not ylat:
        swap = True
    else:
        zc = [c for c in en if c.get("z") is not None][:30]
        if len(zc) >= 3 and _spread([c["z"] for c in zc]) > 15:
            fa = _dem_fit([c["n"] for c in zc], [c["e"] for c in zc], [c["z"] for c in zc])
            fb = _dem_fit([c["e"] for c in zc], [c["n"] for c in zc], [c["z"] for c in zc])
            a = fa["mad"] if fa else None
            b = fb["mad"] if fb else None
            if a is not None and (b is None or a <= b):
                swap = False
            elif b is not None:
                swap = True
        if swap is None:
            la = [c for c in en if _region(c["n"], c["e"])]
            lo = [c for c in en if _region(c["e"], c["n"])]
            swap = len(lo) > len(la)
    for c in en:
        la, lo = (c["e"], c["n"]) if swap else (c["n"], c["e"])
        if abs(la) <= 90 and abs(lo) <= 180:
            c["lat"], c["lon"] = round(la, 6), round(lo, 6)
    where = _region(en[0].get("lat"), en[0].get("lon")) if en[0].get("lat") is not None else None
    notes.append("coordinates: the x/y columns hold decimal degrees, read as "
                 + ("y = longitude, x = latitude" if swap else "x = longitude, y = latitude")
                 + (f" ({where})" if where else ""))
    return {"kind": "latlon", "source": "x/y read as degrees",
            "located": sum(1 for c in en if c.get("lat") is not None)}


def _utm_candidates(e, n, datum="WGS84"):
    """Every UTM zone that puts this easting/northing on land, with its EPSG.

    The easting and northing are the same numbers in all sixty zones, so the
    candidates are one latitude at sixty longitudes; most of them are ocean."""
    from pyproj import Transformer
    out = []
    d = str(datum or "WGS84").upper()
    for hemi, base in (("N", 32600), ("S", 32700)):
        for zone in range(1, 61):
            code = base + zone
            if d.startswith("NAD83") and hemi == "N" and 1 <= zone <= 23:
                code = 26900 + zone
            elif d.startswith("NAD27") and hemi == "N" and 1 <= zone <= 22:
                code = 26700 + zone
            try:
                tr = Transformer.from_crs(f"EPSG:{code}", "EPSG:4326", always_xy=True)
                lo, la = tr.transform(e, n)
            except Exception:
                continue
            if not (math.isfinite(la) and math.isfinite(lo)) or abs(la) > 85:
                continue
            if _region(la, lo):
                out.append({"epsg": code, "zone": zone, "hemi": hemi, "tr": tr,
                            "lat": la, "lon": lo, "where": _region(la, lo)})
    return out


def infer_utm(en, notes, datum="WGS84"):
    """Which UTM zone places these collars on the ground their elevations describe.

    A collar file of x/y/z with no stated reference is the common case and the one
    a geologist cannot answer from memory. Narrow to the zones that land on land,
    then let the terrain decide: the right zone is the one whose hillsides rise and
    fall the way the collar elevations do. A coarse pass shortlists, a finer one
    picks, and a winner has to beat its rival by a distance — a smooth slope
    resembles other smooth slopes, so an ambiguous answer is reported as
    ambiguous rather than guessed."""
    import numpy as np
    zc = [c for c in en if c.get("z") is not None and -500 < c["z"] < 9000][:40]
    cands = _utm_candidates(float(np.median([c["e"] for c in en])),
                            float(np.median([c["n"] for c in en])), datum)
    if not cands:
        return None
    if not zc or _spread([c["z"] for c in zc]) < 15:
        if len(cands) == 1:
            c0 = cands[0]
            notes.append(f"coordinates: only UTM zone {c0['zone']}{c0['hemi']} puts this drilling "
                         f"on land ({c0['where']}) — used, unconfirmed by elevations")
            return c0
        notes.append("coordinates: no UTM zone given and the collar elevations are too flat to "
                     "identify one from the terrain — candidates: "
                     + ", ".join(f"{c['zone']}{c['hemi']} ({c['where']})" for c in cands[:6])
                     + ". Set the zone on the upload form.")
        return None
    Z = [c["z"] for c in zc]

    def score(cand, zoom, scale, pts):
        lo, la = cand["tr"].transform([x["e"] for x in pts], [x["n"] for x in pts])
        return _dem_fit(la, lo, [x["z"] for x in pts], zoom, scale)

    for scale, uz in ((1.0, "m"), (0.3048, "ft")):
        coarse = []
        sub = zc[::max(1, len(zc) // 10)][:10]
        for c in cands:
            f = score(c, 11, scale, sub)
            if f and abs(f["off"]) <= 400:
                coarse.append((f["mad"], c))
        coarse.sort(key=lambda t: t[0])
        fine = []
        for _, c in coarse[:6]:
            f = score(c, 13, scale, zc)
            if not f or abs(f["off"]) > 400:
                continue
            if f["slope"] is not None and not (0.65 <= f["slope"] <= 1.4):
                continue                      # the ground does not rise with the elevations
            fine.append((f, c))
        fine.sort(key=lambda t: (t[0]["mad"], abs(t[0]["off"])))
        if not fine:
            continue
        f, c = fine[0]
        rival = next((t for t in fine[1:] if t[1]["epsg"] != c["epsg"]), None)
        if f["mad"] > 35:
            continue
        if rival and rival[0]["mad"] < max(2.5 * f["mad"], f["mad"] + 8):
            notes.append("coordinates: no UTM zone given, and the collar elevations fit two places "
                         "equally well (zone %d%s in %s to %d m, zone %d%s in %s to %d m) — modelled "
                         "in its own grid. Set the zone on the upload form."
                         % (c["zone"], c["hemi"], c["where"], round(f["mad"]),
                            rival[1]["zone"], rival[1]["hemi"], rival[1]["where"], round(rival[0]["mad"])))
            return None
        notes.append("coordinates: no UTM zone given — zone %d%s (EPSG:%d, %s) is the only one whose "
                     "terrain matches the collar elevations (to %d m, mean offset %d m%s)"
                     % (c["zone"], c["hemi"], c["epsg"], c["where"], round(f["mad"]), round(f["off"]),
                        "; elevations read as feet" if uz == "ft" else ""))
        hit = dict(c)
        hit.update({"z_scale": scale, "z_units": uz, "dem_misfit_m": round(f["mad"], 1)})
        return hit
    best = None
    for c in cands[:6]:
        f = score(c, 11, 1.0, zc)
        if f and (best is None or f["mad"] < best[0]["mad"]):
            best = (f, c)
    notes.append("coordinates: no UTM zone given, and no zone puts these collars on terrain that "
                 "matches their elevations%s — modelled in its own grid, which is what a mine grid "
                 "is. Set the zone or an EPSG code if the coordinates are real."
                 % ((" (closest: zone %d%s, off by %d m)"
                     % (best[1]["zone"], best[1]["hemi"], round(best[0]["mad"]))) if best else ""))
    return None


def _stated_zone(en):
    """The UTM zone the collar table states, when most of its rows agree on one."""
    zs = [c["zone"] for c in en if c.get("zone")]
    if len(zs) < max(2, 0.6 * len(en)):
        return None
    zone = Counter(zs).most_common(1)[0][0]
    if sum(1 for z in zs if z == zone) < 0.6 * len(en):
        return None
    hemis = [c.get("hemi") for c in en if c.get("hemi")]
    return zone, (Counter(hemis).most_common(1)[0][0] if hemis else None)


def _utm_epsg(zone, hemisphere="N", datum="WGS84"):
    base = 32700 if str(hemisphere).upper().startswith("S") else 32600
    d = str(datum or "").upper()
    if d.startswith("NAD83") and not str(hemisphere).upper().startswith("S"):
        base = 26900
    elif d.startswith("NAD27"):
        base = 26700
    return base + int(zone)


def _place(en, code, notes, source, zone=None):
    """Project the collars with a known CRS, and say whether the ground agrees."""
    crs = {"kind": "local"}
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs(f"EPSG:{int(code)}", "EPSG:4326", always_xy=True)
    except Exception as e:
        notes.append(f"coordinates: EPSG:{code} could not be used ({str(e)[:60]}) — kept as a local grid")
        return crs
    ok = 0
    for c in en:
        try:
            lo, la = tr.transform(c["e"], c["n"])
        except Exception:
            continue
        if math.isfinite(la) and math.isfinite(lo) and abs(la) <= 90:
            c["lat"], c["lon"] = round(la, 6), round(lo, 6); ok += 1
    crs = {"kind": "projected", "epsg": int(code), "located": ok, "source": source}
    where = _region(en[0].get("lat"), en[0].get("lon")) if en and en[0].get("lat") is not None else None
    if where:
        crs["where"] = where
    zc = [c for c in en if c.get("z") is not None and c.get("lat") is not None][:40]
    mad = None
    if ok and len(zc) >= 3 and _spread([c["z"] for c in zc]) > 15:
        f = _dem_fit([c["lat"] for c in zc], [c["lon"] for c in zc], [c["z"] for c in zc], 13)
        mad = f["mad"] if f else None
        if mad is not None:
            crs["dem_misfit_m"] = round(mad, 1)
    notes.append("coordinates: placed in EPSG:%d%s from %s%s"
                 % (int(code), f" (UTM zone {zone}{'' if zone is None else ''})" if zone else "",
                    source,
                    (f" — {where}" if where else "")
                    + (", and the terrain matches the collar elevations to %d m" % round(mad)
                       if mad is not None and mad <= 60 else
                       ", but the terrain there does not match the collar elevations (off by %d m) "
                       "— check the zone, the hemisphere and the datum" % round(mad)
                       if mad is not None else "")))
    return crs


# The grade at which a metal starts to be worth modelling, in the standard unit
# (g/t for the precious metals, per cent for the rest). A modern drill database
# reports a 30-element ICP package on every sample, so a silver-cobalt property
# arrives with nineteen elements at trace level and the modeller carries all of
# them through every stage to model three. These floors are coarse — the point is
# only to tell a deposit metal from a pathfinder — and a project's `elements`
# setting overrides them outright.
_WORTH_MODELLING = {
    "Au": 0.08, "AuEq": 0.08, "Ag": 3.0, "AgEq": 3.0, "Pt": 0.05, "Pd": 0.05,
    "Rh": 0.05, "PGE": 0.05, "3E": 0.05,
    "Cu": 0.03, "CuEq": 0.03, "Zn": 0.1, "ZnEq": 0.1, "Pb": 0.1, "Ni": 0.03,
    "NiEq": 0.03, "Co": 0.005, "Mo": 0.003, "Sn": 0.02, "W": 0.05, "WO3": 0.02,
    "U": 0.005, "U3O8": 0.005, "Li": 0.05, "Li2O": 0.1, "Sb": 0.5, "Bi": 0.05,
    "V2O5": 0.2, "Cr2O3": 2.0, "TiO2": 2.0, "P2O5": 1.0, "Fe": 5.0, "Mn": 5.0,
    "TREO": 0.1, "REO": 0.1, "Nb2O5": 0.1, "Ta2O5": 0.01, "Cg": 1.0, "S": 10.0,
}
MAX_CARRY = int(os.environ.get("MMP_MAX_ELEMENTS", "10"))


def _p95(vals):
    v = sorted(vals)
    return v[min(len(v) - 1, int(0.95 * len(v)))] if v else 0.0


def intern_rows(ds):
    """Share the repeated strings in a dataset read back from JSON.

    JSON gives every row its own copy of the hole id, the element and the unit —
    900,000 rows naming 600 holes and 19 elements between them is a hundred and
    fifty megabytes of duplicate strings, and the modeller has a gigabyte for the
    whole build. Interning is a single pass and changes nothing but the memory."""
    pool = {}

    def share(v):
        if not isinstance(v, str):
            return v
        got = pool.get(v)
        if got is None:
            got = pool[v] = sys.intern(v)
        return got

    for rows, keys in ((ds.get("lithology") or [], ("hole", "rock", "alt")),
                       (ds.get("surveys") or [], ("hole",)),
                       (ds.get("collars") or [], ("hole",))):
        for x in rows:
            for k in keys:
                v = x.get(k)
                if type(v) is str:
                    x[k] = share(v)
    return ds


def carry_elements(intervals, wanted, notes):
    """Report the assay suite. Every element that was assayed is carried.

    A client's multi-element package is data they paid a laboratory for, and the
    modeller is not the place to decide which of it was worth having: a
    pathfinder suite is how a geologist reads a system, and an element that looks
    like background in one property is the deposit in the next. The cost of
    carrying it is a memory cost, and that is answered by holding it properly —
    see `projects._materialise(consume=...)` and the import/build split — not by
    throwing it away. `elements` still decides what gets MODELLED; it no longer
    decides what survives the import."""
    by = defaultdict(int)
    hi = {}
    for x in intervals:
        by[x["el"]] += 1
        g = x["grade"]
        if g is not None and g > hi.get(x["el"], 0):
            hi[x["el"]] = g
    seen = {e: {"n": n, "max": round(hi.get(e, 0.0), 4)} for e, n in by.items()}
    if len(by) > 3:
        notes.append("assays: %d elements, %s — all carried. The model estimates the few the "
                     "project is about (set 'elements' to choose); the rest are kept on the "
                     "dataset and reported per metal over the same deposit."
                     % (len(by), ", ".join(sorted(by, key=lambda e: -by[e]))))
    return intervals, seen


def georeference(collars, epsg=None, utm_zone=None, hemisphere="N", datum="WGS84", notes=None,
                 crs_mode=None):
    """Add lat/lon to collars that carry projected coordinates.

    `crs_mode` is what the uploader said the coordinates are — "utm", "epsg",
    "latlon", "local", or "auto"/None to work it out from the data."""
    notes = notes if notes is not None else []
    mode = (crs_mode or "auto").lower()
    have_ll = [c for c in collars if c.get("lat") is not None]
    en = [c for c in collars if c.get("e") is not None]
    crs = {"kind": "local"}
    if have_ll and len(have_ll) >= len(en):
        crs = {"kind": "latlon", "located": len(have_ll)}
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
            zc = [c for c in en if c.get("z") is not None][:40]
            if ok and len(zc) >= 3 and _spread([c["z"] for c in zc]) > 15:
                f = _dem_fit([c["lat"] for c in zc], [c["lon"] for c in zc],
                             [c["z"] for c in zc], 13)
                mad = f["mad"] if f else None
                if mad is not None:
                    crs["dem_misfit_m"] = round(mad, 1)
                    if mad > 60:
                        notes.append("coordinates: EPSG:%d places the drilling where the terrain "
                                     "does not match the collar elevations (off by %d m) — check "
                                     "the zone, the hemisphere and the datum"
                                     % (int(code), round(mad)))
        except Exception as e:
            notes.append(f"coordinates: EPSG:{code} could not be used ({str(e)[:60]}) — kept as a local grid")
    elif en and mode in ("latlon", "auto") and (mode == "latlon" or looks_degrees(en)):
        if looks_degrees(en) or mode == "latlon":
            crs = _as_latlon(en, notes)
        if crs.get("kind") != "latlon":
            notes.append("coordinates: told to read latitude/longitude columns but none were found")
    elif en and mode in ("auto", "utm") and _stated_zone(en):
        # a collar table with a zone column has already answered the question
        zone, hemi = _stated_zone(en)
        crs = _place(en, _utm_epsg(zone, hemi or hemisphere, datum), notes,
                     "the zone column in the collar table", zone=zone)
    elif en and mode == "auto":
        hit = infer_utm(en, notes, datum)
        if hit:
            scale = hit.get("z_scale", 1.0)
            ok = 0
            for c in en:
                lo, la = hit["tr"].transform(c["e"], c["n"])
                if math.isfinite(la) and math.isfinite(lo) and abs(la) <= 90:
                    c["lat"], c["lon"] = round(la, 6), round(lo, 6); ok += 1
                if scale != 1.0 and c.get("z") is not None:
                    c["z"] = round(c["z"] * scale, 2)
            crs = {"kind": "projected", "epsg": int(hit["epsg"]), "located": ok,
                   "source": "zone identified by collar elevations vs terrain",
                   "dem_misfit_m": hit.get("dem_misfit_m"), "where": hit.get("where")}
            if hit.get("z_units") == "ft":
                crs["z_units"] = "ft"
        else:
            notes.append("coordinates: modelled in its own local grid — no terrain, and it cannot "
                         "merge with other sources for the same project")
    elif en:
        notes.append("coordinates: no EPSG code or UTM zone given — modelled in its own local grid "
                     "(no terrain, cannot merge with other sources)")
    if crs.get("kind") == "local" and any("zone" in c for c in collars) and not _stated_zone(en):
        notes.append("coordinates: the collar table has a zone column, but no zone between 1 and 60 "
                     "could be read from it on enough rows to use")
    pts = sorted((c["lat"], c["lon"]) for c in collars if c.get("lat") is not None)
    center = [round(pts[len(pts) // 2][0], 5), round(pts[len(pts) // 2][1], 5)] if pts else None
    return crs, center


# --------------------------------------------------------------------- the import
_WIDE_BYTES = 6 * 1024 * 1024      # below this, reading the whole file costs nothing


def read_for_modelling(path, notes=None):
    """Read a file, keeping only the columns a model can use.

    A drill export carries lab codes, methods, detection limits and QA flags that
    no model reads, and pandas holds every one of them as a Python object. On a
    large assay table that is the difference between a parse that finishes and a
    worker the kernel kills for using too much memory — which looks, from the
    page, exactly like a parse that never ends."""
    notes = notes if notes is not None else []
    ext = os.path.splitext(path)[1].lower()
    try:
        big = os.path.getsize(path) > _WIDE_BYTES
    except OSError:
        big = False
    if ext not in (".csv", ".tsv", ".txt") or not big:
        return read_tables(path)
    try:
        sample = next(iter(read_tables(path, max_rows=3000).values()))
    except Exception:
        return read_tables(path)
    sample.columns = [str(c).strip() for c in sample.columns]
    prof = profile(sample)
    keep = [c for c in sample.columns
            if c in set(prof["roles"].values()) | {e[0] for e in prof["elements"]}]
    if not keep or len(keep) >= len(sample.columns):
        return read_tables(path)
    dropped = [c for c in sample.columns if c not in keep]
    notes.append("%s: using the %d column(s) a model reads, of %d — the other %d are lab and "
                 "logging fields (%s) and are left in the file, not discarded"
                 % (os.path.basename(path), len(keep), len(sample.columns), len(dropped),
                    ", ".join(map(str, dropped[:5])) + ("…" if len(dropped) > 5 else "")))
    try:
        return read_tables(path, usecols=keep)
    except Exception:
        return read_tables(path)


# Text pasted out of a news release is the other way drill data arrives. A
# release states its numbers in a table whose columns are aligned with runs of
# spaces -- which no CSV reader can see, and which `drill_tables` reads directly,
# because that is the same extractor that reads four thousand technical reports.
_TEXTY = (".txt", ".text", ".md", ".log", ".csv", ".tsv", ".dat")


def release_text(path, notes=None):
    """Collars and intervals out of release text, in the importer's own shapes.

    Returned rows are exactly what `parse_collars` and `parse_assays` produce, so
    a pasted release is merged by normalised hole id like any uploaded file --
    there is no second kind of source in the model, only a second way in."""
    notes = notes if notes is not None else []
    from minemodelingpro import drill_tables as DT
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(4_000_000)
    except OSError as e:
        notes.append("%s: could not be read — %s" % (os.path.basename(path), str(e)[:100]))
        return [], [], None
    if not text.strip():
        return [], [], None
    try:
        cols, ivs = DT.extract_tables(DT.TextDoc([text]))
    except Exception as e:
        notes.append("%s: could not be read as a release — %s: %s"
                     % (os.path.basename(path), type(e).__name__, str(e)[:100]))
        return [], [], None
    collars = []
    for c in (cols or []):
        hid = str(c.get("hole") or c.get("id") or "").strip()
        if not hid:
            continue
        rec = {"hole": hid, "e": c.get("e"), "n": c.get("n"), "z": c.get("z"),
               "az": c.get("az"), "dip": c.get("dip"), "depth": c.get("depth")}
        if rec.get("e") is None and c.get("lat") is not None:
            rec["lat"], rec["lon"] = c.get("lat"), c.get("lon")
        if rec.get("e") is None and rec.get("lat") is None:
            continue                       # a collar with no position places nothing
        if rec.get("dip") is not None and rec["dip"] > 0:
            rec["dip"] = -rec["dip"]       # positive-down, as everywhere else
        collars.append(rec)
    intervals = []
    for r in (ivs or []):
        hid = str(r.get("hole") or "").strip()
        fr, to, g = r.get("from"), r.get("to"), r.get("grade")
        if not hid or fr is None or to is None or g is None or not r.get("el"):
            continue
        try:
            fr, to, g = float(fr), float(to), float(g)
        except (TypeError, ValueError):
            continue
        if to <= fr:
            continue
        intervals.append({"hole": hid, "from": fr, "to": to, "el": str(r["el"]),
                          "grade": round(g, 5), "unit": r.get("unit") or "", "sub": 0})
    # the release usually names its own grid in the words above the table
    crs = None
    try:
        crs = DT.detect_crs([text])
    except Exception:
        pass
    if collars or intervals:
        notes.append("%s: read as release text — %d collar(s) and %d assay interval(s) "
                     "from its own tables%s"
                     % (os.path.basename(path), len(collars), len(intervals),
                        (", stated grid %s" % crs.get("epsg")) if crs and crs.get("epsg") else ""))
    return collars, intervals, crs


def build_dataset(paths, project=None, company=None, epsg=None, utm_zone=None, hemisphere="N",
                  datum="WGS84", attach_to=None, settings=None, dataset_id=None, owner=None,
                  private=True, source_name=None, crs_mode=None, azimuth_ref="auto",
                  source_url=None):
    """Read every file, assemble one dataset, and return it with a report of what
    was understood. Nothing is written to disk — see `save`."""
    import pandas as pd
    notes, tables = [], []
    collars, surveys, intervals, lithology = [], [], [], []
    rel_crs = None
    for path in paths:
        try:
            got = read_for_modelling(path, notes)
        except Exception as e:
            notes.append(f"{os.path.basename(path)}: could not be read — {str(e)[:120]}")
            got = {}
        recognised = 0
        n0 = len(notes)                      # so a line disproved below can go
        for name, df in (got or {}).items():
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            if len(df) >= 2_000_000:
                notes.append(f"{name}: only the first 2,000,000 rows were read — split the file "
                             f"if there are more")
            prof = profile(df)
            tables.append({"table": name, **{k: v for k, v in prof.items() if k != "roles"},
                           "roles": {k: str(v) for k, v in prof["roles"].items()},
                           "elements": [[c, e, u] for c, e, u in prof["elements"]]})
            if prof["kind"] == "collar":
                collars += parse_collars(df, prof["roles"], notes); recognised += 1
            elif prof["kind"] == "survey":
                surveys += parse_surveys(df, prof["roles"], notes, azimuth_ref); recognised += 1
            elif prof["kind"] == "assay":
                intervals += parse_assays(df, prof, notes); recognised += 1
            elif prof["kind"] == "lithology":
                lithology += parse_lithology(df, prof["roles"], notes); recognised += 1
            else:
                notes.append(f"{name}: not recognised as collars, surveys, assays "
                             f"or logged geology "
                             f"(columns: {', '.join(map(str, df.columns[:8]))})")
        # Nothing a table reader could see. A news release states its numbers in
        # a space-aligned table, so try the extractor that reads those before
        # telling the person their file holds nothing.
        if not recognised and os.path.splitext(path)[1].lower() in _TEXTY:
            rc, ri, rcrs = release_text(path, notes)
            if rc or ri:
                # the table reader's complaint about this file is wrong now
                notes[n0:] = [x for x in notes[n0:] if "not recognised as collars" not in x]
                collars += rc
                intervals += ri
                rel_crs = rel_crs or rcrs
                tables.append({"table": os.path.basename(path), "kind": "release",
                               "rows": len(rc) + len(ri), "roles": {},
                               "elements": sorted({r["el"] for r in ri})})
                recognised += 1
        if not recognised:
            notes.append(f"{os.path.basename(path)}: no tables found")
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
    clean_settings, errs = PARAMS.validate(settings or {})
    intervals, elements_seen = carry_elements(intervals, clean_settings.get("elements"), notes)
    # A release names its own grid in the words above its table ("NAD83 UTM Zone
    # 17N"), and without it the collars fall back to a local grid — which the
    # importer's own note says "cannot merge with other sources for the same
    # project", the one thing an added source exists to do.
    if rel_crs and not epsg and not utm_zone and rel_crs.get("zone"):
        utm_zone = rel_crs["zone"]
        hemisphere = rel_crs.get("hemi") or hemisphere
        datum = rel_crs.get("datum") or datum
        notes.append("coordinates: the release states UTM zone %s%s (%s), and that is used"
                     % (utm_zone, hemisphere, datum))
    crs, center = georeference(collars, epsg, utm_zone, hemisphere, datum, notes,
                               crs_mode=crs_mode)
    if lithology:
        notes.append(f"logged geology: {len(lithology)} interval(s) over "
                     f"{len({x['hole'] for x in lithology})} hole(s), "
                     f"{len({x['rock'] for x in lithology})} distinct units — set "
                     f"'domain_units' to the ones that are the mineralised body")

    seen, s2 = set(), []
    for x in sorted(surveys, key=lambda r: (holeid.key(r["hole"]), r["depth"])):
        k = (holeid.key(x["hole"]), x["depth"])
        if k in seen:
            continue
        seen.add(k); s2.append(x)
    surveys = s2

    ckeys = {holeid.key(c["hole"]) for c in collars}
    ikeys = {holeid.key(x["hole"]) for x in intervals}
    skeys = {holeid.key(x["hole"]) for x in surveys}
    orphans = sorted(ikeys - ckeys)[:20]
    if orphans:
        notes.append(f"{len(ikeys - ckeys)} hole(s) have assays but no collar "
                     f"(e.g. {', '.join(orphans[:6])}) — they can only be placed if another "
                     f"source in the project has their collars")
    # the three files are joined on the hole id, so say how well they joined
    if collars and (surveys or intervals):
        bits = [f"{len(collars)} collar(s)"]
        if surveys:
            bits.append(f"surveys on {len(skeys & ckeys)} of them ({len(surveys)} stations)")
        if intervals:
            bits.append(f"assays on {len(ikeys & ckeys)}")
        notes.append("joined on hole id: " + ", ".join(bits))
    sorph = sorted(skeys - ckeys)
    if sorph:
        notes.append(f"{len(sorph)} hole(s) have a survey but no collar "
                     f"(e.g. {', '.join(sorph[:6])}) — the survey is kept and used if that collar "
                     f"arrives from another file or source")
    if collars and surveys and not (skeys & ckeys):
        notes.append("surveys: not one survey hole id matches a collar hole id — check that both "
                     "files spell the hole the same way")
    one = sorted({k for k in skeys if sum(1 for x in surveys if holeid.key(x["hole"]) == k) == 1})
    if one:
        notes.append(f"{len(one)} hole(s) have a single survey station — it sets the hole's "
                     f"orientation, but the path cannot be curved from one reading")
    notes += [f"settings: {e}" for e in errs]
    slug = re.sub(r"[^a-z0-9]+", "-", (project or company or "import").lower()).strip("-")[:48] or "import"
    did = dataset_id or f"import:{slug}-{datetime.datetime.utcnow():%Y%m%d%H%M%S}"
    # into columns, releasing the parsed rows as they are read: from here on the
    # assays are five arrays, not a dictionary per interval
    n_iv = len(intervals)
    el_counts = dict(Counter(x["el"] for x in intervals))
    iv = IVT.Intervals.from_rows(intervals, consume=True)
    del intervals
    ds = {
        "id": did, "source": "import", "project": project, "company": company,
        "owner": owner, "private": bool(private), "attach_to": attach_to,
        "title_project": project, "crs": crs, "center": center, "params": clean_settings,
        "date": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        "imported": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_name": source_name or ", ".join(os.path.basename(p) for p in paths)[:160],
        # where this came from, so an added source is attributed in the Sources
        # panel the way a public news release is, and a reader can open it
        "source_url": (str(source_url)[:600] or None) if source_url else None,
        "source_kind": ("news release"
                        if any(t.get("kind") == "release" for t in tables) else None),
        "files": [os.path.basename(p) for p in paths],
        "collars": collars, "surveys": surveys, "intervals": iv,
        "lithology": lithology,
        # the whole suite the file reported, so the deposit style still sees the
        # pathfinders even when the model does not carry their assays
        "suite": sorted(elements_seen), "elements_seen": elements_seen,
        "counts": {"collars": len(collars), "intervals": n_iv,
                   "lithology": len(lithology), "survey_stations": len(surveys),
                   "surveyed_holes": len({holeid.key(s["hole"]) for s in surveys}),
                   "holes_with_intervals": len(ikeys), "linked": len(ckeys & ikeys),
                   "elements": el_counts},
        "tables": tables, "notes": notes,
    }
    return ds


def validate(ds, standalone=True):
    """Blocking problems (the model cannot be built) vs warnings.

    `standalone` is whether this dataset has to be a whole drill database on its
    own. It does when it is the only thing a project has. It does NOT when it is
    a source ADDED to a project that already has drilling: a news release
    reporting six intervals on three holes is a perfectly good addition, and
    holding it to "at least 3 collars, at least 8 intervals, and collars that
    share hole ids with its own assays" rejects exactly the thing the person is
    trying to add. Whether an added source actually joins is a real question, and
    it is answered against the PROJECT's holes before it is stored, not against
    the source's own."""
    errors = []
    c = ds["counts"]
    if not standalone:
        if not c["collars"] and not c["intervals"]:
            errors.append("nothing in this source reads as collars or assay intervals")
        return errors
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
    json.dump(IVT.to_store(ds), open(fn, "w"), separators=(",", ":"))
    return fn


def load_all(include_private=False):
    out = []
    dirs = [IMPORT_DIR] + ([PRIVATE_DIR] if include_private else [])
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                out.append(IVT.from_store(json.load(open(f))))
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
    ap.add_argument("--crs-mode", default="auto",
                    choices=["auto", "utm", "epsg", "latlon", "local"],
                    help="what the coordinates are; auto works it out from the data")
    ap.add_argument("--azimuth-ref", default="auto", choices=["auto", "magnetic", "true"],
                    help="magnetic applies the survey declination to the azimuths")
    ap.add_argument("--attach-to", help="slug of an existing project to merge into")
    ap.add_argument("--set", nargs="*", default=[], help="model parameters, e.g. block=5,5,5 radius_m=60")
    ap.add_argument("--private", action="store_true", help="keep out of the public site")
    ap.add_argument("--build", action="store_true", help="build the model page after importing")
    a = ap.parse_args()
    files = [f for pat in a.files for f in (glob.glob(pat) or [pat])]
    settings = dict(x.split("=", 1) for x in a.set if "=" in x)
    ds = build_dataset(files, project=a.project, company=a.company, epsg=a.epsg, utm_zone=a.utm_zone,
                       hemisphere=a.hemisphere, datum=a.datum, attach_to=a.attach_to,
                       settings=settings, private=a.private, crs_mode=a.crs_mode,
                       azimuth_ref=a.azimuth_ref)
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
