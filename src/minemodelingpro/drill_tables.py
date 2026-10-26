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
EXTRACTOR = "dt20"   # dt20: the body's attitude (dip angle, dip direction) from prose

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
# Which metals a grade is quoted in grams a tonne for; everything else is per
# cent. Keep this in step with the units ore.FLOOR declares — a metal read in one
# unit and floored in the other is how background gallium outranked silver.
_DEFAULT_UNIT = {"Au": "g/t", "Ag": "g/t", "Pt": "g/t", "Pd": "g/t", "AuEq": "g/t", "AgEq": "g/t",
                 "3E": "g/t", "PGM": "g/t", "PGE": "g/t", "Te": "g/t", "Ga": "g/t", "Ge": "g/t",
                 "In": "g/t", "Rh": "g/t", "Re": "g/t", "Se": "g/t", "Cd": "g/t"}

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
class TextDoc:
    """A stand-in for a PDF, backed by text that already has its layout preserved.

    The MineTerminalPro partner API serves every report's text page by page, run
    through ``pdftotext -layout``, which keeps table columns aligned as runs of
    spaces. That is enough for every extractor here, and it means a report can be
    read without downloading a hundred-megabyte PDF — which matters at four and a
    half thousand reports. ``doc[n]`` yields the page's text, and ``page_lines``
    knows what to do with a string."""

    def __init__(self, pages):
        self.pages = list(pages or [])

    def __len__(self):
        return len(self.pages)

    def __iter__(self):
        return iter(self.pages)

    def __getitem__(self, i):
        return self.pages[i]

    @property
    def page_count(self):
        return len(self.pages)


def _lines_from_layout(text):
    """Cells from layout-preserved text: two or more spaces separate columns.

    A single space is inside a cell ("Measured + Indicated"), two or more is the
    gutter between columns — which is exactly the convention pdftotext -layout
    writes and the same distinction page_lines makes from word gaps."""
    out = []
    for i, raw in enumerate(str(text or "").split("\n")):
        if not raw.strip():
            continue
        # Column positions come from where the text sits on the line, which is what
        # -layout preserves; vertical position from the line's order on the page.
        # The table readers compare these, they do not measure them, so a nominal
        # line height carries the same information a PDF's baselines do.
        cells, words = [], []
        for m in re.finditer(r"\S(?:.*?\S)?(?=\s{2,}|$)", raw):
            t = m.group(0).strip()
            if not t:
                continue
            cells.append({"t": t, "x0": float(m.start()), "x1": float(m.end())})
        for m in re.finditer(r"\S+", raw):
            words.append({"t": m.group(0), "x0": float(m.start()), "x1": float(m.end())})
        if cells:
            out.append({"y": float(i) * 12.0, "cells": cells, "words": words})
    return out


def page_lines(page):
    """Rebuild visual lines of cells from word boxes: words sharing a baseline form
    a line; within a line words separated by a small gap form one cell.

    Given a string instead of a page, the layout has already been flattened to
    spaces and the columns are split on those."""
    if isinstance(page, str):
        return _lines_from_layout(page)
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
# "UTM (mE)" is one of the commonest ways a collar table names its easting and
# none of the old alternatives matched it, so the whole header was rejected and
# the table with it — Crawford's 79 collars among them.
# "UTM (mE)" is one of the commonest ways a collar table names its easting and
# none of the old alternatives matched it, so the whole header was rejected and
# the table with it — Crawford's 79 collars among them.
_H_EAST = re.compile(r"\b(easting|east|utm ?e|x|e \(m\)|x \(m\)|e_utm|utm_e|mine ?e|local ?e)\b|utm ?\(?m ?e\)?|\bm ?e\b|east(ing)? ?\(?(m|utm)\)?", re.I)
_H_NORTH = re.compile(r"\b(northing|north|utm ?n|y|n \(m\)|y \(m\)|n_utm|utm_n|mine ?n|local ?n)\b|utm ?\(?m ?n\)?|\bm ?n\b|north(ing)? ?\(?(m|utm)\)?", re.I)
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
    tok = m.group(1).replace(" ", "") if m else ""
    el = _EL.get(tok)
    if not el:
        # The column may be headed "Grade" with the metal inside the unit on the
        # line below — "Grade" over "(% U3O8)" — which a stacked header merges into
        # "Grade (% U3O8)". The leading token is then "grade" and the metal is two
        # words away, so the whole label is searched before giving up. Wheeler
        # River's 70 uranium intervals read this way and not the other.
        for cand in re.findall(r"[a-z0-9]+(?:\s?eq)?", low.replace("_", " ")):
            got = _EL.get(cand.replace(" ", ""))
            # single letters are too ambiguous to pick out of running text
            if got and len(cand) > 1:
                el = got
                break
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


_DMS = re.compile(r"(\d{1,3})[ ]{0,2}[°º][ ]{0,2}"
                  r"(?:(\d{1,2})[ ]{0,2}['’′][ ]{0,2})?"
                  r"(?:(\d{1,2}(?:\.\d+)?)[ ]{0,2}[\"”″][ ]{0,2})?"
                  r"([NSEW])")


def _center_from_text(pages_text):
    """Project centre from prose ("latitude 49°06'N ... longitude 79°27'W") — used
    to sanity-check or infer the UTM zone.

    One pattern per coordinate, paired by proximity, rather than one pattern for
    both: a single expression with a lazy span between the two halves and optional
    minutes and seconds in each backtracks catastrophically on a page that mentions
    degrees many times, and a report of a hundred pages then took minutes instead of
    milliseconds."""
    for t in pages_text[:60]:
        last_ns = None
        for m in _DMS.finditer(t):
            d = float(m.group(1)) + float(m.group(2) or 0) / 60 + float(m.group(3) or 0) / 3600
            hemi = m.group(4)
            if hemi in "NS":
                last_ns = (d if hemi == "N" else -d, m.end())
                continue
            if last_ns is None or m.start() - last_ns[1] > 60:
                continue
            la = last_ns[0]
            lo = d if hemi == "E" else -d
            if abs(la) <= 80 and abs(lo) <= 180 and (la or lo):
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
    # a PDF table header extracts as "Tonnes \n(k)", so the unit sits on its own
    # line: without flattening the whitespace the "(k)" is invisible and every
    # tonnage on the page is read a thousand times too small
    low = re.sub(r"\s+", " ", text).lower()
    tm = None
    if re.search(r"\bmt\b|million tonnes|tonnes? \(m\)|\(mt\)|mtonnes|m tonnes|tonnage \(m", low):
        tm = 1e6
    elif re.search(r"\bkt\b|\(000\s?t|000's|'000|000s\b|x ?1,?000|thousand tonnes|"
                    r"tonn(?:es?|age) ?\(0?00|tonnes? \(k\)|ktonnes|\(,?000\)|\(k ?t\)|"
                    r"\(x ?1,?000\)", low):
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


