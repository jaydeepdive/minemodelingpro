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
2. **NI 43-101 technical reports** — two sources, one extractor.
   - **The MineTerminalPro partner catalogue** (`mtp_reports.py`): 4,687 reports
     from 920 companies filed since 2010. `/tech-reports` lists them,
     `/tech-reports/{id}/text` serves the whole report as layout-preserved text
     (`pdftotext -layout`), which every MMP extractor reads — so a report is read
     without downloading its PDF. The key lives ONLY in `MTP_API_KEY`; this repo
     is public and a leaked key is revoked, not recovered. Commands: `survey`,
     `refresh` (rebuild the catalogue), `fetch` (download text into
     `data/cache/mtp_text/`, gitignored, ~535 MB), `ingest` (fetch + extract),
     `reextract` (re-read everything cached — free, no API calls), `since <stamp>`.
     Downloading and extracting are deliberately separate: the extractor changes
     far more often than a filed report does, so improving it costs a local pass
     rather than four thousand requests against a 20,000/day limit.
   - **ceo.ca's public feed** (`ceo_sedar`), hourly, for new filings as they appear.
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
    - `drill_tables.py` — **full drill-data extraction from 43-101s**, from PDF word positions (PyMuPDF) or from layout text (`extract_report_text`, `TextDoc`): collar tables, intercept/assay tables (multi-page, continuation pages, "incl."/"and" rows, ft→m, ppm/oz/t→std units), CRS/UTM zone (text, EPSG codes, then collar-elevation↔DEM matching), resource statement, parent block size, density, stated deposit extent, project name from the cover. One JSON per report in `data/keep/mmp_reports/` (committed). `python -m minemodelingpro.drill_tables all` processes every archived PDF; `mtp_reports reextract` re-reads the cached catalogue text. **Resource tables are read by their own column headers** (`_res_by_header`) and only fall back to arithmetic triples where no header speaks — `tonnes × grade = contained` is scale-invariant, so a header reading "Tonnage (000)" is the only thing that distinguishes 26 Mt from 50,000 t. Three tables on a page are three statements (`tbl`), the deposit's own metal is preferred over its equivalents (`primary`), and a resource has a floor in tonnes (`_RES_MIN_T`) and in grade (`res_grade_ok`) so a contact-ratio table is not read as ore.
    - `domain.py` — **the wireframe**: a surface triangulated through the mineralised intercepts in the body's own plane (principal axes), inflated to the thickness those intercepts show; a filled 3-D Delaunay for bodies with no plane to them. The alpha criterion drops any patch wider than the drill pattern, so the body ends where the drilling ends. On by default (`domain` setting); it moved every filed-statement project towards its filed tonnage and none away (Aurmac 0.83→1.01, Empire 0.89→1.07, Murray Brook 0.70→0.74).
    - `extent.py` — the body's dimensions as the report states them in prose ("traced over 1,200 m of strike, to 450 m depth, true widths of 2 to 15 m"). The qualified person's wireframe stated in words, and the only check on whether a model reaches its deposit that needs no filed tonnage. `model3d._coverage` turns it into the share of the described body each model covers; reported, never applied.
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
- **A failed push that says `could not read Username for 'https://github.com'` is usually NOT an expired token.** Two separate things produce it. (1) The nightly build and report ingest push to `main` while you work, so you are behind — but git checks fast-forward against the *local* remote-tracking ref and fails before it ever authenticates, so the error can appear as anything. (2) GitHub redirects the push URL (`…​.git` → `…​.git/`) and **git drops credentials across a redirect**, so a token stored as a bare username with no password is lost mid-request. Fix (2) once, in place, without ever printing the token:
  `git remote set-url origin "$(git config --get remote.origin.url | sed -E 's#^https://([^@:]*)@#https://x-access-token:\1@#')"`
  Check which one you have before blaming the credential: `git fetch origin && git rev-list --left-right --count origin/main...HEAD`.
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

## What limits accuracy now (measured 2026-10-16, n=287)

The scoreboard is in `data/keep/mmp_validation.json` and stamped into every page.
It is measured over 287 projects with a filed statement, not the 17 the earlier
version of this section rested on — and the enlarged sample overturned part of
what that said. **Correct the record: the +0.91 correlation between error and
envelope coverage was an artefact of 17 projects. Over 63 with stated geometry it
is −0.17.** What actually predicts the error is how much drilling the filings
published:

