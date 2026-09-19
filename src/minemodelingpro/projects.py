"""Project registry: ONE dataset per mineral project, merged from every source.

A project's drill data arrives from several places — NI 43-101 technical
reports (collar tables + significant-intercept/assay tables), and news releases
in the drill bank (each release a handful of new holes). Before this module the
gallery modelled each source (and each spatial cluster of news holes) separately,
so one deposit could show up as several half-models (e.g. Perron three times).

``assemble()`` builds a single dataset per project:

  1. News holes (geolocated) are clustered spatially, then clusters are merged when
     they belong to the same company and the same named project, or sit within
     ``MERGE_KM`` of each other under the same company.
  2. Each technical report is attached to the project it describes — by shared
     drill-hole IDs (strongest), by georeferenced collar location, or by project
     name — or stands alone when it carries enough drilling to model on its own.
  3. Holes are unified by normalised hole ID across sources (report collars win on
     completeness; news fills gaps), intervals are de-duplicated, and every source
     that contributed is listed on the project so the model page documents exactly
     what went into it.

Everything is expressed in one local metre frame per project (x east, y north,
z = elevation masl): lat/lon sources are projected about the project centre;
report collars in a local mine grid are used as-is when that is the only frame,
or fitted onto the geographic frame through holes common to both.
"""
import os
import re
import math
import json
import sqlite3
from collections import Counter, defaultdict

import numpy as np

from minemodelingpro import holeid

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DRILLBANK = os.path.join(_ROOT, "data", "keep", "drillbank.sqlite")
REPORT_DIR = os.path.join(_ROOT, "data", "keep", "mmp_reports")
OVERRIDES = os.path.join(_ROOT, "data", "keep", "mmp_project_overrides.json")

CLUSTER_KM = 8.0
MERGE_KM = 15.0
REPORT_ATTACH_KM = 25.0

# ------------------------------------------------------------------ naming
_VERBS = re.compile(
    r"\b(Intersect|Confirm|Release|Report|Announce|Identif|Increase|Discover|Drill|Hit|"
    r"Extend|Provide|Expand|Return|Complete|File|Deliver|Encounter|Cut|Advance|Commence|"
    r"Continue|Update|Define|Assay|Highlight|Step|Close|Grant|Acquire|Option|Stake|Mobiliz|"
    r"Present|Show|Reveal|Outline|Intercept|Select|Add|Make|Receive|Begin|Start|Launch|Achieve)\w*", re.I)
_SUFFIX = re.compile(r"[\s,]+(Ltd|Inc|Corp|Limited|Corporation|Co|Company|plc|PLC|S\.?A\.?|LLC|Pty|AG|NL)\.?$", re.I)


def clean_company(name):
    s = str(name or "").strip()
    if not s or s.lower() in ("none", "nan"):
        return None
    s = re.sub(r"\s*\(\s*\d{6,}\s*\)\s*", " ", s)                 # SEDAR profile numbers
    s = re.sub(r"\s*\(formerly[^)]*\)?", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*formerly.*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*Junior Mining Network.*$", "", s)
    s = re.sub(r"^\s*(?:CNW|PRN|GNW)\s*/\s*-*\s*", "", s)
    m = _VERBS.search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
    for _ in range(2):
        s = _SUFFIX.sub("", s).strip(" ,.-")
    if not s or len(s) > 42 or re.search(r"\d+(\.\d+)?\s*(%|g/t|m\b)", s) or len(s.split()) > 6:
        return None
    if s.isupper() and len(s) > 5:
        s = s.title()
    return s


def clean_project(name):
    s = str(name or "").strip()
    if not s or s.lower() in ("none", "nan", "unnamed project"):
        return None
    s = s.replace("_", " ")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s) if re.fullmatch(r"[A-Za-z]+", s) and not s.isupper() else s
    s = re.sub(r"-(?=[A-Za-z])", " ", s) if "-" in s and not re.search(r"\d", s) else s
    s = re.sub(r"\b(Resources?[- ]Inc|Project|Property|Deposit|Mine|Engineering|Intrusion)\b\.?", "", s, flags=re.I).strip(" -,")
    # trailing commodity / deposit-type qualifiers: "Rajapalot Gold-Cobalt", "Knife Lake Copper VMS"
    _q = (r"gold|silver|copper|nickel|cobalt|zinc|lead|lithium|uranium|rare|earths?|au|ag|cu|ni|co|zn|pb|"
          r"vms|sulphide|sulfide|polymetallic|porphyry|pge|pgm|ree|graphite|phosphate|moly|molybdenum|tungsten|"
          r"vanadium|ore|critical|minerals?|metals?|mineral|resources?|precious|updated?")
    while True:
        s2 = re.sub(r"[\s-]+(?:" + _q + r")$", "", s, flags=re.I).strip(" -,")
        if s2 == s or not s2:
            break
        s = s2
    s = re.sub(r"^(?:[A-Z][\w]*’s|[A-Z][\w]*'s)\s+", "", s)          # "CoTec’s Lac Jeannine"
    s = re.sub(r"\s+", " ", s)
    if s.isupper() and len(s) > 4:
        s = s.title()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s or None