# The smallest real filed statement in the corpus is a few tens of thousands of
# tonnes — a single high-grade vein. Below that a "resource" row is something else
# read by mistake: a contact-ratio table, a composite summary, a QA batch. And a
# resource is not reported at a grade nobody would mine; a gold row at 0.005 g/t is
# a boundary-condition average, not ore.
_RES_MIN_T = 2e4
_RES_MIN_G = {"Au": 0.08, "AuEq": 0.08, "Ag": 3.0, "AgEq": 3.0, "Pt": 0.05, "Pd": 0.05,
              "PGM": 0.05, "PGE": 0.05, "3E": 0.05, "Cu": 0.03, "Zn": 0.1, "Pb": 0.1,
              "Ni": 0.03, "Co": 0.005, "Mo": 0.003, "Sn": 0.02, "WO3": 0.02,
              "U3O8": 0.005, "Li2O": 0.1, "P2O5": 1.0, "Fe": 5.0}


# What one deposit can physically hold. These are upper bounds set by the largest
# deposits known, not by what is ordinary: Grasberg and Pebble each carry about a
# hundred million ounces of gold, Chuquicamata about a hundred million tonnes of
# copper, and the biggest molybdenum deposits a couple of million tonnes of
# molybdenum. A statement above these is a mis-read column, and the arithmetic
# cannot catch it because multiplying tonnes and contained metal by the same
# thousand keeps the row internally consistent. Iron, phosphate and the bulk
# commodities get no ceiling: billions of tonnes of ore is ordinary for them.
_MAX_CONTAINED_T = {"Au": 6_000.0, "AuEq": 8_000.0, "Ag": 120_000.0, "AgEq": 150_000.0,
                    "Pt": 3_000.0, "Pd": 3_000.0, "PGM": 6_000.0, "PGE": 6_000.0,
                    "3E": 6_000.0,
                    "Cu": 2.0e8, "Zn": 1.0e8, "Pb": 6.0e7, "Ni": 6.0e7,
                    "Co": 1.0e7, "Mo": 1.0e7, "Sn": 1.0e7, "WO3": 5.0e6,
                    "U3O8": 3.0e6, "Li2O": 2.0e8}
_CUNIT_T = {"oz": 1.0 / 32_150.7, "lb": 1.0 / 2204.62262, "t": 1.0}


# The most ORE one deposit of a given metal can plausibly hold. Copper, iron,
# phosphate, bauxite and lithium brine get no ceiling: a porphyry or a laterite of
# ten billion tonnes is ordinary for them. The rest have real limits — the largest
# gold-only ore bodies run to a couple of billion tonnes at very low grade, the
# whole Sudbury basin to about a billion tonnes of nickel ore — and a statement
# above them is a column read a thousand times too large. The arithmetic cannot
# catch that, because scaling tonnes and contained metal together keeps the row
# internally consistent, so the limit has to come from what deposits actually are.
_MAX_ORE_T = {"Au": 2.5e9, "AuEq": 3.0e9, "Ag": 2.0e9, "AgEq": 2.5e9,
              "Zn": 1.0e9, "ZnEq": 1.2e9, "Pb": 1.0e9, "Ni": 2.0e9,
              "Co": 2.0e9, "Mo": 5.0e9, "Sn": 1.0e9, "WO3": 5.0e8,
              "U3O8": 1.0e9, "Pt": 1.5e9, "Pd": 1.5e9, "PGM": 1.5e9,
              "PGE": 1.5e9, "3E": 1.5e9}


def ore_plausible(el, tonnes):
    """Whether a deposit of this metal can hold this much ore at all."""
    cap = _MAX_ORE_T.get(el)
    return True if not cap or not tonnes else float(tonnes) <= cap


def grade_ceiling_ok(el, grade, unit):
    """A deposit does not average a grade no rock reaches."""
    if not el or grade is None:
        return True
    from minemodelingpro import sanity as _S
    base = str(el).replace("Eq", "")
    # a metal is often reported as its oxide, and the ceiling is tabulated under
    # whichever form the corpus uses most
    base = {"Li": "Li2O", "Ta": "Ta2O5", "U": "U3O8", "V": "V2O5",
            "W": "WO3", "P": "P2O5", "Al": "Al2O3", "Ti": "TiO2"}.get(base, base)
    cap = _S.MAX_PLAUSIBLE.get(base)
    if not cap:
        return True
    g = float(grade)
    if unit == "ppm" and _DEFAULT_UNIT.get(el) == "%":
        g /= 10_000.0
    elif unit == "ppb":
        g /= 1_000.0
    elif unit == "oz/t":
        g *= 34.2857
    return g <= cap


def contained_plausible(el, contained, cunit):
    """Whether a statement's contained metal is within what a deposit can hold."""
    cap = _MAX_CONTAINED_T.get(el)
    if not cap or not contained:
        return True
    return float(contained) * _CUNIT_T.get(cunit, 1.0) <= cap


def res_grade_ok(el, grade, unit):
    """Whether a row's grade is high enough to be ore of that metal at all."""
    if not el or grade is None:
        return True
    lo = _RES_MIN_G.get(el)
    if lo is None:
        return True
    g = float(grade)
    if unit == "ppm" and _DEFAULT_UNIT.get(el) == "%":
        g /= 10_000.0
    elif unit == "ppb":
        g /= 1_000.0
    elif unit == "oz/t":
        g *= 34.2857
    return g >= lo


# ------------------------------------------------------- resource tables, by column
_RH_T = re.compile(r"\b(tonn?es?|tonnage|tonnes?|tons?|kt|mt|ore|quantity|mass)\b", re.I)
_RH_C = re.compile(r"contained|recoverable|\b(oz|ozs|ounces|koz|moz|lbs?|klbs?|mlbs?|"
                   r"blbs?|tonnes? of metal|metal)\b", re.I)
_RH_GRADE_U = re.compile(r"g\s*/\s*t|gram\s*/\s*tonne|grade|ppm|ppb|oz\s*/\s*t|"
                         r"per\s+tonne|%", re.I)
_RH_SKIP = re.compile(r"recovery|recover(ed|ies)|cut-?off|dilut|density|specific gravity|"
                      r"strip|waste|npv|irr|cost|capex|price|\$|年", re.I)


