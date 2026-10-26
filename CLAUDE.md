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
- **The site menu bar has exactly one definition: `site/nav.js`.** It had drifted into three (a hand-written block in `site/index.html`, `site_theme.header()` for the gallery, `chrome()` in `site/app/app.js`), which is why the bar differed page to page. Every page now renders `MMPNav.render({base, active, user})` into `<div class="topbar" id="topbar"></div>` — `base` is the path back to the site root (`""` at root, `"../"` under `app/`), `user` is passed when the page already has the session and omitted when nav.js should ask `/api/me` itself. Add a nav link in nav.js or it will not appear everywhere. The full-screen model viewer keeps its own slim bar on purpose.
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

## What limits accuracy now (measured 2026-10-16, n=321)

The scoreboard is in `data/keep/mmp_validation.json` and stamped into every page.
It is measured over 321 projects with a filed statement, not the 17 an earlier
version of this section rested on — and the enlarged sample overturned part of
what that said. **Correct the record: the +0.91 correlation between error and
envelope coverage was an artefact of 17 projects. Over 63 with stated geometry it
is −0.17.** What predicts the error is how much drilling the filings published:

| | n | median | middle half | within 2× |
|---|---|---|---|---|
| under 20 assayed holes | 50 | 0.322× | 0.05–0.88× | 12 |
| 20 to 60 | 65 | 0.805× | 0.33–2.20× | 23 |
| 60 to 150 | 54 | 0.980× | 0.46–1.64× | 25 |
| over 150 | 24 | 0.983× | 0.62–1.82× | 13 |

Past sixty assayed holes the median is within 2% of the filed tonnage and the
middle half spans less than a factor of four. Below twenty it is 0.32× and the
spread is a factor of seventeen. `accuracy_bands()` in validate.py computes this
and `stamp()` writes each project's own band into its page, so a page quotes the
record for its band rather than one global figure.

Headline, same pipeline, 595 models:

      at their cut-off   n=121  median 1.017x
      like for like      n= 88  median 0.890x
      at their grade     n=193  median 0.756x   within 1.5x 48, 2x 73
      where we hold it   n= 47  median 0.981x   within 1.5x 14, 2x 18

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
- **The deposit style's density over the regional median.** `auto_params` fills
  density from the regional table when no report states one, and that average was
  reaching `density_for` as though the project's own report had stated it. Letting
  the style supply it instead is better reasoned — a Cu-Zn massive sulphide is
  3.2 t/m³ against a 2.7 corpus average, a fifth of the tonnage — and measured
  slightly worse: on the projects where the comparison is valid, |log err| 0.978
  → 1.005 and within 1.5× of filed 48 → 44. The regional medians are measured on
  the same corpus that filed the statements, which is worth more than the physics.
  239 of 595 models rest on that table. Left as it was.
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

## Configuring the geology (2026-10-17)

For a project built from technical reports the style comes from the report's own
Item 8 deposit-type section (`deposit_style.from_text`, 94% hit rate). For an
**imported** dataset there is no report, so the chain is: `deposit_style` set for
the project → a styled report within `STYLE_BORROW_KM` (15 km) → `from_geometry`,
which infers it from intercept widths, grade variability and the commodity suite.
That last step is a guess, and it used to be the only option because the style was
not on the Studio form.

The form now leads with geology, and these are the settings that matter most:

- `deposit_style` — sets density, halo reach, block size and search anisotropy.
- `dip`, `dip_direction`, `plunge` — the structural trend, replacing the
  orientation MMP measures by principal components of the mineralised cloud
  (`model3d.frame_from_trend`). Worth setting when few holes have been drilled: a
  handful on one section have no shape to measure, and the trend they imply is the
  drill pattern's.
- `anisotropy` — search ratios along (major, intermediate, across).
- `true_width_m` — the body's thickness, used to inflate the wireframe.
- `domain_units` — **which logged geology codes ARE the mineralised body.**
- `domain` — whether to triangulate a body between the holes at all.

`imports.py` now reads **lithology and alteration intervals** (`parse_lithology`,
kind `"lithology"`), keeps the codes verbatim, and carries them to the dataset as
`lithology` with the distinct codes listed in `rock_units`. When `domain_units`
names some of them, `model3d.logged_body` samples those intervals down the hole
and the wireframe is triangulated through the LOGGED unit instead of through
whatever passes a grade threshold — the rock defines the domain and the assays
only say what grade is in it. The estimator still refuses to put a grade where no
assay reaches, so the gain is bounded by assay coverage, which is correct.

This is the direction Leapfrog takes further and is worth borrowing from:
implicit surfaces fitted to logged contacts as a signed-distance function rather
than a point cloud; a trend that warps the interpolation rather than only
stretching the search; explicit hangingwall/footwall vein modelling; and hard vs
soft contact treatment between domains, which MMP already *reads* out of reports
(the contact-plot tables) without honouring.

**Validator trap:** every list parameter but `elements` used to be coerced to
floats, so `domain_units` was silently dropped and `anisotropy` had no limits
entry. `NAME_LISTS` in params.py is the fix — add any new string-list parameter
to it.

## Importing a client's own drill files (2026-10-20)

A drill database arrives as separate tables and the importer takes them that way:
collars, downhole surveys, assays and logged geology, in any mix of files or
sheets, recognised by their columns and **joined on the normalised hole id**
(`holeid.key`, so `DDH-26-001` and `ddh26001` are one hole). What each file was
read as, which column filled which role, and how many hole ids matched across
them is written into the dataset's notes and shown on the project page — check
that table before trusting a model. `scripts/test_imports.py` holds the cases.

- **Assays may be wide or long.** A column per element (`Au_ppm`, `Cu (%)`) or one
  row per element (`ANALYTE` + `GRADE_PPM`). In long form the unit comes from the
  row's unit column, else the element label, else **the value column's header**,
  else the element's default — copper in ppm read as per cent is out by ten
  thousand. `_unit_in` treats `_` and brackets as separators, because an
  underscore is a word character to a regex and `Cu_ppm` used to hide its unit
  behind a missing word boundary.
- **Surveys may be stations or sections.** `HOLEID, DEPTH, AZIMUTH, INCLINATION`
  or `HOLEID, FROM, TO, AZIMUTH, INCLINATION`: a table with azimuth and dip, no
  assays and no logging code is a survey either way, and with sections each
  start is a station and the last end is the deepest point the survey reaches.
  A declination column is read and **reported, not applied** — it is as often the
  record of a correction already made as an instruction to make one. "Survey
  azimuths → magnetic" on the upload form adds it (`azimuth_ref`).
- **A collar file of x/y/z alone still orients its holes**: the shallowest survey
  station gives the azimuth and dip, the deepest gives the hole length, and one
  station is enough for the orientation (it just cannot curve the path). Before
  this those holes were drawn vertical.