def name_key(s):
    s = (s or "").lower()
    s = re.sub(r"\b(gold|silver|copper|nickel|zinc|lithium|uranium|project|property|deposit|mine|the|district|updated?)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = (math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p)
         * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(min(1, a)))


_PROJ_KW = (r"(?:Project|Property|Deposit|Prospect|Target|Discovery|Zone|Mine|Claims?|System|Trend|Camp|Occurrence)")
_PROJ_STOP = {"the", "its", "their", "our", "a", "new", "first", "phase", "maiden", "depth", "surface",
              "high", "grade", "near", "step", "further", "this", "that", "all"}
_TAIL = {"gold", "silver", "copper", "lithium", "nickel", "zinc", "lead", "uranium", "cobalt", "gallium",
         "rare", "earth", "moly", "molybdenum", "polymetallic", "vms", "au", "ag", "cu", "flagship"}


def project_from_titles(titles):
    """Project NAME from drill-release headlines. Project-level words (Project,
    Property, Mine, Camp) outrank zone-level words (Zone, Discovery, Target);
    "... Zone at Antino" resolves to Antino."""
    strong = Counter(); weak = Counter()
    NAME = r"([A-Z][\w'’.\-]*(?:\s+(?:[A-Z0-9][\w'’.\-]*|de|del|la|las|los|du|des|le))*?)"
    p_proj = re.compile(r"\b(?:at|on|of|from)\s+(?:the\s+|its\s+|their\s+|our\s+)?(?:100%[- ]\w+\s+)?" + NAME +
                        r"\s+(?:Gold\s+|Silver\s+|Copper\s+|Lithium\s+|Uranium\s+|Nickel\s+|Polymetallic\s+|"
                        r"Precious\s+Metals?\s+|Mineral\s+)?(?:Projects?|Property|Properties|Mine|Camp|District|Complex)\b")
    GNAME = r"([A-Z][\w'’.\-]*(?:\s+[A-Z][\w'’.\-]*){0,3})"
    p_at = re.compile(r"\b(?:Zone|Discovery|Target|Deposit|Prospect|Trend|Corridor|Vein)s?\s+at\s+(?:the\s+|its\s+)?" + GNAME + r"(?=[\s,;:\-–—(]|$)")
    p_zone = re.compile(r"\b(?:at|on|of)\s+(?:the\s+|its\s+|their\s+)?" + NAME +
                        r"\s+(?:Deposit|Prospect|Target|Discovery|Zone|System|Trend|Occurrence)\b")
    p_loose = re.compile(r"\b(?:at|on)\s+(?:the\s+|its\s+|their\s+)?([A-Z][\w'’.\-]*(?:\s+[A-Z][\w'’.\-]*){0,2})(?=\s*[,;:(]|\s+(?:in|near)\s|$)")

    def ok(nm):
        w = nm.split()
        while w and w[0].lower() in _PROJ_STOP:
            w = w[1:]
        while len(w) > 1 and w[-1].lower() in _TAIL:
            w = w[:-1]
        nm = " ".join(w)
        if len(nm) < 3 or nm.lower() in _PROJ_STOP or re.search(r"\d+(\.\d+)?\s*(%|g/t|m\b)", nm) or re.fullmatch(r"[\d.,]+", nm):
            return None
        if nm.isupper() and len(nm) > 4:
            nm = nm.title()
        return nm
    for t in titles:
        if not t:
            continue
        t = re.sub(r"\s*-\s*Junior Mining Network.*$", "", str(t)).strip()
        if t.isupper():
            t = t.title()
        for rx, bag, w in ((p_proj, strong, 3), (p_at, strong, 2), (p_zone, weak, 1), (p_loose, weak, 1)):
            for m in rx.finditer(t):
                nm = ok(m.group(1).strip(" '’.-"))
                if nm:
                    bag[nm] += w
    if strong:
        return strong.most_common(1)[0][0]
    return weak.most_common(1)[0][0] if weak else None