def _res_header(lines, i0, look=4):
    """The columns of the table whose first data row is lines[i0].

    A resource table says in its header what each column is — "Tonnage (000)",
    "Au g/t", "Contained koz" — and reading that is how a person knows that
    18,720 means 18.7 million tonnes. Guessing it from arithmetic cannot: tonnes
    x grade = contained balances just as well with every column multiplied by a
    thousand, which is how a 26 Mt silver deposit was read as 50,000 tonnes.

    A header is often stacked over two or three lines ("Tonnage" above "(000)",
    "Au" above "g/t"), so the band above the data is merged by column position."""
    band = []
    for j in range(max(0, i0 - look), i0):
        ln = lines[j]
        if sum(1 for c in ln["cells"] if _is_num(c["t"])) >= 3:
            band = []                      # another data row: the header is below it
            continue
        txt = " ".join(c["t"] for c in ln["cells"])
        if re.match(r"\s*(table|figure|exhibit)\s*\d", txt, re.I):
            band = []                      # a caption, and everything above it
            continue
        # A caption or a sentence is one wide cell that spans the whole table, so
        # merging it by position welds every column into one. Only a line that is
        # itself divided into columns can say what the columns are.
        if len(ln["cells"]) >= 2:
            band.append(ln)
    if not band:
        return None
    cols = []
    for ln in band:
        for c in ln["cells"]:
            hit = None
            for col in cols:
                if min(col["x1"], c["x1"]) - max(col["x0"], c["x0"]) > -1.5:
                    hit = col
                    break
            if hit:
                hit["t"] += " " + c["t"]
                hit["x0"] = min(hit["x0"], c["x0"]); hit["x1"] = max(hit["x1"], c["x1"])
            else:
                cols.append({"t": c["t"], "x0": c["x0"], "x1": c["x1"]})
    cols.sort(key=lambda c: c["x0"])
    out = []
    for c in cols:
        txt = re.sub(r"\s+", " ", c["t"]).strip()
        role, el, unit, mul, kind = None, None, None, 1.0, None
        eu = _elem_of(txt)
        # A grade column is tested FIRST, because "Gold Grade gram/tonne" contains
        # the word tonne: reading it as the tonnage column left the table with no
        # grade in it and the whole statement unread.
        if _RH_SKIP.search(txt):
            role = None
        elif eu and not _RH_C.search(txt):
            role, el, unit = "grade", eu[0], eu[1]
        elif _RH_GRADE_U.search(txt) and not _RH_C.search(txt):
            role = None                            # a grade column of no known metal
        elif _RH_T.search(txt) and not _RH_C.search(txt):
            role = "tonnes"
            tm, _ = _unit_hints(txt)
            mul = tm or 1.0
        elif _RH_C.search(txt):
            role = "contained"
            _, cmh = _unit_hints(txt)
            low = txt.lower()
            kind = ("lb" if re.search(r"lbs?|pound", low) else
                    "oz" if re.search(r"oz|ounce", low) else "t")
            mul = cmh.get(kind, 1.0)
            if eu:
                el = eu[0]
        out.append({**c, "label": txt, "role": role, "el": el, "unit": unit,
                    "mul": float(mul), "kind": kind})
    if not any(c["role"] == "tonnes" for c in out):
        return None
    if not any(c["role"] in ("grade", "contained") for c in out):
        return None
    return out


def _res_assign(line, cols):
    """Each numeric cell to the header column it sits under."""
    got = {}
    for c in line["cells"]:
        v = num(c["t"])
        if v is None:
            continue
        mid = (c["x0"] + c["x1"]) / 2.0
        best, bov = None, -1e9
        for k, col in enumerate(cols):
            ov = min(col["x1"], c["x1"]) - max(col["x0"], c["x0"])
            if not (col["x0"] - 6 <= mid <= col["x1"] + 6) and ov <= 0:
                continue
            if ov > bov:
                best, bov = k, ov
        if best is not None and best not in got:
            got[best] = v
    return got


def _res_blocks(lines):
    """Contiguous runs of data rows — one table body each.

    The header has to be found above the FIRST row of a table, not above whichever
    row happened to be noticed first: a table of four zones with the total last
    puts its header five lines up, and looking back four lines from the total row
    finds only more data."""
    out, i, n = [], 0, len(lines)
    while i < n:
        if not _is_data_line(lines[i], 2):
            i += 1
            continue
        j = i
        while j + 1 < n:
            nxt = lines[j + 1]
            if _is_data_line(nxt, 2):
                j += 1
                continue
            lab = " ".join(c["t"] for c in nxt["cells"])
            if lab.strip() and _cat_of(lab) and len(lab) < 40:
                j += 1                       # a bare category heading inside a table
                continue
            break
        out.append((i, j))
        i = j + 1
    return out


