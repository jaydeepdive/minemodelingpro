"""Full drill-data extraction from NI 43-101 technical reports.

Technical reports carry nearly everything a deposit model needs: collar tables
(hole, easting, northing, elevation, azimuth, dip, length), significant-intercept
/ assay tables (hole, from, to, length, grades for every payable metal), the
coordinate system, the resource statement (tonnes, grade, contained metal by
category) and the resource block-model parameters (block size, density).

This module reads those straight from the PDF's text layer using WORD POSITIONS
(PyMuPDF), rebuilding each table row-by-row and column-by-column from the
header layout. That handles the borderless, multi-line-header, multi-page tables
that 43-101s are full of (the old Camelot/regex path found collars in only a
handful of reports). Continuation pages without a header reuse the previous
page's column layout.

Output is one JSON per report (``data/keep/mmp_reports/<sid>.json``) — compact,
diffable, committed — which ``model3d`` reads directly:

  {id, company, project, commodity, jurisdiction, report_url, archive_url,
   crs: {kind: utm|local|latlon, zone, hemi, datum, source},
   center: [lat, lon] | null,
   collars: [{hole, e, n, z, az, dip, depth, lat, lon}],
   intervals: [{hole, from, to, el, grade, unit, sub}],
   resources: {category rows + headline}, block_size: [x,y,z], density}

Run:  PYTHONPATH=src python -m minemodelingpro.drill_tables <pdf> [--id ID]
      PYTHONPATH=src python -m minemodelingpro.drill_tables all   # every archived report
"""
import os
import re
import sys
import json
import math
import datetime
from collections import Counter, defaultdict

from minemodelingpro import holeid

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(_ROOT, "data", "keep", "mmp_reports")
EXTRACTOR = "dt15"

# ------------------------------------------------------------------ elements
_EL = {"au": "Au", "gold": "Au", "ag": "Ag", "silver": "Ag", "cu": "Cu", "copper": "Cu",
       "pb": "Pb", "lead": "Pb", "zn": "Zn", "zinc": "Zn", "ni": "Ni", "nickel": "Ni",
       "co": "Co", "cobalt": "Co", "mo": "Mo", "moly": "Mo", "sn": "Sn", "w": "W", "wo3": "WO3",
       "u3o8": "U3O8", "li2o": "Li2O", "li": "Li", "sb": "Sb", "v2o5": "V2O5", "fe": "Fe",
       "mn": "Mn", "pt": "Pt", "pd": "Pd", "aueq": "AuEq", "ageq": "AgEq", "cueq": "CuEq",
       "zneq": "ZnEq", "nieq": "NiEq", "treo": "TREO", "reo": "REO", "p2o5": "P2O5",
       "graphite": "Cg", "cg": "Cg", "tgc": "Cg", "ta2o5": "Ta2O5", "nb2o5": "Nb2O5", "bi": "Bi",
       "te": "Te", "ga": "Ga", "ge": "Ge", "in": "In", "3e": "3E", "pgm": "PGM", "pge": "PGE",
       "2pge": "PGE", "tpm": "PGE", "cr2o3": "Cr2O3", "tio2": "TiO2", "k2o": "K2O"}
_DEFAULT_UNIT = {"Au": "g/t", "Ag": "g/t", "Pt": "g/t", "Pd": "g/t", "AuEq": "g/t", "AgEq": "g/t",
                 "3E": "g/t", "PGM": "g/t", "PGE": "g/t", "Te": "g/t", "Ga": "g/t", "Ge": "g/t", "In": "g/t"}

_NUMRX = re.compile(r"^[<>~≤≥]?\s*[-−–]?\(?\d[\d,  ]*(?:\.\d+)?\)?$")


def num(s):
    """Parse a table cell to float; handles 1,234.5 / 1 234,5? / −12 / <0.01 / (5)."""
    if s is None:
        return None
    t = str(s).strip().replace("−", "-").replace("–", "-").replace(" ", "").replace("\xa0", "")
    t = t.lstrip("<>~≤≥ ")
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    t = t.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ------------------------------------------------------------ page -> rows/cells
def page_lines(page):
    """Rebuild visual lines of cells from word boxes: words sharing a baseline form
    a line; within a line words separated by a small gap form one cell."""
    words = page.get_text("words")
    if not words:
        return []
    words = [w for w in words if w[4].strip()]
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines, cur, cy, ch = [], [], None, None
    for w in words:
        yc = (w[1] + w[3]) / 2
        h = max(w[3] - w[1], 1.0)
        if cur and abs(yc - cy) <= 0.45 * max(h, ch):
            cur.append(w)
            cy = (cy * (len(cur) - 1) + yc) / len(cur)
        else:
            if cur:
                lines.append(cur)
            cur, cy, ch = [w], yc, h
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        cells = []
        for w in ln:
            h = max(w[3] - w[1], 1.0)
            gap_tol = max(2.2, 0.32 * h)
            if cells and w[0] - cells[-1]["x1"] <= gap_tol:
                c = cells[-1]
                c["t"] += " " + w[4]; c["x1"] = max(c["x1"], w[2])
            else:
                cells.append({"t": w[4], "x0": w[0], "x1": w[2]})
        y = sum((w[1] + w[3]) / 2 for w in ln) / len(ln)
        words_ = [{"t": w[4], "x0": w[0], "x1": w[2]} for w in ln]
        out.append({"y": y, "cells": cells, "words": words_})
    return out


def _is_num(t):
    return num(t) is not None


def _is_data_line(line, min_nums=3):
    return sum(1 for c in line["cells"] if _is_num(c["t"])) >= min_nums


# --------------------------------------------------------------- header parsing
_H_HOLE = re.compile(r"\b(hole|drill ?hole|drillhole|ddh|bhid|borehole|hole ?id|hole ?no|hole ?#|hole ?number|drill ?id|well)\b", re.I)
_H_EAST = re.compile(r"\b(easting|east|utm ?e|x|e \(m\)|x \(m\)|e_utm|utm_e|mine ?e|local ?e)\b", re.I)
_H_NORTH = re.compile(r"\b(northing|north|utm ?n|y|n \(m\)|y \(m\)|n_utm|utm_n|mine ?n|local ?n)\b", re.I)
_H_LAT = re.compile(r"\blat(itude)?\b", re.I)
_H_LON = re.compile(r"\blong?(itude)?\b", re.I)
_H_ELEV = re.compile(r"\b(elev|elevation|rl|z|masl|m ?asl|collar ?elev|z \(m\))\b", re.I)
_H_AZ = re.compile(r"\b(azimuth|azi|az|bearing|brg)\b", re.I)
_H_DIP = re.compile(r"\b(dip|inclination|incl|plunge)\b", re.I)
_H_DEPTH = re.compile(r"\b(length|depth|eoh|total ?depth|hole ?length|final ?depth|td|drilled|max ?depth|meterage|metres|meters)\b", re.I)
_H_FROM = re.compile(r"^\s*(from|depth ?from|from ?depth)\b", re.I)
_H_TO = re.compile(r"^\s*(to|depth ?to|to ?depth)\b", re.I)
_H_LEN = re.compile(r"\b(length|interval|width|thickness|core ?length|intercept|drilled ?width|downhole|apparent)\b", re.I)
_H_TRUE = re.compile(r"\btrue\b|\bETW\b|\bTW\b", re.I)
_FT = re.compile(r"\(\s*(ft|feet)\s*\)|\bft\b|\bfeet\b", re.I)


