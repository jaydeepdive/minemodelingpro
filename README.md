# MineModelingPro

Interactive 3D deposit models for junior mining projects — one model per project,
built from every source MMP holds for it:

- **NI 43-101 technical reports** — collar tables, significant-intercept / assay
  tables, coordinate system, resource statement and block-model parameters, read
  straight from the archived PDFs (`drill_tables.py` → `data/keep/mmp_reports/`).
- **Drill-result news** — geolocated releases from Closeology's drill bank, synced
  in at build time (`closeology_sync.py`).
- **Government drill-hole backbone + geochemistry** — provincial collar/assay data
  (`mmp-backbone`, `mmp-geochem`), stored as parquet shards under `data/keep/mmp/`.

`projects.py` unifies the sources into one dataset per project (holes joined by
normalised hole ID, every contributing report/release listed on the model page);
`model3d.py` builds the grade-coloured drill intervals, an inverse-distance block
model on the technical report's own block size, grade-tonnage and contained-metal
curves checked against the published resource, and real terrain from the AWS
Terrain Tiles DEM. The site (gallery + per-project viewers) is built and deployed
to GitHub Pages by the `mmp-daily-build` workflow.

## Local build

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m minemodelingpro.closeology_sync          # drill bank + Closeology data
PYTHONPATH=src python -m minemodelingpro.drill_tables all         # extract any new archived reports
PYTHONPATH=src python -c "from minemodelingpro import model3d; model3d.build_all('site')"
```

Heavy assets are not committed: `data/keep/mmp.sqlite` (gov backbone, rebuilt
weekly), `data/keep/drillbank.sqlite` (synced), report PDFs (kept in the
report-archive GitHub releases) and `data/cache/` (DEM tiles).

---
*Grades are inverse-distance estimates for visualization only — not a mineral
resource estimate.*