- **The coordinate reference is worked out from the data** (`crs_mode="auto"`, the
  form's default). x/y inside ±180/±90 with a spread under two units are decimal
  degrees — no drill programme spans less than two metres — and which of the two
  is the latitude is settled by the terrain, or by which assignment lands in a
  known region. A bare easting/northing has its **UTM zone identified by matching
  the collar elevations to the DEM**: zones that land on land (`terrain.region_of`)
  are shortlisted at zoom 11 and judged at zoom 13 on three numbers — deviation
  about the mean difference, that offset, and the slope of ground against stated
  elevation. A constant offset is forgiven (a GPS z is an ellipsoid height), a
  wrong hillside is not, and the winner must beat its rival by a distance or the
  answer is reported as ambiguous and the data stays in its own grid. A flat
  plateau cannot be placed this way and says so. Feet are only considered after
  metres fail, because a compressed elevation range fits too many places.

## What a real client database costs the modeller (2026-10-20)

The first client upload — 602 collars, 10,779 survey stations, a 48,260-row
multi-element assay table — found four things, measured not guessed. The
modeller has **1 GB** (`apps-s-1vcpu-1gb`), and at that scale it peaked at
**1.67 GB**: the kernel killed it, which from the page looked like a parse that
never ended. After the fixes below the same build peaks at **720 MB** in 42 s.

- **A job that dies with its worker used to stay `running` for ever.** Nothing
  requeued it, nothing reset the project. `jobs.heartbeat_at` + `requeue_stale`
  + the sweep at worker start fix that; `scripts/test_jobs.py` covers it against
  a real Postgres (`apt-get install postgresql-16`, initdb under
  `/var/lib/postgresql`, `DATABASE_URL=...host=/var/run/postgresql&port=5433`).
  `/api/health` now reports the worker's pulse, so "is anything listening" is
  answerable without signing in.
- **The block search was chunked by block count, not by what the search
  allocates.** Every block carries its 24 nearest samples, so a 400,000-block
  chunk was half a gigabyte in flight. `MMP_BLOCK_CHUNK` (60,000 fine blocks)
  bounds it; identical results, and faster for cache reasons.
- **A modern ICP package reports 19 elements on every sample, and all of them
  are carried.** A client's multi-element package is data they paid a laboratory
  for, and the modeller is not the place to decide which of it was worth having
  — Jordan's call, and the right one. `elements` chooses what gets ESTIMATED; it
  does not decide what survives the import. What that costs is memory, and the
  memory is answered three ways: the import and the build are now separate jobs
  (the parse's several hundred megabytes are gone before the block model starts),
  `projects._materialise(consume=True)` releases each stored row as it reads it
  so two copies never coexist, and `imports.intern_rows` shares the hole ids,
  elements and units that JSON gives every row its own copy of. Measured at
  602 holes / 915,040 intervals / 19 elements: parse 409 MB in its own process,
  build 986 MB — against 1.67 GB before, in one process, reporting a third as
  many metals.
- **How many metals get reported is a question about the machine.**
  `model3d.mem_budget_mb()` reads the container's own limit, and `_by_metal`
  reports up to 24 metals with 1.8 GB or more and 8 below that, rather than
  being killed halfway through reporting all of them. **The modeler component
  needs 2 GB** (`.do/app.yaml` says so now): at 1 GB this deposit peaks at
  ~0.99 GB with 8 metals and 1.07 GB with all 19 — too close either way. The
  live spec is in the DO console, not in the repo.
- **A lab export names au_cert_num, au_analysis_code and au_flag beside
  au_ppm.** All four start with the element, and all four were being read as
  gold; a certificate number makes a six-figure grade that passes every
  plausibility check. `_element_of` now requires the words beside the element to
  be units or grade words. (Related, same day: stripping a unit suffix before
  matching every column role turned `y_ppm` — yttrium — into the northing. The
  stripped name answers for the value column only.)
- **A collar table with a `zone` column has already answered the coordinate
  question.** `_stated_zone` uses it, and the terrain check still runs as a
  cross-check rather than as the only method.

`POST /api/datasets/{id}/reimport` ("Read the files again") re-parses stored
files in place, so an importer fix costs the client no second upload.

## Intervals are columns, not rows (2026-10-21)

`src/minemodelingpro/ivtable.py`. A drill assay was a dictionary — about 360
bytes of dictionary and three boxed floats for 26 bytes of measurement — and a
client database is 900,000 of them, carried twice. They are now five numpy
arrays and a few short string tables, written to disk as deflated buffers rather
than as text, and built back into row objects **one element at a time**, because
that is how the modeller reads them (composite this metal, then that one).

Measured on a 600,000-interval import:

| | rows | columns |
|---|---|---|
| dataset on disk | 65.9 MB | **2.1 MB** |
| load | 0.93 s | **0.05 s** |
| in memory | ~330 MB | **22.8 MB** |

And at the client's scale (602 holes, 915,040 intervals, 19 elements), the build
peaks at **663 MB** with every metal reported — against 1.67 GB when this started.

**Nothing about the data changes**, and that is held to the corpus rather than
asserted: `scripts/test_ivtable.py` checks 28 properties including 82 real
extracted reports round-tripping unchanged, and the harness in the session notes
rebuilds real projects and diffs the model JSON field by field — 11 of 11
identical, timestamps aside. Keep that habit for anything that touches this
path.

- `Intervals` holds the columns; `Row` is a `__slots__` object that answers to
  `x["grade"]` and `x.get(...)` exactly as the dictionary did, so the estimator,
  the compositing and the wireframe did not have to learn a new shape.
- `ByElement` is the `by_el` mapping the modeller had, over the table. Only the
  element being worked on is ever built into rows (cache of one).
- `sanity.fix_table` is `fix_intervals` on columns — the same `fix_values`
  decision, different plumbing. `dedupe`, `restrict_keys`, `max_to_by_key` and
  the per-element means are all vectorised; none of them builds a row.
- `to_store` / `from_store` are the storage seam. `from_json` reads **either**
  shape, so the 4,931 committed report JSONs still load as lists and nothing had
  to be re-extracted. Converting those to columns too is the next easy win —
  `assemble()` holding every report's intervals as dicts is what the nightly
  build's memory is now mostly made of.
- Depths and grades stay float64 on purpose. float32 would round the fifth
  decimal of a grade, and "the same data" has to mean exactly that.

## Which metal a deposit is about (2026-10-21)

A silver mine was reported in copper. The cause: `score(el)` ranked elements by
**how many holes carry them**, and a modern laboratory package assays every
element on every hole — so every element tied, and the tie fell through to
`ELEMENT_PRIORITY`, a generic list on which Cu sits two places above Ag. The
same tie ordered `_by_metal`, so the metal table and the NSR value formula left
the silver out of a silver deposit as well. One cause, three visible wrongs.

`ore.py` is the fix: the grade at which each metal starts to be ore, and
`significance(el, grades)` — how far above that floor this element gets here, in
orders of magnitude, measured at the 99th percentile rather than the middle,
because a deposit is the few per cent of the rock worth mining. Ranking is now
`holes x (0.05 + significance) x bonus`.

**Measured, not asserted.** Against the metal the filed resource names, with the
filed-element bonus removed from both scorers so neither is helped: ranking by
hole coverage picked it for 156 of 269 projects (58%); ranking by ore
significance picks it for 175 (65%). Against each project's own stated
commodity: 81% -> 84%. Of 449 multi-element projects, 39 change primary, and the
ones that can be checked against the geology are nearly all corrections —
Pulacayo, San Acacio, Haldane, La Parrilla and Cachinal to Ag; Lucky Shot,
Rajapalot and Frotet to Au; Accha and Brabant-McKenzie to Zn; Maniitsoq to Ni;
Opemiska to Cu.

Also on the back of that build:

- **A cut-off tonnage on its own misleads every reader of the project card.**
  "7 kt" was the rock above a 0.1% *copper* cut-off on a silver property whose
  model held 13.26 Mt. The card now shows the whole model's tonnage beside the
  cut-off slice, names the metal in both labels, and lists which elements were
  estimated with a line saying `elements` overrides the choice.
- A collar table with a zone column that yields no usable zone now says so
  rather than reporting "no UTM zone given".

**A model built before this is wrong in these ways and does not fix itself** —
the stored dataset and model are what they were. "Read the files again" re-parses
and rebuilds; "Rebuild model" alone re-runs the model on the old parse, which is
why a rebuild did not restore the coordinates.

## How far mineralisation is carried between holes (2026-10-24)

Jordan: "we aren't accounting for mineralisation in the gaps between holes which
are a reasonable distance apart ... we are coming up too short." He was right,
and the corpus says by how much.

**What qualified persons actually search.** 796 extracted reports state the
search range their own estimate used; 149 of those are projects MMP also models,
so the range can be read as a multiple of the drill spacing MMP measures from
the same collars.

| style | n | stated range, median | as a multiple of spacing |
|---|---|---|---|
| orogenic | 63 | 45 m | 1.4x |
| vms | 40 | 60 m | 2.2x |
| bulk | 123 | 100 m | 2.2x |
| pegmatite | 81 | 65 m | 2.7x |
| skarn | 69 | 75 m | 3.7x |
| vein | 284 | 60 m | 4.7x |
| iron | 30 | 100 m | 5.0x |
| porphyry | 58 | 200 m | 5.9x |

MMP searched **0.75x the spacing, capped at 70 m, for every deposit on earth** —
a median envelope of 31 m, clamped at the cap on 134 of 612 projects. (Also
worth knowing: nearest-neighbour spacing understates the pattern. The median
distance to the *fourth* nearest hole is 2.34x the nearest, because collars
cluster on pads. The multiples above are measured against the same
nearest-neighbour definition MMP uses, so they already absorb that.)

**What shipped.** `STYLE_REACH` + `reach_for_style`, and the key distinction:

- **Inside the body** — the gaps between holes — the estimate now carries as far
  as filings of that style carry it (`domain_reach`), bounded by the upper
  quartile of what those filings state. This is the change that fills the gaps.
- **Outside the body**, every metre is extrapolation past the last hole, and
  that leash is unchanged: 0.75x the spacing, capped at 70 m.
- The three passes now tier on **distance as well as hole count** (0.35 / 0.65 /
  1.0 of the envelope, with 3 / 2 / 1 holes), which is what a classification is.
  The tiers stay MMP's own words — well / moderately / thinly drilled — not CIM
  categories.
- The search radius is now never smaller than the envelope. It could be, and a
  block at the edge then had nothing to be estimated from: a silent hole.

**Measured on 78-33 projects with a filed statement, same projects each arm:**

| | med abs log error | median ratio | within 1.5x | within 2x | worse than 10x |
|---|---|---|---|---|---|
| grade-matched, as it was | 1.097 | 0.82 | 16 | 26 | 20 |
| grade-matched, shipped | **1.029** | **0.87** | 17 | 27 | 20 |
| at their cut-off, as it was | 1.149 | 1.41 | 7 | 9 | 3 |
| at their cut-off, shipped | **1.047** | 1.41 | 7 | 10 | 4 |

### Tested and NOT shipped: the long reach outside the body too

Letting the style reach set the extrapolation envelope as well looks better on
the band counts (within 1.5x 7 -> 11 at their cut-off) and is not worth having:
it leaves the symmetric error flat on the larger sample (1.097 -> 1.100), and it
creates blow-ups. **Blue Moon went to 121x its filed tonnage**, Cachinal to 9.5x,
Eastern Borosi to 7.9x — small deposits on wide spacing, where 0.75x a sparse
pattern is a long way and nothing stops it. Twelve projects moved closer to
their filing and nineteen moved further.

A related mistake, caught the same way: raising the **wireframe's** link reach to
the estimator's reach. The body's shape is a geological question — how far a unit
of this kind can be carried between two holes before the link is invention — and
`halo_max_m x 3` is what bounds it. The estimator may search further than the
body is drawn; it may not redraw the body.

**What is actually limiting accuracy now is not reach.** 20 of those 78 projects
are off by more than 10x in *both* arms, and no setting of this knob touches
them. That bucket — not the search distance — is where the next gain is.

## Why twenty of seventy-eight were ten times out (2026-10-25)

They are not comparisons of the same deposit. One number separates them, and it
is not about the model: **the tonnage the filing claims for every assayed hole
the report published.**

| filed t per assayed hole | n | median | within 2x | worse than 10x |
|---|---|---|---|---|
| under 30 kt | 10 | 3.42x | 1 | 3 |
| 30 kt - 100 kt | 9 | 1.08x | 6 | 0 |
| 100 kt - 300 kt | 9 | 1.92x | 5 | 0 |
| 300 kt - 1 Mt | 9 | 0.86x | 6 | 1 |
| over 1 Mt | 14 | 0.44x | 4 | 3 |

The correlation between log(filed tonnes per assayed hole) and log(model/filed)
is **-0.53** over 79 projects, and every >10x failure sits in one of the two
tails:

- **Under ~30 kt per hole — "the filing covers part of what we model."** Aguila
  files 112 kt Indicated; MMP holds 45 holes across the property and models all
  of it. Cajueiro files 254 kt Indicated while its own Inferred is 11 Mt. These
  read 4.4x high as a group, and the model is not wrong — it is answering a
  different question from the one the filing answers.
- **Over ~1 Mt per hole — "a resource published without its drilling."**
  Duparquet files 69 Mt behind 5 assayed holes and 7 intervals: 9.9 Mt of filed
  rock per published interval. No estimator reaches that from that data. This is
  the floor documented above, now measured rather than asserted.

What is NOT the cause: MMP's own tonnes per assayed hole is steady across the
groups — 240 kt where the score is within 2x, 268 kt filed on the same projects,
and 662 kt on the over-group. The estimator is behaving consistently; the
denominators are not.

**What shipped.** `validate.py` gains `comparability()`, `mark_comparability()`
and `comparability_bands()`, the headline gains a comparable-only line, and
`stamp()` writes each project's own verdict into its page, so a reader can see
whether the filing beside the model describes the same rock.

      AT THEIR GRADE    n=51  median 1.006x  within 1.5x 14  2x 22
      COMPARABLE ONLY   n=27  median 1.076x  within 1.5x 10  2x 17   >10x: 1
        set aside: 22 resource published without its drilling
        set aside: 19 filing covers part of what we model

Quoting one blended figure over all 51 describes neither population. Where the
filing and its drilling are in the same world, **63% of models land within 2x
and exactly one is more than 10x out.**

**The next real gain is per-zone modelling.** The 19 "filing covers part of what
we model" projects are properties with several mineralised zones where the
statement covers one. MMP builds one model per project by design; clustering the
mineralised intercepts and reporting the zone that matches the filing's own
extent would make those nineteen comparable. That is the work, not more reach.

## One project, modelled body by body (2026-10-25)

`src/minemodelingpro/zones.py`. A property is not a deposit. Triangulating every
mineralised intercept on a property together draws a sheet across the ground
between two lenses a kilometre apart and fills it with grade — which is how
Aguila's 112 kt filing came to sit beside a model of the whole property.

A **zone** is a connected body: intercepts reachable from one another in steps no
longer than the style's own reach. Each zone now gets its own wireframe, and the
project is the union of them plus a body built from mineralisation that belongs
to no zone — **the zones take rock away from the bridges between them, never from
the drilling itself.** Dropping those leftovers was measured and is worse (see
below). 71 of 79 scored models carry more than one zone.

**It is still one model.** `el["zones"]` carries each body's holes, span, bounding
box, block count and its own tonnes/grade/contained series on the project's own
cut-offs; every block carries a zone id (`blocks.zone`). The viewer shows a Zones
panel listing what each body holds, and picking one isolates its blocks and flies
to it — the totals above it stay the project's. Measured in a browser on
Forrest Kerr: 12,027 blocks whole, 7,598 for Zone 1, 4,425 for Zone 2.

**Measured on the 79 scored projects, same projects each arm:**

| | med abs log error | median | within 1.5x | within 2x | worse than 10x |
|---|---|---|---|---|---|
| one body per project | 1.013 | 0.89 | 18 | 28 | 20 |
| zones, leftovers dropped | 1.073 | 0.83 | 16 | 29 | 18 |
| **zones, leftovers kept** | **0.994** | **0.91** | 16 | 28 | 19 |

Accuracy is a small net gain, not a transformation — the point of this work is
that a property with four lenses can now be *assessed*: what each body holds,
how much of the project it is, and which one the filing beside it describes.
Splitting without keeping the leftovers is the trap: it helps the over-estimates
(Auld Creek 112x -> 92x) by shrinking everything, and pushes the already-short
further down (Aurora 0.40x -> 0.11x).

## Improvements reach models on their own (2026-10-25)

Jordan: "do i need to have the model itself rebuilt every time we improve our
modeling processes? if so that is counter intuitive." Correct, and it was true.

A model is a stored artifact, not a live calculation. The public gallery hid
this — CI rebuilds it nightly from current code — but a client's private model
sat at whatever the code was the day they pressed the button, with nothing
telling them a newer one existed.

`model3d.MODEL_VERSION` is stamped into every model and into the project's
`stats`. `worker.refresh_stale_models()` finds ready projects whose stamp is
behind and queues a build: `REFRESH_BATCH` (3) at a time, newest first, only when
the queue is otherwise empty so a client's own upload always goes first, never
for a project that already has a build queued. `MMP_AUTO_REFRESH=0` switches it
off. A resumed gallery run (`resume_after`) also refuses to skip a page whose
stamp is behind — "built recently" and "built by this code" are different things.

**Bump MODEL_VERSION when a change alters what a model CONTAINS** — estimator,
wireframe, reach, primary metal, deposit style, block sizing. Not for a viewer or
page change: those re-render anyway and a bump costs every client's data a trip
through the estimator for nothing.

## Presence is not grade (2026-10-25)

Two faults, one mistake, found in one screenshot: a silver-cobalt property
modelled as a **primary gallium** deposit of **sedimentary phosphate** type.

- `ore.FLOOR` wrote every floor as a bare number under a comment saying which
  unit was meant. Gallium is assayed in g/t and was floored at 0.01 as though
  that were per cent, so 17 g/t of plain crustal background scored
  `log10(16.3/0.01)` = 3.2, clipped to the maximum 1.0 — it outranked silver at
  300 g/t (0.708) on every package that assayed it. Floors now carry their own
  unit, `ore.floor(el, unit)` converts, `Intervals.ore_fraction` passes the
  table's actual unit, and the by-product metals get the grade they are mined at
  (Ga 50 g/t, Ge 50, In 20, Te 5). `drill_tables._DEFAULT_UNIT` and `ore.FLOOR`
  are checked against each other in `test_imports.py` — keep them in step.
- `deposit_style.from_geometry` had `if "P2O5" in com: return "phosphate"` with no
  grade and no width test, so any lab package reporting phosphorus made the
  property a bedded phosphate horizon — which then set the block size, density,
  search reach and anisotropy. `model3d` now filters the suite to elements that
  reach ore grade before passing it as evidence, and the phosphate rule needs a
  bedded width as well.

The general rule, and it has now cost three visible wrongs: **a modern ICP
package reports thirty elements on every sample, so "is it present" discriminates
nothing. Only grade does.**

## Seeing the body, zone by zone (2026-10-25)

Jordan, on gaps between nearby holes: "i need to be able to toggle the wireframe
that is being used so i can get a better sense of what the model has imagined
exists in terms of deposit shape, not just the block model."

The wireframe decided which blocks may exist and was then discarded, so a hole in
a block model had two unreadable explanations: the body never reached there, or
it did and the estimator found nothing. `el["body"]` now carries the domain on the
block grid — deduped cells, capped at `BODY_MAX_CELLS` (120,000) and thinned onto
a coarser lattice beyond that — **with a zone id per cell**. The viewer draws it
as a translucent envelope under "Deposit shape (wireframe)", coloured by zone
(`ZCOL`, shared with the zone chips and the per-zone rows so a body is the same
thing in the picture and in the table), labelled in place, and isolated to one
body when a zone chip is picked. Zone 0 is real mineralisation belonging to no
named body: grey, labelled as such, never hidden.

Browser-verified on Angilak (17 bodies): 100,473 cells, Zone 2 isolates to 12,847
cells and 13,137 blocks, back to all restores 100,473. Los Ricos North (55
bodies): 36,309 cells, Zone 1 isolates to 2,516. No page errors.

**Measured while checking the gap complaint.** Share of mineralised intervals with
no estimated block within one block diagonal, on the four most heavily drilled
public projects: Adelita 2.3%, Angilak 4.6%, Pico Machay 4.0%, Los Ricos North
4.2% — median 4.1%. The estimator is not systematically refusing ground the
drilling tested. What to watch instead is body fragmentation (55 bodies on one
property), and modelling the wrong metal: a diffuse background element fragments
into many small zones and looks exactly like a gappy deposit.

## The landing page had no menu bar (2026-10-25)

`/nav.js` was not in `_OPEN_EXACT` in `studio/server.py`, so a signed-out visitor
got `401 {"detail":"sign in first"}`, `MMPNav` was never defined, and the empty
`<div class="topbar">` rendered as nothing — a page returning 200 with no way to
sign in. The open list is hand-maintained and nav.js was added when the bar was
unified (2026-10-19) without updating it. `scripts/test_public_assets.py` walks
every public page's scripts and stylesheets and fails if one would be refused or
is missing from disk. **Add any new asset a public page loads to `_OPEN_EXACT`.**

## Reading the drilling against the model built from it (2026-10-25)

Jordan: "i need to be able to see hole data outside of the model. the drill data
on the modeling screen is saying we have long intervals of mineralization that
was intersected but the model is not reflecting the same type of data."

That is a checkable statement, so the viewer now checks it instead of leaving it
to the eye. Blocks sit on a regular lattice, so "is there a block at this point,
and what grade did it get" is a hash lookup (`blockCells`, `blockAt`) rather than
a search. `reconcile()` walks every assay interval at or above the current
cut-off down its own hole at half a block a step and records how much of its
length falls inside an estimated block. Entirely client-side, on data the page
already carries — no rebuild, and it follows the cut-off slider (debounced 260 ms,
since it is a full pass over the intervals).

- The Holes table gains **Ore m** (interval length at or above the cut-off) and
  **In model** (how much of it the blocks cover). A hole under 50% is shaded.
- One line under the table says it for the project: "the assays report N m at or
  above X, and the block model covers P% of it."
- Three CSV exports: **Holes**, **Intervals** (every interval with its position,
  whether a block exists there and that block's grade) and **Unmodelled
  intervals** (the ones under 50%, longest first). Coordinates are labelled
  `x_local_m`/`y_local_m` because the model is recentred on the drilling, with
  real `latitude`/`longitude` beside them from `frame.lat0/lon0`.

Measured on Angilak: 723 m of U3O8 at or above 0.05%, model covers 89% (645 m);
156 intervals totalling 72 m sit mostly outside the blocks. The whole pass takes
1.6 s over 3,668 intervals.

## The renderer was stale for six days (2026-10-25, found while doing the above)

`site/app/model.html` fetches `../models/_shell.html` and `../models/viewer.js`.
Those are **build artifacts committed into `site/`**, refreshed only when the
nightly gallery build runs — so every viewer improvement since 2026-10-19 (the
zones panel, the deposit-shape toggle, this drill-data work) reached a client's
private model a day late at best, and not at all if the build failed. The same
staleness that made models need a button press, one layer down.

`studio/server.py` now serves `/models/viewer.js` and `/models/_shell.html` from
the **package** (`src/minemodelingpro/`), which is this deployment's own code.
The explicit routes are registered before the `app.mount("/")` static mount, so
they win; `scripts/test_public_assets.py` asserts both the routes and that
ordering. The committed copies under `site/models/` are still refreshed (public
gallery pages on GitHub Pages are served statically and have no FastAPI in front
of them) — but Studio no longer depends on them.

**Old model pages are safe with a new renderer**: every new panel lookup guards on
a null element, so a page whose baked shell predates a control simply does not
show it. Keep that habit.

## A build that failed silently, and names that lied (2026-10-25)

Three complaints, three real causes.

**"building the model also keeps failing."** The card said "attempt 2" and gave no
reason, because there was none to give: `requeue_stale` put a dead job back on
the queue without writing anything, and `log()` on the attempt that succeeded
overwrote whatever had been there. A build that died once and worked the second
time was indistinguishable from one failing for ever. `jobs.notes` (idempotent
ALTER) now accumulates a line per dead attempt — "attempt 1 stopped responding
after 20 minutes and was restarted — most often the modeler ran out of memory and
the kernel stopped it without a message" — and the project card shows it in red
above the log. Four tests in `test_jobs.py`, including that a later success does
not erase it.

**The worker now says how much memory it has**, on every beat
(`worker._pulse()` → `/api/health.worker_note`: "mem 2048 MB · model v8"). The
modeler is sized in the DO console, not in the repo, and a build that dies at
1 GB and survives at 2 GB looks identical from the page. Check this first when a
build dies.

*Caught by the test, worth remembering:* `concat_ws(chr(10), notes, %s)` fails
with "could not determine data type of parameter $1" — Postgres cannot infer a
bare parameter's type inside `concat_ws`. It needs `%s::text`, and without the
cast `requeue_stale` raises, the sweep swallows it, and **no stale job is ever
requeued again**. Always exercise a store change against the real Postgres.

## The zone names were in the wrong place (2026-10-25)

Jordan: "zone labels are completly blocking my ability to be able to see the
model" and "why does the wireframe not correspond whatsoever to the zones being
outlined."

The second is the real bug. A label was drawn at its zone's **mean** cell
position, and a body that bends round a corner or sits in two lobes has a mean
in empty ground — or inside a different zone's body. The names genuinely did not
correspond to the shapes. The viewer now computes each body's **medoid** (the
cell nearest the mean, which is always a cell of that body) when it builds the
mesh — client-side, from cells already shipped, so no rebuild — and anchors the
name there. Verified 17/17 anchors are cells of their own body on Angilak.

The first is a default: nineteen names over a deposit cover the thing they
describe. Labels are now **opt-in** ("Name each body on screen", nested under the
shape layer, which only appears when the shape is on), smaller and at 82%
opacity. Picking a zone still names that one body, because then it is the only
label on screen.

**And the question under both:** "is this telling me the bulk of the
mineralization has been excluded from the wireframe?" That is answerable, so the
shape note now answers it in place rather than leaving it to the eye: *"Of the
723 m of U3O8 the assays report at or above 0.050%, the model covers 89%"* —
turning red with "open Drill data to see which holes" below 70%. `reconcile()`
got there by desurveying once per interval instead of once per sample step (a
surveyed path is straight to well under a block over a metre of core): 1.6 s →
1.1 s, and it scales with the intervals above the cut-off, not all of them.

## The data browser belongs on the project page (2026-10-25)

Jordan asked four times to be able to see his own drill data, and I answered
three of them by building something inside the 3D viewer. **Someone checking what
was imported is on the project page, not orbiting a block model.** That is the
lesson; the feature was never the hard part.

`GET /api/projects/{id}/data?table=assays|collars|surveys&q=&el=&limit=&offset=`
reads the parsed dataset straight out of storage — so it shows exactly what the
modeller was given, not a re-derivation — and pages server-side, so a 919,152-row
assay file opens as fast as a small one. The project page gains a **Your data**
card: three tabs, an element filter, a hole filter, paging, and a CSV of the
current page. `scripts/test_data_api.py` (14 tests, real Postgres) covers the
three tables, both filters, paging, the limit bound, another account's project
being refused, and a project with no parsed data saying so.

*Caught by the test:* a stored interval's element key is **`el`**, not
`"element"` (`ivtable.from_rows`). Collars are `e`/`n`/`z` + `hole`, surveys are
`md`/`az`/`dip`. The endpoint accepts `x`/`y` as well, but check `from_rows`
before assuming a key name.

**The first version 500'd on the real file and the tests did not catch it**,
because the fixture had four rows. It built a `Row` object per interval to count
and slice them — a million objects in a web process on a 919,152-row assay file.
The endpoint now does every selection, filter and count on the numpy columns and
materialises **only the rows on screen**: `index()` for the element, `isin` on
the hole NAME TABLE (a few hundred strings, not a million), then a slice.
`test_data_api.py` builds a **720,000-row** dataset and asserts the first page,
a page at offset 700,000, both filters, and that peak RSS does not move across
them — 510 MB before and after. **Any endpoint over a client's data gets a test
at a client's scale; a four-row fixture proves nothing about this.**

**And it 500'd a second time, for a different reason the tests also missed.**
`do_import` stores `IVT.to_store(dataset)`, whose `intervals` is a **dict of
deflated base64 buffers**, not a list of rows — and `Intervals.coerce()` reads a
list or a table, so on a dict it iterated the keys as though each string were a
row. Every fixture I had written was a list, so the endpoint was only ever tested
against a shape production does not store. `test_data_api.py` now builds its
fixture with **`IVT.to_store`** — the production writer — and asserts the dict
shape, the round trip and the filters on it. **Write the fixture with the code
that writes production data, or the test is checking a shape nobody stores.**

The page layout followed the same lesson as the viewer panel: **Your data** sits
*above* the import log, and the log's prose ("using 32 of 338 columns…", which
column became what) is folded into `<details>`. A wall of text between the person
and their data is the same mistake as a wall of controls.

## Zones were being split by the wrong distance (2026-10-25)

Jordan: "if mineralization is intersected at roughly the same depth theyre just
trying to prove continuity. it doesnt mean theyre entire new fucking zones", and
"why is no continuity being inferred anywhere".

Right, and the cause was a conflation I introduced. **Two different questions had
one answer:**

1. *How far may I draw a solid between two holes?* — the wireframe's. Tight on
   purpose and measured: a long one invents rock (Blue Moon, 121x).
2. *Are these two intercepts the same body?* — the zone's. A step-out hole exists
   precisely to prove continuity across more ground than (1) allows.

Zone membership was using (1)'s answer — 26 m on Langis — so every step-out
longer than that started a new "zone", giving 19 of them on one vein system, and
because each zone gets its own wireframe, **no blocks filled the ground between
them.** The fragmentation and the missing continuity were the same bug.

Zone membership now uses `max(wireframe reach, el_reach)` — the distance this
style's own filings infer continuity over — **in the deposit's own anisotropic
frame** (`zones.warp`, the same transform the estimator searches in), so it is
generous along strike and down dip and tight across. The wireframe keeps its own
bound, unchanged. MODEL_VERSION 9.

Measured: Los Ricos North 55 bodies -> 32, Pico Machay 15 -> 2, Angilak 17 -> 11,
Adelita 2 -> 1.

## The viewer panel had become a scroll (2026-10-25)

Jordan: "i dont want to have to scroll for fucking ever to toggle everything on
screen this layout is retarded", and "why is this being shown in the fucking
toggle menu" (19 zone chips and a zone table, inside the Model panel).

Fair. I had been appending each new feature to one panel. Now:

- **Show (the toggles) is first** — it is the most-used control and was four
  scrolls down. Opacity sliders are in a `<details>` under it.
- **Bodies is its own panel** (`pZones`), minimised, with its own button in the
  mobile tool strip. Zone chips are a reading of the model, not a display toggle.
- **Size & extent** is a `<details>` at the bottom; the nav hint went inside it.
- The shape note is one line with the detail on `title=`, not a paragraph.

**Rule for anything added next: a new control goes in the panel whose question it
answers, and prose goes on a tooltip.**

## Efficiency inside 1 GB, measured not assumed (2026-10-25)

Jordan: "im not increasing the monthly cost of this. you need to find a way to
make it more efficient." The modeler stays on **1 GB** (`/api/health` reports
what it actually has: `"mem 1048 MB"` — `.do/app.yaml` asks for 2 GB but that
file is a reference copy; the live size is only in the DO console).

