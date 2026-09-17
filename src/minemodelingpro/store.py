"""MineModelingPro data store — the standard drill-hole data model for building
complete deposit models (collar / survey / assay / lithology), plus a
deposit-model reference store for resource & block-model parameters lifted from
NI 43-101 technical reports (to cross-check MMP's own models).

This is a SEPARATE database from the news drill bank (data/keep/drillbank.sqlite):
the government drill-hole databases run to hundreds of thousands of holes and
would swamp the timely news feed. MMP consumes from BOTH — the news bank for
recent full-assay releases, and this store for the historical backbone.

Tables
------
sources      one row per data source pull (province DB, 43-101 report, news)
collars      one row per hole: id, source, project, x/y/z, datum, az, dip, depth,
             + assay-presence flags and the linked assessment/report id
survey       downhole survey stations (depth, az, dip) where available
assays       one row per sample interval per element: from/to/length/element/grade
             — the FULL downhole string, every sample (not headline intercepts)
lithology    downhole geology intervals where available
deposit_model resource/block-model parameters from 43-101 (tonnes, grade, cutoff,
             extents) for cross-referencing modelled deposits

Every table carries source_id so provenance is never lost and a source can be
re-pulled idempotently (delete-by-source then re-insert).
"""
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_ROOT, "data", "keep", "mmp.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,          -- stable slug, e.g. 'gov:nb', 'ni43101:<hash>'
    kind TEXT,                    -- 'gov_drillholes' | 'ni43101' | 'assessment' | 'news'
    name TEXT,                    -- human label
    url TEXT,
    jurisdiction TEXT,            -- province/territory/country
    pulled_at TEXT,               -- ISO timestamp of last pull
    n_collars INTEGER DEFAULT 0,
    n_assays INTEGER DEFAULT 0,
    note TEXT
);
CREATE TABLE IF NOT EXISTS collars (
    hole_uid TEXT PRIMARY KEY,    -- source_id + ':' + native hole id
    source_id TEXT,
    native_id TEXT,               -- hole id as given by the source
    company TEXT,
    project TEXT,
    jurisdiction TEXT,
    lat REAL, lon REAL,
    easting REAL, northing REAL, utm_zone INTEGER, utm_hemi TEXT, datum TEXT,
    elev_m REAL, azimuth REAL, dip REAL, depth_m REAL,
    year_drilled INTEGER,
    has_assay INTEGER DEFAULT 0,  -- 1 if assays exist for this hole (in assays table OR flagged)
    assay_flags TEXT,             -- e.g. 'Au,Ag,Cu' when the source only flags presence
    report_ref TEXT,              -- assessment-file / report number to chase full assays
    url TEXT
);
CREATE TABLE IF NOT EXISTS survey (
    source_id TEXT, hole_uid TEXT, depth_m REAL, azimuth REAL, dip REAL
);
CREATE TABLE IF NOT EXISTS assays (
    source_id TEXT, hole_uid TEXT, native_id TEXT,
    from_m REAL, to_m REAL, length_m REAL,
    element TEXT, grade REAL, unit TEXT,
    is_subinterval INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lithology (
    source_id TEXT, hole_uid TEXT, from_m REAL, to_m REAL, rock TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS deposit_model (
    id TEXT PRIMARY KEY,          -- source_id + ':' + zone/category
    source_id TEXT, project TEXT, jurisdiction TEXT,
    category TEXT,                -- Measured/Indicated/Inferred/Proven/Probable
    tonnes REAL, grade REAL, grade_unit TEXT, contained REAL, contained_unit TEXT,
    cutoff REAL, cutoff_unit TEXT, commodity TEXT,
    report_url TEXT, report_date TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS model_method (
    id TEXT PRIMARY KEY,          -- source_id + ':' + section/zone
    source_id TEXT, project TEXT, jurisdiction TEXT, commodity TEXT,
    estimation_method TEXT,       -- OK / ID2 / ID3 / NN / MIK / ...
    software TEXT,                 -- Leapfrog / Vulcan / Datamine / GEMS / Surpac ...
    block_size TEXT,              -- e.g. "10 x 10 x 5 m"
    compositing_m TEXT,
    capping TEXT,                 -- top-cut / grade capping note
    density TEXT,                 -- bulk density t/m3 (by domain)
    cutoff TEXT, cutoff_basis TEXT,   -- e.g. "0.3 g/t Au", "US$75/t NSR"
    search_params TEXT,           -- search ellipse ranges / orientation
    compositing TEXT, domaining TEXT, classification TEXT, qaqc TEXT,
    section_ref TEXT,             -- e.g. "Section 14"
    method_text TEXT,             -- the narrative excerpt (training/RAG material)
    report_url TEXT, report_date TEXT
);
CREATE TABLE IF NOT EXISTS metallurgy (
    id TEXT PRIMARY KEY,          -- source_id + ':met'
    source_id TEXT, project TEXT, jurisdiction TEXT, commodity TEXT,
    process_types TEXT,           -- CIL/CIP/heap leach/flotation/POX/roasting/BIOX/gravity/...
    refractory TEXT,              -- 'refractory' | 'non-refractory' | 'double refractory' | 'preg-robbing' | null
    recovery_summary TEXT,        -- headline recovery statement(s)
    recoveries TEXT,              -- captured recovery % values (element -> %, best effort)
    grind_p80_um REAL,            -- primary grind size (P80, microns)
    reagent_notes TEXT,           -- cyanide/lime/reagent consumption
    throughput TEXT,              -- plant throughput (tpd / Mtpa)
    met_text TEXT,                -- metallurgy narrative excerpt (training/RAG)
    report_url TEXT, report_date TEXT
);
CREATE TABLE IF NOT EXISTS economics (
    id TEXT PRIMARY KEY,          -- source_id + ':econ'
    source_id TEXT, project TEXT, jurisdiction TEXT, commodity TEXT,
    study_type TEXT,              -- PEA | Pre-Feasibility | Feasibility | (null)
    npv_aftertax_musd REAL, npv_pretax_musd REAL, npv_discount_pct REAL,
    irr_aftertax_pct REAL, irr_pretax_pct REAL,
    payback_years REAL,
    initial_capital_musd REAL, sustaining_capital_musd REAL, total_capital_musd REAL,
    aisc TEXT,                    -- all-in sustaining cost (raw, e.g. 'US$950/oz')
    cash_cost TEXT,               -- C1 / cash cost (raw)
    mine_life_years REAL,
    annual_production TEXT,       -- e.g. '150,000 oz/yr' (raw)
    throughput TEXT,              -- e.g. '10,000 tpd' / '3.5 Mtpa' (raw)
    avg_grade TEXT,               -- headline mill/head grade (raw) when stated
    recovery TEXT,                -- headline recovery (raw) when stated in econ summary
    metal_price_assumptions TEXT, -- price deck used (raw)
    highlights TEXT,              -- the matched economic sentences (provenance)
    econ_text TEXT,               -- economic-section narrative excerpt (training/RAG)
    report_url TEXT, report_date TEXT
);
CREATE INDEX IF NOT EXISTS ix_economics_src ON economics(source_id);
CREATE INDEX IF NOT EXISTS ix_collars_src ON collars(source_id);
CREATE INDEX IF NOT EXISTS ix_model_method_src ON model_method(source_id);
CREATE INDEX IF NOT EXISTS ix_metallurgy_src ON metallurgy(source_id);
CREATE INDEX IF NOT EXISTS ix_collars_juris ON collars(jurisdiction);
CREATE INDEX IF NOT EXISTS ix_assays_hole ON assays(hole_uid);
CREATE INDEX IF NOT EXISTS ix_assays_src ON assays(source_id);
"""


def connect(db=DB_PATH):
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def record_source(con, src):
    con.execute("""INSERT INTO sources(id,kind,name,url,jurisdiction,pulled_at,n_collars,n_assays,note)
        VALUES(:id,:kind,:name,:url,:jurisdiction,:pulled_at,:n_collars,:n_assays,:note)
        ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,url=excluded.url,
        jurisdiction=excluded.jurisdiction,pulled_at=excluded.pulled_at,
        n_collars=excluded.n_collars,n_assays=excluded.n_assays,note=excluded.note""", {
        "id": src["id"], "kind": src.get("kind"), "name": src.get("name"),
        "url": src.get("url"), "jurisdiction": src.get("jurisdiction"),
        "pulled_at": src.get("pulled_at"), "n_collars": src.get("n_collars", 0),
        "n_assays": src.get("n_assays", 0), "note": src.get("note")})


def replace_collars(con, source_id, rows):
    con.execute("DELETE FROM collars WHERE source_id=?", (source_id,))
    con.executemany("""INSERT OR REPLACE INTO collars
        (hole_uid,source_id,native_id,company,project,jurisdiction,lat,lon,
         easting,northing,utm_zone,utm_hemi,datum,elev_m,azimuth,dip,depth_m,
         year_drilled,has_assay,assay_flags,report_ref,url)
        VALUES (:hole_uid,:source_id,:native_id,:company,:project,:jurisdiction,:lat,:lon,
         :easting,:northing,:utm_zone,:utm_hemi,:datum,:elev_m,:azimuth,:dip,:depth_m,
         :year_drilled,:has_assay,:assay_flags,:report_ref,:url)""", rows)


def replace_assays(con, source_id, rows):
    con.execute("DELETE FROM assays WHERE source_id=?", (source_id,))
    if rows:
        con.executemany("""INSERT INTO assays
            (source_id,hole_uid,native_id,from_m,to_m,length_m,element,grade,unit,is_subinterval)
            VALUES (:source_id,:hole_uid,:native_id,:from_m,:to_m,:length_m,:element,:grade,:unit,:is_subinterval)""", rows)


def add_deposit_model(con, rows):
    con.executemany("""INSERT OR REPLACE INTO deposit_model
        (id,source_id,project,jurisdiction,category,tonnes,grade,grade_unit,contained,
         contained_unit,cutoff,cutoff_unit,commodity,report_url,report_date,note)
        VALUES (:id,:source_id,:project,:jurisdiction,:category,:tonnes,:grade,:grade_unit,
         :contained,:contained_unit,:cutoff,:cutoff_unit,:commodity,:report_url,:report_date,:note)""", rows)


def stats(con):
    s = {}
    s["sources"] = con.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    s["collars"] = con.execute("SELECT COUNT(*) FROM collars").fetchone()[0]
    s["collars_located"] = con.execute(
        "SELECT COUNT(*) FROM collars WHERE lat IS NOT NULL").fetchone()[0]
    s["assays"] = con.execute("SELECT COUNT(*) FROM assays").fetchone()[0]
    s["deposit_models"] = con.execute("SELECT COUNT(*) FROM deposit_model").fetchone()[0]
    s["by_jurisdiction"] = {r[0]: r[1] for r in con.execute(
        "SELECT jurisdiction, COUNT(*) FROM collars GROUP BY jurisdiction ORDER BY 2 DESC").fetchall()}
    return s