def _res_by_header(lines, ctx):
    """Resource rows read from a page's own table headers."""
    rows, tbl = [], 0
    for i0, i1 in _res_blocks(lines):
        body_lines = lines[i0:i1 + 1]
        labels = [" ".join(c["t"] for c in ln["cells"] if not _is_num(c["t"]))
                  for ln in body_lines]
        if not any(_cat_of(x) or re.search(r"total|combined|all zones|global", x, re.I)
                   for x in labels):
            continue
        cols = _res_header(lines, i0)
        if not cols:
            continue
        ti = next((k for k, c in enumerate(cols) if c["role"] == "tonnes"), None)
        gi = [k for k, c in enumerate(cols) if c["role"] == "grade"]
        ci = [k for k, c in enumerate(cols) if c["role"] == "contained"]
        if ti is None or not gi:
            continue
        # A resource table carries a column per metal and often an equivalent
        # beside each. The deposit's own metal is the one the table leads with,
        # and an equivalent is a derived figure, not a metal — so prefer the
        # leftmost column that names a real element.
        pref_k = next((k for k in gi if cols[k]["el"]
                       and not re.search(r"eq$", cols[k]["el"], re.I)), gi[0])
        got_any = False
        tmp, cmp_ = ctx["tm_hint"], ctx["cm_hint"]
        for ln, label in zip(body_lines, labels):
            got = _res_assign(ln, cols)
            if ti not in got:
                continue
            cat = _cat_of(label)
            is_total = bool(re.search(r"total|[+]|combined|all zones|global|overall",
                                      label, re.I))
            if not cat and not is_total:
                continue
            cat = cat or "Total"
            T = got[ti] * (cols[ti]["mul"] if cols[ti]["mul"] != 1.0 else (tmp or 1.0))
            if not (_RES_MIN_T <= T <= 2e10):
                continue
            for k in gi:
                G = got.get(k)
                if G is None or G <= 0:
                    continue
                el = cols[k]["el"]
                unit = cols[k]["unit"] or _DEFAULT_UNIT.get(el, "%")
                cc = next((c for c in ci if cols[c]["el"] in (el, None)), None)
                kind = cols[cc]["kind"] if cc is not None else (
                    "t" if unit == "%" else "oz")
                f = {"oz": 31.1034768, "lb": 100.0 / 2204.62262, "t": 100.0}[kind]
                calc = T * G / f
                if cc is not None and got.get(cc) is not None:
                    C = got[cc] * (cols[cc]["mul"] if cols[cc]["mul"] != 1.0
                                   else (cmp_.get(kind) or 1.0))
                    if not calc or not (0.6 <= C / calc <= 1.7):
                        continue            # the columns do not agree: not this table
                else:
                    C = calc
                if not res_grade_ok(el, G, unit) or not grade_ceiling_ok(el, G, unit):
                    continue
                if not contained_plausible(el, C, kind) or not ore_plausible(el, T):
                    continue
                if kind == "oz" and C > 1.5e8:
                    continue
                rows.append({"page": ctx["pno"] + 1, "cat": cat,
                             "cutoff": ctx["cog"], "cutoff_unit": ctx["cog_unit"],
                             "tonnes": round(T), "grade": G, "contained": round(C),
                             "cunit": kind, "hist": ctx["hist"],
                             "reserve": cat in ("Proven", "Probable", "P+P"),
                             "pscore": ctx["pscore"] + 2, "year": ctx["pyear"],
                             "total": is_total, "el": el,
                             "grade_unit": unit, "scope": ctx["pscope"],
                             "byhdr": True, "tbl": tbl + 1, "primary": k == pref_k,
                             "text": label[:160]})
                got_any = True
        if got_any:
            tbl += 1
    return rows


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
        cog, cog_unit = _page_cutoff(t)
        pscope = _page_scope(t)
        years = [int(y) for y in re.findall(r"effective\s+date[^.\n]{0,60}?((?:19|20)\d\d)", t, re.I)]
        pyear = max(years) if years else None
        try:
            lines = page_lines(doc[pno])
        except Exception:
            continue
        ctx = {"pno": pno, "tm_hint": tm_hint, "cm_hint": cm_hint, "cog": cog,
               "cog_unit": cog_unit, "pscope": pscope, "pyear": pyear, "hist": hist,
               "pscore": pscore}
        try:
            hdr = _res_by_header(lines, ctx)
        except Exception:
            hdr = []
        if hdr:
            # the table said what its columns are; arithmetic guessing on the same
            # page is strictly weaker evidence and is dropped rather than mixed in
            rows.extend(hdr)
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
            if not label.strip():
                # Every row of a filed statement is named — a category, a zone, a
                # total. A row with no name at all and a number in its first column
                # is a cut-off row of a grade-tonnage table, and inheriting the
                # category from a heading further up the page turned Stuhini's
                # molybdenum sensitivity into "Measured 16,997 Mt" and Cruz de
                # Plata's into "Inferred 3,389 Mt of gold ore".
                continue
            tr = _consistent_triples(nums)
            if not tr:
                continue
            # resolve unit multipliers from the page's stated units (else the
            # smallest plausible ones), then take the largest-tonnage triple in the
            # row (the row total when a table splits by zone/pit).
            def pref(x):
                """Resolve the unit multipliers. tonnes x grade = contained is
                scale-invariant — multiply both by a thousand and it still balances
                — so the arithmetic alone cannot say whether a row reads 21,105 t or
                21,105 kt. Only the column headers can, and a stated contained-metal
                unit ("Mlb", "Moz") is hard evidence where "is this at least five
                thousand tonnes" is a guess. So headers rank above guesses, and a
                guess is used only where no header speaks."""
                T, G, C, kind, tmul, cmul = x
                t_said = tm_hint is not None
                c_said = kind in cm_hint
                tok = (tm_hint == tmul) if t_said else (T * tmul >= 5e3 and tmul == min(
                    m for m in (1, 1e3, 1e6) if T * m >= 5e3))
                cok = (cm_hint.get(kind) == cmul) if c_said else (cmul == min(
                    y[5] for y in tr if y[3] == kind and y[4] == tmul) if any(
                    y[3] == kind and y[4] == tmul for y in tr) else False)
                if c_said and not t_said:
                    return (cok, tok, kind != "t", T)   # the header beats the guess
                return (tok, cok, kind != "t", T)
            best = max(tr, key=pref)
            T, G, C, kind, tmul, cmul = best
            if not (_RES_MIN_T <= T * tmul <= 2e10):
                continue
            _el0 = _page_element(t, kind, G)
            _u0 = grade_unit_for(kind)
            if not res_grade_ok(_el0, G, _u0) or not grade_ceiling_ok(_el0, G, _u0):
                continue
            if not contained_plausible(_el0, C * cmul, kind) \
                    or not ore_plausible(_el0, T * tmul):
                continue
            if kind == "oz" and (C * cmul > 1.5e8 or G > 60):
                continue
            if kind == "lb" and C * cmul > 2e11:
                continue
            rows.append({"page": pno + 1, "cat": cat, "cutoff": cog, "cutoff_unit": cog_unit,
                         "tonnes": round(T * tmul),
                         "grade": G, "contained": round(C * cmul), "cunit": kind,
                         "hist": hist, "reserve": cat in ("Proven", "Probable", "P+P"), "pscore": pscore,
                         "year": pyear, "total": is_total,
                         "el": _page_element(t, kind, G),
                         "grade_unit": grade_unit_for(kind),
                         "scope": pscope,
                         "text": txt[:160]})
    return rows



_SCOPE = [
    ("in_pit", r"in[- ]pit|pit[- ]constrained|within (?:the )?(?:optimi[sz]ed )?pit|open[- ]pit"),
    ("underground", r"\bunderground\b|out[- ]of[- ]pit|below (?:the )?pit"),
]
_SCOPE_CTX = re.compile(r"mineral resource|resource estimate|resource statement", re.I)


def _page_scope(t):
    """Whether a filed statement is pit-constrained, underground, or neither.

    It matters because MMP now reports those separately, and a pit-constrained
    statement compared against MMP's whole deposit is not a comparison at all —
    Murray Brook's filed table is titled "In-Pit Mineral Resource Estimate". The
    wording lives in the table's caption rather than its rows, so it is read from
    the page text around wherever the resource is introduced.

    Returns "in_pit", "underground", "both" or None."""
    flat = re.sub(r"\s+", " ", t or "")
    found = set()
    for m in _SCOPE_CTX.finditer(flat):
        win = flat[max(0, m.start() - 160): m.end() + 160]
        for name, pat in _SCOPE:
            if re.search(pat, win, re.I):
                found.add(name)
    if len(found) == 1:
        return found.pop()
    if len(found) > 1:
        return "both"
    return None


def _page_cutoff(t):
    """The cut-off a resource page reports at — what makes a published statement
    reproducible. Returns (value, unit): a grade cut-off ("0.3", "%") when the page
    states one, else a value cut-off ("23", "C$/t NSR"), which a grade model cannot
    reproduce directly but which the model page should still show.
    """
    g, v = [], []
    for m in re.finditer(r"cut[- ]?off\s*(?:grade|value)?[^.\n]{0,40}?"
                         r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)\s*(g/t|gpt|g\s*/\s*t|%|ppm|oz/t)", t, re.I):
        g.append((float(m.group(1)), m.group(2).lower().replace(" ", "")))
    for m in re.finditer(r"(?<![\d.])(\d{1,4}(?:\.\d{1,3})?)\s*(g/t|gpt|g\s*/\s*t|%|ppm|oz/t)"
                         r"[^.\n]{0,30}?cut[- ]?off", t, re.I):
        g.append((float(m.group(1)), m.group(2).lower().replace(" ", "")))
    for m in re.finditer(r"(?:(C\$|US\$|\$|A\$)\s*(\d{1,4}(?:\.\d{1,2})?)\s*/\s*t|"
                         r"(\d{1,4}(?:\.\d{1,2})?)\s*/\s*t\s*(?:NSR|net\s+smelter))"
                         r"[^.\n]{0,30}?cut[- ]?off|cut[- ]?off[^.\n]{0,40}?(C\$|US\$|\$|A\$)\s*(\d{1,4}(?:\.\d{1,2})?)\s*/\s*t",
                         t, re.I):
        cur = m.group(1) or m.group(4) or "$"
        val = m.group(2) or m.group(3) or m.group(5)
        if val:
            v.append((float(val), f"{cur}/t NSR"))
    clean = []
    for val, u in g:
        u = {"gpt": "g/t", "g/t": "g/t", "%": "%", "ppm": "ppm", "oz/t": "oz/t"}.get(u, u)
        if u == "%" and not (0.01 <= val <= 20):
            continue
        if u == "g/t" and not (0.05 <= val <= 20):
            continue
        if u == "ppm" and not (1 <= val <= 20000):
            continue
        if u == "oz/t" and not (0.001 <= val <= 1):
            continue
        clean.append((val, u))
    if clean:
        unit = Counter(u for _v, u in clean).most_common(1)[0][0]
        return sorted(val for val, u in clean if u == unit)[0], unit
    if v:
        unit = Counter(u for _v, u in v).most_common(1)[0][0]
        return sorted(val for val, u in v if u == unit)[0], unit
    return None, None