**Every build now reports its own time AND memory per phase** (`Phases`,
`_rss_mb`), stamped on the model as `timings` / `memory_mb` / `peak_mb` and
printed on the project card: *"44s: estimate 38s, wireframe 5s, metal table 1s ·
peak 882 MB"*. Measure before optimising; this is the instrument.

Measured at a client's scale (602 holes, 910,224 intervals, 27 elements):

| phase | time | memory added |
|---|---|---|
| block estimation | 26 s | 150 MB |
| metal table (24 metals) | 12 s | **0 MB** |
| wireframe | 4 s | 46 MB |
| zones | 4 s | 41 MB |

**The 8-metal cap below 1.8 GB was wrong.** It was set when every metal's
intervals were dictionaries; on the columnar table the metal table reuses the
primary's blocks and estimates a grade at their centres, so 24 metals cost
**12 s and no memory at all**. Cutting a client to 8 metals on a 1 GB box bought
nothing and removed the one check that catches a wrong primary metal — which has
now been wrong twice (copper, then gallium), and is exactly why Jordan requires
every metal. Threshold is now 900 MB, so a 1 GB modeler reports all 24.

`grade_at` also caches its search plan (`_geom_key`, `_LRU(2)`): every metal is
estimated at the same block centres from composites at the same positions, so it
was one neighbour search repeated per metal with only the grade vector changing.
The plan is bounded by a share of `mem_budget_mb()` and skipped when it would not
fit — the modeller has been killed by exactly this kind of "small" extra before.