| | n | median | middle half | within 2× |
|---|---|---|---|---|
| under 20 assayed holes | 44 | 0.397× | 0.05–1.36× | 10 |
| 20 to 60 | 61 | 1.045× | 0.34–2.61× | 22 |
| 60 to 150 | 50 | 0.972× | 0.48–2.22× | 21 |
| over 150 | 16 | 0.880× | 0.35–1.83× | 8 |

Past twenty assayed holes the median is essentially right and the spread narrows
as drilling grows. `accuracy_bands()` in validate.py computes this and `stamp()`
writes each project's own band into its page, so a page quotes the record for its
band rather than one global figure.

### Tested and NOT worth redoing (all measured, all negative)

- **Block size against vein width.** The error grows monotonically with the ratio
  (|log err| 0.94 / 1.29 / 1.69 for block ≤ width, 1–3×, >3×) which looks causal
  and is not. Capping the block at the vein thickness on the 22 worst cases:
  median |log err| 1.416 → 1.427, within 1.5× 2 → 1. Confounded — the worst
  ratios are the thinnest veins, hard for other reasons.
- **The zero-fill margin, and removing the zero-fill.** Costs a median 57% of the
  grade, and the wireframe now does the bounding it used to do, so it was worth
  re-testing. Over 21 projects with ≥60 intervals: median 0.590× → 0.821× with no
  fill, error 0.991 → 0.993. It shifts the distribution rather than tightening it
  (Lockerby 0.76× → 1.16×, Zacatecas 11.2× → 22.9×). A bias knob, not an accuracy
  knob — leave `gap_fill` on.
- **Government collars to place orphaned assays.** 813,586 gov holes with
  coordinates; 95 of 199 orphaned-assay projects have gov holes within 25 km and
  there is **1 ID match in 199 reports**. Quebec logs its own internal numbers
  (`23121`) not company hole names (`GR-10-108`), and carries no assays. No join
  exists to build.

### Real and fixed today

- **Scoring a filed statement on the primary metal's cut-off curve.** Raising a
  lead cut-off does not concentrate copper, so a filed copper grade could sit
  above the whole series and clamp the score to the end of the curve. Each metal
  now carries an `own` series over the same deposit blocks ordered by its own
  grade (`_own_series`). Comparisons running off the curve 56 → 35, overall
  |log err| 1.312 → 1.234, within 2× 55 → 61.

### Still open, ranked

1. **Extractor recall.** 16% of the reports MMP read nothing from have a page
   that parses as a drill table; about half of those are genuine (São Jorge page
   62 is a standard intercept table with a two-line header that we walk past).
   **448 resource-estimate reports** are in that bucket and come with a filed
   statement, so recovering them adds drilling *and* a figure to score against.
   Build a labelled recall set from the candidate pages first — my crude probe's
   false positives were statistics tables.
2. **News-release joins.** 526 projects have located holes and no grades;
   Closeology's bank carries both. The join exists; its failure rate is unmeasured.
3. **Prose intercepts.** 44% of collars-only reports state intercepts in prose but
   only 9% name a hole we can locate. Worth 30–50 thin models. Last.

### The floor

1,904 reports tabulate no drill data at all (Diablillos cites 977 holes totalling
193 km and tabulates none); 54% of those still state a resource. Those projects
contribute a statement, the body's geometry and metallurgy, and will never produce
a model. 563 models from 4,927 reports is mostly this, plus the fact that reports
cluster: 4,927 reports describe ~2,736 distinct properties.

## Reasonable backlog / ideas

- Downhole surveys: supported end to end (drill_tables reads survey tables — hole/depth/azimuth/dip, plausibility-filtered; model3d desurveys by minimum curvature; the viewer draws curved paths). As of 2026-09-19 none of the 201 archived reports publish real survey tables, so holes are straight on collar az/dip until a report or feed supplies them.
- Coordinate systems: drill_tables searches every EPSG projected CRS for the report's stated location (UTM, MTM, US state plane in ft, national grids), confirmed by collar-elevation↔DEM fit. Reports in a pure mine grid (e.g. Goldboro, Island Gold) stay local-frame.
- Navigation (2026-10-13): Orbit/Pan buttons bottom-centre, hold space or right-drag to pan,
  grab-the-ground panning, arrow keys/WASD (Q/E up-down), +/- zoom, R reset, flick-to-glide.
- Names: automatic from report covers / release headlines; fix the rest in `data/keep/mmp_project_overrides.json` (`{slug: {name, company}}`, `_merge` to join projects).
- News holes with easting/northing but no zone (~1,900 in the bank) could be placed by borrowing the zone of a report/release for the same project.
- Optionally consume MiningNewsTerminal's pre-parsed intervals to fill releases where geolocation succeeds but assay extraction was thin.