_EL_WORDS = (("Au", r"\bau\b|gold"), ("Ag", r"\bag\b|silver"), ("Pt", r"\bpt\b|platinum"),
             ("Pd", r"\bpd\b|palladium"), ("Cu", r"\bcu\b|copper"), ("Zn", r"\bzn\b|zinc"),
             ("Pb", r"\bpb\b|\blead\b"), ("Ni", r"\bni\b|nickel"),
             ("U3O8", r"u3o8|uranium"), ("Li2O", r"li2o|lithium"),
             ("Co", r"\bco\b(?!-)|cobalt"), ("Mo", r"\bmo\b|molybdenum"),
             ("Sn", r"\bsn\b|\btin\b"), ("WO3", r"wo3|tungsten"))
# a contained-metal unit decides the grade unit, and the grade unit decides which
# metals are even possible: ounces come from a g/t grade (precious), pounds and
# tonnes of metal come from a per-cent grade (base).
_EL_FAMILY = {"oz": ("Au", "Ag", "Pt", "Pd"),
              "lb": ("Cu", "Zn", "Pb", "Ni", "Co", "Mo", "U3O8", "Sn", "WO3"),
              "t": ("Cu", "Zn", "Pb", "Ni", "Co", "Mo", "Li2O", "U3O8", "Sn", "WO3")}


def grade_unit_for(kind):
    """The grade unit a contained-metal unit implies. ``t`` and ``lb`` of metal are
    arithmetic on a per-cent grade; ``oz`` on grams per tonne."""
    return "g/t" if kind == "oz" else "%"


def _page_element(t, kind=None, grade=None):
    """Which metal a resource row reports — or ``None`` when the page does not say
    clearly.

    Word counts alone mislabel statements: a copper page that mentions gold credits
    reads as gold, and the number then gets compared against MMP's gold model as
    though 1.187 % Cu were 1.187 g/t Au. So the arithmetic leads: the row's own
    contained-metal unit fixes the grade unit (see ``grade_unit_for``) and that
    restricts the candidates to metals reported in it. Within the family the page's
    own words decide, and only by a clear margin — a wrong label is worse than no
    label, because an unlabelled statement is shown as published rather than
    compared.
    """
    low = t.lower()
    fam = _EL_FAMILY.get(kind) if kind else None
    c = Counter()
    for el, pats in _EL_WORDS:
        if fam and el not in fam:
            continue
        n = len(re.findall(pats, low))
        if n:
            c[el] = n
    if not c:
        return None
    top = c.most_common(2)
    e1, n1 = top[0]
    n2 = top[1][1] if len(top) > 1 else 0
    # a precious-metal grade tells gold from silver on its own: a whole resource
    # averaging more than ~15 g/t is silver, below ~5 g/t is not silver
    if kind == "oz" and grade and {"Au", "Ag"} & set(dict(top)):
        if grade >= 15 and c.get("Ag"):
            return "Ag"
        if grade <= 5 and c.get("Au") and c.get("Au") >= 2:
            return "Au"
    if n1 < 2 or n1 < 1.5 * n2:
        return None                      # ambiguous page: say nothing
    return e1


_SENS_CTX = re.compile(r"sensitiv|at (?:various|a range of|different) cut|"
                       r"cut[- ]?off (?:grade )?sensitiv|resource.{0,30}by cut[- ]?off", re.I)


def extract_sensitivity(doc, pages_text):
    """Grade-tonnage tables: what the deposit holds at a range of cut-offs.

    Reports print these, and they are worth more to MMP than the headline statement
    is. A single filed tonnage can only be compared against one point on MMP's own
    grade-tonnage curve; a sensitivity table can be compared against the whole
    curve, which tests the shape of the estimate rather than one number on it. They
    also carry a cut-off by construction, so they are scorable where two thirds of
    filed statements are not.

    The table validates itself: a cut-off series must rise while its tonnage falls.
    Nothing else in a technical report does that, so a monotone run of three or more
    rows is strong evidence the columns were read in the right order."""
    out = []
    for pno, t in enumerate(pages_text):
        if not _SENS_CTX.search(t or ""):
            continue
        _tm, cm = _unit_hints(t)
        tm_hint, _cm2 = _unit_hints(t)
        unit = None
        # flattened: a table header extracts as "Cut-off \n(NSR \nCAD$/t)", and a
        # pattern that cannot cross a newline sees a cut-off with no unit at all
        um = re.search(r"cut[- ]?off[^.]{0,40}?"
                       r"((?:NSR\s*)?(?:CAD?\$|C\$|US\$|A\$|\$)\s*/\s*t|g\s*/\s*t|gpt|%|ppm|oz/t)",
                       re.sub(r"\s+", " ", t or ""), re.I)
        if um:
            u = re.sub(r"\s+", "", um.group(1))
            if "/t" in u and "$" in u:
                cur = "C$" if re.match(r"(?:NSR)?CAD?\$|(?:NSR)?C\$", u, re.I) else (
                    "US$" if "US$" in u.upper() else "$")
                unit = f"{cur}/t NSR"
            else:
                unit = u.lower().replace("gpt", "g/t")
        try:
            lines = page_lines(doc[pno])
        except Exception:
            continue
        rows = []
        for ln in lines:
            nums = [num(c["t"]) for c in ln["cells"] if _is_num(c["t"])]
            nums = [n for n in nums if n is not None]
            if len(nums) < 2:
                continue
            rows.append(nums)
        series = _monotone_series(rows, tm_hint)
        if len(series) >= 3:
            out.append({"page": pno + 1, "cutoff_unit": unit,
                        "rows": [{"cutoff": c, "tonnes": round(tn), "grade": g}
                                 for c, tn, g in series]})
    # the fullest table on any page wins; summaries repeat shortened versions
    out.sort(key=lambda x: -len(x["rows"]))
    return out[:1]