def _elem_of(label):
    """(element, unit) from a header label like 'Au (g/t)', 'Cu %', 'Zn (%)', 'AgEq g/t'."""
    low = label.lower().replace("\n", " ")
    low = re.sub(r"\s+", " ", low).strip()
    if _H_FROM.search(low) or _H_TO.search(low) or _H_LEN.search(low) or _H_HOLE.search(low):
        return None
    m = re.match(r"^([a-z0-9]+(?:\s?eq)?)", low.replace("_", " "))
    if not m:
        return None
    tok = m.group(1).replace(" ", "")
    el = _EL.get(tok)
    if not el:
        return None
    unit = None
    if "g/t" in low or "gpt" in low or "g/mt" in low or "ppm" in low and el in _DEFAULT_UNIT:
        unit = "g/t"
    elif "oz/t" in low or "opt" in low or "oz/ton" in low:
        unit = "oz/t"
    elif "ppm" in low:
        unit = "ppm"
    elif "ppb" in low:
        unit = "ppb"
    elif "%" in low or "pct" in low or "per cent" in low:
        unit = "%"
    elif "lb/t" in low:
        unit = "lb/t"
    unit = unit or _DEFAULT_UNIT.get(el, "%")
    return el, unit


def _to_std(el, unit, v):
    """Convert grade to the standard unit (g/t for precious, % for base)."""
    if v is None:
        return None, None
    std = _DEFAULT_UNIT.get(el, "%")
    if unit == std:
        return v, std
    if std == "g/t":
        if unit == "oz/t":
            return v * 34.2857, std
        if unit == "ppm":
            return v, std
        if unit == "ppb":
            return v / 1000.0, std
        if unit == "%":
            return v * 10000.0, std
    else:
        if unit == "ppm" or unit == "g/t":
            return v / 10000.0, std
        if unit == "ppb":
            return v / 1e7, std
        if unit == "lb/t":
            return v / 20.0, std
    return v, unit


def _header_columns(hlines):
    """Merge the cells of 1-4 header lines into columns by horizontal overlap."""
    cols = []
    for ln in hlines:
        for c in ln["cells"]:
            placed = False
            for col in cols:
                ov = min(col["x1"], c["x1"]) - max(col["x0"], c["x0"])
                if ov > -1.0:
                    col["t"] += " " + c["t"]; col["x0"] = min(col["x0"], c["x0"])
                    col["x1"] = max(col["x1"], c["x1"]); placed = True; break
            if not placed:
                cols.append(dict(c))
    cols.sort(key=lambda c: c["x0"])
    # merge columns that overlap after growth
    merged = []
    for c in cols:
        if merged and c["x0"] <= merged[-1]["x1"] - 1.0:
            m = merged[-1]; m["t"] += " " + c["t"]; m["x1"] = max(m["x1"], c["x1"])
        else:
            merged.append(c)
    for c in merged:
        c["xc"] = (c["x0"] + c["x1"]) / 2
        c["t"] = re.sub(r"\s+", " ", c["t"]).strip()
    return merged


def _classify(cols):
    """Map header columns -> roles. Returns (kind, roles) or (None, None)."""
    roles = {}
    elems = []
    feet = False
    for i, c in enumerate(cols):
        t = c["t"]
        low = t.lower()
        if _FT.search(low):
            feet = feet or bool(_H_FROM.search(low) or _H_TO.search(low) or _H_DEPTH.search(low) or _H_LEN.search(low))
        if "hole" not in roles and _H_HOLE.search(low):
            roles["hole"] = i; continue
        if _H_FROM.search(low):
            roles.setdefault("from", i); continue
        if _H_TO.search(low) and "from" in roles:
            roles.setdefault("to", i); continue
        if "lat" not in roles and _H_LAT.search(low):
            roles["lat"] = i; continue
        if "lon" not in roles and _H_LON.search(low) and "lat" in roles:
            roles["lon"] = i; continue
        if "e" not in roles and _H_EAST.search(low) and not _H_NORTH.search(low.split()[0] if low.split() else ""):
            roles["e"] = i; continue
        if "n" not in roles and _H_NORTH.search(low):
            roles["n"] = i; continue
        if "z" not in roles and _H_ELEV.search(low):
            roles["z"] = i; continue
        if "az" not in roles and _H_AZ.search(low):
            roles["az"] = i; continue
        if "dip" not in roles and _H_DIP.search(low):
            roles["dip"] = i; continue
        if _H_TRUE.search(low) and _H_LEN.search(low):
            roles.setdefault("tw", i); continue
        if "from" in roles and "len" not in roles and _H_LEN.search(low):
            roles["len"] = i; continue
        if "depth" not in roles and _H_DEPTH.search(low) and "from" not in roles:
            roles["depth"] = i; continue
        e = _elem_of(t)
        if e:
            elems.append((i, e[0], e[1]))
    roles["elements"] = elems
    roles["feet"] = feet
    if ("e" in roles and "n" in roles) or ("lat" in roles and "lon" in roles):
        return "collar", roles
    if "from" in roles and "to" in roles and elems:
        return "interval", roles
    if "az" in roles and "dip" in roles and "depth" in roles and "from" not in roles and "hole" in roles:
        return "survey", roles          # downhole survey: hole, depth, azimuth, dip
    return None, None


def _assign(cells, cols):
    """Assign each data cell to a header column (boundaries = midpoints between
    adjacent header column centres)."""
    bounds = []
    for i in range(len(cols) - 1):
        bounds.append((cols[i]["x1"] + cols[i + 1]["x0"]) / 2)
    row = [""] * len(cols)
    for c in cells:
        xc = (c["x0"] + c["x1"]) / 2
        k = 0
        while k < len(bounds) and xc > bounds[k]:
            k += 1
        row[k] = (row[k] + " " + c["t"]).strip() if row[k] else c["t"]
    return row


def _data_spans(dlines):
    """Column spans from the x-extents of data words across several rows (union of
    overlapping intervals) — far more reliable than header spacing."""
    iv = sorted((w["x0"], w["x1"]) for l in dlines for w in l["words"])
    spans = []
    for a, b in iv:
        if spans and a <= spans[-1][1] + 0.8:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return spans


def _label_spans(spans, hlines):
    cols = [{"t": "", "x0": a, "x1": b} for a, b in spans]
    for l in hlines:
        for w in l["words"]:
            xc = (w["x0"] + w["x1"]) / 2
            best, bd = None, 1e9
            for c in cols:
                if c["x0"] - 2 <= xc <= c["x1"] + 2:
                    d = 0
                else:
                    d = min(abs(xc - c["x0"]), abs(xc - c["x1"]))
                if d < bd:
                    best, bd = c, d
            if best is not None and bd < 40:
                best["t"] = (best["t"] + " " + w["t"]).strip()
    for c in cols:
        c["xc"] = (c["x0"] + c["x1"]) / 2
    return cols


def _find_header(lines, i):
    """If a table header starts at line i, return (cols, kind, roles, n_header_lines)."""
    for span in (1, 2, 3, 4, 5):
        if i + span >= len(lines):
            break
        h = lines[i:i + span]
        if any(_is_data_line(l, 2) for l in h):
            break
        if span > 1 and (h[-1]["y"] - h[0]["y"]) > 60:
            break
        nxt = lines[i + span]
        if not _is_data_line(nxt, 2):
            continue
        # quick keyword gate on the header text
        htxt = " ".join(w["t"] for l in h for w in l["words"]).lower()
        if not (re.search(r"east|north|\bx\b|lat", htxt) or re.search(r"\bfrom\b", htxt)
                or (re.search(r"azimuth|\baz\b", htxt) and re.search(r"\bdip\b|inclination", htxt))):
            continue
        dl = []
        for l in lines[i + span:i + span + 10]:
            if _is_data_line(l, 2):
                dl.append(l)
            elif dl:
                break
        spans = _data_spans(dl)
        if len(spans) < 3 or len(spans) > 30:
            continue
        cols = _label_spans(spans, h)
        kind, roles = _classify(cols)
        if not kind:
            # fall back to header-cell geometry
            cols = _header_columns(h)
            if 3 <= len(cols) <= 24:
                kind, roles = _classify(cols)
        if kind:
            return cols, kind, roles, span
    return None