**The next lever, not yet taken:** `estimate` is 60% of the build and runs once
per element in `elements` (3 by default). The metal table already carries every
metal's grade at the primary's block centres, so two of those three full block
models exist mainly to let "Colour by" switch layers. Serving those from the
metal table instead would cut the dominant phase by about two thirds. It changes
what the viewer reads, so it needs the field-by-field model diff first.

## Compositing used the midpoint, not the interval (2026-10-25)

Jordan: "are we not using the full length of an interval to properly assess metal
content and modeling". **We were not.**

A composite took the grade of whichever segment its **midpoint** landed in. So a
metre of 1,000 g/t inside a 5 m composite scored **0 g/t-metres** when the
midpoint missed the vein and **5,000** when it hit — the same metre of core worth
nothing or five times itself depending on where it sat in the hole. The error is
the composite length over the sample length, which on narrow veins (1 m assays,
5 m composites) is the deposit. Both cases are in the commit; metal was not
conserved in either.

A composite is now the **length-weighted average of everything it spans**:
`sum(overlap x grade) / composite length` with the zero-fill on, `/ assayed
length` with it off. Metal is conserved exactly. `MMP_COMPOSITE=midpoint`
restores the old rule so this stays A/B-able.

**Measured, 52 projects with a filed statement AND at least 60 assayed holes —
the population where the comparison means anything** (`/tmp/claude-0/ab.py`,
resumable because each pair takes longer than a tool call):

