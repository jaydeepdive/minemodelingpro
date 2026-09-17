"""NI 43-101 technical-report extractor for MineModelingPro.

A 43-101 carries what deposit modelling needs and government DBs lack: full
downhole assays, the resource/reserve estimate, and — crucially for MMP learning
to model — the METHODOLOGY (how the deposit was actually estimated: method,
block size, capping, density, cut-off, search, software). This module pulls all
of it from the PDF into the sharded MMP store under a per-report source id.

Reports use borderless tables, so extraction is text/line based (pdfplumber's
line-table detection misses them). Three outputs:
  * deposit_model  — resource/reserve rows (category, tonnes, grade, cut-off)
  * model_method   — estimation methodology + the Section-14 narrative (training)
  * assays/collars — from drilling appendices (staged; see extract_intervals)

Run:  python -m minemodelingpro.pdf_reports <pdf_url_or_path> [project] [commodity] [jurisdiction]
"""
import os
import re
import sys
import json
import glob
import hashlib
import datetime
import urllib.request

from minemodelingpro import store

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"}
_NUM = re.compile(r"\d[\d,]*\.?\d*")
_CAT = re.compile(r"\b(measured\s*(?:\+|and|&)\s*indicated|measured|indicated|inferred|"
                  r"proven\s*(?:\+|and|&)\s*probable|proven|probable|total\s+mineral\s+resource|"
                  r"total\s+resource|total\s+reserve)\b", re.I)

# methodology signals
_METHOD = re.compile(r"\b(ordinary kriging|simple kriging|multiple indicator kriging|"
                     r"indicator kriging|inverse distance (?:squared|cubed|weighting|to the \w+ power)|"
                     r"nearest neighbou?r|ID2|ID3|ID\^?2|MIK|kriging)\b", re.I)
_SOFTWARE = re.compile(r"\b(Leapfrog|Seequent|Vulcan|Datamine|GEMS|GEOVIA|Surpac|Micromine|"
                       r"Isatis|MineSight|Hexagon|Deswik|Snowden Supervisor|Supervisor)\b", re.I)
_BLOCK = re.compile(r"(?:block (?:model )?(?:size|dimensions?)[^.]{0,60}?|parent block[^.]{0,40}?)"
                    r"(\d+(?:\.\d+)?\s*m?\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m?\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m?)", re.I)
_BLOCK2 = re.compile(r"(\d+(?:\.\d+)?\s*m\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m\s*(?:x|×|by)\s*\d+(?:\.\d+)?\s*m)", re.I)
_DENSITY = re.compile(r"(?:bulk )?(?:density|specific gravity|SG)[^.]{0,50}?(\d\.\d{1,3})\s*(?:t/m3|t/m³|tonnes?/m3|g/cm3|g/cc)?", re.I)
_CAP = re.compile(r"(?:capp(?:ed|ing)|top[- ]?cut|grade cut)[^.]{0,110}", re.I)
_CUTOFF = re.compile(r"cut[- ]?off[^.]{0,90}", re.I)
_COMPOSITE = re.compile(r"composit(?:e|ed|ing)[^.]{0,80}", re.I)
_SEARCH = re.compile(r"search (?:ellipse|radius|distance|neighbou?rhood)[^.]{0,120}", re.I)
_CLASS = re.compile(r"(?:classif(?:ied|ication))[^.]{0,140}", re.I)
_METH_PAGE = re.compile(r"kriging|inverse distance|block model|specific gravity|bulk density|"
                        r"search ellipse|composit|capp|cut-?off|variogram|estimation domain|wireframe", re.I)


# bump when the extractor changes so --refresh re-processes reports once (and
# only once) under the new engine, staying resumable across CI runs.
EXTRACTOR_VERSION = "8"          # v8 = robust AISC/cash-cost (table layouts) + memory-safe large-PDF text (PyMuPDF)


# Large reports (100+ MB, hundreds of pages) blow pdfplumber's memory (it retains
# parsed objects for every page); PyMuPDF streams page text at a fraction of the
# RAM. Use pdfplumber below the threshold (unchanged behaviour for the reports
# already extracted) and PyMuPDF above it so the big economic studies come in too.
_BIG_PDF_BYTES = 40 * 1024 * 1024


def _pages_text_fitz(path):
    """Page text via PyMuPDF, but reconstructed into visual ROWS from word
    positions rather than raw get_text('text'). Raw mode emits borderless-table
    cells one-per-line, which breaks resource-row detection (it needs a whole
    'category  tonnes  grade  ...' row on one line). Grouping words by y-band and
    ordering by x rebuilds those rows (as pdfplumber does), while still leaving
    stacked label/unit/value cells on separate lines for the cost regexes."""
    import pymupdf                      # PyMuPDF (memory-safe, page-streamed)
    from collections import defaultdict
    out = []
    doc = pymupdf.open(path)
    try:
        for pg in doc:
            words = pg.get_text("words")   # (x0, y0, x1, y1, word, block, line, wordno)
            if not words:
                out.append(pg.get_text("text") or "")
                continue
            rows = defaultdict(list)
            for w in words:
                rows[round(w[1] / 3.0)].append((w[0], w[4]))   # ~3pt y-band
            lines = [" ".join(t for _, t in sorted(cells))
                     for _, cells in sorted(rows.items())]
            out.append("\n".join(lines))
    finally:
        doc.close()
    return out


def _pages_text_plumber(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return [pg.extract_text() or "" for pg in pdf.pages]


# --------------------------------------------------------------- OCR (scanned PDFs)
# Some technical reports are scanned images with NO text layer (PyMuPDF returns
# empty). Tesseract turns them into text so they flow through the SAME extraction
# as every other report. OCR is slow (~1s/page), so it is cached per page and
# resumable: run ocr_pages_cached repeatedly (time-budgeted) until it reports done,
# after which _pages_text transparently serves the cached text for that source.
OCR_DIR = os.path.join(_ROOT, "data", "keep", "ocr_cache") if "_ROOT" in dir() else \
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "data", "keep", "ocr_cache")


def _ocr_safe(sid):
    return str(sid).replace(":", "__").replace("/", "_")


def _ocr_page_text(png_bytes):
    """OCR one rendered page image into reading-order line text (Tesseract groups
    words into block/paragraph/line, which reconstructs table rows for us)."""
    import io
    import pytesseract
    from PIL import Image
    d = pytesseract.image_to_data(Image.open(io.BytesIO(png_bytes)),
                                  output_type=pytesseract.Output.DICT)
    lines = {}
    for i, txt in enumerate(d["text"]):
        if not txt or not txt.strip() or str(d["conf"][i]) in ("-1", "-1.0"):
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines.setdefault(key, (d["top"][i], []))[1].append((d["left"][i], txt))
    rows = [(top, " ".join(t for _, t in sorted(cells)))
            for _, (top, cells) in lines.items()]
    return "\n".join(t for _, t in sorted(rows))