def _monotone_series(rows, tm_hint):
    """Find the reading of a table that behaves like a grade-tonnage curve.

    What identifies one is the RELATIONSHIP, not the row order: tonnage falls as the
    cut-off rises, whichever way round the table is printed. P&E print Murray Brook's
    from the highest cut-off down (100, 50, 45 ... 15) with tonnage climbing beside
    it, so a reader that insists on ascending cut-offs finds nothing at all.

    A monotone run alone is still not enough — a column of rounded tonnages marches
    just as obediently — so the series must also SPAN something: the cut-off roughly
    half again across it and the tonnage down by a quarter.
    """
    best = []
    for ci in range(0, 2):
        for ti in range(ci + 1, min(ci + 4, 8)):
            for gi in range(ci + 1, min(ci + 5, 9)):
                if gi == ti:
                    continue
                run = []
                for nums in rows:
                    if len(nums) <= max(ci, ti, gi):
                        continue
                    c, tn, g = nums[ci], nums[ti], nums[gi]
                    if None in (c, tn, g):
                        continue
                    if not (0 < c < 1e4 and tn > 0 and 0 < g < 1e4):
                        continue
                    run.append((float(c), float(tn), float(g)))
                ok = []
                for row in run:
                    if len(ok) < 2:
                        ok.append(row)
                        if len(ok) == 2 and not _opposed(ok[0], ok[1]):
                            ok = [ok[1]]
                        continue
                    if _opposed(ok[-1], row) and _same_way(ok[-2], ok[-1], row):
                        ok.append(row)
                    else:
                        if len(ok) > len(best):
                            best = ok
                        ok = [row]
                if len(ok) > len(best):
                    best = ok
    if len(best) < 3:
        return []
    if best[0][0] > best[-1][0]:          # printed high cut-off first
        best = best[::-1]
    cuts = [b[0] for b in best]
    tons = [b[1] for b in best]
    if cuts[-1] / max(cuts[0], 1e-9) < 1.5:
        return []
    if tons[-1] / max(tons[0], 1e-9) > 0.8:
        return []
    if not all(0.005 <= c <= 500 for c in cuts):
        return []
    mult = tm_hint or (1e3 if max(tons) < 5e4 else 1.0)
    return [(c, tn * mult, g) for c, tn, g in best]


def _opposed(a, b):
    """Cut-off and tonnage must move in opposite directions between two rows."""
    dc, dt = b[0] - a[0], b[1] - a[1]
    return dc != 0 and dt != 0 and (dc > 0) != (dt > 0)


def _same_way(a, b, c):
    """And the whole run must keep going the same way."""
    return ((b[0] - a[0]) > 0) == ((c[0] - b[0]) > 0)


def headline_resource(rows):
    """Pick the CURRENT resource statement: latest effective date first, then the
    most complete category set / statement-like page, earliest page (summary)."""
    if not rows:
        return None
    by_page = defaultdict(list)
    for r in rows:
        if r["hist"] or r["reserve"]:
            continue
        # three tables on one page are three statements, not one jumbled one: a
        # page of silver resources by area read as a single table mixed the
        # underground tonnage with the open-pit grades.
        by_page[(r["page"], r["cunit"], r.get("tbl"))].append(r)
    if not by_page:
        return None

    def score(item):
        (pg, _, _tb), rs = item
        cats = {r["cat"] for r in rs}
        named = len(cats & {"Measured", "Indicated", "Inferred", "M+I"})
        tot = any(r["total"] for r in rs)
        # an identified metal and more rows break ties: a page can carry both a
        # per-cent base-metal total and the gold statement, and the one MMP can
        # name is the one worth publishing
        ident = 1 if any(r.get("el") for r in rs) else 0
        return ((rs[0].get("year") or 0), named + (1 if tot else 0) + rs[0].get("pscore", 0),
                ident, len(rs), -pg)
    (page, cunit, _tb), rs = max(by_page.items(), key=score)
    # within a table, the deposit's own metal rather than every column of it
    if any(r.get("primary") for r in rs):
        rs = [r for r in rs if r.get("primary")]
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
    if not _cats_consistent(cats, tot):
        return None                      # a mis-read table (M+I ≠ M + I) is worse than none
    els = Counter(r.get("el") for r in rs if r.get("el"))
    cog = next((r.get("cutoff") for r in rs if r.get("cutoff") is not None), None)
    cogu = next((r.get("cutoff_unit") for r in rs if r.get("cutoff") is not None), None)
    gu = grade_unit_for(cunit)
    if cog is not None and cogu and not str(cogu).endswith("NSR"):
        # a cut-off only belongs to this statement if it is quoted in the statement's
        # own grade unit; a g/t cut-off beside a per-cent resource came from a
        # different table on the same page, and showing it implies a reproducibility
        # MMP cannot deliver
        ok = {"%": {"%"}, "g/t": {"g/t", "ppm", "oz/t"}}.get(gu, set())
        if cogu not in ok:
            cog, cogu = None, None
    el = els.most_common(1)[0][0] if els else None
    if el and els[el] < 0.6 * sum(els.values()):
        el = None                        # the rows disagree about the metal
    return {"page": page, "contained_unit": cunit, "categories": cats, "year": rs[0].get("year"),
            "by_header": bool(rs[0].get("byhdr")),
            "cutoff": cog, "cutoff_unit": cogu, "grade_unit": gu,
            "scope": next((r.get("scope") for r in rs if r.get("scope")), None),
            "element": el, **tot}



def _cats_consistent(cats, tot):
    """Reject a statement whose own rows disagree: M+I must be about M plus I, and no
    category may exceed the total. Column drift in a PDF table produces rows like
    "Indicated 102 Mt / M+I 60 kt", which are worse than publishing nothing."""
    def t(k):
        v = (cats or {}).get(k) or {}
        return float(v.get("tonnes") or 0)
    m, i, mi = t("Measured"), t("Indicated"), t("M+I")
    if mi and (m or i):
        s = m + i
        if s and not (0.75 * s <= mi <= 1.25 * s):
            # "M&I 18,7230" against Measured 5,470 and Indicated 13,250 is a digit
            # dropped into the wrong column. Where measured and indicated are
            # themselves present, they ARE the statement and the combined row adds
            # nothing, so discard the row rather than the whole table.
            if m and i:
                cats.pop("M+I", None)
                return _cats_consistent(cats, tot)
            return False
    total = float((tot or {}).get("tonnes") or 0)
    if total:
        for k in ("Measured", "Indicated", "M+I", "Inferred"):
            if t(k) > total * 1.25:
                return False
    for k, v in (cats or {}).items():
        g, c, tn = v.get("grade"), v.get("contained"), v.get("tonnes")
        if g and c and tn:                # tonnes x grade should land near contained
            # the divisor per contained unit: grams per troy ounce for ounces, per
            # cent for tonnes of metal, and for POUNDS the per-cent divided by
            # pounds per tonne — dividing by 2204.62/100 instead of by its
            # reciprocal rejected every base-metal statement quoted in pounds,
            # which is most of them
            for f in (31.1034768, 100.0, 100.0 / 2204.62262):
                if 0.5 <= (tn * g / f) / c <= 2.0:
                    break
            else:
                return False
    return True


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
_DENS = re.compile(r"(?:bulk\s*)?(?:density|specific\s*gravity|\bSG\b|tonnage\s*factor)[^.\n]{0,60}?"
                   r"(?<![\d.])(\d\.\d{1,3})(?!\d)\s*(?:t/m3|t/m³|g/cm3|g/cm³|g/cc|tonnes?/m)?", re.I)


# ------------------------------------------------- modelling parameters
# A technical report publishes the estimation recipe it used. We lift the parts
# the MMP modeller can honour, so a project's starting parameters are the ones
# its most recent report actually used (and are then adjustable per project).
_COMP = re.compile(r"(?:composite[sd]?|compositing|composit\w*)[^.\n]{0,80}?(\d{1,2}(?:\.\d+)?)\s*(m|metre|meter|ft|foot|feet)\b"
                   r"|(\d{1,2}(?:\.\d+)?)\s*(m|metre|meter|ft|foot|feet)\s*(?:down[- ]?hole\s*)?composite", re.I)
