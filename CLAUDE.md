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

## Reasonable backlog / ideas

- Downhole surveys: supported end to end (drill_tables reads survey tables — hole/depth/azimuth/dip, plausibility-filtered; model3d desurveys by minimum curvature; the viewer draws curved paths). As of 2026-09-19 none of the 201 archived reports publish real survey tables, so holes are straight on collar az/dip until a report or feed supplies them.
- Coordinate systems: drill_tables searches every EPSG projected CRS for the report's stated location (UTM, MTM, US state plane in ft, national grids), confirmed by collar-elevation↔DEM fit. Reports in a pure mine grid (e.g. Goldboro, Island Gold) stay local-frame.
- Navigation (2026-10-13): Orbit/Pan buttons bottom-centre, hold space or right-drag to pan,
  grab-the-ground panning, arrow keys/WASD (Q/E up-down), +/- zoom, R reset, flick-to-glide.
- Names: automatic from report covers / release headlines; fix the rest in `data/keep/mmp_project_overrides.json` (`{slug: {name, company}}`, `_merge` to join projects).
- News holes with easting/northing but no zone (~1,900 in the bank) could be placed by borrowing the zone of a report/release for the same project.
- Optionally consume MiningNewsTerminal's pre-parsed intervals to fill releases where geolocation succeeds but assay extraction was thin.