def ocr_pages_cached(path, sid, dpi=200, max_seconds=None):
    """Resumable OCR of `path` into per-page text cache. Returns
    (done, pages_cached, n_pages). When done, also writes the assembled
    <sid>.json so _pages_text can serve it."""
    import time
    import pymupdf
    safe = _ocr_safe(sid)
    cdir = os.path.join(OCR_DIR, safe)
    os.makedirs(cdir, exist_ok=True)
    doc = pymupdf.open(path)
    n = doc.page_count
    t0 = time.time()
    try:
        for i in range(n):
            cf = os.path.join(cdir, f"p{i:04d}.txt")
            if os.path.exists(cf):
                continue
            if max_seconds and time.time() - t0 > max_seconds:
                break
            pix = doc[i].get_pixmap(dpi=dpi)
            txt = _ocr_page_text(pix.tobytes("png"))
            with open(cf, "w") as fh:
                fh.write(txt)
    finally:
        doc.close()
    done_n = len(glob.glob(os.path.join(cdir, "p*.txt")))
    finished = done_n >= n
    if finished:
        pages = [open(os.path.join(cdir, f"p{i:04d}.txt")).read() for i in range(n)]
        json.dump(pages, open(os.path.join(OCR_DIR, safe + ".json"), "w"))
    return finished, done_n, n


def _load_ocr_cache(sid):
    if not sid:
        return None
    f = os.path.join(OCR_DIR, _ocr_safe(sid) + ".json")
    if os.path.exists(f):
        try:
            return json.load(open(f))
        except Exception:
            return None
    return None


def _pages_text(path, ocr_sid=None):
    """Page text as a list of strings. PyMuPDF is the primary engine: an order of
    magnitude faster, low memory (a 118 MB report no longer OOMs), and — key — it
    never HANGS on the vector-heavy reports that stall pdfplumber. pdfplumber is a
    fallback ONLY when PyMuPDF raises: a PDF whose PyMuPDF text is empty is an
    image/scanned PDF with no text layer, which pdfplumber can't read either (and
    on which it can hang for minutes). When a scanned PDF has a prebuilt OCR cache
    (see ocr_pages_cached) we serve that instead, so scanned reports extract just
    like every other one; otherwise the empty result is recorded as image-only."""
    cached = _load_ocr_cache(ocr_sid)
    if cached is not None:
        return cached
    try:
        return _pages_text_fitz(path)
    except Exception as e:
        print(f"[43-101] PyMuPDF text failed ({str(e)[:60]}); trying pdfplumber")
    try:
        return _pages_text_plumber(path)
    except Exception as e:
        print(f"[43-101] pdfplumber text failed ({str(e)[:60]})")
        return []


def _rid(url):
    return "ni43101:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def fetch_pdf(url, cache_dir="/tmp/mmp_reports"):
    if os.path.exists(url):
        return url
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers=_UA)
        data = urllib.request.urlopen(req, timeout=180).read()
        open(path, "wb").write(data)
    return path


def _first(rx, text):
    m = rx.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip()[:200] if m else None


def extract_resources(pages_text):
    """Resource/reserve rows from category-bearing numeric lines. Column order
    varies per report, so we keep the full line verbatim (note) and pull the
    category + tonnage; downstream parsing can refine per-report."""
    rows, seen = [], set()
    for i, t in enumerate(pages_text):
        for ln in (t or "").split("\n"):
            low = ln.lower()
            if not _CAT.search(ln):
                continue
            nums = _NUM.findall(ln)
            if len(nums) < 3:
                continue
            if any(w in low for w in ("figure", "table of", "see section", "...", "…")):
                continue
            cat = re.sub(r"\s+", " ", _CAT.search(ln).group(0)).strip().title()
            key = (cat, ln.strip()[:60])
            if key in seen:
                continue
            seen.add(key)
            def _f(x):
                try:
                    return float(x.replace(",", ""))
                except ValueError:
                    return None
            tonnes = _f(nums[0])
            rows.append({"category": cat, "tonnes": tonnes, "note": re.sub(r"\s+", " ", ln).strip()[:300],
                         "page": i + 1})
    return rows


def extract_methodology(pages_text):
    """Gather the resource-estimation methodology: the narrative pages + the
    high-signal parameters (method, software, block size, density, capping,
    cut-off, compositing, search, classification)."""
    meth_pages = [t for t in pages_text if t and len(_METH_PAGE.findall(t)) >= 3]
    blob = "\n".join(meth_pages)
    if not blob:
        return None
    block = _first(_BLOCK, blob) or _first(_BLOCK2, blob)
    return {
        "estimation_method": _first(_METHOD, blob),
        "software": _first(_SOFTWARE, blob),
        "block_size": block,
        "density": _first(_DENSITY, blob),
        "capping": _first(_CAP, blob),
        "cutoff": _first(_CUTOFF, blob),
        "compositing": _first(_COMPOSITE, blob),
        "search_params": _first(_SEARCH, blob),
        "classification": _first(_CLASS, blob),
        # keep a bounded narrative excerpt for training / RAG
        "method_text": re.sub(r"\s+", " ", blob)[:12000],
        "n_method_pages": len(meth_pages),
    }


# --------------------------------------------------------- appendix drill tables
# Full collar + assay tables live in the report appendices as BORDERLESS tables.
# Camelot's stream flavour reconstructs them from text alignment (pdfplumber's
# text strategy over-fragments columns). We pre-scan text for candidate pages so
# Camelot only parses the table pages (it is slow), then map columns by header.
_ELEM = {"au": ("Au", "g/t"), "gold": ("Au", "g/t"), "ag": ("Ag", "g/t"), "silver": ("Ag", "g/t"),
         "cu": ("Cu", "%"), "copper": ("Cu", "%"), "pb": ("Pb", "%"), "lead": ("Pb", "%"),
         "zn": ("Zn", "%"), "zinc": ("Zn", "%"), "ni": ("Ni", "%"), "nickel": ("Ni", "%"),
         "co": ("Co", "%"), "mo": ("Mo", "%"), "moly": ("Mo", "%"), "sn": ("Sn", "%"),
         "w": ("W", "%"), "wo3": ("WO3", "%"), "u3o8": ("U3O8", "%"), "u": ("U", "%"),
         "li2o": ("Li2O", "%"), "li": ("Li", "%"), "sb": ("Sb", "%"), "v2o5": ("V2O5", "%"),
         "fe": ("Fe", "%"), "mn": ("Mn", "%"), "cr2o3": ("Cr2O3", "%"), "pt": ("Pt", "g/t"),
         "pd": ("Pd", "g/t"), "aueq": ("AuEq", "g/t"), "ageq": ("AgEq", "g/t"),
         "cueq": ("CuEq", "%"), "reo": ("REO", "%"), "treo": ("TREO", "%")}
