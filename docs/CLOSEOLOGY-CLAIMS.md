# What MineModelingPro needs from Closeology: mineral claims

**Status:** request / specification. Written 2026-10-14 by the MMP side.
**Audience:** the Closeology chat and crawler.
**Why:** MMP models deposits. It does not know what ground those deposits sit on, who
holds it, or when that ground is let go. Closeology already crawls, geolocates and
publishes mining data; claims belong on that side of the line, exactly like the drill
bank. MMP wants to consume them, not collect them.

---

## 1. What MMP will do with it

1. **Attribute every model to ground.** Each MMP model has a footprint — the block
   model's outline and the collars that built it, in real coordinates. Intersecting
   that footprint with claim polygons answers two different questions:
   - *which claims the project holds* (the whole property), and
   - *which claims the deposit is actually on* (the ones the mineralised blocks sit
     under). These are rarely the same, and the second is the valuable one.
2. **Watch for ground being let go.** A claim lapsing or being dropped is a signal on
   its own. A claim lapsing **with modelled mineralisation under it** is a much louder
   one. MMP will raise that as an alert, scored by how much estimated metal sits on
   the dropped ground, and publish it for Closeology's radar to deep-link.

So the ask is for two things: **current claims** (a snapshot), and **changes to
claims** (a stream). The second cannot be reconstructed reliably from snapshots alone
unless the snapshots are kept, so please keep them.

---

## 2. How to deliver it (no API needed)

MMP already mirrors **every** data file Closeology commits: `closeology_sync` walks
`data/keep/` in the Closeology repo and copies `.json`, `.parquet`, `.csv`,
`.geojson` and `.sqlite` files into MMP at build time. Nothing new is required on
either side if the files simply appear there:

```
closeology/data/keep/claims_current.parquet     # the snapshot
closeology/data/keep/claims_events.parquet      # the change stream
closeology/data/keep/claims_meta.json           # coverage + freshness
```

That is the preferred route — it is already automated, needs no credentials, and
fails safe. An HTTP API is only worth building if Closeology would rather not commit
the geometry (see §7).

Size: geometry for a full province is large. Please ship **claim outlines simplified
to ~5 m tolerance** and keep each file under the repo's practical limit (~90 MB);
split per jurisdiction if needed (`claims_current_bc.parquet`, …). MMP will read any
`claims_current*` file it finds.

---

## 3. `claims_current` — the snapshot

One row per **active** tenure. Weekly is enough; daily is better.

| field | type | notes |
|---|---|---|
| `claim_id` | string | **Stable** registry identifier (tenure number). Must not change between runs for the same ground. |
| `jurisdiction` | string | `BC`, `ON`, `QC`, `NB`, `NL`, `YT`, `NT`, `NU`, `SK`, `MB`, `AB`, `NS`, `PE`, or a US state / country code. |
| `registry` | string | Which system it came from (`MTO`, `CLAIMaps`, `GESTIM`, `MARIS`, …). |
| `status` | string | Normalised: `active`, `pending`, `lapsed`, `cancelled`, `surrendered`, `forfeited`. Only non-terminal rows belong in `claims_current`. |
| `holder` | string | Registered holder, as the registry spells it. |
| `holder_key` | string | Normalised holder for matching (lowercase, legal suffixes stripped). |
| `holder_pct` | float | Optional, when a claim is held jointly. |
| `recorded_date` | date | When the claim was staked / recorded. |
| `expiry_date` | date | Current good-standing / expiry date. **This is what makes an alert predictable.** |
| `area_ha` | float | Hectares. |
| `geometry` | WKT or GeoJSON string | Polygon or MultiPolygon, **EPSG:4326**, valid (no self-intersections), simplified ≤5 m. |
| `centroid_lat`, `centroid_lon` | float | Convenience; MMP will compute if absent. |
| `project_name` | string | Optional — only if the registry or Closeology already attributes it. |
| `source_url` | string | Deep link to the claim in the registry, if one exists. |
| `as_of` | timestamp | When this row was read from the registry (UTC). |

**Hard requirements:** `claim_id` stable, `geometry` valid and in EPSG:4326, `as_of`
present. Everything else MMP can live without.

---

## 4. `claims_events` — the change stream

One row per observed change. This is what drives alerts; append-only.