# ----------------------------------------------------------------- extraction
def _cell(row, roles, k):
    i = roles.get(k)
    return row[i] if i is not None and i < len(row) else None


def extract_tables(doc, max_pages=None, surveys=None):
    collars, intervals = {}, []
    surveys = surveys if surveys is not None else []
    spec = None            # (cols, kind, roles) carried to continuation pages
    spec_page = -9
    n_pages = len(doc) if not max_pages else min(len(doc), max_pages)
    for pno in range(n_pages):
        try:
            lines = page_lines(doc[pno])
        except Exception:
            continue
        if not lines:
            continue
        # quick reject: pages with little numeric content
        nnum = sum(1 for l in lines if _is_data_line(l, 3))
        if nnum < 2:
            continue
        i = 0
        last_hole = None
        active = spec if (spec and pno - spec_page <= 1) else None
        while i < len(lines):
            h = _find_header(lines, i)
            if h:
                cols, kind, roles, span = h
                active = (cols, kind, roles)
                i += span
                last_hole = None
                continue
            ln = lines[i]
            i += 1
            if not active or not _is_data_line(ln, 2):
                # a text line (e.g. a zone label or prose) — keep the table active
                # unless it's clearly prose (long)
                if active and sum(len(c["t"]) for c in ln["cells"]) > 90:
                    active = None
                continue
            cols, kind, roles = active
            row = _assign(ln["words"], cols)
            hraw = (_cell(row, roles, "hole") if "hole" in roles else row[0]) or ""
            hraw = hraw.strip()
            sub = 0
            lowh = hraw.lower()
            if re.match(r"^(incl\.?|including|inc\.?|with|incl)\b", lowh):
                sub = 1; hraw = ""
            elif re.match(r"^(and|&|plus|also)\b", lowh):
                hraw = ""
            if hraw and not holeid.looks_like_hole(hraw.split()[0] if " " in hraw and len(hraw) > 16 else hraw):
                # first token may be the hole with a zone label glued on
                tok = hraw.split()[0]
                hraw = tok if holeid.looks_like_hole(tok) else ""
            if hraw:
                last_hole = hraw
            hole = hraw or last_hole
            if not hole:
                continue
            if kind == "collar":
                if hraw == "":          # collar rows must name their hole
                    continue
                rec = {"hole": hole}
                if "e" in roles:
                    rec["e"] = num(_cell(row, roles, "e")); rec["n"] = num(_cell(row, roles, "n"))
                if "lat" in roles:
                    la, lo = _latlon_cell(_cell(row, roles, "lat")), _latlon_cell(_cell(row, roles, "lon"))
                    if la is not None and lo is not None and abs(la) <= 90 and abs(lo) <= 180:
                        rec["lat"], rec["lon"] = la, lo
                if rec.get("e") is None and rec.get("lat") is None:
                    continue
                if rec.get("e") is not None and (rec.get("n") is None or rec["e"] == 0):
                    continue
                f = 0.3048 if roles.get("feet") else 1.0
                rec["z"] = num(_cell(row, roles, "z"))
                rec["az"] = num(_cell(row, roles, "az"))
                d = num(_cell(row, roles, "dip"))
                if d is not None and d > 0 and d <= 90:
                    d = -d                              # dips written positive downward
                rec["dip"] = d
                dep = num(_cell(row, roles, "depth"))
                rec["depth"] = round(dep * f, 2) if dep else None
                if rec["az"] is not None and not (0 <= rec["az"] <= 360):
                    rec["az"] = None
                if rec["dip"] is not None and not (-90 <= rec["dip"] <= 0):
                    rec["dip"] = None
                if rec["depth"] is not None and not (1 <= rec["depth"] <= 4000):
                    rec["depth"] = None
                k = holeid.key(hole)
                old = collars.get(k)
                if not old or sum(v is not None for v in rec.values()) >= sum(v is not None for v in old.values()):
                    collars[k] = rec
            elif kind == "survey":
                f = 0.3048 if roles.get("feet") else 1.0
                dep, az, dp = num(_cell(row, roles, "depth")), num(_cell(row, roles, "az")), num(_cell(row, roles, "dip"))
                if dep is None or az is None or dp is None or not (0 <= az <= 360) or not (-90 <= dp <= 90) or not (0 <= dep * f <= 4000):
                    continue
                surveys.append({"hole": hole, "depth": round(dep * f, 2), "az": az, "dip": -abs(dp)})
            else:
                fr, to = num(_cell(row, roles, "from")), num(_cell(row, roles, "to"))
                if fr is None or to is None or to <= fr or to - fr > 1500 or fr < 0:
                    continue
                f = 0.3048 if roles.get("feet") else 1.0
                for ci, el, unit in roles["elements"]:
                    g = num(row[ci]) if ci < len(row) else None
                    if g is None or g < 0:
                        continue
                    g2, u2 = _to_std(el, unit, g)
                    intervals.append({"hole": hole, "from": round(fr * f, 2), "to": round(to * f, 2),
                                      "el": el, "grade": round(g2, 5), "unit": u2, "sub": sub})
        if active:
            spec, spec_page = active, pno
    # de-duplicate intervals (summary tables repeat the appendix)
    seen, uniq = set(), []
    for r in intervals:
        k = (holeid.key(r["hole"]), r["from"], r["to"], r["el"])
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    return list(collars.values()), uniq


_DMS = re.compile(r"(-?\d{1,3})\s*[°º]\s*(\d{1,2}(?:\.\d+)?)?\s*['’′]?\s*(\d{1,2}(?:\.\d+)?)?\s*[\"”″]?\s*([NSEWnsew])?")


def _latlon_cell(s):
    if s is None:
        return None
    v = num(s)
    if v is not None:
        return v
    m = _DMS.search(str(s))
    if not m:
        return None
    d = float(m.group(1)); mi = float(m.group(2) or 0); se = float(m.group(3) or 0)
    v = abs(d) + mi / 60 + se / 3600
    if d < 0 or (m.group(4) or "").upper() in ("S", "W"):
        v = -v
    return v


# ----------------------------------------------------------- coordinate system
_ZONE_RX = [
    re.compile(r"\bEPSG\s*[:#]?\s*269(\d{2})\b"),                       # NAD83 UTM N
    re.compile(r"\bEPSG\s*[:#]?\s*267(\d{2})\b"),                       # NAD27 UTM N
    re.compile(r"\bEPSG\s*[:#]?\s*326(\d{2})\b"),                       # WGS84 UTM N
    re.compile(r"\bEPSG\s*[:#]?\s*327(\d{2})\b"),                       # WGS84 UTM S
]
_ZONE_TXT = re.compile(
    r"(?:UTM|NAD\s?-?83|NAD\s?-?27|WGS\s?-?84|SIRGAS|PSAD\s?-?56|GDA\s?-?94|GDA\s?-?2020|Datum)"
    r"[^\n]{0,40}?\b(?:zone|z|fuso|huso)\s*[-:]?\s*(\d{1,2})\s*([NSns](?![a-z]))?", re.I)
_ZONE_TXT2 = re.compile(r"\b(?:UTM\s*)?zone\s*(\d{1,2})\s*([NSns])?\b[^\n]{0,30}?(?:UTM|NAD|WGS)", re.I)
_ZONE_COMPACT = re.compile(r"\b(?:NAD\s?83|NAD\s?27|WGS\s?84)\s*[/ ]?\s*(?:UTM\s*)?Z?\s?(\d{1,2})\s?([NS])\b")
_SOUTH_JUR = {"peru", "chile", "argentina", "brazil", "bolivia", "australia", "south africa",
              "zambia", "tanzania", "drc", "congo", "madagascar", "namibia", "botswana",
              "zimbabwe", "malawi", "mozambique", "angola", "fiji", "new zealand", "papua new guinea",
              "indonesia", "ecuador", "paraguay", "uruguay"}