_COL_C = re.compile(r"easting|northing|utm[_ ]?[en]\b|azimuth|\bdip\b|\bcollar\b", re.I)
_COL_A = re.compile(r"\bfrom\b|\bto\s*\(|\binterval\b|\bassay", re.I)


def _num_cell(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("−", "-").strip()
    m = re.match(r"-?\d+\.?\d*", s.lstrip("~<>= "))
    try:
        return float(m.group(0)) if m else None
    except ValueError:
        return None


def _candidate_pages(pages_text):
    """1-indexed pages likely holding collar or assay tables (strict, to skip prose)."""
    coll, assay = [], []
    for i, t in enumerate(pages_text):
        low = (t or "").lower()
        if "easting" in low and "northing" in low and len(re.findall(r"\b\d{5,7}\b", t or "")) >= 6:
            coll.append(i + 1)
        if re.search(r"\bfrom\b", low) and re.search(r"\bto\b", low) and \
           re.search(r"\b(au|ag|cu|pb|zn|g/t|grade)\b", low) and len(re.findall(r"\d+\.\d", t or "")) >= 8:
            assay.append(i + 1)
    return coll, assay


def _merge_header(rows, max_head=4):
    """Combine the leading header rows (until the first mostly-numeric row) into a
    per-column header string. Returns (col_headers, data_start_index)."""
    data_start = 0
    for idx, r in enumerate(rows[:max_head + 1]):
        nums = sum(1 for c in r if _num_cell(c) is not None)
        cells = sum(1 for c in r if str(c).strip())
        if cells and nums >= max(2, cells // 2):
            data_start = idx
            break
    else:
        data_start = min(max_head, len(rows) - 1)
    if data_start == 0:
        data_start = 1
    ncol = max(len(r) for r in rows) if rows else 0
    # header rows only — but skip "title" rows (a single non-empty cell spanning
    # the table, e.g. "Table 9-4 ... Drillholes") so a title can't contaminate a
    # column header (the classic "drillholes" -> false hole-id match).
    hrows = [r for r in range(data_start)
             if sum(1 for c in rows[r] if str(c).strip()) >= 2]
    if not hrows:
        hrows = list(range(data_start))
    heads = []
    for c in range(ncol):
        parts = [str(rows[r][c]).strip() for r in hrows
                 if c < len(rows[r]) and str(rows[r][c]).strip()]
        heads.append(" ".join(parts).lower())
    return heads, data_start


def _map_columns(heads):
    m = {"elements": []}
    for i, h in enumerate(heads):
        if not h:
            continue
        if re.search(r"hole.*(id|no|number|name)|^hole$|ddh|bhid|drill ?hole|hole id", h):
            m.setdefault("hole", i)
        elif "easting" in h or re.search(r"utm[_ ]?e\b|^east", h):
            m.setdefault("easting", i)
        elif "northing" in h or re.search(r"utm[_ ]?n\b|^north", h):
            m.setdefault("northing", i)
        elif re.search(r"elev|^rl\b|elevation", h):
            m.setdefault("elev", i)
        elif re.search(r"azimuth|\baz\b", h):
            m.setdefault("azimuth", i)
        elif re.search(r"\bdip\b|inclination|incl", h):
            m.setdefault("dip", i)
        elif re.search(r"^from|\bfrom\b", h):
            m.setdefault("from", i)
        elif re.search(r"^to\b|\bto\b|\bto\(", h):
            m.setdefault("to", i)
        elif re.search(r"length|width|interval|thickness|core len", h):
            m.setdefault("length", i)
        elif re.search(r"depth|eoh|total depth|hole length|final depth", h):
            m.setdefault("depth", i)
        else:
            tok = re.sub(r"[^a-z0-9]", "", re.split(r"[\s(]", h)[0])
            if tok in _ELEM:
                el, unit = _ELEM[tok]
                u = "g/t" if "g/t" in h or "gpt" in h or "g/t" in h else ("%" if "%" in h or "pct" in h else unit)
                m["elements"].append((i, el, u))
    return m


_ASSAY_ROW = re.compile(r"^\s*[A-Za-z0-9\-\/]{0,16}\s*\d{1,4}\.\d+\s+\d{1,4}\.\d+")
_COLLAR_ROW = re.compile(r"\b\d{5,6}(?:\.\d+)?\b\s+\b\d{6,7}(?:\.\d+)?\b")


def _real_collar_hdr(line):
    w = line.split()
    return len(w) < 16 and re.search(r"\beasting\b", line, re.I) and re.search(r"\bnorthing\b", line, re.I)


def _real_assay_hdr(line):
    w = line.split()
    return (len(w) < 16 and re.search(r"\bfrom\b", line, re.I) and re.search(r"\bto\b", line, re.I)
            and re.search(r"\b(au|ag|cu|pb|zn|ni|co|mo|g/t|grade|width)\b", line, re.I))


def _page_signals(t):
    """(kind_by_density, header_kind, n_assay_rows, n_collar_rows) for a page.
    Density catches continuation pages (no header); a real short-line header
    catches short/header-led tables that density alone would skip. Prose has
    neither (its 'from ... to' is a sentence, not a short header line)."""
    lines = (t or "").split("\n")
    a = sum(1 for l in lines if _ASSAY_ROW.match(l))
    c = sum(1 for l in lines if _COLLAR_ROW.search(l))
    dens = "collar" if (c >= 3 and c >= a) else ("assay" if a >= 4 else None)
    hdr = None
    for l in lines:
        if _real_collar_hdr(l):
            hdr = "collar"; break
        if _real_assay_hdr(l):
            hdr = "assay"; break
    return dens, hdr, a, c


def _blocks(pages_text):
    """One logical table = a run of pages that starts at a real header (or a dense
    data page) and continues while data rows of that kind persist. Captures both
    short header-led tables and tables spanning tens of pages."""
    sig = [_page_signals(t) for t in pages_text]
    n = len(sig)
    out, i = [], 0
    while i < n:
        dens, hdr, a, c = sig[i]
        kind = hdr or dens
        if not kind:
            i += 1; continue
        j = i
        while j + 1 < n:
            nd, nh, na, nc = sig[j + 1]
            rows = nc if kind == "collar" else na
            if nh == kind or nd == kind or rows >= 2:   # >=2 real data rows to continue
                j += 1
            else:
                break
        out.append((kind, i, j))
        i = j + 1
    return out


def extract_drill_tables(path, pages_text, max_block_pages=60):
    """Extract full collar + assay tables from appendices, following each table
    across ALL its pages (column mapping + hole-id carried forward; continuation
    pages have no header and reuse the block's mapping)."""
    import warnings; warnings.filterwarnings("ignore")
    import camelot
    collars, assays = {}, {}
    for kind, s, e in _blocks(pages_text):
        e = min(e, s + max_block_pages - 1)
        try:
            tabs = camelot.read_pdf(path, pages=f"{s + 1}-{e + 1}", flavor="stream")
        except Exception:
            continue
        cm = None
        last_hole = None
        for tb in tabs:
            rows = tb.df.values.tolist()
            if len(rows) < 2:
                continue
            heads, ds = _merge_header(rows)
            m = _map_columns(heads)
            has_hdr = ("easting" in m and "northing" in m) or ("from" in m and "to" in m and m["elements"])
            if has_hdr:
                cm = m; start = ds
            elif cm is not None:
                m = cm; start = 0          # continuation page: reuse mapping, all rows are data
            else:
                continue
            is_collar = "easting" in m and "northing" in m
            is_assay = "from" in m and "to" in m and m["elements"]
            for r in rows[start:]:
                def cell(k):
                    return r[m[k]] if k in m and m[k] < len(r) else None
                hid = str(cell("hole") or "").strip()
                if hid:
                    last_hole = hid
                hid = hid or last_hole
                if not hid or hid.lower() in ("total", "average", "mean", "hole", "hole id"):
                    continue
                if is_collar:
                    ea, no = _num_cell(cell("easting")), _num_cell(cell("northing"))
                    if ea is not None and no is not None:
                        collars[hid] = {"native_id": hid, "easting": ea, "northing": no,
                                        "elev_m": _num_cell(cell("elev")), "azimuth": _num_cell(cell("azimuth")),
                                        "dip": _num_cell(cell("dip")), "depth_m": _num_cell(cell("depth"))}
                if is_assay:
                    fr, to = _num_cell(cell("from")), _num_cell(cell("to"))
                    if fr is None or to is None:
                        continue
                    ln = _num_cell(cell("length"))
                    ln = ln if ln is not None else (round(to - fr, 2) if to >= fr else None)
                    for ci, el, unit in m["elements"]:
                        g = _num_cell(r[ci]) if ci < len(r) else None
                        if g is None:
                            continue
                        assays[(hid, fr, to, el)] = {"native_id": hid, "from_m": fr, "to_m": to,
                                                     "length_m": ln, "element": el, "grade": g,
                                                     "unit": unit, "is_subinterval": 0}
    return list(collars.values()), list(assays.values())


_MET_PAGE = re.compile(r"metallurg|recover(y|ies)|leach|flotation|oxidation|cyanid|"
                       r"comminut|grind|concentrate|tailings|assay recovery|roast", re.I)
_PROCESS = re.compile(r"\b(CIL|CIP|carbon[- ]in[- ]leach|carbon[- ]in[- ]pulp|heap leach|"
                      r"agitat(?:ed|ion) leach|whole[- ]ore leach|flotation|pressure oxidation|POX|"
                      r"autoclav\w*|roast\w*|bio[- ]?oxidation|BIOX|gravity (?:concentration|recovery|circuit)|"
                      r"Merrill[- ]Crowe|Albion|ultra[- ]?fine grind\w*|resin[- ]in[- ]leach|RIL|"
                      r"dump leach|vat leach|SART|Knelson|dense media|magnetic separation)\b", re.I)
_REFRACTORY = re.compile(r"\b(double\s+refractory|partially\s+refractory|non[- ]refractory|"
                         r"not\s+refractory|refractory|preg[- ]robbing)\b", re.I)
_RECOV = re.compile(r"((?:gold|silver|copper|au|ag|cu|zinc|zn|lead|pb|nickel|ni|overall|average|life[- ]of[- ]mine)"
                    r"[^.\n]{0,40}?recover\w*[^.\n]{0,25}?(\d{1,3}(?:\.\d)?)\s*%|"
                    r"recover\w*[^.\n]{0,25}?(\d{1,3}(?:\.\d)?)\s*%[^.\n]{0,30}?(?:gold|silver|copper|au|ag|cu))", re.I)
_P80 = re.compile(r"P\s*80[^.\n]{0,30}?(\d{2,4})\s*(?:µm|um|microns?|µm)", re.I)
_CN = re.compile(r"(?:cyanide|NaCN|lime)[^.\n]{0,40}?(\d+\.?\d*)\s*kg\s*/\s*t", re.I)
_TPUT = re.compile(r"(\d[\d,]*\.?\d*)\s*(?:tpd|t/d|tonnes per day|Mtpa|Mt/a|ktpa)", re.I)


def extract_metallurgy(pages_text):
    """Capture metallurgical/process data when present: recovery, process route,
    (for gold) whether the ore is refractory, grind size, reagent consumption,
    throughput — plus the narrative for training. Returns None if the report has
    no metallurgy content (which is expected for many technical reports)."""
    met_pages = [t for t in pages_text if t and len(_MET_PAGE.findall(t)) >= 3]
    blob = "\n".join(met_pages)
    if not blob or len(met_pages) < 1:
        return None
    procs = sorted({re.sub(r"\s+", " ", m).strip().upper() for m in _PROCESS.findall(blob)})
    procs = [p for p in procs if p]
    refr = None
    rm = _REFRACTORY.search(blob)
    if rm:
        refr = re.sub(r"\s+", " ", rm.group(0)).strip().lower()
    recs = []
    for m in _RECOV.finditer(blob):
        recs.append(re.sub(r"\s+", " ", m.group(0)).strip()[:80])
    p80 = _P80.search(blob)
    cn = _CN.search(blob)
    tput = _TPUT.search(blob)
    if not (procs or refr or recs):
        return None
    return {
        "process_types": ", ".join(procs) or None,
        "refractory": refr,
        "recovery_summary": " | ".join(dict.fromkeys(recs))[:400] or None,
        "recoveries": " | ".join(dict.fromkeys(recs))[:400] or None,
        "grind_p80_um": float(p80.group(1)) if p80 else None,
        "reagent_notes": re.sub(r"\s+", " ", cn.group(0)).strip()[:120] if cn else None,
        "throughput": re.sub(r"\s+", " ", tput.group(0)).strip()[:60] if tput else None,
        "met_text": re.sub(r"\s+", " ", blob)[:12000],
    }


# ---- economic-study capture (PEA / PFS / FS): NPV, IRR, payback, capital, unit
# costs, mine life, production, throughput, price deck. Many technical reports are
# resource-only and carry none of this — extract_economics returns None then.
_ECON_HINT = re.compile(r"\b(NPV|IRR|payback|all[- ]in sustaining|AISC|initial capital|"
                        r"pre[- ]?production capital|cash cost|life[- ]of[- ]mine|LOM|"
                        r"pre[- ]?feasibility|feasibility study|preliminary economic assessment)\b", re.I)
_MONEY = r"(?:US|C|CAD|USD|A)?\$\s?([\d,]+(?:\.\d+)?)\s*(billion|million|bn|mm|B|M)\b"
_STUDY = re.compile(r"\b(preliminary economic assessment|pre[- ]?feasibility study|"
                    r"feasibility study|PEA|PFS|DFS|BFS)\b", re.I)
_NPV = re.compile(r"(after[- ]?tax|pre[- ]?tax|post[- ]?tax)?\s*NPV\s*\(?\s*(\d+(?:\.\d+)?)?\s*%?\s*\)?"
                  r"[^.\n]{0,45}?" + _MONEY, re.I)
_IRR = re.compile(r"(after[- ]?tax|pre[- ]?tax|post[- ]?tax)?\s*IRR[^.\n]{0,25}?(\d+(?:\.\d+)?)\s*%", re.I)
_PAYBACK = re.compile(r"payback[^.\n]{0,45}?(\d+(?:\.\d+)?)\s*(years?|yrs?|months?)", re.I)
_INITCAP = re.compile(r"(initial|pre[- ]?production|up[- ]?front|development|start[- ]?up|pre[- ]?prod)\s+"
                      r"cap(?:ital|ex|ital costs?)[^.\n]{0,45}?" + _MONEY, re.I)
_SUSCAP = re.compile(r"sustaining\s+cap(?:ital|ex|ital costs?)[^.\n]{0,45}?" + _MONEY, re.I)
_AISC = re.compile(r"(all[- ]in sustaining cost[s]?|AISC)[^.\n]{0,45}?"
                   r"((?:US|C|CAD|USD)?\$\s?[\d,]+(?:\.\d+)?\s*/?\s*(?:oz|ounce|lb|pound)[a-z ]{0,10})", re.I)
_CASHCOST = re.compile(r"(C1 cash cost[s]?|cash cost[s]?|C1 cost[s]?)[^.\n]{0,45}?"
                       r"((?:US|C|CAD|USD)?\$\s?[\d,]+(?:\.\d+)?\s*/?\s*(?:oz|ounce|lb|pound)[a-z ]{0,10})", re.I)
_LOM = re.compile(r"(life[- ]of[- ]mine|mine life|LOM)[^.\n]{0,30}?(\d+(?:\.\d+)?)\s*(years?|yrs?)", re.I)
_APROD = re.compile(r"(average annual|annual|LOM average|life[- ]of[- ]mine average)\s+production"
                    r"[^.\n]{0,55}?([\d,]+(?:\.\d+)?\s*(?:koz|k oz|oz|ounces|Mlb|klb|lb|pounds|t|tonnes|Mt|kt)[a-z/ ]{0,12})", re.I)
_ECON_TPUT = re.compile(r"([\d,]+(?:\.\d+)?)\s*(tpd|t/d|tonnes per day|Mtpa|Mt/a|ktpa|tonnes per annum|t/day|Mt/y)", re.I)
_PRICE = re.compile(r"(gold|silver|copper|zinc|lead|nickel|uranium|lithium|cobalt|moly\w*)\s+price"
                    r"[^.\n]{0,25}?((?:US|C|CAD|USD)?\$\s?[\d,]+(?:\.\d+)?\s*(?:/oz|/lb|/t|per ounce|per pound|per tonne)?)", re.I)


def _musd(num, unit):
    try:
        v = float(str(num).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return round(v * 1000, 1) if str(unit).lower() in ("billion", "bn", "b") else round(v, 1)


def _snip(m):
    return re.sub(r"\s+", " ", m.group(0)).strip()[:160]


# --- unit-cost capture (AISC / cash cost). These live in cost TABLES far more
# often than in prose, so a single "$1,150/oz" pattern misses most of them: the
# number and its "$/oz" unit are routinely split across table cells and can land
# in either order ("AISC US$/oz 1,150", "All-in sustaining cost (US$/oz) 1,187",
# "AISC 1,150 /oz", "AISC of US$1,150 per ounce"). _unit_cost_near scans a short
# window after each label and tries every ordering, currency-bearing first.
_LBL_AISC = re.compile(r"all[- ]in sustaining(?:\s+cost)?s?|\bAISC\b|all[- ]in cost[s]?|\bAIC\b", re.I)
_LBL_CASH = re.compile(r"\bC1\b(?:\s+cash)?(?:\s+cost)?s?|total cash cost[s]?|cash operating cost[s]?|"
                       r"site cash cost[s]?|cash cost[s]?|C1 cost[s]?", re.I)
_UNIT_MAP = {"ounce": "oz", "oz": "oz", "tonne": "t", "t": "t", "lb": "lb", "pound": "lb"}
_CURR_TOK = r"(?:US\$|C\$|CAD\$?|USD\$?|A\$|\$)"


def _normunit(u):
    return _UNIT_MAP.get(u.lower(), u.lower())


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _plausible_cost(val, unit):
    """AISC / cash cost magnitude sanity by unit. Precious metals run ~US$8-20/oz
    (silver) to ~US$2,500/oz (gold); base metals ~US$0.5-10/lb. This rejects a
    by-product sub-figure like 'US$/oz Ag 0.11' or a stray count, so the scan can
    fall through to the real headline AISC on the next label row."""
    v = _num(val)
    if v is None:
        return False
    if unit == "oz":
        return 3 <= v <= 6000
    if unit == "lb":
        return 0.05 <= v <= 100
    return True


def _unit_cost_near(blob, label_rx, window=170):
    """Return a normalized 'US$NNN/oz'-style cost for the label, or None. For each
    label occurrence it reads the FIRST unit-cost after the label (trying the three
    cell layouts and taking whichever appears earliest), and accepts it only if the
    magnitude is a plausible AISC/cash cost. A glossary entry (no value) or an
    implausible first value (a by-product $0.11/oz line) makes it fall through to
    the next label occurrence — which is where the headline figure usually is.

    Units are per-OUNCE / per-POUND only: AISC and C1/cash cost are quoted per unit
    of METAL. A '$/t' next to an AISC label is per-tonne opex / cut-off cost, not
    the headline unit cost, so it is deliberately never matched."""
    NUM = r"(?<![\d.,])\d[\d,]*(?:\.\d+)?"     # 1,150 | 1.85 | 950 — never a fractional tail
    UNIT = r"(oz|ounce|lb|pound)"
    pats = [
        ("A", re.compile(r"(" + _CURR_TOK + r")\s?(" + NUM + r")\s*(?:/\s?|per\s+)" + UNIT + r"\b", re.I)),
        ("B", re.compile(r"(" + _CURR_TOK + r")?\s?/\s?" + UNIT + r"\)?[^\d]{0,16}(" + NUM + r")", re.I)),
        ("C", re.compile(r"(" + NUM + r")\s*/\s?" + UNIT + r"\b", re.I)),
    ]
    for lm in label_rx.finditer(blob):
        seg = blob[lm.end(): lm.end() + window]
        cands = [(m.start(), tag, m) for tag, rx in pats for m in [rx.search(seg)] if m]
        if not cands:
            continue
        _, tag, m = min(cands, key=lambda x: x[0])
        if tag == "A":
            cur, num, unit = m.group(1), m.group(2), _normunit(m.group(3))
        elif tag == "B":
            cur, unit, num = (m.group(1) or "US$"), _normunit(m.group(2)), m.group(3)
        else:
            cur, num, unit = "US$", m.group(1), _normunit(m.group(2))
        if _plausible_cost(num, unit):
            return f"{cur}{num}/{unit}"
    return None


def extract_economics(pages_text):
    """Capture economic-study results (PEA/PFS/FS) when present. Returns None for
    resource-only reports (the common case)."""
    hint_pages = [t for t in pages_text if t and _ECON_HINT.search(t)]
    if not hint_pages:
        return None
    blob = "\n".join(hint_pages)
    econ = {"highlights": []}

    def cap(regex, key, snip=True):
        m = regex.search(blob)
        if m and snip:
            econ["highlights"].append(_snip(m))
        return m

    # NPV (prefer after-tax; capture pre-tax separately)
    for m in _NPV.finditer(blob):
        basis = (m.group(1) or "").lower().replace(" ", "").replace("-", "")
        disc = m.group(2)
        val = _musd(m.group(3), m.group(4))
        if disc and not econ.get("npv_discount_pct"):
            try:
                econ["npv_discount_pct"] = float(disc)
            except ValueError:
                pass
        if "pre" in basis:
            econ.setdefault("npv_pretax_musd", val)
        else:                               # after/post-tax or unspecified -> after-tax slot
            econ.setdefault("npv_aftertax_musd", val)
        econ["highlights"].append(_snip(m))
    for m in _IRR.finditer(blob):
        basis = (m.group(1) or "").lower()
        try:
            v = float(m.group(2))
        except ValueError:
            continue
        if "pre" in basis:
            econ.setdefault("irr_pretax_pct", v)
        else:
            econ.setdefault("irr_aftertax_pct", v)
        econ["highlights"].append(_snip(m))
    m = cap(_PAYBACK, "payback")
    if m:
        yrs = float(m.group(1))
        econ["payback_years"] = round(yrs / 12, 2) if "month" in m.group(2).lower() else yrs
    m = cap(_INITCAP, "initcap")
    if m:
        econ["initial_capital_musd"] = _musd(m.group(2), m.group(3))
    m = cap(_SUSCAP, "suscap")
    if m:
        econ["sustaining_capital_musd"] = _musd(m.group(1), m.group(2))
    # AISC / cash cost — robust to cost-table layouts (number and $/unit split or
    # reordered). Fall back to the old contiguous pattern only if the scan misses.
    aisc = _unit_cost_near(blob, _LBL_AISC)
    if not aisc:
        m = _AISC.search(blob)
        aisc = re.sub(r"\s+", " ", m.group(2)).strip()[:40] if m else None
    if aisc:
        econ["aisc"] = aisc[:40]
        lm = _LBL_AISC.search(blob)
        if lm:
            econ["highlights"].append(re.sub(r"\s+", " ", blob[lm.start():lm.start() + 120]).strip()[:160])
    cash = _unit_cost_near(blob, _LBL_CASH)
    if not cash:
        m = _CASHCOST.search(blob)
        cash = re.sub(r"\s+", " ", m.group(2)).strip()[:40] if m else None
    # A cash cost identical to AISC is a spurious grab of the AISC value from an
    # adjacent cell (cash cost is always < AISC), so discard it.
    if cash and cash != econ.get("aisc"):
        econ["cash_cost"] = cash[:40]
        lm = _LBL_CASH.search(blob)
        if lm:
            econ["highlights"].append(re.sub(r"\s+", " ", blob[lm.start():lm.start() + 120]).strip()[:160])
    m = cap(_LOM, "lom")
    if m:
        econ["mine_life_years"] = float(m.group(2))
    m = cap(_APROD, "aprod")
    if m:
        econ["annual_production"] = re.sub(r"\s+", " ", m.group(2)).strip()[:50]
    m = _ECON_TPUT.search(blob)
    if m:
        econ["throughput"] = re.sub(r"\s+", " ", m.group(0)).strip()[:40]; econ["highlights"].append(_snip(m))
    prices = [re.sub(r"\s+", " ", pm.group(0)).strip() for pm in _PRICE.finditer(blob)]
    if prices:
        econ["metal_price_assumptions"] = " | ".join(dict.fromkeys(prices))[:240]
    # An AISC/cash value equal to a metal-PRICE assumption is a price grabbed near
    # the label (e.g. a US$1,800/oz gold price in a resource report that never
    # states a real per-oz AISC), not a cost — drop it.
    price_nums = {_num(pn) for pn in re.findall(r"\d[\d,]*(?:\.\d+)?",
                                                econ.get("metal_price_assumptions") or "")}
    for k in ("aisc", "cash_cost"):
        if econ.get(k):
            vn = _num(re.sub(r"[^\d.,]", "", econ[k].split("/")[0]))
            if vn in price_nums:
                econ[k] = None
    sm = _STUDY.search(blob)
    if sm:
        econ["study_type"] = re.sub(r"\s+", " ", sm.group(1)).strip()

    signal = any(econ.get(k) is not None for k in
                 ("npv_aftertax_musd", "npv_pretax_musd", "irr_aftertax_pct", "irr_pretax_pct",
                  "payback_years", "initial_capital_musd", "aisc", "mine_life_years"))
    if not signal:
        return None
    econ["highlights"] = " | ".join(dict.fromkeys(econ["highlights"]))[:1500] or None
    econ["econ_text"] = re.sub(r"\s+", " ", blob)[:12000]
    return econ


def ingest_report(url, project=None, commodity=None, jurisdiction=None, report_date=None,
                  drill_tables=True, source_id=None, pdf_path=None):
    """Extract + retain a technical report. `url` is the citable source (a web
    URL, or a SEDAR filing reference). `pdf_path` ingests an already-downloaded
    file (e.g. a SEDAR PDF in Downloads) instead of fetching. `source_id` gives a
    stable id (e.g. sedar:<filing>) so re-ingesting the same filing is idempotent."""
    sid = source_id or _rid(url)
    path = pdf_path if (pdf_path and os.path.exists(pdf_path)) else fetch_pdf(url)
    pages_text = _pages_text(path, ocr_sid=sid)
    _txt_chars = sum(len(t) for t in pages_text)
    _image_only = _txt_chars < 200          # no text layer (scanned PDF) — needs OCR
    if _image_only:
        print(f"[43-101] {project or url}: image-only PDF (no text layer, "
              f"{len(pages_text)} pages) — text extraction needs OCR; recording as image-only")
    try:
        _pdf_bytes = os.path.getsize(path)
    except OSError:
        _pdf_bytes = 0
    res = extract_resources(pages_text)
    meth = extract_methodology(pages_text)
    met = extract_metallurgy(pages_text)
    eco = extract_economics(pages_text)
    # retain the source PDF in the durable archive (idempotent; skipped without a token)
    archive_url = None
    try:
        from minemodelingpro import report_archive
        archive_url = report_archive.archive_pdf(path, sid.replace(":", "_"))
    except Exception as e:
        print(f"[43-101] archive skipped: {str(e)[:80]}")
    collars, assays = ([], [])
    if drill_tables and _pdf_bytes <= _BIG_PDF_BYTES:
        try:
            collars, assays = extract_drill_tables(path, pages_text)
        except Exception as e:
            print(f"[43-101] drill-table extract skipped: {str(e)[:100]}")
    elif drill_tables:
        # Camelot on a 100+ MB PDF risks OOM in the 4 GB worker; text-derived
        # data (resources/method/metallurgy/economics) is still captured above.
        print(f"[43-101] drill-table extract skipped for large PDF "
              f"({_pdf_bytes // (1024*1024)} MB > {_BIG_PDF_BYTES // (1024*1024)} MB)")

    con = store.connect()
    # collars + assays from appendix drill tables
    con.execute("DELETE FROM collars WHERE source_id=?", (sid,))
    if collars:
        crow = [{"hole_uid": f"{sid}:{c['native_id']}", "source_id": sid, "native_id": c["native_id"],
                 "company": None, "project": project, "jurisdiction": jurisdiction, "lat": None, "lon": None,
                 "easting": c["easting"], "northing": c["northing"], "utm_zone": None, "utm_hemi": "N",
                 "datum": None, "elev_m": c["elev_m"], "azimuth": c["azimuth"], "dip": c["dip"],
                 "depth_m": c["depth_m"], "year_drilled": None, "has_assay": 1, "assay_flags": None,
                 "report_ref": None, "url": url} for c in collars]
        store.replace_collars(con, sid, crow)
    con.execute("DELETE FROM assays WHERE source_id=?", (sid,))
    if assays:
        arow = [{"source_id": sid, "hole_uid": f"{sid}:{a['native_id']}", "native_id": a["native_id"],
                 "from_m": a["from_m"], "to_m": a["to_m"], "length_m": a["length_m"], "element": a["element"],
                 "grade": a["grade"], "unit": a["unit"], "is_subinterval": a["is_subinterval"]} for a in assays]
        store.replace_assays(con, sid, arow)
    # deposit_model rows
    con.execute("DELETE FROM deposit_model WHERE source_id=?", (sid,))
    dm = []
    for k, r in enumerate(res):
        dm.append({"id": f"{sid}:{k}", "source_id": sid, "project": project,
                   "jurisdiction": jurisdiction, "category": r["category"],
                   "tonnes": r["tonnes"], "grade": None, "grade_unit": None,
                   "contained": None, "contained_unit": None, "cutoff": None,
                   "cutoff_unit": None, "commodity": commodity, "report_url": url,
                   "report_date": report_date, "note": r["note"]})
    if dm:
        store.add_deposit_model(con, dm)
    # model_method row
    con.execute("DELETE FROM model_method WHERE source_id=?", (sid,))
    if meth:
        con.execute("""INSERT INTO model_method
            (id,source_id,project,jurisdiction,commodity,estimation_method,software,block_size,
             compositing_m,capping,density,cutoff,cutoff_basis,search_params,compositing,domaining,
             classification,qaqc,section_ref,method_text,report_url,report_date)
            VALUES (:id,:source_id,:project,:jurisdiction,:commodity,:estimation_method,:software,:block_size,
             NULL,:capping,:density,:cutoff,NULL,:search_params,:compositing,NULL,
             :classification,NULL,:section_ref,:method_text,:report_url,:report_date)""", {
            "id": f"{sid}:method", "source_id": sid, "project": project, "jurisdiction": jurisdiction,
            "commodity": commodity, "estimation_method": meth["estimation_method"],
            "software": meth["software"], "block_size": meth["block_size"], "capping": meth["capping"],
            "density": meth["density"], "cutoff": meth["cutoff"], "search_params": meth["search_params"],
            "compositing": meth["compositing"], "classification": meth["classification"],
            "section_ref": f"{meth['n_method_pages']} methodology pages", "method_text": meth["method_text"],
            "report_url": url, "report_date": report_date})
    # metallurgy (recovery / process route / refractory) when present
    con.execute("DELETE FROM metallurgy WHERE source_id=?", (sid,))
    if met:
        con.execute("""INSERT INTO metallurgy
            (id,source_id,project,jurisdiction,commodity,process_types,refractory,recovery_summary,
             recoveries,grind_p80_um,reagent_notes,throughput,met_text,report_url,report_date)
            VALUES (:id,:source_id,:project,:jurisdiction,:commodity,:process_types,:refractory,:recovery_summary,
             :recoveries,:grind_p80_um,:reagent_notes,:throughput,:met_text,:report_url,:report_date)""", {
            "id": f"{sid}:met", "source_id": sid, "project": project, "jurisdiction": jurisdiction,
            "commodity": commodity, "process_types": met["process_types"], "refractory": met["refractory"],
            "recovery_summary": met["recovery_summary"], "recoveries": met["recoveries"],
            "grind_p80_um": met["grind_p80_um"], "reagent_notes": met["reagent_notes"],
            "throughput": met["throughput"], "met_text": met["met_text"],
            "report_url": url, "report_date": report_date})
    # economics (NPV/IRR/payback/capex/AISC/LOM/production) when it's an economic study
    con.execute("DELETE FROM economics WHERE source_id=?", (sid,))
    if eco:
        con.execute("""INSERT INTO economics
            (id,source_id,project,jurisdiction,commodity,study_type,npv_aftertax_musd,npv_pretax_musd,
             npv_discount_pct,irr_aftertax_pct,irr_pretax_pct,payback_years,initial_capital_musd,
             sustaining_capital_musd,total_capital_musd,aisc,cash_cost,mine_life_years,annual_production,
             throughput,avg_grade,recovery,metal_price_assumptions,highlights,econ_text,report_url,report_date)
            VALUES (:id,:source_id,:project,:jurisdiction,:commodity,:study_type,:npv_aftertax_musd,:npv_pretax_musd,
             :npv_discount_pct,:irr_aftertax_pct,:irr_pretax_pct,:payback_years,:initial_capital_musd,
             :sustaining_capital_musd,:total_capital_musd,:aisc,:cash_cost,:mine_life_years,:annual_production,
             :throughput,:avg_grade,:recovery,:metal_price_assumptions,:highlights,:econ_text,:report_url,:report_date)""", {
            "id": f"{sid}:econ", "source_id": sid, "project": project, "jurisdiction": jurisdiction,
            "commodity": commodity, "study_type": eco.get("study_type"),
            "npv_aftertax_musd": eco.get("npv_aftertax_musd"), "npv_pretax_musd": eco.get("npv_pretax_musd"),
            "npv_discount_pct": eco.get("npv_discount_pct"), "irr_aftertax_pct": eco.get("irr_aftertax_pct"),
            "irr_pretax_pct": eco.get("irr_pretax_pct"), "payback_years": eco.get("payback_years"),
            "initial_capital_musd": eco.get("initial_capital_musd"),
            "sustaining_capital_musd": eco.get("sustaining_capital_musd"),
            "total_capital_musd": eco.get("total_capital_musd"), "aisc": eco.get("aisc"),
            "cash_cost": eco.get("cash_cost"), "mine_life_years": eco.get("mine_life_years"),
            "annual_production": eco.get("annual_production"), "throughput": eco.get("throughput"),
            "avg_grade": eco.get("avg_grade"), "recovery": eco.get("recovery"),
            "metal_price_assumptions": eco.get("metal_price_assumptions"),
            "highlights": eco.get("highlights"), "econ_text": eco.get("econ_text"),
            "report_url": url, "report_date": report_date})
    store.record_source(con, {
        "id": sid, "kind": "ni43101", "name": project or url.rsplit("/", 1)[-1],
        "url": url, "jurisdiction": jurisdiction,
        "pulled_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_collars": len(collars), "n_assays": len(assays),
        "note": f"ev{EXTRACTOR_VERSION}; {len(dm)} resource rows; method={'y' if meth else 'n'}; "
                f"met={'y' if met else 'n'}; econ={'y' if eco else 'n'}; "
                f"{len(collars)} collars; {len(assays)} assays"
                + ("; image-only(needs OCR)" if _image_only else "")
                + (f"; archive={archive_url}" if archive_url else "")})
    con.commit(); con.close()
    print(f"[43-101] {project or url}: {len(dm)} resource rows, {len(collars)} collars, "
          f"{len(assays)} assays | method={meth['estimation_method'] if meth else None} | "
          f"met={ {k: v for k, v in (met or {}).items() if k not in ('met_text',) and v} if met else None}")
    if eco:
        print(f"[43-101]   economics: study={eco.get('study_type')} "
              f"NPV(at)={eco.get('npv_aftertax_musd')}M IRR(at)={eco.get('irr_aftertax_pct')}% "
              f"payback={eco.get('payback_years')}y initCap={eco.get('initial_capital_musd')}M "
              f"AISC={eco.get('aisc')} LOM={eco.get('mine_life_years')}y")
    return {"resources": len(dm), "collars": len(collars), "assays": len(assays),
            "method": bool(meth), "metallurgy": bool(met), "economics": bool(eco)}


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QUEUE = os.path.join(_ROOT, "data", "keep", "mmp_report_queue.json")


def _already(con, url):
    return con.execute("SELECT 1 FROM sources WHERE id=?", (_rid(url),)).fetchone() is not None


def _current(con, url):
    """True if the report is ingested AND under the current extractor version."""
    r = con.execute("SELECT note FROM sources WHERE id=?", (_rid(url),)).fetchone()
    return bool(r) and f"ev{EXTRACTOR_VERSION}" in (r[0] or "")


def run_queue(path=QUEUE, limit=None, max_seconds=None, refresh=False):
    """Ingest every report in the queue not already in the store. Idempotent and
    resumable (skips ingested reports), time-budgeted for CI. Shards at the end.
    refresh=True re-ingests every report (e.g. after an extractor upgrade)."""
    import json
    import time
    from minemodelingpro import shards
    q = json.load(open(path))
    con = store.connect()
    # refresh: re-do reports not yet at the current extractor version (resumable);
    # normal: only reports not ingested at all.
    todo = [r for r in q if not _current(con, r["url"])] if refresh \
        else [r for r in q if not _already(con, r["url"])]
    con.close()
    print(f"[43-101] queue: {len(q)} reports, {len(todo)} new to ingest")
    t0 = time.time()
    done = ok = 0
    for r in todo:
        if limit and done >= limit:
            break
        if max_seconds and time.time() - t0 > max_seconds:
            print(f"[43-101] time budget reached — {done} done this run, rest resume next run")
            break
        done += 1
        try:
            res = ingest_report(r["url"], project=r.get("project"),
                                commodity=r.get("commodity"), jurisdiction=r.get("jurisdiction"))
            ok += 1
        except Exception as e:
            print(f"[43-101] FAILED {r.get('project') or r['url']}: {str(e)[:120]}")
    shards.export_shards()
    try:
        from minemodelingpro import report_archive
        report_archive.build_index()
    except Exception as e:
        print(f"[43-101] index build skipped: {str(e)[:80]}")
    print(f"[43-101] run complete: {ok}/{done} ingested this run")
    return {"new": len(todo), "ingested_this_run": ok}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "queue":
        limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
        secs = int(a[a.index("--max-seconds") + 1]) if "--max-seconds" in a else None
        run_queue(limit=limit, max_seconds=secs, refresh="--refresh" in a)
    elif a:
        ingest_report(a[0], project=a[1] if len(a) > 1 else None,
                      commodity=a[2] if len(a) > 2 else None,
                      jurisdiction=a[3] if len(a) > 3 else None)
