# MineModelingPro

Deposit-modelling project that shares a data bank with **Project Closeology**.

## The shared bank

Both projects read and write **one** store: `data/keep/drillbank.sqlite`
(created and filled by `src/newswire/`). It is committed to the repo so it
survives CI rebuilds and **accumulates** — every daily build adds the day's new
mining news releases; a `backfill` run reaches further back in history.

### Tables

| table | grain | key columns |
|---|---|---|
| `releases` | one press release | `id`, `source`, `url`, `company`, `published`, `country`, `status` (`ok`/`empty`/`error`), `reason`, `utm_zone`, `datum` |
| `holes` | one drill collar | `hole_id`, `easting`, `northing`, `utm_zone`, `datum`, `lat`, `lon`, `elev_m`, `azimuth`, `dip`, `depth_m` |
| `intervals` | one assay interval × element | `hole_id`, `from_m`, `to_m`, `length_m`, `element`, `grade`, `unit`, `is_subinterval` |

`releases.status` is also the **failure log**: rows with `empty` (nothing
parsed) or `error` (fetch/parse failed) record which releases the deterministic
parser missed and why — the queue for the planned LLM-assisted extraction pass.

```sql
-- what got missed, newest first
SELECT source, url, reason FROM releases WHERE status!='ok' ORDER BY fetched_at DESC;
```

## How the two projects use it

- **Closeology / Drill Radar** (`src/newswire/radar.py`) — takes the *geolocated*
  recent results, places them on the map, and feeds the open-ground screen.
- **MineModelingPro** (`export.py`) — takes the *full* collar + assay record
  (worldwide, all commodities, geolocated or not — easting/northing is enough
  for a local model) and flattens it to modelling-ready tables:
  - `data/keep/mmp_collars.parquet`
  - `data/keep/mmp_assays.parquet`

```bash
PYTHONPATH=src python -m minemodelingpro.export
```

## Collection

```bash
PYTHONPATH=src python -m newswire.run incremental            # daily (in CI)
PYTHONPATH=src python -m newswire.run backfill --limit 400   # deeper history, resumable
PYTHONPATH=src python -m newswire.run stats                  # bank + failure counts
```

Sources: newsfilecorp (full), thenewswire (full); Cision/newswire.ca,
GlobeNewswire, AccessWire, BusinessWire are registered adapters that activate as
their listings/feeds (or the LLM fetch) are wired in — no orchestrator change
needed. Extraction is deterministic today (HTML collar/assay tables + common
prose intercept patterns); an LLM fallback for prose-only releases is planned
and will read straight from the `status!='ok'` failure queue.