def detect_crs(pages_text, jurisdiction=None):
    zones = Counter()
    hemis = Counter()
    datum = Counter()
    for t in pages_text:
        for k, rx in enumerate(_ZONE_RX):
            for m in rx.finditer(t):
                z = int(m.group(1)); zones[z] += 3
                hemis["S" if k == 3 else "N"] += 1
        for rx in (_ZONE_TXT, _ZONE_TXT2, _ZONE_COMPACT):
            for m in rx.finditer(t):
                z = int(m.group(1))
                if 1 <= z <= 60:
                    zones[z] += 1
                    if m.group(2):
                        hemis[m.group(2).upper()] += 1
        for m in re.finditer(r"\b(NAD\s?-?83|NAD\s?-?27|WGS\s?-?84|SIRGAS|PSAD\s?-?56|GDA\s?-?94)", t, re.I):
            datum[re.sub(r"[\s-]", "", m.group(1)).upper()] += 1
    zone = zones.most_common(1)[0][0] if zones else None
    hemi = hemis.most_common(1)[0][0] if hemis else None
    if hemi is None:
        hemi = "S" if (jurisdiction or "").lower() in _SOUTH_JUR else "N"
    return {"zone": zone, "hemi": hemi, "datum": datum.most_common(1)[0][0] if datum else None,
            "zone_votes": dict(zones.most_common(4))}


def utm_to_latlon(e, n, zone, hemi="N", datum=None):
    from pyproj import Transformer
    epsg = (32700 if hemi == "S" else 32600) + int(zone)
    if datum and datum.startswith("NAD27") and hemi == "N":
        epsg = 26700 + int(zone)
    elif datum and datum.startswith("NAD83") and hemi == "N" and 1 <= zone <= 23:
        epsg = 26900 + int(zone)
    tr = _TR.get(epsg)
    if tr is None:
        tr = _TR[epsg] = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(e, n)
    return lat, lon


_TR = {}


def _center_from_text(pages_text):
    """Project centre from prose ('latitude 49°06'N ... longitude 79°27'W' or
    decimal degrees) — used to sanity-check / infer the UTM zone."""
    rx = re.compile(r"(\d{1,2})\s*[°º]\s*(\d{1,2})?\s*['’′]?\s*(\d{1,2}(?:\.\d+)?)?\s*[\"”″]?\s*([NS])"
                    r"[^\n]{0,40}?(\d{1,3})\s*[°º]\s*(\d{1,2})?\s*['’′]?\s*(\d{1,2}(?:\.\d+)?)?\s*[\"”″]?\s*([EW])")
    for t in pages_text[:60]:
        m = rx.search(t)
        if m:
            la = float(m.group(1)) + float(m.group(2) or 0) / 60 + float(m.group(3) or 0) / 3600
            lo = float(m.group(5)) + float(m.group(6) or 0) / 60 + float(m.group(7) or 0) / 3600
            if m.group(4) == "S":
                la = -la
            if m.group(8) == "W":
                lo = -lo
            if abs(la) <= 80 and abs(lo) <= 180:
                return [round(la, 4), round(lo, 4)]
    return None