| | median ratio | med abs log err | within 2x | >10x |
|---|---|---|---|---|
| midpoint | 0.441 | 1.324 | 17 | 17 |
| **length-weighted** | **0.520** | **1.222** | 16 | **16** |

Paired: length is closer to the filing on 28 projects, midpoint on 23, one tied.
**That is a weak edge, and it must be read with the mechanism, not instead of
it.** Sampling a spike at the midpoint *inflated* composite grades; removing that
inflation lowers them, so at grade-match a lower cut-off is needed and more
tonnage comes with it — tonnage rose on 41 of 52. Most MMP models are short, so
that helps (Cahuilla 0.131x -> 0.896x); the ones already over get worse
(Cajueiro 13.7x -> 93.9x, Herbert 0.67x -> 3.22x). Cajueiro is a known "filing
covers part of what we model" case, so its ratio was never a measure of the
estimator.

**Shipped on the mechanism, not the median.** A rule that turns one metre of
1,000 g/t into either nothing or five times itself is indefensible whatever it
scores in aggregate, and the corpus is at worst neutral on it. An early sample
sorted smallest-project-first showed the opposite and was noise: those are the
"resource published without its drilling" projects where every ratio is 0.1x.
**Sample the population where the comparison is valid, or measure nothing.**

## The assay table was broken by a duplicate id (2026-10-25)

