# MineModelingPro

Interactive 3D deposit models and NI 43-101 economic extraction for junior mining
projects. Built from two data streams:

- **Government drill-hole backbone + geochemistry** — provincial collar/assay data
  (`mmp-backbone`, `mmp-geochem` workflows), stored as size-capped parquet shards
  under `data/keep/mmp/`.
- **NI 43-101 technical reports** — resource tables, economics (NPV/IRR/payback/
  capex/AISC/LOM), methodology and metallurgy extracted from SEDAR filings
  (`mmp-reports` workflow; `ceo-sedar` collects the public-feed manifest).
- **Drill-result news** — recent full-assay releases, synced in at build time (see
  below) and rendered as live, densifying 3D models.

The site (a 3D model gallery + per-deposit viewers) is built and deployed to
GitHub Pages by the `mmp-daily-build` workflow.

## Drill-news source

MMP reads a drill-news bank at `data/keep/drillbank.sqlite` (gitignored — it is not
MMP's to own). `minemodelingpro.drill_sync` populates it at build time from either:

1. **MineTerminalPro API** (target) — set repo secrets `MTP_API_URL` and
   `MTP_API_KEY`. Pulls the drill-results category and writes the
   releases/holes/intervals tables MMP expects.
2. **A local copy** (interim, until the API key lands) — set
   `CLOSEOLOGY_DRILLBANK=/path/to/closeology/data/keep/drillbank.sqlite`.

If neither is set it is a no-op; the build still succeeds on the government backbone
and 43-101 data (the news layer is simply skipped).

## Local build

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m minemodelingpro.drill_sync          # optional: sync news
PYTHONPATH=src python -m minemodelingpro.shards rebuild      # working store from shards
PYTHONPATH=src python -m minemodelingpro.export              # merge + re-shard
PYTHONPATH=src python -c "from minemodelingpro import model3d; model3d.build_all('site')"
```

Heavy assets (`data/keep/mmp.sqlite` ~100 MB gov backbone; `data/keep/sedar_pdfs/`)
are not committed — the backbone is regenerated weekly by `mmp-backbone`, and report
PDFs are retained in the report-archive GitHub release.

---
*Split out of the Closeology repository. Grades are inverse-distance estimates for
visualization only — not a mineral resource estimate.*