_SEARCH = re.compile(r"(?:search\s*(?:ellipse|ellipsoid|radius|radii|range|distance|neighbourhood|neighborhood)|"
                     r"first\s*(?:search\s*)?pass|range\s*of\s*influence)[^.\n]{0,120}", re.I)
_CAP = re.compile(r"(?:cap(?:ped|ping)?|top[- ]?cut|grade\s*cap)[^.\n]{0,90}?(\d{1,4}(?:\.\d+)?)\s*(g/t|gpt|g\s*/\s*t|%|ppm)", re.I)
_COG = re.compile(r"cut[- ]?off\s*(?:grade)?[^.\n]{0,60}?(\d{1,3}(?:\.\d+)?)\s*(g/t|gpt|%|ppm|\$)", re.I)
_METH = (("idw", 3, r"inverse\s*distance\s*(?:cub|to\s*the\s*(?:third|3rd)\s*power)|\bID3\b|IDW3|inverse\s*distance\s*weighting\s*cub"),
         ("idw", 2, r"inverse\s*distance\s*(?:squar|to\s*the\s*(?:second|2nd)\s*power)|\bID2\b|IDW2|inverse\s*distance\s*square"),
         ("idw", 2, r"ordinary\s*kriging|\bOK\b\s*(?:estimat|interpolat)|kriged|kriging"),
         ("nn", None, r"nearest\s*neighbour|nearest\s*neighbor|\bNN\b\s*estimat"),
         ("idw", None, r"inverse\s*distance"))


def _ft(v, unit):
    return v * 0.3048 if (unit or "").lower().startswith(("ft", "foot", "feet")) else v


_MP_ANY = re.compile(r"block|densit|specific gravity|composit|search|cap|cut-?off|"
                     r"krig|inverse distance|nearest neighbour|nearest neighbor|"
                     r"t/m|g/cm|tonnes? per cubic", re.I)


