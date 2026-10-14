# MMP Studio — upload your own drill data, keep it private, model it

Studio adds accounts, uploads and private projects to MineModelingPro. The public
gallery (43-101 + news models) is unchanged; Studio sits beside it at `/app/`.

```
browser (site/app/*)  ──►  studio/server.py  ──►  Postgres + Spaces
  sign in / upload          FastAPI, session         accounts, projects,
  parameters, viewer        cookie, no keys          jobs, files
                                   │
                                   ▼
                            studio/worker.py — parses the files, builds the model
```

Everything runs on DigitalOcean, in one App Platform app inside its own DO project
(**studio/DEPLOY-DO.md** is the setup and operations guide). There is no third-party
auth service and no API key in the browser.

* **Invite-only.** MMP runs on a client basis: an admin issues an invite code, the
  client signs up with it. There is no open sign-up.
* **Private by default.** Every API call is authenticated and scoped to the signed-in
  account; object keys are prefixed with the owner's id. A model becomes visible to
  others only when its owner shares it — and only the *model* is shared, never the
  uploaded source files.
* **Nothing sensitive in the browser.** The page holds a session cookie and nothing
  else. The database URL and the Spaces keys exist only in the app's environment.

## What a client does

1. Sign in at `/app/`, create a project (optionally "built on top of" a public model,
   which merges their holes with the drilling MMP already has, by hole id).
2. Drop in collars / assays / surveys — CSV, Excel, Access, or a zip. Pick the UTM
   zone or EPSG code (or say it's a local grid).
3. The worker parses, reports exactly what it understood per table, and builds the
   model. The project page shows holes, intervals, blocks, tonnes, grade and
   contained metal, and opens the full 3D viewer.
4. Parameters (block size, search radius, composite length, estimator, top-cut,
   density, cut-off, elements …) are editable per project; saving rebuilds.

## Where the parameters start

A project's parameters are not guesses:

1. **The most recent technical report for that project.** `drill_tables` lifts the
   parent block size, bulk density, composite length, search range, capping grade,
   reporting cut-off and estimator from every 43-101 it extracts;
   `projects._report_params` takes them newest-first (by stated effective date), so a
   2024 resource update supplies the block size even if only a 2016 PEA printed a density.
2. **Otherwise, what is typical for the region and commodity** —
   `minemodelingpro.regional` measures that across the whole extracted corpus
   (`data/keep/mmp_regional_params.json`), narrowing from region+commodity to
   commodity to region to global as the evidence allows.
3. **Then whatever the client sets**, per project, which always wins.

The model page and the parameter form both say which of the three each number came
from (`model.param_sources`), so nothing is a black box.

## Columns the importer understands

| Table | Needs | Also handled |
|---|---|---|
| Collars | hole id + easting/northing or lat/long | elevation, azimuth, dip, total depth; feet or metres |
| Assays | hole id, from, to (or length), grade column(s) | long format (element + value), %, g/t, ppm, ppb, oz/t |
| Surveys | hole id, depth, azimuth, dip | drives minimum-curvature hole paths |

Headers are matched by meaning: `HOLEID`, `Hole_No`, `BHID`, `DDH`, `FROM_FT`,
`Au_ppm`, `Cu (%)`, `Depth_m` all land in the right place. Anything skipped is
counted and explained on the project page.

Access databases (`.mdb`/`.accdb`) need `mdbtools`, which the Docker image installs.

## Running it locally

```bash
createdb mmp
export DATABASE_URL=postgresql:///mmp PGSSLMODE=disable MMP_SECRET=dev
export MMP_ADMIN_EMAIL=you@example.com MMP_ADMIN_PASSWORD=a-good-password
export MMP_COOKIE_SECURE=0            # plain http
pip install -r studio/requirements.txt
PYTHONPATH=src:. python -m uvicorn studio.server:app --port 8099     # the site + API
PYTHONPATH=src:. python studio/worker.py                             # the modeller
```

With no `SPACES_*` set, uploads and models go to `data/private/studio/` on disk.

The same import path also works straight from the command line, without a server:

```bash
PYTHONPATH=src python3 -m minemodelingpro.imports ~/data/*.csv \
    --project "North Ridge" --company "Example Mining" --utm-zone 11 \
    --set block=5,5,5 density=2.75 --private --build
```

## Limits

200 MB per uploaded file, 2 GB per account (`users.quota_bytes`, raise it in SQL).
Spaces includes 250 GiB of storage and 1 TiB of transfer; the in-app Postgres is
sized for a client roster, not a public sign-up.