Hours of this, and the cause was two characters. The project page already had
`<button id="del">Delete project</button>`. I added `<select id="del">` for the
element filter. **`querySelector` returns the first match**, so every `$("#del")`
resolved to the Delete button, and `.options.length` threw
*"Cannot read properties of undefined (reading 'length')"* on page load — before
a single row could render.

It survived because the symptom pointed everywhere except at the markup: the
endpoint answers 926,478 rows in 0.17 s and 50 MB, the route is live, the static
assets are current, the server is fast. **All of that was true and none of it was
the bug.** What found it was putting the first stack frame into the error message
and counting lines: `project.html:275:30` is exactly `.length` on line 275 of the
deployed commit.

The filter is now `#d-el`, and `test_public_assets.py` fails on a duplicate id in
any page under `site/` — verified to flag `del` when the duplicate is put back.

**Lesson, and it cost a day: when a page throws, read the stack frame before
theorising about the server.** I blamed deploys, object storage, memory and row
counts in turn, shipped four changes against those theories, and the browser had
been naming the line all along.

## A detection limit is not a grade (2026-10-25)

Jordan, reading his own assay table: "several metals appear to have 'filler'
values in them. for instance on the gallium which says 10 across the board for
some holes or 5 or whatever." He is right, and it is why Langis reported
**12.9 g/t Ga over 91% of its tonnage and 7.40 Moz of contained gallium**.

A laboratory reports a sample under its detection limit as `<10`. An export turns
that into the number `10` — or `5`, half the limit. Those are not measurements of
anything; they say *we looked and found none*. Carried as grades they make a
metal look pervasive at a constant low value, which inflates its mean, its
contained metal, and its `ore.significance` — the same background-metal failure
as the gallium floor, arriving by a different road.

`sanity.detection_fill` finds the signature: one exact value repeated across at
least a quarter of the positive assays (minimum 40), sitting at the very bottom
of the distribution (nothing meaningfully below it), and not the entire
population. Those samples become **zero**, not half the limit — MMP's question is
whether a metal is ore here, and DL/2 answers it with a number nobody measured.
`strip_detection_fill` runs first in `fix_table`, so every later step — ore
significance, the primary metal, the estimate, the metal table — sees non-detects
as non-detections. Reported in "Repaired on the way in", never silent.

**Measured against the corpus before shipping:** over 1,385 element populations
in the extracted reports it fires on **23 (1.66%)**, and every one is a textbook
limit — Zn 0.003%, Mo 0.001%, Bi 0.0005%, Ni 1e-05%, Cu 0.01%, Ag 1 g/t. Five
tests in `test_imports.py` cover the catch and the four ways it must decline:
a real lognormal population, an element that is one value everywhere, too few
samples, and a spike that is not at the bottom.

Both of the items this screenshot left open are now done — see the two sections
below.

## The ground, interpolated from the collars (2026-10-25)

A project in a local mine grid has no map position, so no DEM can be read for it
and every intercept was drawn against a **flat datum**, which misplaces the whole
deposit in depth. But a drill collar is a surveyed point ON the topography, and
it is the one elevation record every drill database has.

`terrain.from_collars` fits the ground from them: inverse-square over the twelve
nearest collars, on a lattice about half the collar spacing, smoothed by half a
cell. **Local rather than global on purpose** — a weighted mean of every collar
reverts to the property's average elevation wherever the drilling thins, which
flattens exactly the relief this exists to show. `model3d.build_project` uses it
whenever `terrain.grid` returns nothing, and it carries `from="collars"` plus a
note, so the viewer says what it is instead of passing it off as a DEM.

Measured over 32 local-grid public projects: the surface passes within **0.82 m**
of the collars it was built from (median of medians), median p90 3.6 m, median
payload 122 KB.

**It also finds bad data.** 75 of 5,256 collars (1.43%) state an elevation no
surface can pass through — Goldboro has two 1.6 m apart differing by 75 m, South
Mountain two 21 m apart differing by 431 m. Mixed datums between reports, not
hillsides, and one of them bends the ground around it. They are dropped and the
count goes into the model's note rather than being smoothed over silently.

**The outlier test is a SLOPE, not a number of metres.** A first version used a
flat 20 m allowance and threw away real folded hillsides: 5.44% dropped across
the corpus and 1 in 80 on noiseless synthetic ground. Judged instead against
twice the neighbours' own baseline, clean data loses nothing and the 47:1 slopes
go. `scripts/test_terrain.py` holds 19 checks, including that ordinary survey
scatter is not called an outlier and that a planted 400 m collar does not move
the ground near it.

## Why the wireframe looked nothing like the veins (2026-10-25)

Jordan: "there seems to be minimal correlation between the wire frame and the
actual mineralization. this is a vein type system." Two separate faults, and the
number he was reading was one of them.

**1. The fill ratio was measuring the cut-off slider.** "The estimate fills 31%
of this shape" was computed in the browser against `blockCells()` — and the page
only ever receives the blocks above the display threshold (`shown_from`), never
the rest. So it compared a whole shape against a cut-off slice. Counted at build
time against every estimated block (`body.filled`), the same projects are Avino
**91.8%** (the old metric: 6.9%) and El Cubo **98.2%**. **Any ratio computed in
the viewer is computed over the blocks the page carries, which is not the model.**

**2. Mineralisation belonging to no named zone was drawn as ONE body across the
whole property.** Intercepts too isolated for the zone finder to name are kept
rather than dropped (dropping them shrinks models that are already short) — but
they were all triangulated together, and on the worst cases that sheet *was* the
model: White one sheet spanning **6,825 x 7,800 m**, 8,169 of 8,173 cells, with
its two named zones holding 2 cells each; Castle **1,940 x 1,124 x 622 m**,
52,755 of 52,757. Each triangle passed the alpha test individually; the sheet was
a legal chain across ground the zone finder had already judged discontinuous.
That is the failure the zone split exists to prevent, reintroduced for the
remainder.

Each leftover is now its own connected body, built **exactly as a named zone is**:
clustered at the continuity distance, then given the composites that belong to it
before triangulating. Clustering at the wireframe's tighter reach instead, with
no second step, left most components under three points and collapsed White from
8,173 cells to 12 — one fiction for another. A body one or two holes have seen
gets no surface (three intercepts are needed for a triangle) and is drawn as the
intercepts themselves; `no_surface` counts them and the viewer says so rather
than showing an all-but-empty layer.

**Measured over 131 projects, same projects both arms:**

| | median fill | lower quartile | under 60% | under 30% | median cells |
|---|---|---|---|---|---|
| one leftover sheet | 0.805 | 0.52 | 47 | 14 | 13,353 |
| **leftovers as bodies** | **0.968** | **0.92** | **3** | **2** | 4,571 |