def _fold(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()
    return s


def _company_from_title(t):
    if not t:
        return None
    m = _VERBS.search(t)
    if m and 2 < m.start() < 45:
        return clean_company(t[:m.start()])
    return None


# --------------------------------------------------------------- union-find
class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


# ---------------------------------------------------------------- loaders
def load_news():
    """News drill bank -> list of hole dicts (lat/lon required) + intervals by hole."""
    if not os.path.exists(DRILLBANK):
        return [], {}
    import pandas as pd
    con = sqlite3.connect(DRILLBANK)
    try:
        h = pd.read_sql_query(
            "SELECT h.release_id, h.hole_id, h.project, h.lat, h.lon, h.elev_m, h.azimuth, h.dip, "
            "h.easting, h.northing, h.utm_zone, h.utm_hemi, "
            "h.depth_m, r.company, r.country, r.published, r.url, r.title FROM holes h "
            "JOIN releases r ON h.release_id=r.id", con)
        iv = pd.read_sql_query("SELECT release_id, hole_id, from_m, to_m, element, grade, unit, "
                               "is_subinterval FROM intervals", con)
    finally:
        con.close()
    h = h.dropna(subset=["lat", "lon"])
    h = h[(h.lat.abs() <= 85) & (h.lon.abs() <= 180)].copy()
    h = _repair_zones(h)
    holes = []
    for r in h.itertuples(index=False):
        holes.append({"rid": r.release_id, "id": str(r.hole_id).strip(), "key": holeid.key(r.hole_id),
                      "lat": float(r.lat), "lon": float(r.lon),
                      "z": _f(r.elev_m), "az": _f(r.azimuth), "dip": _f(r.dip), "depth": _f(r.depth_m),
                      "company": _s(r.company), "country": _s(r.country), "date": _s(r.published),
                      "url": _s(r.url), "title": _s(r.title), "project": _s(r.project)})
    ivs = defaultdict(list)
    for r in iv.itertuples(index=False):
        if r.from_m is None or r.to_m is None or r.grade is None:
            continue
        ivs[(r.release_id, str(r.hole_id).strip())].append(
            {"from": float(r.from_m), "to": float(r.to_m), "el": r.element, "grade": float(r.grade),
             "unit": r.unit, "sub": int(r.is_subinterval or 0)})
    return holes, ivs


def _f(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _repair_zones(h):
    """Catch mis-geolocated releases: collar elevations (masl, as published) must
    match the ground. If a release's collars sit >250 m off the DEM, its UTM zone
    was guessed wrong upstream — re-project its easting/northing through nearby
    zones and keep the one whose ground elevations match the collars."""
    try:
        from minemodelingpro import terrain
        from pyproj import Transformer
    except Exception:
        return h
    import pandas as pd
    out = []
    for rid, g in h.groupby("release_id"):
        g = g.copy()
        ok = g.dropna(subset=["elev_m", "easting", "northing", "utm_zone"])
        ok = ok[(ok.easting > 100000) & (ok.easting < 900000) & (ok.elev_m > -500) & (ok.elev_m < 6000)]
        if len(ok) >= 2:
            try:
                dem = terrain.elevations(ok.lat.values, ok.lon.values, 11)
                mis = float(np.nanmedian(np.abs(dem - ok.elev_m.values)))
            except Exception:
                mis = 0.0
            if mis > 250:
                z0 = int(ok.utm_zone.mode().iloc[0])
                south = str(ok.utm_hemi.dropna().iloc[0] if ok.utm_hemi.notna().any() else "N").upper().startswith("S")
                best = (mis, z0, None)
                for dz in range(-9, 10):
                    z = z0 + dz
                    if dz == 0 or not (1 <= z <= 60):
                        continue
                    tr = Transformer.from_crs(f"EPSG:{(32700 if south else 32600) + z}", "EPSG:4326", always_xy=True)
                    lo, la = tr.transform(ok.easting.values, ok.northing.values)
                    try:
                        dz_ = terrain.elevations(la, lo, 11)
                        m = float(np.nanmedian(np.abs(dz_ - ok.elev_m.values)))
                    except Exception:
                        continue
                    if np.isfinite(m) and m < best[0]:
                        best = (m, z, tr)
                if best[2] is not None and best[0] < 60 and best[0] < mis / 4:
                    gg = g.dropna(subset=["easting", "northing"])
                    lo, la = best[2].transform(gg.easting.values, gg.northing.values)
                    g.loc[gg.index, "lat"] = la
                    g.loc[gg.index, "lon"] = lo
                    g["zone_fixed"] = f"{z0}->{best[1]}"
                    print(f"[projects] re-geolocated release {rid[:10]}: UTM zone {z0} -> {best[1]} "
                          f"(collar/DEM misfit {mis:.0f} m -> {best[0]:.0f} m)")
        out.append(g)
    return pd.concat(out) if out else h


def _s(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = str(v).strip()
    return v or None


def load_reports():
    import glob
    out = []
    for f in sorted(glob.glob(os.path.join(REPORT_DIR, "*.json"))):
        try:
            out.append(json.load(open(f)))
        except Exception:
            pass
    return out


def _overrides():
    try:
        return json.load(open(OVERRIDES))
    except Exception:
        return {}


# ---------------------------------------------------------------- assembly
def assemble(min_holes=3, min_intervals=8, verbose=False):
    news_holes, news_ivs = load_news()
    reports = load_reports()
    ov = _overrides()

    # ---- 1. spatial clusters of news holes
    n = len(news_holes)
    uf = _UF(n)
    if n:
        la = np.radians([h["lat"] for h in news_holes]); lo = np.radians([h["lon"] for h in news_holes])
        for i in range(n - 1):
            dlat = la[i + 1:] - la[i]; dlon = lo[i + 1:] - lo[i]
            a = np.sin(dlat / 2) ** 2 + np.cos(la[i]) * np.cos(la[i + 1:]) * np.sin(dlon / 2) ** 2
            d = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
            for off in np.where(d <= CLUSTER_KM)[0]:
                uf.union(i, i + 1 + int(off))
    clusters = defaultdict(list)
    for i in range(n):
        clusters[uf.find(i)].append(i)

    groups = []          # each: dict with news idx list, reports list
    for idx in clusters.values():
        hs = [news_holes[i] for i in idx]
        comp = Counter(c for c in (clean_company(h["company"]) or _company_from_title(h["title"]) for h in hs) if c)
        titles = list({h["title"] for h in hs})
        pname = project_from_titles(titles)
        if not pname:
            pc = Counter(clean_project(h["project"]) for h in hs if h.get("project"))
            pc.pop(None, None)
            pname = pc.most_common(1)[0][0] if pc else None
        groups.append({"news": idx, "reports": [], "company": comp.most_common(1)[0][0] if comp else None,
                       "companies": set(comp), "project": pname,
                       "lat": float(np.median([h["lat"] for h in hs])),
                       "lon": float(np.median([h["lon"] for h in hs])),
                       "keys": {h["key"] for h in hs}})

    # ---- 2. merge clusters: same company + (same project name or within MERGE_KM)
    g_uf = _UF(len(groups))
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            a, b = groups[i], groups[j]
            d = _km(a["lat"], a["lon"], b["lat"], b["lon"])
            same_co = bool(a["companies"] & b["companies"])
            same_name = a["project"] and b["project"] and name_key(a["project"]) == name_key(b["project"])
            diff_name = a["project"] and b["project"] and not same_name
            if (same_co and same_name and d <= 60) or (same_co and not diff_name and d <= MERGE_KM) \
                    or (same_name and d <= 10) or d <= 2.5:
                g_uf.union(i, j)
    merged = defaultdict(list)
    for i in range(len(groups)):
        merged[g_uf.find(i)].append(groups[i])
    projs = []
    for gl in merged.values():
        idx = [k for g in gl for k in g["news"]]
        hs = [news_holes[i] for i in idx]
        comp = Counter(c for c in (clean_company(h["company"]) or _company_from_title(h["title"]) for h in hs) if c)
        names = Counter(g["project"] for g in gl if g["project"])
        projs.append({"news": idx, "reports": [], "company": comp.most_common(1)[0][0] if comp else None,
                      "companies": set(comp), "project": names.most_common(1)[0][0] if names else None,
                      "lat": float(np.median([h["lat"] for h in hs])), "lon": float(np.median([h["lon"] for h in hs])),
                      "keys": {h["key"] for h in hs}, "country": Counter(h["country"] for h in hs if h["country"]).most_common(1)[0][0] if any(h["country"] for h in hs) else None})

    # ---- 3. attach reports
    def rep_name(r):
        return clean_project(r.get("project")) or clean_project(r.get("title_project")) or None

    def rep_company(r):
        return clean_company(r.get("company"))
    standalone = []
    for r in reports:
        ivs = r.get("intervals") or []
        cols = r.get("collars") or []
        ikeys = {holeid.key(x["hole"]) for x in ivs} | {holeid.key(c["hole"]) for c in cols}
        rn = name_key(rep_name(r) or "")
        rc = (rep_company(r) or "").lower()
        rcenter = r.get("center") if (r.get("crs") or {}).get("kind") in ("utm", "latlon") else None
        best, best_s = None, 0
        for p in projs:
            s = 0
            sh = {k for k in ikeys & p["keys"] if len(re.sub(r"[^A-Z]", "", k)) >= 2 and len(k) >= 5}
            shared = len(sh)
            if shared >= 3 and shared >= 0.2 * min(len(ikeys), len(p["keys"]) or 1):
                s += 10 + min(shared, 20)
            rj, pc = _fold(r.get("jurisdiction")), _fold(p.get("country"))
            if rj and pc and rj != pc and not (rj in pc or pc in rj):
                s -= 12
            pn = name_key(p["project"] or "")
            if rn and pn and (rn == pn or (len(rn) > 4 and (rn in pn or pn in rn))):
                s += 6
            if rc and any(rc == c.lower() for c in p["companies"]):
                s += 3
            if rcenter:
                d = _km(rcenter[0], rcenter[1], p["lat"], p["lon"])
                if d <= REPORT_ATTACH_KM:
                    s += 5
                elif d > 150:
                    s -= 20
            if s > best_s:
                best, best_s = p, s
        if best is not None and best_s >= 6:
            best["reports"].append(r)
            best["keys"] |= ikeys
        else:
            standalone.append(r)

    # standalone reports: merge reports of the same project with each other
    su = _UF(len(standalone))
    for i in range(len(standalone)):
        for j in range(i + 1, len(standalone)):
            a, b = standalone[i], standalone[j]
            na, nb = name_key(rep_name(a) or ""), name_key(rep_name(b) or "")
            ka = {holeid.key(x["hole"]) for x in (a.get("intervals") or []) + (a.get("collars") or [])}
            kb = {holeid.key(x["hole"]) for x in (b.get("intervals") or []) + (b.get("collars") or [])}
            ca, cb = a.get("center"), b.get("center")
            near = ca and cb and _km(ca[0], ca[1], cb[0], cb[1]) < 25
            far = ca and cb and not near
            if not far and ((na and na == nb) or len(ka & kb) >= 10):
                su.union(i, j)
    sg = defaultdict(list)
    for i in range(len(standalone)):
        sg[su.find(i)].append(standalone[i])
    for rl in sg.values():
        c = next((r.get("center") for r in rl if r.get("center")), None)
        projs.append({"news": [], "reports": rl, "company": next((rep_company(r) for r in rl if rep_company(r)), None),
                      "companies": {rep_company(r) for r in rl if rep_company(r)},
                      "project": next((rep_name(r) for r in rl if rep_name(r)), None),
                      "lat": c[0] if c else None, "lon": c[1] if c else None, "keys": set(),
                      "country": next((r.get("jurisdiction") for r in rl if r.get("jurisdiction")), None)})

    # ---- 4. materialise each project's unified dataset
    out = []
    for p in projs:
        ds = _materialise(p, news_holes, news_ivs, ov)
        if ds is None:
            continue
        nh = sum(1 for h in ds["holes"].values() if h.get("_ok"))
        if nh >= min_holes and len(ds["intervals"]) >= min_intervals:
            out.append(ds)
        elif verbose:
            print(f"[projects] skip {ds['name']}: {nh} located holes, {len(ds['intervals'])} intervals")
    # unique slugs
    seen = Counter()
    for ds in out:
        s = ds["slug"]
        seen[s] += 1
        if seen[s] > 1:
            ds["slug"] = f"{s}-{seen[s]}"
    return out


def _slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "project").lower()).strip("-")[:60] or "project"


def _materialise(p, news_holes, news_ivs, ov):
    holes = {}          # key -> hole dict (frame fields: lat/lon or local e/n)
    intervals = []
    sources = []
    reports = p["reports"]
    # --- report collars first (most complete), then news fills gaps
    local_frames = {}
    for r in reports:
        crs = r.get("crs") or {}
        sid = r["id"]
        for c in r.get("collars") or []:
            k = holeid.key(c["hole"])
            if not k:
                continue
            h = holes.get(k) or {"id": c["hole"], "key": k, "src": set()}
            if c.get("lat") is not None:
                h.setdefault("lat", c["lat"]); h.setdefault("lon", c["lon"])
            elif c.get("e") is not None:
                h.setdefault("le", c["e"]); h.setdefault("ln", c["n"]); h.setdefault("lframe", sid)
                local_frames.setdefault(sid, 0)
                local_frames[sid] += 1
            for a, b in (("z", "z"), ("az", "az"), ("dip", "dip"), ("depth", "depth")):
                if h.get(a) is None and c.get(b) is not None:
                    h[a] = c[b]
            h["src"].add(sid)
            holes[k] = h
        for x in r.get("intervals") or []:
            intervals.append({"key": holeid.key(x["hole"]), "hole": x["hole"], "from": x["from"], "to": x["to"],
                              "el": x["el"], "grade": x["grade"], "unit": x.get("unit"), "sub": x.get("sub", 0),
                              "src": sid})
        res = r.get("resource")
        sources.append({"kind": "NI 43-101 technical report", "id": sid,
                        "title": _report_title(r),
                        "url": r.get("report_url") or r.get("archive_url"), "archive": r.get("archive_url"),
                        "date": r.get("date"), "collars": len(r.get("collars") or []),
                        "intervals": len(r.get("intervals") or []), "has_resource": bool(res)})
    rel_seen = {}
    for i in p["news"]:
        nh = news_holes[i]
        k = nh["key"]
        h = holes.get(k) or {"id": nh["id"], "key": k, "src": set()}
        if h.get("lat") is None:
            h["lat"], h["lon"] = nh["lat"], nh["lon"]
        for a in ("z", "az", "dip", "depth"):
            if h.get(a) is None and nh.get(a) is not None:
                h[a] = nh[a]
        h["src"].add("news:" + nh["rid"])
        holes[k] = h
        for x in news_ivs.get((nh["rid"], nh["id"]), []):
            intervals.append({"key": k, "hole": nh["id"], **x, "src": "news:" + nh["rid"]})
        if nh["url"] and nh["url"] not in rel_seen:
            rel_seen[nh["url"]] = {"kind": "news release", "id": "news:" + nh["rid"],
                                   "title": re.sub(r"\s*-\s*Junior Mining Network.*$", "", (nh["title"] or nh["url"]))[:140],
                                   "url": nh["url"], "date": nh["date"]}
    sources.extend(sorted(rel_seen.values(), key=lambda s: s.get("date") or "", reverse=True))

    # --- dedupe intervals across sources (same hole/from/to/element)
    seen, iv2 = set(), []
    for x in intervals:
        if not x["key"] or x["to"] is None or x["from"] is None or x["to"] <= x["from"]:
            continue
        kk = (x["key"], round(x["from"], 1), round(x["to"], 1), x["el"])
        if kk in seen:
            continue
        seen.add(kk)
        iv2.append(x)
    intervals = iv2

    # --- frame: geographic when possible
    geo = [h for h in holes.values() if h.get("lat") is not None]
    loc = [h for h in holes.values() if h.get("lat") is None and h.get("le") is not None]
    frame = None
    if geo:
        lat0 = float(np.median([h["lat"] for h in geo])); lon0 = float(np.median([h["lon"] for h in geo]))
        mE = 111320.0 * math.cos(math.radians(lat0)); mN = 110540.0
        for h in geo:
            h["x"] = (h["lon"] - lon0) * mE; h["y"] = (h["lat"] - lat0) * mN; h["_ok"] = True
        frame = {"kind": "geo", "lat0": lat0, "lon0": lon0, "mE": mE, "mN": mN}
        # fit local-grid report collars onto the geographic frame via shared holes
        for fid in local_frames:
            both = [h for h in holes.values() if h.get("lframe") == fid and h.get("lat") is not None]
            only = [h for h in holes.values() if h.get("lframe") == fid and h.get("lat") is None]
            if len(both) >= 3 and only:
                T = _fit_similarity([(h["le"], h["ln"]) for h in both], [(h["x"], h["y"]) for h in both])
                if T is not None:
                    for h in only:
                        h["x"], h["y"] = _apply(T, h["le"], h["ln"]); h["_ok"] = True
    elif loc:
        fid = Counter(h["lframe"] for h in loc).most_common(1)[0][0]
        xs = [h["le"] for h in loc if h["lframe"] == fid]; ys = [h["ln"] for h in loc if h["lframe"] == fid]
        x0, y0 = float(np.median(xs)), float(np.median(ys))
        for h in loc:
            if h["lframe"] == fid:
                h["x"] = h["le"] - x0; h["y"] = h["ln"] - y0; h["_ok"] = True
        frame = {"kind": "local", "grid": fid, "e0": x0, "n0": y0}
    if frame is None:
        return None
    # drop wild outliers (>40 km from the median — a mis-parsed coordinate)
    okh = [h for h in holes.values() if h.get("_ok")]
    if okh:
        mx = float(np.median([h["x"] for h in okh])); my = float(np.median([h["y"] for h in okh]))
        for h in okh:
            if math.hypot(h["x"] - mx, h["y"] - my) > 40000:
                h["_ok"] = False

    rnames = []
    for r in sorted(reports, key=lambda r: r.get("date") or "", reverse=True):
        x = clean_project(r.get("project")) or clean_project(r.get("title_project"))
        if x and name_key(x) != name_key(clean_company(r.get("company")) or ""):
            rnames.append(x)
    name = (rnames[0] if rnames else None) or clean_project(p.get("project")) or clean_company(p.get("company")) or "Unnamed project"
    company = clean_company(p.get("company"))
    if company and name_key(company) == name_key(name):
        company = None
    slug = _slugify(name + ("-" + company if company else ""))
    o = ov.get(slug) or {}
    name = o.get("name", name)
    company = o.get("company", company)
    region = p.get("country") or next((r.get("jurisdiction") for r in reports if r.get("jurisdiction")), None)
    dates = [s.get("date") for s in sources if s.get("date")]
    # resource statements & block parameters from attached reports (latest first)
    res = []
    for r in sorted(reports, key=lambda r: r.get("date") or "", reverse=True):
        if r.get("resource"):
            res.append({"source": r["id"], "date": r.get("date"), "url": r.get("report_url") or r.get("archive_url"),
                        "title": sources[[s.get("id") for s in sources].index(r["id"])]["title"] if r["id"] in [s.get("id") for s in sources] else r["id"],
                        **r["resource"]})
    blocks = [r.get("block_size") for r in reports if r.get("block_size")]
    dens = [r.get("density") for r in reports if r.get("density")]
    return {"slug": slug, "name": name, "company": company, "region": region,
            "frame": frame, "holes": holes, "intervals": intervals, "sources": sources,
            "resources": res, "report_block": blocks[0] if blocks else None,
            "commodities": [c for c in (str(r.get("commodity") or "").split("-")[0] for r in reports) if c],
            "report_density": dens[0] if dens else None,
            "updated": max(dates) if dates else None,
            "kinds": sorted({("report" if s["kind"].startswith("NI") else "news") for s in sources})}


def _report_title(r):
    nm = clean_project(r.get("project")) or clean_project(r.get("title_project"))
    co = clean_company(r.get("company"))
    t = "NI 43-101 technical report"
    if nm:
        t += " — " + nm
    if co and (not nm or name_key(co) != name_key(nm)):
        t += " (" + co + ")"
    if r.get("date"):
        t += ", " + str(r["date"])[:7]
    return t


def _fit_similarity(src, dst):
    """2D similarity transform (scale, rotation, translation) src->dst, least squares."""
    A = np.asarray(src, float); B = np.asarray(dst, float)
    ca, cb = A.mean(0), B.mean(0)
    a, b = A - ca, B - cb
    H = a.T @ b
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[1] *= -1
        R = Vt.T @ U.T
    s = S.sum() / max((a ** 2).sum(), 1e-9)
    if not (0.2 < s < 5):
        return None
    t = cb - s * R @ ca
    resid = np.sqrt((((s * (R @ A.T)).T + t - B) ** 2).sum(1)).mean()
    if resid > 250:
        return None
    return (s, R, t)


def _apply(T, x, y):
    s, R, t = T
    v = s * R @ np.array([x, y]) + t
    return float(v[0]), float(v[1])