def georeference(collars, pages_text, jurisdiction=None, trust_jurisdiction=False):
    """Attach lat/lon to each collar when its coordinates are UTM and the zone is
    known (from the report text, or inferred from a text centre)."""
    crs = detect_crs(pages_text, jurisdiction)
    tc = _center_from_text(pages_text)
    ll = [c for c in collars if c.get("lat") is not None]
    utm = [c for c in collars if c.get("e") is not None and 100000 <= c["e"] <= 900000
           and 0 < c["n"] < 10000000]
    kind = "local"
    if ll and len(ll) >= len(utm):
        kind = "latlon"
    elif utm and len(utm) >= 0.6 * len(collars):
        zone = crs["zone"]
        if zone is None and tc:
            zone = int((tc[1] + 180) // 6) + 1
            crs["source"] = "inferred from text centre"
        if not zone and any(c.get("z") is not None for c in utm):
            try:
                crs["zone"] = None
                _dem_fix_zone(utm, crs)
                zone = crs.get("zone")
            except Exception:
                zone = None
        if zone:
            kind = "utm"
            ok = 0
            for c in utm:
                try:
                    la, lo = utm_to_latlon(c["e"], c["n"], zone, crs["hemi"], crs["datum"])
                    c["lat"], c["lon"] = round(la, 6), round(lo, 6); ok += 1
                except Exception:
                    pass
            crs["zone"] = zone
            # sanity: if a text centre exists and we're > 150 km away, the zone is wrong
            if tc and ok:
                lat = sum(c["lat"] for c in utm if "lat" in c) / ok
                lon = sum(c["lon"] for c in utm if "lon" in c) / ok
                if _km(lat, lon, tc[0], tc[1]) > 150:
                    for c in utm:
                        c.pop("lat", None); c.pop("lon", None)
                    kind = "utm?"
    # 1) place check: the coordinate system must put the collars where the report
    #    says the project is (its own lat/long text, or the curated jurisdiction).
    #    Searches every EPSG projected CRS for that area — UTM, MTM (Québec / NS),
    #    US state plane in feet, national grids — not just UTM.
    en = [c for c in collars if c.get("e") is not None and c.get("n") is not None]
    placed = False
    if len(en) >= 3 and kind != "latlon" and (tc or trust_jurisdiction):
        try:
            placed = _crs_search(en, crs, tc, jurisdiction if trust_jurisdiction else None, kind)
            if placed:
                kind = crs["kind"]
        except Exception as ex:
            crs["search_error"] = str(ex)[:80]
    # 2) otherwise, elevation check: published collar elevations must sit on the
    #    ground; a UTM zone that puts them far off the DEM is wrong.
    if not placed and utm and kind in ("utm", "utm?"):
        try:
            _dem_fix_zone(utm, crs)
            if all(c.get("lat") is not None for c in utm[:5]):
                kind = "utm"
        except Exception:
            pass
    crs["kind"] = kind
    pts = [(c["lat"], c["lon"]) for c in collars if c.get("lat") is not None]
    center = None
    if pts:
        pts.sort()
        mid = pts[len(pts) // 2]
        center = [round(mid[0], 5), round(mid[1], 5)]
    return crs, center or None, tc


def _crs_search(en, crs, tc, jurisdiction, kind):
    """Find the projected CRS that places the collar coordinates at the project.
    Candidates: every EPSG projected CRS whose area of use covers the place.
    Accept one that lands the collars within 25 km of the report's stated centre
    (or inside the trusted jurisdiction), tie-broken by collar-elevation/DEM fit
    (metres or feet). Returns True when collars were (re)georeferenced."""
    import numpy as np
    from shapely.geometry import Point
    from pyproj import Transformer
    from pyproj.aoi import AreaOfInterest
    from pyproj.database import query_crs_info
    from pyproj.enums import PJType
    from minemodelingpro import terrain
    geom = None
    if tc:
        aoi = AreaOfInterest(tc[1] - 0.3, tc[0] - 0.3, tc[1] + 0.3, tc[0] + 0.3)
    else:
        geom = terrain.region_geom(jurisdiction)
        if geom is None or geom.area > 30:        # a whole country/large province can't discriminate
            return False
        b = geom.bounds
        aoi = AreaOfInterest(b[0], b[1], b[2], b[3])
    e = float(np.median([c["e"] for c in en])); n = float(np.median([c["n"] for c in en]))
    # the current UTM solution is kept if it already agrees with the place
    if kind == "utm" and en[0].get("lat") is not None:
        la = float(np.median([c["lat"] for c in en if c.get("lat") is not None]))
        lo = float(np.median([c["lon"] for c in en if c.get("lon") is not None]))
        if (tc and _km(la, lo, tc[0], tc[1]) < 40) or (geom is not None and geom.buffer(0.3).contains(Point(lo, la))):
            return False
    cands = []
    for ci in query_crs_info(auth_name="EPSG", pj_types=[PJType.PROJECTED_CRS], area_of_interest=aoi):
        if ci.deprecated:
            continue
        try:
            tr = _TR.get(("x", ci.code))
            if tr is None:
                tr = _TR[("x", ci.code)] = Transformer.from_crs(f"EPSG:{ci.code}", "EPSG:4326", always_xy=True)
            lo, la = tr.transform(e, n)
        except Exception:
            continue
        if not (np.isfinite(lo) and np.isfinite(la)):
            continue
        if tc:
            d = _km(la, lo, tc[0], tc[1])
            if d <= 25:
                cands.append((d, ci.code, ci.name, tr))
        elif geom.buffer(0.05).contains(Point(lo, la)):
            cands.append((0.0, ci.code, ci.name, tr))
    if not cands:
        return False
    # collapse near-identical solutions (datum realisations), keep distinct ones
    zc = [c for c in en if c.get("z") is not None and -500 < c["z"] < 20000][:40]
    best = None
    if zc:
        for d, code, name, tr in cands:
            lo, la = tr.transform([c["e"] for c in zc], [c["n"] for c in zc])
            dem = terrain.elevations(np.asarray(la), np.asarray(lo), 11)
            Z = np.array([c["z"] for c in zc])
            for scale in (1.0, 0.3048):
                m = float(np.nanmedian(np.abs(dem - Z * scale)))
                sc = (m, d, 0 if code.startswith(("326", "327", "269")) else 1)
                if best is None or sc < best[0]:
                    best = (sc, code, name, tr, scale)
        if best[0][0] > 60:
            # elevations don't confirm any candidate (often a local mine grid whose
            # numbers happen to fall near the project): only a UTM solution within
            # 5 km of the report's stated centre is trusted.
            utm_c = [c for c in cands if c[1].startswith(("326", "327", "269")) and c[0] <= 5]
            if not utm_c:
                return False
            d, code, name, tr = min(utm_c, key=lambda x: x[0])
            best = ((None, d, 0), code, name, tr, 1.0)
    else:
        # no elevations to confirm: trust a UTM zone close to the stated centre, or a
        # national/state grid named for the place itself (e.g. "Arizona West")
        place = (terrain.region_of(tc[0], tc[1]) if tc else None) or jurisdiction or ""
        pk = terrain._fold(place).split(" ")[0] if place else ""
        good = [c for c in cands if (c[1].startswith(("326", "327", "269")) and c[0] <= 5)
                or (pk and len(pk) > 3 and pk in terrain._fold(c[2]))]
        if not good:
            return False
        d, code, name, tr = min(good, key=lambda x: (x[0], 0 if x[1].startswith(("326", "327", "269")) else 1))
        best = ((None, d, 0), code, name, tr, 1.0)
    _, code, name, tr, scale = best
    lo_, la_ = tr.transform(np.array([c["e"] for c in en]), np.array([c["n"] for c in en]))
    mlat, mlon = float(np.median(la_)), float(np.median(lo_))
    if (tc and _km(mlat, mlon, tc[0], tc[1]) > 40) or (geom is not None and not geom.buffer(0.3).contains(Point(mlon, mlat))):
        return False
    for c in en:
        lo, la = tr.transform(c["e"], c["n"])
        c["lat"], c["lon"] = round(la, 6), round(lo, 6)
        if scale != 1.0 and c.get("z") is not None:
            c["z"] = round(c["z"] * scale, 2)
    crs.update({"kind": "projected", "epsg": int(code), "crs_name": name,
                "source": "placed at " + ("report lat/long" if tc else str(jurisdiction)),
                "dem_misfit_m": round(best[0][0], 1) if best[0][0] is not None else None,
                "z_units": "ft" if scale != 1.0 else crs.get("z_units")})
    return True


def _dem_fix_zone(utm, crs):
    import numpy as np
    from minemodelingpro import terrain
    zc = [c for c in utm if c.get("z") is not None and -500 < c["z"] < 6000]
    if len(zc) < 3:
        return
    zc = zc[:: max(1, len(zc) // 40)]
    E = [c["e"] for c in zc]; N = [c["n"] for c in zc]; Z = np.array([c["z"] for c in zc])

    def misfit(zone, hemi, scale=1.0):
        ll = [utm_to_latlon(e, n, zone, hemi, crs.get("datum")) for e, n in zip(E, N)]
        d = terrain.elevations([a for a, _ in ll], [b for _, b in ll], 11)
        return float(np.nanmedian(np.abs(d - Z * scale)))
    z0 = crs.get("zone")
    cur = misfit(z0, crs["hemi"]) if z0 else 1e9
    if z0 and cur > 60:
        # collar elevations published in FEET (common in US reports)?
        ft = misfit(z0, crs["hemi"], 0.3048)
        if ft < 60 and ft < cur / 3:
            crs["z_units"] = "ft"
            for c in utm:
                if c.get("z") is not None:
                    c["z"] = round(c["z"] * 0.3048, 2)
            crs["dem_misfit_m"] = round(ft, 1)
            if any(c.get("lat") is None for c in utm):
                for c in utm:
                    la, lo = utm_to_latlon(c["e"], c["n"], z0, crs["hemi"], crs.get("datum"))
                    c["lat"], c["lon"] = round(la, 6), round(lo, 6)
            return
    crs["dem_misfit_m"] = round(cur, 1) if cur < 1e8 else None
    if cur <= 250:
        if any(c.get("lat") is None for c in utm):
            for c in utm:
                la, lo = utm_to_latlon(c["e"], c["n"], z0, crs["hemi"], crs.get("datum"))
                c["lat"], c["lon"] = round(la, 6), round(lo, 6)
        return
    best = (cur, z0, crs["hemi"])
    full = not z0
    cands = range(1, 61) if full else [z for z in range(z0 - 8, z0 + 9) if 1 <= z <= 60]
    scores = []
    for z in cands:
        for hemi in ("N", "S"):
            try:
                m = misfit(z, hemi)
            except Exception:
                continue
            if np.isfinite(m):
                scores.append(m)
                if m < best[0]:
                    best = (m, z, hemi)
    if full:
        scores.sort()
        if not (best[0] < 60 and len(scores) > 1 and scores[1] > 2.5 * best[0]):
            return
    if best[0] < 60 and best[0] < cur / 4:
        crs["zone_from_text"] = z0
        crs["zone"], crs["hemi"] = best[1], best[2]
        crs["source"] = f"collar elevations matched to DEM (misfit {best[0]:.0f} m)"
        crs["dem_misfit_m"] = round(best[0], 1)
        for c in utm:
            la, lo = utm_to_latlon(c["e"], c["n"], best[1], best[2], crs.get("datum"))
            c["lat"], c["lon"] = round(la, 6), round(lo, 6)


def _km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = (math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p)
         * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(min(1, a)))


# -------------------------------------------------------------- resources
_CATS = [("measured and indicated", "M+I"), ("measured & indicated", "M+I"), ("measured + indicated", "M+I"),
         ("m&i", "M+I"), ("m+i", "M+I"), ("total m&i", "M+I"),
         ("measured", "Measured"), ("indicated", "Indicated"), ("inferred", "Inferred"),
         ("proven and probable", "P+P"), ("proven & probable", "P+P"), ("p&p", "P+P"),
         ("proven", "Proven"), ("proved", "Proven"), ("probable", "Probable")]
_OZ_PER_T = 1 / 31.1034768
_LB_PER_T = 2204.62262


def _cat_of(text):
    low = text.lower()
    for k, v in _CATS:
        if re.search(r"(^|\W)" + re.escape(k) + r"(\W|$)", low):
            return v
    return None


def _consistent_triples(nums):
    """(tonnes_raw, grade, contained_raw, kind, tmul, cmul) where contained ≈
    tonnes × grade for some unit multipliers — the self-check that makes resource
    parsing reliable. The multipliers are resolved later from the page's units."""
    out = []
    n = len(nums)
    for i in range(n):
        T = nums[i]
        if T is None or T <= 0:
            continue
        for j in range(i + 1, min(n, i + 3)):
            G = nums[j]
            if G is None or G <= 0 or G > 5000:
                continue
            for k in range(j + 1, min(n, j + 3)):
                C = nums[k]
                if C is None or C <= 0:
                    continue
                for tmul in (1, 1e3, 1e6):
                    t = T * tmul
                    oz = t * G * _OZ_PER_T
                    for cmul in (1, 1e3, 1e6):
                        if abs(C * cmul - oz) / oz < 0.035:
                            out.append((T, G, C, "oz", tmul, cmul))
                    if G <= 80:
                        lb = t * G / 100 * _LB_PER_T
                        for cmul in (1, 1e3, 1e6, 1e9):
                            if abs(C * cmul - lb) / lb < 0.035:
                                out.append((T, G, C, "lb", tmul, cmul))
                        mt = t * G / 100
                        for cmul in (1, 1e3, 1e6):
                            if abs(C * cmul - mt) / mt < 0.035:
                                out.append((T, G, C, "t", tmul, cmul))
    return out


def _unit_hints(text):
    low = text.lower()
    tm = None
    if re.search(r"\bmt\b|million tonnes|tonnes? \(m\)|\(mt\)|mtonnes|m tonnes|tonnage \(m", low):
        tm = 1e6
    elif re.search(r"\bkt\b|\(000\s?t|000's|'000|000s\b|x ?1,?000|thousand tonnes|tonnes? \(000|tonnes? \(k\)|ktonnes", low):
        tm = 1e3
    cm = {}
    if re.search(r"\bmoz\b|million (troy )?ounces|oz \(m\)", low):
        cm["oz"] = 1e6
    elif re.search(r"\bkoz\b|000 ?oz|oz \(000|ounces \(000|thousand ounces|oz \(k\)", low):
        cm["oz"] = 1e3
    if re.search(r"\bblbs?\b|billion (pounds|lbs?)", low):
        cm["lb"] = 1e9
    elif re.search(r"\bmlbs?\b|million (pounds|lbs?)|lbs? \(m\)|lbs? \(000,000|m ?lbs", low):
        cm["lb"] = 1e6
    elif re.search(r"\bklbs?\b|lbs? \(000|000 ?lbs|pounds \(000", low):
        cm["lb"] = 1e3
    return tm, cm


def extract_resources(doc, pages_text):
    """Resource statement rows (category, tonnes, grade, contained) validated by
    tonnes × grade ≈ contained, from table rows on resource pages."""
    rows = []
    for pno, t in enumerate(pages_text):
        low = t.lower()
        if not re.search(r"resource|reserve", low):
            continue
        if not re.search(r"measured|indicated|inferred|proven|probable", low):
            continue
        hist = bool(re.search(r"historic(al)? (mineral )?(resource|estimate)", low))
        pscore = 0
        if re.search(r"mineral resource (statement|estimate|summary)|summary of (the )?mineral resource|"
                     r"resource statement|mineral resources? (are|is) (reported|summari)", low):
            pscore += 3
        if re.search(r"effective date", low):
            pscore += 1
        if re.search(r"sensitivity|grade[- ]tonnage|by cut-?off|at various|cut-?off grades? of \d.*and", low):
            pscore -= 4
        if re.search(r"\bpit\b.*\bshell\b|block model validation|swath|comparison|previous|\b20[01]\d\b mineral resource", low):
            pscore -= 1
        tm_hint, cm_hint = _unit_hints(t)
        years = [int(y) for y in re.findall(r"effective\s+date[^.\n]{0,60}?((?:19|20)\d\d)", t, re.I)]
        pyear = max(years) if years else None
        el_hint = _page_element(t)
        try:
            lines = page_lines(doc[pno])
        except Exception:
            continue
        cur_cat = None
        for ln in lines:
            txt = " ".join(c["t"] for c in ln["cells"])
            label = " ".join(c["t"] for c in ln["cells"] if not _is_num(c["t"]))
            cat = _cat_of(label)
            nums = [num(c["t"]) for c in ln["cells"] if _is_num(c["t"])]
            if cat and len(nums) < 3:
                cur_cat = cat                   # a section label line ("Inferred")
                continue
            is_total = bool(re.search(r"total|\+|combined|all zones|global|overall", label, re.I))
            if not cat:
                cat = cur_cat
            if not cat:
                if not label.strip():
                    continue
                cat = "Total" if is_total else "Zone"
            if len(nums) < 3:
                continue
            tr = _consistent_triples(nums)
            if not tr:
                continue
            # resolve unit multipliers from the page's stated units (else the
            # smallest plausible ones), then take the largest-tonnage triple in the
            # row (the row total when a table splits by zone/pit).
            def pref(x):
                T, G, C, kind, tmul, cmul = x
                tok = (tm_hint == tmul) if tm_hint else (T * tmul >= 5e3 and tmul == min(
                    m for m in (1, 1e3, 1e6) if T * m >= 5e3))
                cok = (cm_hint.get(kind) == cmul) if kind in cm_hint else (cmul == min(
                    y[5] for y in tr if y[3] == kind and y[4] == tmul) if any(
                    y[3] == kind and y[4] == tmul for y in tr) else False)
                return (tok, cok, kind != "t", T)
            best = max(tr, key=pref)
            T, G, C, kind, tmul, cmul = best
            if not (5e3 <= T * tmul <= 2e10):
                continue
            if kind == "oz" and (C * cmul > 1.5e8 or G > 60):
                continue
            if kind == "lb" and C * cmul > 2e11:
                continue
            rows.append({"page": pno + 1, "cat": cat, "tonnes": round(T * tmul),
                         "grade": G, "contained": round(C * cmul), "cunit": kind,
                         "hist": hist, "reserve": cat in ("Proven", "Probable", "P+P"), "pscore": pscore,
                         "year": pyear, "total": is_total, "el": el_hint,
                         "text": txt[:160]})
    return rows


def _page_element(t):
    """Which metal a resource page reports: counts of element words near contained-metal units."""
    low = t.lower()
    c = Counter()
    for el, pats in (("Au", r"\bau\b|gold"), ("Ag", r"\bag\b|silver"), ("Cu", r"\bcu\b|copper"), ("Zn", r"\bzn\b|zinc"),
                     ("Pb", r"\bpb\b|\blead\b"), ("Ni", r"\bni\b|nickel"), ("U3O8", r"u3o8|uranium"), ("Li2O", r"li2o|lithium"),
                     ("Co", r"\bco\b(?!-)|cobalt"), ("Mo", r"\bmo\b|molybdenum")):
        c[el] = len(re.findall(pats, low))
    if not c or c.most_common(1)[0][1] == 0:
        return None
    (e1, n1), = c.most_common(1)
    return e1


def headline_resource(rows):
    """Pick the CURRENT resource statement: latest effective date first, then the
    most complete category set / statement-like page, earliest page (summary)."""
    if not rows:
        return None
    by_page = defaultdict(list)
    for r in rows:
        if r["hist"] or r["reserve"]:
            continue
        by_page[(r["page"], r["cunit"])].append(r)
    if not by_page:
        return None

    def score(item):
        (pg, _), rs = item
        cats = {r["cat"] for r in rs}
        named = len(cats & {"Measured", "Indicated", "Inferred", "M+I"})
        tot = any(r["total"] for r in rs)
        return ((rs[0].get("year") or 0), named + (1 if tot else 0) + rs[0].get("pscore", 0), -pg)
    (page, cunit), rs = max(by_page.items(), key=score)
    named = [r for r in rs if r["cat"] in ("Measured", "Indicated", "Inferred", "M+I")]
    cats = {}
    if named:
        # per category: the total row if the table splits by zone, else the largest row
        for c in ("Measured", "Indicated", "M+I", "Inferred"):
            cr = [r for r in named if r["cat"] == c]
            if not cr:
                continue
            tt = [r for r in cr if r["total"]]
            best = max(tt or cr, key=lambda r: r["tonnes"])
            cats[c] = {"tonnes": best["tonnes"], "grade": best["grade"], "contained": best["contained"]}
        tot = _sum_cats([{"cat": k, **v} for k, v in cats.items()])
    else:
        tt = [r for r in rs if r["total"]] or rs
        seen, uniq = set(), []
        for r in tt:
            k = (r["tonnes"], r["contained"])
            if k not in seen:
                seen.add(k); uniq.append(r)
        if len(uniq) > 6:
            return None
        t = sum(r["tonnes"] for r in uniq); c = sum(r["contained"] for r in uniq)
        cats = {"Total": {"tonnes": t, "grade": round(c / t * (31.1034768 if cunit == "oz" else 100 / 2204.62262 if cunit == "lb" else 100), 3) if t else None, "contained": c}}
        tot = {"tonnes": t, "contained": c, "mi_tonnes": None, "mi_contained": None, "inf_tonnes": None, "inf_contained": None}
    els = Counter(r.get("el") for r in rs if r.get("el"))
    return {"page": page, "contained_unit": cunit, "categories": cats, "year": rs[0].get("year"),
            "element": els.most_common(1)[0][0] if els else None, **tot}


def _sum_cats(rs):
    best = {}
    for r in rs:
        if r["cat"] not in best or r["tonnes"] > best[r["cat"]]["tonnes"]:
            best[r["cat"]] = r
    mi = best.get("M+I")
    if mi is None and ("Measured" in best or "Indicated" in best):
        t = sum(best[c]["tonnes"] for c in ("Measured", "Indicated") if c in best)
        c = sum(best[c]["contained"] for c in ("Measured", "Indicated") if c in best)
        mi = {"tonnes": t, "contained": c}
    inf = best.get("Inferred")
    t = (mi["tonnes"] if mi else 0) + (inf["tonnes"] if inf else 0)
    c = (mi["contained"] if mi else 0) + (inf["contained"] if inf else 0)
    return {"tonnes": t, "contained": c,
            "mi_tonnes": mi["tonnes"] if mi else None, "mi_contained": mi["contained"] if mi else None,
            "inf_tonnes": inf["tonnes"] if inf else None, "inf_contained": inf["contained"] if inf else None}


# -------------------------------------------------------- block model params
_BLOCK3 = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:m\s*)?[x×X*]\s*(\d{1,3}(?:\.\d+)?)\s*(?:m\s*)?[x×X*]\s*(\d{1,3}(?:\.\d+)?)\s*(m|metres|meters|ft|feet)?\b")
_BLOCKCTX = re.compile(r"block\s*(?:model\s*)?(?:size|dimension)|parent\s*block|block\s*model|block\s*size|blocks?\s*(?:of|measuring)|cell\s*size|SMU|selective\s*mining\s*unit", re.I)
_DENS = re.compile(r"(?:bulk\s*)?(?:density|specific\s*gravity|\bSG\b|tonnage\s*factor)[^.\n]{0,60}?(\d\.\d{1,3})\s*(?:t/m3|t/m³|g/cm3|g/cm³|g/cc|tonnes?/m)?", re.I)


def extract_block_params(pages_text):
    votes = Counter()
    dens = Counter()
    for t in pages_text:
        for m in _BLOCKCTX.finditer(t):
            win = t[m.start(): m.start() + 260]
            b = _BLOCK3.search(win)
            if not b:
                continue
            x, y, z = (float(b.group(i)) for i in (1, 2, 3))
            f = 0.3048 if (b.group(4) or "").lower() in ("ft", "feet") else 1.0
            x, y, z = x * f, y * f, z * f
            if 0.5 <= x <= 100 and 0.5 <= y <= 100 and 0.5 <= z <= 100:
                w = 3 if re.search(r"parent", m.group(0), re.I) else 1
                votes[(round(x, 2), round(y, 2), round(z, 2))] += w
        for m in _DENS.finditer(t):
            v = float(m.group(1))
            if 2.0 <= v <= 5.0:
                dens[round(v, 2)] += 1
    block = list(votes.most_common(1)[0][0]) if votes else None
    density = dens.most_common(1)[0][0] if dens else None
    return block, density


# ---------------------------------------------------------- report identity
_TP = [
    re.compile(r"(?:technical\s+report|NI\s*43-101|preliminary\s+economic\s+assessment|feasibility\s+study|"
               r"mineral\s+resource\s+estimate|resource\s+update)[^\n]{0,60}?\b(?:on|for|of)\s+(?:the\s+)?"
               r"([A-Z][\w'’.&\- ]{2,50}?)\s+(?:Gold\s+|Silver\s+|Copper\s+|Lithium\s+|Uranium\s+|Nickel\s+|"
               r"Zinc\s+|Polymetallic\s+|Rare\s+Earth\s+|Graphite\s+|Phosphate\s+|Mineral\s+)?"
               r"(?:Project|Property|Deposit|Mine|Complex|Operations?)\b", re.I | re.S),
    re.compile(r"^\s*([A-Z][\w'’.&\- ]{2,40}?)\s+(?:Gold\s+|Silver\s+|Copper\s+|Lithium\s+|Uranium\s+|Nickel\s+|Zinc\s+)?"
               r"(?:Project|Property|Deposit|Mine|Mines)\b(?!\s*(?:No|Number|#|Manager|Director))[^\n]{0,70}$", re.M),
]
_TP_BAD = re.compile(r"^(the|a|an|this|mineral|technical|report|updated?|amended|independent|ni|nat|"
                     r"slr|amc|wsp|micon|ausenco|tetra ?tech|srk|rpa|agp|p ?& ?e|moose mountain|hatch|wood|golder|"
                     r"stantec|bba|gms|kappes|lycopodium|mining plus|erm|goldspot|innovexplo|ginto|caracle|apex|"
                     r"equity|sgs|dra|fluor|jds|nordmin|cube|snowden|optiro|entech|kca|m3|samuel|global resource|"
                     r"mining associates|red pine|mercator geological|watts|minefill)\b", re.I)


def title_project(pages_text):
    """Project name from the report cover / first pages ("Technical Report on the
    X Project"), which the SEDAR ledgers rarely carry."""
    blob = "\n".join(pages_text[:3])
    blob = re.sub(r"[ \t]+", " ", blob)
    cnt = Counter()
    for rx in _TP:
        for m in rx.finditer(blob):
            nm = re.sub(r"\s+", " ", m.group(1)).strip(" -,.")
            nm = re.sub(r"^(?:the)\s+", "", nm, flags=re.I)
            if 3 <= len(nm) <= 45 and not _TP_BAD.match(nm) and not re.search(r"\d{3,}", nm):
                cnt[nm] += 2 if rx is _TP[0] else 1
    # running page headers ("AURMAC PROPERTY, MAYO MINING DISTRICT | TECHNICAL REPORT")
    hdr = Counter()
    hrx = re.compile(r"^\s*([A-Z][\w'’.&\- ]{2,40}?)\s+(?:Gold\s+|Silver\s+|Copper\s+)?(PROJECT|PROPERTY|Project|Property|DEPOSIT|Deposit)\b(?!\s*(?:No|Number|#))", re.M)
    for t in pages_text[4:60]:
        for m in {m.group(1).strip() for m in hrx.finditer(t[:400])}:
            if not _TP_BAD.match(m) and not re.search(r"\d{3,}", m):
                hdr[m] += 1
    if hdr:
        nm, c = hdr.most_common(1)[0]
        if c >= 5:
            cnt[nm] += c
    if not cnt:
        return None
    for nm, _ in cnt.most_common():
        m = re.search(r"\bat\s+(?:the\s+)?(.+)$", nm)
        nm = m.group(1) if m else nm
        nm = re.sub(r"^(?:preliminary economic assessment|mineral resource estimate|pre-?feasibility study|"
                    r"feasibility study|geological introduction to the|technical report on the|updated)\s+", "", nm, flags=re.I)
        if re.search(r"reclamation|restoration|national id|introduction|table of contents|summary|appendix", nm, re.I):
            continue
        return nm
    return None


# ----------------------------------------------------------------- main entry
def extract_report(pdf_path, meta=None):
    import pymupdf
    meta = dict(meta or {})
    doc = pymupdf.open(pdf_path)
    pages_text = []
    for p in doc:
        try:
            pages_text.append(p.get_text())
        except Exception:
            pages_text.append("")
    surveys = []
    collars, intervals = extract_tables(doc, surveys=surveys)
    surveys = _clean_surveys(surveys)
    crs, center, tcenter = georeference(collars, pages_text, meta.get("jurisdiction"),
                                        trust_jurisdiction=str(meta.get("id", "")).startswith("ni43101:"))
    res_rows = extract_resources(doc, pages_text)
    head = headline_resource(res_rows)
    block, density = extract_block_params(pages_text)
    # link collars <-> intervals via normalised hole ids
    ckeys = {holeid.key(c["hole"]) for c in collars}
    ikeys = {holeid.key(r["hole"]) for r in intervals}
    commodity = meta.get("commodity")
    if not commodity and intervals:
        commodity = Counter(r["el"] for r in intervals).most_common(1)[0][0]
    out = {
        **meta,
        "title_project": title_project(pages_text),
        "extractor": EXTRACTOR,
        "extracted": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "pages": len(doc),
        "crs": crs, "center": center or tcenter, "text_center": tcenter,
        "block_size": block, "density": density,
        "resource": head, "resource_rows": res_rows[:80],
        "counts": {"collars": len(collars), "intervals": len(intervals), "surveyed_holes": len({x["hole"] for x in surveys}),
                   "holes_with_intervals": len(ikeys), "linked": len(ckeys & ikeys)},
        "collars": collars, "intervals": intervals, "surveys": surveys,
    }
    if commodity:
        out["commodity"] = commodity
    return out


def _clean_surveys(rows):
    """Per-hole survey stations sorted by depth, duplicates removed; a hole needs
    >=2 stations to be a real downhole survey (1 station = collar orientation)."""
    by = defaultdict(dict)
    for r in rows:
        by[holeid.key(r["hole"])].setdefault(r["depth"], r)
    out = []
    for k, st in by.items():
        ds = sorted(st)
        if len(ds) < 3 or ds[0] > 150:
            continue
        ok = True
        for a, b in zip(ds, ds[1:]):
            A, B = st[a], st[b]
            daz = abs((B["az"] - A["az"] + 180) % 360 - 180)
            # real downhole surveys drift gently between closely spaced stations;
            # anything else is a collar/summary table mis-read as a survey
            if b - a > 200 or daz > 25 or abs(B["dip"] - A["dip"]) > 12:
                ok = False
                break
        if ok:
            out.extend(st[d] for d in ds)
    return out


def save(rec, out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    fn = os.path.join(out_dir, rec["id"].replace(":", "__").replace("/", "_") + ".json")
    json.dump(rec, open(fn, "w"), separators=(",", ":"))
    return fn


def load_all(out_dir=OUT_DIR):
    import glob
    out = []
    for f in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        try:
            out.append(json.load(open(f)))
        except Exception:
            pass
    return out


# ------------------------------------------------ catalogue of archived PDFs
def catalogue():
    """Every archived technical report we know of: the ni43101 queue index and
    the SEDAR/ceo.ca ledgers. Returns [{id, archive_url, company, project, ...}]."""
    keep = os.path.join(_ROOT, "data", "keep")
    out, seen = [], set()
    try:
        idx = json.load(open(os.path.join(keep, "mmp_reports_index.json")))["reports"]
    except Exception:
        idx = []
    for r in idx:
        if r.get("archive_url") and r["id"] not in seen:
            seen.add(r["id"])
            out.append({"id": r["id"], "archive_url": r["archive_url"], "report_url": r.get("source_url"),
                        "company": r.get("company"), "project": r.get("project"),
                        "commodity": r.get("commodity"), "jurisdiction": r.get("jurisdiction"),
                        "date": (r.get("collected") or "")[:10] or None})
    for fn in ("sedar_manifest.json", "ceo_sedar_manifest.json"):
        try:
            rows = json.load(open(os.path.join(keep, fn)))
        except Exception:
            rows = []
        for r in rows:
            u = r.get("archive_url")
            if not u:
                continue
            base = u.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            sid = "sedar:" + re.sub(r"^(sedar_|ceo_)", "", base)
            if sid in seen:
                continue
            seen.add(sid)
            sub = r.get("submitted") or ""
            dt = None
            m = re.search(r"(\d{4}-\d{2}-\d{2})", sub)
            if m:
                dt = m.group(1)
            else:
                try:
                    dt = datetime.datetime.strptime(sub[:11], "%d %b %Y").date().isoformat()
                except Exception:
                    dt = None
            out.append({"id": sid, "archive_url": u, "report_url": r.get("sedar_url") or u,
                        "company": r.get("company"), "project": r.get("project"),
                        "commodity": r.get("commodity"), "jurisdiction": r.get("jurisdiction"),
                        "date": dt})
    return out


def run_all(pdf_dir="/tmp/mmp_pdfs", refresh=False, limit=None, max_seconds=None):
    import time
    import urllib.request
    t0 = time.time()
    os.makedirs(pdf_dir, exist_ok=True)
    done = 0
    for r in catalogue():
        fn = os.path.join(OUT_DIR, r["id"].replace(":", "__").replace("/", "_") + ".json")
        if not refresh and os.path.exists(fn):
            try:
                if json.load(open(fn)).get("extractor") == EXTRACTOR:
                    continue
            except Exception:
                pass
        if limit and done >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            print("[drill_tables] time budget reached"); break
        local = os.path.join(pdf_dir, r["archive_url"].rsplit("/", 1)[-1])
        try:
            if not os.path.exists(local):
                req = urllib.request.Request(r["archive_url"], headers={"User-Agent": "mmp/1.0"})
                with urllib.request.urlopen(req, timeout=180) as resp, open(local, "wb") as f:
                    f.write(resp.read())
            rec = extract_report(local, r)
            save(rec)
            done += 1
            c = rec["counts"]
            print(f"[drill_tables] {r['id']} {r.get('project') or r.get('company')}: "
                  f"{c['collars']} collars, {c['intervals']} intervals, linked {c['linked']}, "
                  f"crs={rec['crs'].get('kind')}/{rec['crs'].get('zone')} block={rec['block_size']} "
                  f"res={'y' if rec['resource'] else 'n'}")
        except Exception as e:
            print(f"[drill_tables] FAILED {r['id']}: {str(e)[:120]}")
    print(f"[drill_tables] processed {done} report(s)")
    return done


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "all":
        run_all(pdf_dir=os.environ.get("MMP_PDF_DIR", "/tmp/mmp_pdfs"), refresh="--refresh" in a,
                max_seconds=int(a[a.index("--max-seconds") + 1]) if "--max-seconds" in a else None)
    elif a:
        rec = extract_report(a[0], {"id": a[a.index("--id") + 1] if "--id" in a else "test"})
        print(json.dumps({k: v for k, v in rec.items() if k not in ("collars", "intervals", "resource_rows")}, indent=1))
        print("collars sample:", rec["collars"][:3])
        print("intervals sample:", rec["intervals"][:3])