Fill improved on 76 projects and worsened on **0**. By style it is exactly the
two that go down the tabular-sheet branch, and the styles that were already right
barely move:

      vein       55   0.545 -> 0.945        skarn      11   0.993 -> 0.995
      orogenic   13   0.468 -> 0.983        porphyry    7   0.998 -> 0.998

**Against the filed statements it is neutral, and shipped on the mechanism.**
114 projects with a statement and >=60 assayed holes, `MMP_LEFTOVER=sheet`
restores the old rule so this stays A/B-able:

| | median ratio | med abs log err | within 2x | >10x |
|---|---|---|---|---|
| one leftover sheet | 0.800 | **1.210** | 41 | 32 |
| leftovers as bodies | 0.789 | 1.260 | **42** | 32 |

Median tonnage change **0.998x** — one project moves more than 2x down
(Castle, already at 0.049x of its filing), none up. Paired it is a coin flip:
closer on 31, further on 33, tied 50. The movers that matter moved the right way
(Auld Creek 130x -> 84x, Montauban 3.35x -> 1.72x). A sheet drawn 6.8 km across a
property from intercepts already judged discontinuous is indefensible whatever it
scores, and the corpus is neutral on removing it.

MODEL_VERSION 13. `scripts/test_zones.py`: 15 checks, including that two lenses a
kilometre apart stay two bodies, that triangulating them together does span the
gap (the failure, pinned), that lone intercepts are kept, and that clustering
happens in the deposit's own frame.

## A client's own build does not queue behind the auto-refresh (2026-10-26)

`refresh_stale_models` queues `REFRESH_BATCH` (3) rebuilds on a version bump, and
there is **one worker** — so a client pressing "Rebuild model" sat behind all of
them, which is part of "why are rebuilds taking so fucking long". `jobs.source`
(idempotent ALTER, default `'user'`) marks what the sweep queues as `'refresh'`,
and `claim_job` orders by `(source = 'refresh'), created_at`: a person waiting on
their own build goes first however long a refresh has been queued, and among jobs
of the same kind oldest still wins. Five more tests in `test_jobs.py` (31 total),
including that the sweep actually tags its own jobs — without the tag the
ordering is a no-op.

**Running the Postgres tests in this container:** the log path must be writable by
the postgres user, which is why `pg_ctl -l /tmp/...` fails and
`-l /var/lib/postgresql/pg.log` works:

```bash
mkdir -p /var/run/postgresql && chown postgres /var/run/postgresql
su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/t \
  -o '-p 5433 -k /var/run/postgresql' -l /var/lib/postgresql/pg.log start"
su postgres -c "/usr/lib/postgresql/16/bin/psql -p 5433 -h /var/run/postgresql \
  -c 'create database mmptest' postgres"
DATABASE_URL='postgresql://postgres@/mmptest?host=/var/run/postgresql&port=5433' \
  PYTHONPATH=src python3 scripts/test_jobs.py
```

The pool prints "couldn't stop thread … within 5.0 seconds" at exit after the
summary line; it is noise from psycopg's pool teardown, not a failure — grep for
`passed,` rather than reading the tail.

## A drill database is read hole by hole (2026-10-26)

Jordan, looking at 144,774 rows of one-metre assays paged 200 at a time: "this is
impossible to read. i need one line per drill hole, with the ability to expand the
data on each hole."

Right, and the shape was the whole problem. The table was one row per **element
per metre**, so a 602-hole database assayed for four elements over 60 m is
144,480 rows — 723 pages — to find out what is in one hole. A drill database is
read hole by hole, and a metre of core assayed for twenty elements is ONE interval
with twenty numbers on it, not twenty rows.

**The hole list is now the default** for Assays and Surveys (Collars is already
one line per hole, so its view control is hidden). Click a hole and its intervals
open inline, **pivoted wide**: one row per from/to, a column per element with its
unit. "Every row" keeps the flat table for anyone who wants it.

      All elements   hole | samples | elements | from | to | assayed (m)
      Ag picked      hole | samples | from | to | assayed (m) | max Ag | mean Ag

- `GET .../data?group=hole` — one row per hole, vectorised over the interval
  columns (`np.bincount`, `np.minimum.at`, `np.unique` on `(hole, from, to)`),
  cached per (stored file, element) in `_HOLE_CACHE`. **Never builds a Row**, the
  lesson from the first version of this endpoint.
- `GET .../data?hole=X&wide=1` — that hole's intervals, pivoted.
- **Assayed metres are CORE metres.** Summing interval lengths over a 20-element
  package counts every metre twenty times; the length comes from distinct
  `(hole, from, to)` triples. Both the server and the client grouping do this, and
  both are tested for it.
- The page groups client-side when it is reading the built model (those rows are
  already in memory) and server-side for the raw parse. Two implementations
  because they sit over two different sources; `groupByHole` / `pivot` in
  `project.html` and `_read_holes` / `_read_one_hole` in `server.py`.

*Caught by driving it in a browser:* on a project whose model is not written yet
the page falls back to the raw parse, and the element dropdown was only ever
filled from the model — so "All elements" was the only choice those projects ever
offered. The raw path now fills it from the response's own `elements`.

`scripts/test_data_api.py` is 68 tests. At 720,000 rows the hole list returns
**600 holes** rather than 720,000 rows, a second call is served from the cache,
and expanding one hole returns 60 rows with 23 columns rather than 1,200 rows.
Browser-verified on both sources with no page errors: 39,600 model rows -> 220
holes, expanding LM-16-001 gives 60 intervals as `48.6 | 49.6 | 1 | Ag 0.1 |
Co 0 | Ni 0`.

## Adding a source that was never a file (2026-10-26)

Jordan: "it seems my assay data is missing some details that werent provided for
the langis project. i need a way to add additional sources to the model (i.e.
data pulled from a news release or whatever) to better assemble the model."

Two mechanisms already existed and neither reached: a project merges every
dataset on it by normalised hole id (so a second upload already adds drilling),
and `attach_to` folds a PUBLIC MMP project in. **There is no public Langis data
in the corpus** — checked — so attaching could not help. What was missing is the
way drilling actually arrives when there is no file: a news release, which states
its numbers in a space-aligned table nobody exports.

`drill_tables` already reads exactly that — it is the extractor behind four
thousand technical reports. So the work was plumbing, not parsing:

- `imports.release_text()` runs it over a text file and returns the importer's
  OWN collar and interval shapes. `build_dataset` calls it when a text file
  yielded no table a reader could see, so **a paste is an ordinary dataset** —
  merged by hole id, shown in the import log, present in the hole list. There is
  no second kind of source in the model, only a second way in.
- A release names its grid in the words above its table ("NAD83 UTM Zone 17N")
  and `detect_crs` reads it. Without that the collars fall back to a local grid,
  which the importer's own note says "cannot merge with other sources for the
  same project" — the one thing an added source exists to do. With it, the
  Langis-style fixture places at EPSG 26917, 47.430 N 80.016 W: Cobalt, Ontario.
- `POST /api/projects/{id}/sources/preview` reads a paste and stores nothing.
  `POST .../sources` keeps it as the .txt it is, so "Read the files again"
  re-reads it like any upload.

**The preview is the feature.** The row counts are not what decides whether a
source reaches the model — whether its holes JOIN does. A release naming
`LM-26-101` against a collar table that says `LM26101` lands nowhere, silently.
The card reports, before anything is stored: how many of its holes are already in
the project, how many are new, and **how many carry results but have no collar
anywhere** — naming them *as the release wrote them*, never as normalised keys,
because nobody recognises `ZZ-99-1` as their own `ZZ-99-001`.

**`validate` now takes `standalone`.** A release of six intervals on three holes
is a good addition and a bad standalone database, and `do_import` RAISES on a
validation error — so holding an added source to "at least 3 collars, at least 8
intervals, collars sharing hole ids with its own assays" made every added source
fail. The minimums apply to a project's FIRST dataset; an addition only has to
hold something. Whether it joins is answered against the project's holes, at
preview time, which is the right place and the right question.

*Caught by driving the page:* `app.js`'s `api()` stringifies the body itself, so
`body: JSON.stringify({...})` sends a JSON **string** and `.get` on a `str` is a
bare 500. `_body(request)` now says "the body looks double-encoded" instead.
**And the helper must sit ABOVE the `@app.post` decorator** — defined between the
decorator and its function, the route binds to the helper, and every call fails
with "Field required: query.request".

