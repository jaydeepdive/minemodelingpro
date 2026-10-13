# MMP Studio — upload your own drill data, keep it private, model it

Studio adds accounts, uploads and private projects to MineModelingPro. The public
gallery (43-101 + news models) is unchanged; Studio sits beside it at `/app/`.

```
browser (site/app/*)  ──►  Supabase            ──►  worker (studio/worker.py)
  sign in / upload          auth, Postgres,          parses the files, builds the
  parameters, viewer        storage, job queue       model, writes it back
```

* **Private by default.** Row-level security means a signed-in account can only read
  its own projects, datasets and files. A model becomes public only when its owner
  flips "Share this model publicly", and even then only the model is shared — never
  the uploaded source files.
* **Nothing sensitive in the browser.** The page uses the Supabase *anon* key, which
  acts strictly as the signed-in user. The *service-role* key lives only on the worker.

## 1. Create the Supabase project (5 minutes)

1. supabase.com → New project. Keep the database password somewhere safe.
2. SQL Editor → paste `studio/schema.sql` → Run. It creates the tables, the
   row-level-security policies, the three storage buckets and the sign-up trigger.
3. Project Settings → API. Copy:
   * **Project URL** and **anon public** key → into `site/app/config.js`
   * **service_role** key → the worker's environment (never into the repo)
4. Authentication → Providers → Email: leave "Confirm email" on for public sign-up.
   Authentication → URL Configuration → Site URL:
   `https://jaydeepdive.github.io/minemodelingpro/app/index.html`

Commit `site/app/config.js` with the first two values and the app is live on the
existing GitHub Pages site — no server needed for the front end.

## 2. Run the worker

The worker is the only piece that needs Python. It can run anywhere that reaches
Supabase — the Mac mini that already runs the SEDAR collector is the obvious home.

```bash
cd ~/minemodelingpro
pip3 install -r requirements.txt supabase
export SUPABASE_URL=https://<project-ref>.supabase.co
export SUPABASE_SERVICE_KEY=<service-role key>
PYTHONPATH=src python3 studio/worker.py          # --once for a single pass
```

Keep it running with launchd (`scripts/com.thedeepdive.mmpstudio.plist`):

```bash
cp scripts/com.thedeepdive.mmpstudio.plist ~/Library/LaunchAgents/
# put the two environment values in the plist first, then:
launchctl load ~/Library/LaunchAgents/com.thedeepdive.mmpstudio.plist
```

If the worker is down, uploads still succeed — jobs queue and run when it comes back.

## 3. What a user does

1. Sign up at `/app/`, create a project (optionally "built on top of" a public model,
   which merges their holes with the drilling MMP already has, by hole id).
2. Drop in collars / assays / surveys — CSV, Excel, Access, or a zip. Pick the UTM
   zone or EPSG code (or say it's a local grid).
3. The worker parses, reports exactly what it understood per table, and builds the
   model. The project page shows holes, intervals, blocks, tonnes, grade and
   contained metal, and opens the full 3D viewer.
4. Parameters (block size, search radius, composite length, estimator, top-cut,
   density, cut-off, elements …) are editable per project; saving rebuilds.
5. "Share this model publicly" copies the model — not the files — into the public
   bucket, where it appears under Shared models.

## Columns the importer understands

| Table | Needs | Also handled |
|---|---|---|
| Collars | hole id + easting/northing or lat/long | elevation, azimuth, dip, total depth; feet or metres |
| Assays | hole id, from, to (or length), grade column(s) | long format (element + value), %, g/t, ppm, ppb, oz/t |
| Surveys | hole id, depth, azimuth, dip | drives minimum-curvature hole paths |

Headers are matched by meaning: `HOLEID`, `Hole_No`, `BHID`, `DDH`, `FROM_FT`,
`Au_ppm`, `Cu (%)`, `Depth_m` all land in the right place. Anything skipped is
counted and explained on the project page.

Access databases (`.mdb`/`.accdb`) need `mdbtools` on the worker machine
(`brew install mdbtools` / `apt-get install mdbtools`).

## Local use without Supabase

The same import path works from the command line, which is handy for testing:

```bash
PYTHONPATH=src python3 -m minemodelingpro.imports ~/data/*.csv \
    --project "North Ridge" --company "Example Mining" --utm-zone 11 \
    --set block=5,5,5 density=2.75 --private --build
```

`--private` keeps the dataset in `data/private/` (gitignored); without it the
dataset is written to `data/keep/mmp_imports/` and the daily public build picks it up.

## Costs and limits

Supabase's free tier covers a small team comfortably (500 MB database, 1 GB storage,
50k monthly active users). Studio enforces a 200 MB per-file limit and a 512 MB
per-account storage quota (`profiles.quota_bytes`) — raise it per account in SQL.
Heavy public sign-up would eventually need the paid tier for storage and egress.