| field | type | notes |
|---|---|---|
| `event_id` | string | Stable hash of (`jurisdiction`, `claim_id`, `event`, `observed_at`). |
| `claim_id`, `jurisdiction` | string | As above. |
| `event` | string | `added`, `dropped`, `expired`, `transferred`, `renewed`, `expiring_soon`, `area_changed`. |
| `observed_at` | timestamp | When Closeology saw the change (UTC). |
| `effective_date` | date | When the registry says it happened, if stated. |
| `prev_holder`, `new_holder` | string | For `transferred`. |
| `prev_expiry`, `new_expiry` | date | For `renewed` / `expiring_soon`. |
| `geometry` | WKT/GeoJSON | The claim's outline **as it was when the event was seen** — essential for `dropped`, since the row leaves `claims_current`. |
| `area_ha` | float | |
| `source_url` | string | |

`dropped` and `expired` are the ones MMP acts on. `expiring_soon` (say, 90 days out)
is almost as useful, because it is actionable *before* the ground is open.

---

## 5. `claims_meta.json` — coverage and freshness

So MMP can be honest about what it does and does not know:

```json
{
  "generated": "2026-10-14T06:00Z",
  "jurisdictions": [
    {"code": "BC", "registry": "MTO", "claims": 112340, "as_of": "2026-10-14",
     "cadence": "daily", "history_from": "2026-03-01", "geometry": true},
    {"code": "ON", "registry": "CLAIMaps", "claims": 98211, "as_of": "2026-10-13",
     "cadence": "weekly", "history_from": "2026-06-14", "geometry": true}
  ],
  "notes": "QC GESTIM polygons are cell-based; areas are nominal."
}
```

---

## 6. Jurisdictions, in the order they are worth doing

Ranked by how many MMP models sit in them today (120 models, 2026-10-14):

1. **Ontario** — 24 models · CLAIMaps / Ontario GeoHub open data
2. **Nevada** — 11 · BLM LR2000 / MLRS claim data
3. **Québec** — 10 · GESTIM
4. **British Columbia** — 9 · Mineral Titles Online, open data on the BC catalogue
5. **Arizona, Yukon** — 6 each · BLM; Yukon mining recorder open data
6. **Mexico, New Brunswick, Newfoundland** — 4–5 each
7. Everything else as it comes.

Canadian provinces and BLM all publish claim polygons as open data; none of this
needs scraping behind a login, and none of it should touch an account.

---

## 7. If you would rather expose an API than commit files

Equivalent contract, read-only, token in a header:

```
GET /claims?bbox=minlon,minlat,maxlon,maxlat&as_of=2026-10-14   -> claims_current rows
GET /claims/{jurisdiction}/{claim_id}                           -> one claim + history
GET /claims/events?since=2026-10-01&event=dropped,expired       -> claims_events rows
GET /claims/meta                                                -> claims_meta.json
```

JSON, paginated by `cursor`, same field names as above. MMP would cache responses
and still expects `as_of` on every row. The file route remains preferable: it costs
Closeology nothing to serve and MMP already mirrors it.

---

## 8. What MMP publishes back

So this is a loop, not a one-way ask:

- `models_index.json` already maps release URLs to model URLs (Closeology's radar
  deep-links to it). MMP will add, per model, the claims its footprint touches:
  `{"slug": …, "claims": [{"jurisdiction": "ON", "claim_id": "…", "hosts_deposit": true}]}`.
- A new `claims_alerts.json`: every dropped or expiring claim that has modelled
  mineralisation under it, with the tonnes and contained metal of the blocks on that
  ground, the model's URL, and the event that triggered it. Closeology's radar can
  surface those directly.

---

## 9. What MMP will build on its side, once the data is there

1. `claims.py` — read the mirrored files, index polygons (STRtree), match each model's
   block footprint and collars, write `claims` onto the dataset.
2. Model page — a Claims section: which claims the deposit is on, who holds them,
   expiry dates, and a flag when any of it is lapsing.
3. `claims_alerts.json` + an alert feed in Studio, scored by contained metal on the
   affected ground.
4. Nightly, in the same CI build as everything else. No new infrastructure.

**The blocking dependency is §3 and §4.** Everything else is MMP's problem.