def extract_model_params(pages_text):
    """The estimation parameters the report states: parent block size, density,
    composite length, search radius, grade cap, reporting cut-off, estimator."""
    votes = Counter(); dens = Counter(); comp = Counter(); rad = []
    caps = []; cogs = []; meth = Counter()
    for t in pages_text:
        # Nine patterns over every page of an eight-hundred-page report is most of
        # the cost of reading one, and almost every page says none of these words.
        # One cheap test first leaves the nine to the pages that might answer.
        if not _MP_ANY.search(t):
            continue
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
            if not 2.0 <= v <= 5.0:
                continue
            seg = m.group(0)
            if re.search(r"var(y|ies|ied)|rang(e|ed|es|ing)|between|minimum|maximum", seg, re.I):
                continue                      # "varied from 2.04 to 6.86" is a range
            win = seg + t[m.end(): m.end() + 60]
            w = 3 if re.search(r"applied|assigned|used|average|block[- ]model|assumed", win, re.I) else 1
            dens[round(v, 2)] += w
        for m in _COMP.finditer(t):
            v = _ft(float(m.group(1) or m.group(3)), m.group(2) or m.group(4))
            if 0.25 <= v <= 20:
                comp[round(v, 2)] += 1
        for m in _SEARCH.finditer(t):
            win = m.group(0)
            b = _BLOCK3.search(win)
            vals = []
            if b:
                vals = [_ft(float(b.group(i)), b.group(4)) for i in (1, 2)]
            else:
                vals = [_ft(float(a), u) for a, u in
                        re.findall(r"(\d{2,4}(?:\.\d+)?)\s*(m|metres|meters|ft|feet)\b", win, re.I)]
            for v in vals:
                if 20 <= v <= 400:
                    rad.append(v)
        for m in _CAP.finditer(t):
            v, u = float(m.group(1)), m.group(2).lower()
            if u.startswith("%") and 0.05 <= v <= 40:
                caps.append(v)
            elif u.startswith("ppm") and 0.1 <= v <= 5000:
                caps.append(v)
            elif 0.5 <= v <= 200:
                caps.append(v)
        for m in _COG.finditer(t):
            v, u = float(m.group(1)), m.group(2).lower()
            if u == "$":
                continue
            if 0.01 <= v <= 20:
                cogs.append(v)
        for name, pw, rx in _METH:
            n = len(re.findall(rx, t, re.I))
            if n:
                meth[(name, pw)] += n
                break
    out = {}
    if votes:
        out["block"] = list(votes.most_common(1)[0][0])
    if dens:
        firm = Counter({v: n for v, n in dens.items() if v >= 2.3})
        out["density"] = (firm or dens).most_common(1)[0][0]
    if comp:
        out["composite_m"] = comp.most_common(1)[0][0]
    if rad:
        rad.sort()
        out["radius_m"] = round(rad[len(rad) // 2], 1)       # median stated range
    if caps:
        out["top_cut"] = round(max(caps), 2)                 # least aggressive stated cap
    cogs = [v for v in cogs if 0.05 <= v <= 15 and
            not (out.get("density") and abs(v - out["density"]) < 0.02)]
    if cogs:
        cogs.sort()
        out["default_cutoff"] = round(cogs[len(cogs) // 2], 3)   # median stated cut-off
    if meth:
        (name, pw), _n = meth.most_common(1)[0]
        out["method"] = name
        if pw:
            out["power"] = float(pw)
    # the net-smelter-return formula, where the report states one. For a deposit
    # carrying more than one metal of value this is the cut-off that the published
    # tonnage was actually reported at, so without it MMP cannot reproduce the
    # statement no matter how good the block model is.
    try:
        from minemodelingpro import nsr as _nsr
        f = _nsr.stated(pages_text)
        if f:
            out["nsr"] = f
    except Exception:
        pass
    # what the rock is and what must be done to it: two deposits of the same grade
    # are not the same asset if one heap leaches and the other needs an autoclave
    try:
        from minemodelingpro import metallurgy as _met
        mm = _met.extract(pages_text)
        if mm:
            out["metallurgy"] = mm
    except Exception:
        pass
    # what the report calls the deposit — the strongest evidence there is for how
    # it should be modelled, and the report usually says it outright
    try:
        from minemodelingpro import deposit_style as _dsty
        k, _why = _dsty.from_text(pages_text)
        if k:
            out["deposit_style"] = k
    except Exception:
        pass
    # and the dollar cut-off it was reported at — a value cut-off is not a grade,
    # so it has its own key rather than being squeezed into default_cutoff
    vals = []
    for t in pages_text:
        v, u = _page_cutoff(t)
        if v is not None and str(u or "").endswith("NSR"):
            vals.append(float(v))
    if vals:
        vals.sort()
        out["value_cutoff"] = round(vals[len(vals) // 2], 2)
    return out


def extract_block_params(pages_text):
    """Back-compatible shim: (block, density)."""
    P = extract_model_params(pages_text)
    return P.get("block"), P.get("density")


_MONTHS = ("january february march april may june july august september october "
           "november december").split()
_EFF = re.compile(r"effective\s*(?:date|as\s*of)?[^.\n]{0,40}?"
                  r"(?:(\d{1,2})\s+)?(" + "|".join(_MONTHS) + r"|" +
                  "|".join(m[:3] for m in _MONTHS) + r")\.?,?\s*(?:(\d{1,2})\s*,?\s*)?((?:19|20)\d\d)", re.I)
_EFF_ISO = re.compile(r"effective\s*date[^.\n]{0,40}?((?:19|20)\d\d)-(\d{2})-(\d{2})", re.I)


def effective_date(pages_text):
    """The report's stated effective date (latest one stated), as YYYY-MM-DD."""
    found = []
    for t in pages_text[:80]:
        for m in _EFF_ISO.finditer(t):
            found.append("%s-%s-%s" % m.groups())
        for m in _EFF.finditer(t):
            d1, mon, d2, yr = m.groups()
            mon = mon.lower()[:3]
            try:
                mi = [x[:3] for x in _MONTHS].index(mon) + 1
            except ValueError:
                continue
            day = int(d1 or d2 or 1)
            if not 1 <= day <= 31:
                day = 1
            found.append("%04d-%02d-%02d" % (int(yr), mi, day))
    found = [d for d in found if "1990-01-01" <= d <= "2035-12-31"]
    return max(found) if found else None


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



_CO_SUFFIX = (r"(?:Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|LLC|PLC|plc|"
              r"Resources|Resource\s+Corp|Mining|Mines|Minerals|Metals|Gold|Silver|Copper|"
              r"Exploration|Explorations|Ventures|Holdings|Energy|Lithium|Graphite|Uranium)")
_CO_PREP = re.compile(r"(?:prepared\s+for|report\s+prepared\s+for|prepared\s+on\s+behalf\s+of|"
                      r"submitted\s+to|client)\s*:?\s*\n?\s*([A-Z][\w'’.,&\- ]{2,60}?\s+" + _CO_SUFFIX + r")",
                      re.I)
_CO_ANY = re.compile(r"\b([A-Z][\w'’.&\-]*(?:\s+[A-Z][\w'’.&\-]*){0,4}\s+" + _CO_SUFFIX + r")\b")
_CO_BAD = re.compile(r"^(?:the|this|a|an|technical|report|mineral|ni\s*43-101|qualified|"
                     r"srk|slr|amc|wsp|micon|ausenco|rpa|agp|p ?& ?e|moose mountain|hatch|wood|golder|"
                     r"stantec|bba|gms|kappes|lycopodium|mining plus|erm|goldspot|innovexplo|nordmin|"
                     r"snowden|optiro|entech|dra|fluor|jds|cube|tetra ?tech|sgs|m3|global resource)\b", re.I)


def title_company(pages_text):
    """The issuer a technical report was prepared for — SEDAR ledgers often leave
    the company blank, but the cover page always names it ("Prepared for ...")."""
    blob = re.sub(r"[ \t]+", " ", "\n".join(pages_text[:4]))
    for m in _CO_PREP.finditer(blob):
        nm = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
        if 4 <= len(nm) <= 60 and not _CO_BAD.match(nm):
            return nm
    cnt = Counter()
    for m in _CO_ANY.finditer(blob[:4000]):
        nm = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
        if 4 <= len(nm) <= 60 and not _CO_BAD.match(nm):
            cnt[nm] += 1
    return cnt.most_common(1)[0][0] if cnt else None


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
def extract_report_text(pages, meta=None):
    """Everything extract_report finds, from page text alone.

    Same extractors, same validation; only the source of the lines differs."""
    doc = TextDoc(pages)
    return _extract(doc, list(pages), meta or {}, pages_n=len(pages))


def extract_report(pdf_path, meta=None):
    import pymupdf
    doc = pymupdf.open(pdf_path)
    pages_text = []
    for p in doc:
        try:
            pages_text.append(p.get_text())
        except Exception:
            pages_text.append("")
    return _extract(doc, pages_text, meta, pages_n=len(doc))


def _with_attitude(mp, att):
    """Carry a stated dip into the modelling parameters.

    `dip` and `dip_direction` already orient the wireframe and stretch the search
    along strike and down dip (`model3d.frame_from_trend`) — they were simply
    never filled from a report, so a vein took its orientation from the principal
    axes of the DRILL PATTERN rather than from the rock. A qualitative reading
    ("steeply dipping" -> 70) is carried too, flagged as such, because an
    approximate attitude from the geologist beats one measured off the drilling.
    """
    mp = dict(mp or {})
    if not att:
        return mp
    d = (att.get("dip") or {}).get("median")
    if d is not None and 0.0 < float(d) <= 90.0:
        # only a NUMBER goes into the parameters; "steeply dipping" is kept on
        # `attitude` where a reader can see it was a word, not a measurement
        if not att.get("dip_is_qualitative"):
            mp.setdefault("dip", float(d))
    dd = (att.get("dip_direction") or {}).get("median")
    if dd is not None and 0.0 <= float(dd) <= 360.0:
        mp.setdefault("dip_direction", float(dd))
    return mp


def _extract(doc, pages_text, meta=None, pages_n=None):
    meta = dict(meta or {})
    surveys = []
    collars, intervals = extract_tables(doc, surveys=surveys)
    surveys = _clean_surveys(surveys)
    crs, center, tcenter = georeference(collars, pages_text, meta.get("jurisdiction"),
                                        trust_jurisdiction=str(meta.get("id", "")).startswith("ni43101:"))
    res_rows = extract_resources(doc, pages_text)
    head = headline_resource(res_rows)
    mp = extract_model_params(pages_text)
    block, density = mp.get("block"), mp.get("density")
    # the body's shape, as the report describes it in prose: the one part of the
    # qualified person's wireframe that cannot be recovered from the published
    # assays, and the only independent check on whether a model reaches the
    # deposit at all or a corner of it
    try:
        from minemodelingpro import extent as EXT
        ext = EXT.stated(pages_text) or None
        # the body's ORIENTATION, which `stated` does not answer: its "dip" is a
        # down-dip extent in metres, and a dip angle is a different quantity
        att = EXT.attitude(pages_text) or None
    except Exception:
        ext = att = None
    eff = effective_date(pages_text)
    # link collars <-> intervals via normalised hole ids
    ckeys = {holeid.key(c["hole"]) for c in collars}
    ikeys = {holeid.key(r["hole"]) for r in intervals}
    commodity = meta.get("commodity")
    if not commodity and intervals:
        commodity = Counter(r["el"] for r in intervals).most_common(1)[0][0]
    out = {
        **meta,
        "title_project": title_project(pages_text),
        "cover_company": title_company(pages_text),
        "extractor": EXTRACTOR,
        "extracted": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "pages": pages_n if pages_n is not None else len(doc),
        "crs": crs, "center": center or tcenter, "text_center": tcenter,
        "block_size": block, "density": density,
        "sensitivity": (extract_sensitivity(doc, pages_text) or [None])[0],
        "extent": ext, "attitude": att,
        "model_params": _with_attitude(mp, att) or None, "effective_date": eff,
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
