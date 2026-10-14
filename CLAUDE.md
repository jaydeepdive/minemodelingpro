# MineModelingPro — session handoff & operations guide

You are taking over management of **MineModelingPro (MMP)**. This file is the
context for that. (Project Closeology is managed in a *separate* chat — see
"Relationship to Closeology" below. Don't manage Closeology from here.)

## What MMP is

MMP turns drill-hole data into **interactive 3D deposit models** and extracts
**NI 43-101 economics** (NPV/IRR/payback/capex/AISC/LOM/resource tables) from
SEDAR technical reports. Output is a public gallery of orbitable grade models
plus per-deposit viewers.

- **Repo:** github.com/jaydeepdive/minemodelingpro (public)
- **Local (this Mac):** `~/minemodelingpro` — connect this folder to the session
- **Live site:** https://jaydeepdive.github.io/minemodelingpro/ (GitHub Pages, source = GitHub Actions)
- **Push auth:** the git remote embeds a fine-grained token (Contents R/W on this repo). `git push` just works. To hit the GitHub API, extract it: `TOK=$(git remote get-url origin | sed -E 's#https://([^@]*)@.*#\1#'); TOK="${TOK#*:}"`.

## Data streams (three inputs, one shard store)

1. **Government drill-hole backbone + geochemistry** — provincial collars/assays
   (`mmp-backbone`, `mmp-geochem` workflows) → size-capped parquet shards under
   `data/keep/mmp/` (committed).
2. **NI 43-101 technical reports** — resource/economics/methodology extracted
   from SEDAR PDFs (`mmp-reports` workflow; `ceo_sedar` collects the manifest and
   the PDFs hourly from ceo.ca's public feed — the SEDAR+ scraper is retired).
3. **Drill-result news** — recent full-assay releases from Closeology's drill
   bank, synced in at build time (see below), rendered as live, densifying models.

`minemodelingpro.export` merges gov + reports + news into the shard store;
`model3d.build_all('site')` renders the gallery + viewers + `models_index.json`.

## Repo layout

- `src/minemodelingpro/` — the package:
  - **Modelling (rebuilt 2026-09-19):**
    - `projects.py` — the **project registry**: ONE dataset per project. Clusters news holes, merges clusters of the same company/project, attaches each 43-101 report by shared hole IDs / location / name, unifies holes across sources by normalised hole ID (`holeid.py`), and lists every contributing source. Also **re-geolocates mis-zoned news releases** by matching published collar elevations to the DEM (`_repair_zones`). Manual name/company fixes: `data/keep/mmp_project_overrides.json` (`{slug: {name, company}}`).
    - `model3d.py` — per-project model: desurvey, grade SEGMENTS (overlapping "including" intervals split, remainder grade back-calculated), composites (gaps between intercepts = 0 grade), IDW² block model on the **report's parent block size** (else the median 43-101 block for that commodity), grade-tonnage + **contained metal** curve, terrain, gallery, `models_index.json`.
    - `model3d_viewer.html` — viewer template (three.js r128): screen-space-width drill intervals coloured by discrete grade bins, collapsible panels (bottom sheets on mobile), touch controls, double-click/double-tap pivot, zoom-to-cursor, go-to-hole, plan/section views, terrain with hillshade + contours + opacity, grade/tonnes/metal panel with the published-resource cross-check.
    - `gallery.html` — gallery template: search, commodity/region/source filters, sort (project A–Z default).
    - `terrain.py` — AWS Terrain Tiles DEM (cached in `data/cache/dem`, gitignored) + `region_of()` (province/state/country from `data/geo/admin_regions.json`, simplified Natural Earth).
    - `drill_tables.py` — **full drill-data extraction from 43-101 PDFs** by word positions (PyMuPDF): collar tables, intercept/assay tables (multi-page, continuation pages, "incl."/"and" rows, ft→m, ppm/oz/t→std units), CRS/UTM zone (text, EPSG codes, then collar-elevation↔DEM matching), resource statement (rows validated by tonnes×grade≈contained, latest effective date wins), parent block size, density, project name from the cover. One JSON per report in `data/keep/mmp_reports/` (committed). `python -m minemodelingpro.drill_tables all` processes every archived report (both release archives).
    - `model3d_legacy.py` was removed — the old per-source/per-cluster builder (git history if needed).
  - `export.py` — merge all sources → shards; `shards.py` — the sharded parquet store; `store.py` — working sqlite.
  - `gov_drillholes.py`, `gov_samples.py` — government backbone/geochem ingest.
  - `pdf_reports.py`, `sedar.py`, `sedar_collect.py`, `ceo_sedar.py`, `report_archive.py` — SEDAR 43-101 pipeline (economics/methodology/metallurgy text extraction + archive).
  - `closeology_sync.py` — mirrors ALL Closeology-collected data in at build time (see below).
  - `drill_sync.py` — **dormant.** Early direct-API adapter for MiningNewsTerminal. Keep as a stub.
- `src/site_theme.py` — standalone MMP nav/footer for the gallery.
- `data/keep/mmp/` — committed parquet shard store. `data/keep/mmp_reports/` — per-report drill extraction JSON (what the models use). `data/keep/mmp_*` json — report queue/index/ledgers.
- `scripts/` — `sedar_batch.sh` (run by launchd), `reextract_econ.py`, the launchd plist.

**Not committed (runtime, gitignored):** `data/keep/mmp.sqlite`, `data/keep/drillbank.sqlite` (synced from Closeology), `data/keep/sedar_pdfs/`, `data/closeology/`, `data/cache/` (DEM tiles).

## Build & deploy (GitHub Actions)

- **`mmp-daily-build`** (build.yml, daily 05:40 UTC): `closeology_sync` → `shards rebuild` → `export` → `model3d.build_all('site')` → commit `site/` + shards → deploy Pages. This is the one that publishes the site.
- **`mmp-43101-reports`** (mmp-reports.yml, daily 06:00 UTC): SEDAR report text extraction into shards, then **`drill_tables all`** — full drill-data extraction for any archived report not yet at the current extractor version (`EXTRACTOR` in drill_tables.py; bump it to force a re-extract, or dispatch with `refresh=true`).
- **`mmp-drillhole-backbone`** (mmp-backbone.yml, Mondays 08:00 UTC): rebuild the gov collar backbone (owns `mmp.sqlite` + the committed parquet).
- **`mmp-gov-geochem`** (mmp-geochem.yml, monthly): government geochemistry.
- **`mmp-ceo-sedar`** (ceo-sedar.yml, hourly): collect the SEDAR manifest from ceo.ca's public feed. (Renamed from `closeology-ceo-sedar` 2026-09-19.)
- **`deploy-pages-fast`** (deploy.yml): fast Pages deploy on ordinary `site/**` pushes.

Trigger any of these on demand: `curl -s -X POST "https://api.github.com/repos/jaydeepdive/minemodelingpro/actions/workflows/<file>.yml/dispatches" -H "Authorization: token $TOK" -d '{"ref":"main"}'`.

## Relationship to Closeology (important)

MMP is **downstream** of Closeology. They are separate repos, sites, and chats.
- MMP does **not** run in Closeology and Closeology does **not** build MMP. Never re-add MMP code to the Closeology repo.
- `closeology_sync` pulls Closeology's public data (drill bank, metal prices, MINFILE facts, boundaries) at build time — locally from `~/closeology` if present (`CLOSEOLOGY_REPO`), else from GitHub (public repo, no token needed). It also places `drillbank.sqlite` where the modeler reads it. Because it enumerates the data directory, new Closeology datasets flow in automatically.
- Closeology's **drill radar deep-links to MMP models** via the `models_index.json` MMP publishes (release_url → model_url map). Keep that file's shape stable (`by_release` object) or those links break.
- The **MiningNewsTerminal API integration lives in Closeology's crawler**, not here — because the API has no coordinates and Closeology already geolocates. MMP just inherits the geolocated bank.

## No machine of Jordan's is in the loop (2026-10-14)

Everything runs in the cloud: GitHub Actions collects and extracts, DigitalOcean
serves, and the app deploys itself. The **SEDAR+ direct scraper is retired** —
it drove a real Chrome session on the Mac for NI 43-101 PDFs, SEDAR+ changed its
search form in July 2026, and every run after that collected nothing.
`scripts/sedar_batch.sh` is now a stub that exits quietly (so a leftover launchd
job does no harm) and `sedar_collect.py` refuses to run as a script. New technical
reports come from the **ceo.ca public feed**, collected hourly by `ceo_sedar.py` in
CI. To remove the old agent from the Mac entirely:
`launchctl unload ~/Library/LaunchAgents/com.thedeepdive.sedar.plist && rm ~/Library/LaunchAgents/com.thedeepdive.sedar.plist`

## Local how-tos (on the Mac, via device_bash)

```bash
cd ~/minemodelingpro     # or $HOME/mnt/minemodelingpro inside device_bash
pip3 install -q --user pandas pyarrow shapely pyproj pdfplumber camelot-py opencv-python-headless pymupdf pytesseract
CLOSEOLOGY_REPO=$HOME/mnt/closeology PYTHONPATH=src python3 -m minemodelingpro.closeology_sync
PYTHONPATH=src python3 -m minemodelingpro.shards rebuild
PYTHONPATH=src python3 -m minemodelingpro.export
PYTHONPATH=src python3 -c "from minemodelingpro import model3d; model3d.build_all('site')"
```

## Gotchas learned (save yourself the pain)

- **`device_bash` runs in an isolated Linux VM with a 180 s cap per call, separate PID namespace.** A full `model3d.build_all` render can exceed 180 s and background processes do NOT survive across calls — let **CI** do the heavy render, or bound local runs. Files persist between calls; shell state does not.
- **git `index.lock` / `*.lock`** get left by concurrent jobs and block commits. Clear with `find .git -name '*.lock' -delete` (needs delete permission on the folder — request it once if `rm` says "Operation not permitted").
- **Pushes race** with CI/collector commits. Pattern: `git stash -u -q; git pull --rebase origin main; git stash drop; git push` and retry.
- **Heavy assets are NOT in git** — never try to commit `mmp.sqlite`, `sedar_pdfs/`, `drillbank.sqlite`; they're regenerated/synced.
- **MCP connections drop** mid-session; reload deferred tools via ToolSearch and continue.
- A project model needs ≥3 located holes and ≥8 intervals on them (`projects.assemble`). Report intervals only place in 3D when their holes have collars — from the same report, another report, or a news release (joined by normalised hole ID). Reports with intercept tables but no collar table (common in PEAs) still contribute their published resource to the cross-check.
- Closeology's drill bank sometimes geolocates a release in the wrong UTM zone (e.g. Goliath's Golddigger put in Manitoba). MMP repairs these at build time by DEM-matching collar elevations; the build log prints `re-geolocated release …`. Worth fixing upstream in Closeology.
- The cloud Claude session cannot call the GitHub API (proxy); clone/pull over git works, release-asset downloads work. Push from the Mac (device_bash) if the container has no push credentials.

## Constraints

- **ceo.ca:** collect from the PUBLIC feed only — never log in (the single work account must not be risked).
- Grades are inverse-distance estimates **for visualization only** — not a resource estimate. Every model page says so; keep that disclaimer.

## Current state (as of 2026-09-19)

- Viewer + gallery rebuilt (mobile, collapsible panels, pivot, grade-coloured drill intervals, realistic terrain, contained metal vs published resource). One model per project (≈66–80 projects from 201 archived reports + the news bank, depending on the drill bank snapshot).
- `drill_tables` extracted all 201 archived reports (both the ni43101 queue archive and the SEDAR/ceo.ca archive); ~98 SEDAR PDFs had been archived but never ingested before.
- Region labels come from the model's coordinates (not the SEDAR filing province).

## MMP Studio — accounts, uploads, private models (added 2026-10-13, moved to DigitalOcean 2026-10-14)

Studio lets an invited client upload their own drill data and model it privately,
beside the public gallery. It is **invite-only** (MMP runs on a client basis) and
runs entirely on DigitalOcean — no third-party auth, no keys in the browser.

- **Hosting:** one App Platform app (`minemodelingpro`, region Toronto) in its own DO
  project, built from this public repo over https (no GitHub authorisation on DO).
  `web` = FastAPI serving the site + API; `modeler` = the worker; an attached
  Postgres; Spaces bucket `mmp-studio` (tor1). Domains: **minemodelingpro.com**
  (primary), www, **minemodelingpro.ca** (alias). See `studio/DEPLOY-DO.md` for the
  spec, the environment variables and the operational commands; `.do/app.yaml` is the
  reference copy of the spec.
- `studio/server.py` — the API and the static site. Session cookie (scrypt passwords,
  HMAC-signed cookie); every query scoped to the signed-in account; admins issue
  invites at `/app/admin.html`.
- `studio/worker.py` — jobs: `import` (parse files → dataset JSON → build), `build`,
  `publish`, `delete`. Claims jobs with `FOR UPDATE SKIP LOCKED`.
- `studio/autodeploy.py` — **deploys are automatic**: a thread in the worker watches
  the tip of `main` on GitHub every 5 minutes and asks the DO Apps API for a
  deployment when it moves (code push, or the nightly gallery build committing
  `site/`). No webhook, no GitHub secret, no console clicking. Needs `DO_APP_ID` +
  `DO_DEPLOY_TOKEN` in the app environment (set); `MMP_AUTODEPLOY=0` disables it.
- `studio/store.py` — the only place that talks to Postgres (psycopg3) or object
  storage (Spaces via boto3; a local directory when `SPACES_*` is unset).
- `studio/schema_pg.sql` — users, invites, projects, datasets, jobs, events. Applied
  automatically at start; idempotent. (The old Supabase `schema.sql` was removed.)
- `site/app/` — `index.html` (sign in / invite sign-up), `dashboard.html`,
  `project.html` (upload, parameters, publish), `model.html` (private 3D viewer),
  `public.html` (shared models), `admin.html` (invites + people), `app.js` (fetch
  client), `config.js` (no keys — just `/api` and the accepted file types).
- `src/minemodelingpro/imports.py` — the importer: CSV/TSV/Excel/Access/zip/Parquet →
  collars, surveys, intervals; headers matched by meaning; ft→m; %/g/t/ppm/ppb/oz-t →
  standard units; EPSG/UTM/lat-long or a local grid; writes a dataset JSON in the same
  shape as a report, so `projects.assemble` merges it like any other source.
- `src/minemodelingpro/params.py` — per-project parameters and, importantly, the
  **precedence**: `layered()` merges `auto` (report → regional) < file `_defaults` <
  per-slug entry < dataset/web settings, and reports where each value came from.
- Viewer split: `src/minemodelingpro/viewer.js` is the renderer, `model3d_viewer.html`
  the shell. `write_viewer` copies both next to the pages (`site/models/viewer.js`,
  `_shell.html`), so public pages and Studio's private viewer share one renderer.
  **Never delete `site/models/_shell.html`** — `build_all` keeps files starting with `_`.

## Where modelling parameters come from (2026-10-14)

1. **The project's most recent technical report.** `drill_tables.extract_model_params`
   lifts parent block size, bulk density, composite length, search range, capping
   grade, reporting cut-off and estimator/power from each 43-101, and
   `drill_tables.effective_date` reads its stated effective date.
   `projects._report_params` then takes them **newest first**, per key, so a recent
   resource update supplies the block size even if an older PEA supplied the density.
   Carried on the dataset as `report_params` / `report_params_from`.
2. **Else what is typical for the region + commodity.** `minemodelingpro.regional`
   measures block size and density across the whole extracted corpus and narrows from
   region+commodity → commodity → region → global as the evidence allows (minimum
   counts per key; a group's density is dropped when it disagrees with the commodity
   as a whole). Cached in `data/keep/mmp_regional_params.json`; rebuild with
   `python -m minemodelingpro.regional --build`.
3. **Else the per-commodity default**, and above all of it, whatever the user set.

`model3d.auto_params(ds)` returns both the values and a human label for each; the
model carries `param_sources`, and the block panel says "block source: technical
report (2021)" or "typical for Au in Québec (7 reports)".

## Reasonable backlog / ideas

- Downhole surveys: supported end to end (drill_tables reads survey tables — hole/depth/azimuth/dip, plausibility-filtered; model3d desurveys by minimum curvature; the viewer draws curved paths). As of 2026-09-19 none of the 201 archived reports publish real survey tables, so holes are straight on collar az/dip until a report or feed supplies them.
- Coordinate systems: drill_tables searches every EPSG projected CRS for the report's stated location (UTM, MTM, US state plane in ft, national grids), confirmed by collar-elevation↔DEM fit. Reports in a pure mine grid (e.g. Goldboro, Island Gold) stay local-frame.
- Navigation (2026-10-13): Orbit/Pan buttons bottom-centre, hold space or right-drag to pan,
  grab-the-ground panning, arrow keys/WASD (Q/E up-down), +/- zoom, R reset, flick-to-glide.
- Names: automatic from report covers / release headlines; fix the rest in `data/keep/mmp_project_overrides.json` (`{slug: {name, company}}`, `_merge` to join projects).
- News holes with easting/northing but no zone (~1,900 in the bank) could be placed by borrowing the zone of a report/release for the same project.
- Optionally consume MiningNewsTerminal's pre-parsed intervals to fill releases where geolocation succeeds but assay extraction was thin.