*Also caught, in an existing test:* "another account's data is refused" was
passing on a 404 that meant "that project has no data". The test account is an
admin (the invite-only fallback makes one) and an admin may read any project. It
now checks with a plain user against a project that does have data, and asserts
separately that an admin still can.

`scripts/test_data_api.py` is 109 tests, including the full round trip: the paste
imports without raising, its dataset is `ready` and marked as read from a release,
and its new hole appears in the hole list beside the upload's. Browser-verified
with no page errors.

**Still not read: prose.** "returned 845 g/t silver over 3.5 metres from 210.1 m"
yields nothing — it needs a hole id, a from and a to, and a grade column. That is
the same gap ranked third under "Still open" above, and the card says so rather
than failing silently.

## This container's corpus is REPORTS-ONLY unless the drill bank is synced (2026-10-26)

**Read this before claiming MMP holds nothing for a property.** I told Jordan
"there is no public Langis data in the corpus", having checked `assemble()` here.
There is: `langis-brixton-metals`, Brixton Metals, 125 holes, 179 intervals, **10
sources, every one a news release with a URL** — and its page has been committed
since the 2026-10-25 nightly build.

The cause: `data/keep/drillbank.sqlite` is **gitignored** and synced from
Closeology at build time, so a fresh container has `load_news()` returning **0
holes**. Every project whose drilling comes from releases is simply absent —
673 projects here against **758** once the bank is in place. Any measurement run
without it is over reports only, and any statement of the form "MMP has nothing
for X" is unsound.

Fix it before measuring. `closeology_sync` lists files through the **GitHub API**,
which this container's proxy refuses with 403 — but `git clone` works:

```bash
git clone --depth 1 https://github.com/jaydeepdive/closeology.git ~/closeology
cd ~/mmp && CLOSEOLOGY_REPO=$HOME/closeology PYTHONPATH=src \
  python3 -m minemodelingpro.closeology_sync      # places drillbank.sqlite
```

(759 MB clone, 21 MB drill bank, ~1 minute.) The corpus sweeps earlier in this
file were run reports-only; that is the right population for filed-statement
scoring, which comes from reports — but it is not the whole gallery.

## Every public model page was black (2026-10-26)

Jordan: "what is going on with the public modeling. its just black."

One line. A model page is a **baked artifact** — its shell is whatever the
nightly build wrote that day — while `/models/viewer.js` is served from the
package and is this deployment's **current** code. The deposit-shape layer added
`L-body`; pages baked before it have no such element; and

    $("L-body").onchange = fn

on a null is a TypeError thrown at **module level**. The renderer stopped before
it sized the canvas: canvas 300x150, every panel dead, the whole page black. On
every page in the gallery, over one optional toggle.

This file already stated the rule — *"every new panel lookup guards on a null
element"* — and I did not follow it. `ctl(id)` makes it structural now: the
element, or a sink that swallows writes and reads back empty. **19 sites across
9 controls** an older shell can lack go through it (`L-body`, `L-zlabels`,
`body-op`, `body-op-v`, `body-note`, `checknote`, `iv-n`, `ivnote`, `zone-n`,
`zones`), and the audit that found them — ids viewer.js writes to, minus ids a
deployed page has — now returns nothing.

**`scripts/test_viewer_render.py` is the guard.** It renders real committed pages
AND one stripped of every optional control, against the renderer as it stands,
and fails on a page error or a canvas still at its default 300x150. Verified to
catch this exact bug: with the guard removed, three of three real pages fail with
the original message. It skips cleanly where playwright is absent.

**The general shape of this failure will recur**, because the renderer is live
and the pages are not. Anything the renderer touches that a page might not have
goes through `ctl()`.

*Also worth knowing:* `/models/*` is gated unless `MMP_PUBLIC_GALLERY=1`
(`PUBLIC_GALLERY` in server.py, default off), so a signed-out visitor to a model
page is redirected to sign-in. That is a deliberate setting, not a bug — but it
means "the public gallery" on minemodelingpro.com is not public today.

## Where an added source came from (2026-10-26)

Jordan: "i want to be able to provide the source url."

A public model credits the release it was built from — Langis lists ten, each
with its link. An added source now does the same: the Add a source card takes a
link beside the text, `build_dataset(source_url=...)` carries it onto the
dataset, and `projects.assemble` prefers it over `_filing_url`, so it appears in
the Sources panel as a link like any news release. A dataset read through
`release_text` is also labelled `source_kind="news release"` rather than
"imported data", so it reads as what it is.

**Fetching the URL was considered and is not worth building:** globenewswire
returns **403** to an automated request, and that is where these releases live.
The card says so — the link is the attribution, not the input.
`_clean_url` accepts http(s) only (`javascript:` and `ftp:` are refused, tested).

## Connecting a public model to a project that already exists (2026-10-26)

Jordan: "i still cant connect another model to it" / "i need to be able to add a
project after the initial one has been created."

Two separate reasons it could not work.

**1. There was no control.** `attach_to` could only be set on the dashboard when
the project was CREATED; the project page merely printed "built on X". A project
that turned out to have a public counterpart could never be joined to it.

**2. And it would have died if there had been one.** Finding one public project
meant `PROJ.assemble()`: the whole drill bank (**643 MB**) plus every extracted
report (**516 MB**, 599,838 intervals as dicts), 758 projects materialised —
**1,298 MB peak, 127 s**, on a modeler that has **1,048 MB**. The kernel would
have stopped the build, which from the page looks like nothing happening.

`projects.published_sources(slug)` reads the published page's own model JSON,
which already lists every source with its id, and `assemble_slug(slug)` loads
**only those releases and reports** — `load_news(release_ids=...)` filters in the
SQL, `load_reports(only=...)` reads a handful of files. Same answer:

      assemble() + find the slug     1,298 MB    127 s
      assemble_slug(slug)               35 MB      0.0 s

End to end on a real project: 220 holes of the client's own upload + the 125
public Langis holes = **345 holes, 39,828 intervals, 6,023 blocks, 3 s, 177 MB
peak**. Merged on normalised hole id, so a hole both sides have is one hole.

**A slug with no published page is refused** (`_attachable_or_400`). Stored
silently it would attach to nothing, the build would use only the client's own
data, and it would look like it had worked. `GET /api/public/attachable` lists
what can be picked; the project page has a search box, a picker, Connect and
Disconnect, and either queues the rebuild or says to press Rebuild model.

*Two bugs worth keeping in mind, both found by the tests:*

- **A report id carries whichever catalogue's prefix it came from** — `mtp:` for
  the partner catalogue, `ni43101:` for the SEDAR archive. Filtering on
  `ni43101:` dropped every report source silently, so a report-built project
  attached to nothing. The split is "news, or not". And the FILE is the id with
  the colon doubled into underscores (`mtp:0017c…` -> `mtp__0017c….json`), so
  matching on the bare hex finds nothing at all.
- **`ru_maxrss` is a high-water mark that a forked child INHERITS.** Measuring a
  subprocess's memory from a parent that had already read the drill bank reported
  1,160 MB for a call that uses 35. Measure in a fresh process, spawned *before*
  anything in the test balloons — `scripts/test_attach.py` does the probe first,
  on purpose, and says why.

## Reasonable backlog / ideas

- Downhole surveys: supported end to end (drill_tables reads survey tables — hole/depth/azimuth/dip, plausibility-filtered; model3d desurveys by minimum curvature; the viewer draws curved paths). As of 2026-09-19 none of the 201 archived reports publish real survey tables, so holes are straight on collar az/dip until a report or feed supplies them.
- Coordinate systems: drill_tables searches every EPSG projected CRS for the report's stated location (UTM, MTM, US state plane in ft, national grids), confirmed by collar-elevation↔DEM fit. Reports in a pure mine grid (e.g. Goldboro, Island Gold) stay local-frame.
- Navigation (2026-10-13): Orbit/Pan buttons bottom-centre, hold space or right-drag to pan,
  grab-the-ground panning, arrow keys/WASD (Q/E up-down), +/- zoom, R reset, flick-to-glide.
- Names: automatic from report covers / release headlines; fix the rest in `data/keep/mmp_project_overrides.json` (`{slug: {name, company}}`, `_merge` to join projects).
- News holes with easting/northing but no zone (~1,900 in the bank) could be placed by borrowing the zone of a report/release for the same project.
- Optionally consume MiningNewsTerminal's pre-parsed intervals to fill releases where geolocation succeeds but assay extraction was thin.
