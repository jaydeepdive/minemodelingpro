# MineModelingPro on DigitalOcean

Everything — the public gallery, Studio's API and the modelling worker — runs as one
App Platform app in its own DigitalOcean **project** (`MineModelingPro`), so it shares
nothing with the other projects on the account: its own app, its own database, its own
Spaces bucket, its own scoped API token.

```
DO project "MineModelingPro"  (region: Toronto)
├── app  minemodelingpro
│   ├── service web      Dockerfile → uvicorn studio.server:app      minemodelingpro.com
│   ├── worker  modeler  Dockerfile → python studio/worker.py
│   └── database db      Postgres (attached, DATABASE_URL injected)
├── Spaces  mmp-studio   tor1 — uploads/ models/ public/
└── DNS     minemodelingpro.com (primary), www, minemodelingpro.ca (alias)
```

The app builds straight from the **public** GitHub repo over https, so DigitalOcean
needs no GitHub authorisation and holds no repo credentials.

## Environment variables

Set at the app level; the ones marked SECRET are encrypted by App Platform and are
never readable again through the API.

| Key | Value | |
|---|---|---|
| `DATABASE_URL` | `${db.DATABASE_URL}` | injected by App Platform |
| `MMP_ENV` | `production` | makes the session cookie https-only and requires `MMP_SECRET` |
| `MMP_SECRET` | 48 random bytes | SECRET — signs session cookies; changing it signs everyone out |
| `SPACES_BUCKET` | `mmp-studio` | |
| `SPACES_REGION` | `tor1` | |
| `SPACES_KEY` / `SPACES_SECRET` | Spaces access key | SECRET |
| `MMP_ADMIN_EMAIL` / `MMP_ADMIN_PASSWORD` | bootstrap admin | SECRET; used only when that account does not exist yet |

## Day-to-day

```bash
export DIGITALOCEAN_TOKEN=...            # scoped: app, database, domain, project
A="Authorization: Bearer $DIGITALOCEAN_TOKEN"
APP=<app-id>

# what is live, and the last few deployments
curl -s -H "$A" https://api.digitalocean.com/v2/apps/$APP | jq '.app.live_url, .app.active_deployment.phase'
curl -s -H "$A" "https://api.digitalocean.com/v2/apps/$APP/deployments?per_page=5" | jq '.deployments[] | {id, phase, created_at}'

# deploy the current main. The app is wired to the PUBLIC git url, which DigitalOcean
# cannot receive webhooks for, so a deployment is triggered explicitly — including
# after the daily GitHub Actions gallery rebuild. (Connecting the repo through DO's
# GitHub integration in the console would make deploys automatic on push.)
curl -s -X POST -H "$A" https://api.digitalocean.com/v2/apps/$APP/deployments -d '{"force_build":true}'

# logs
curl -s -H "$A" "https://api.digitalocean.com/v2/apps/$APP/deployments/<dep>/components/web/logs?type=BUILD" | jq -r .historic_urls[0]
curl -s -H "$A" "https://api.digitalocean.com/v2/apps/$APP/components/modeler/logs?type=RUN&follow=false" | jq -r .historic_urls[0]
```

`.do/app.yaml` is the reference copy of the spec. To change the shape of the app,
edit it and `PUT /v2/apps/$APP` with `{"spec": …}` (or `doctl apps update`).

## Database

The app's Postgres is attached to the app, so only this app can reach it; there is no
public listener to firewall. The schema is applied by the app itself on start
(`studio/schema_pg.sql`, idempotent).

To open a psql session, use the connection details from
`GET /v2/apps/$APP` → `.app.spec.databases` / the DO console, or run a one-off
command inside the app console in the DO UI.

Raising an account's quota:

```sql
update users set quota_bytes = 10 * 1024^3 where email = 'client@example.com';
```

## Rotating credentials

1. Spaces: create a new access key limited to `mmp-studio`, update `SPACES_KEY` /
   `SPACES_SECRET`, redeploy, then delete the old key.
2. API token: generate a new scoped token, replace it wherever it is stored, delete
   the old one. The app itself does not use the API token — only operators do.
3. `MMP_SECRET`: changing it invalidates every session (everyone signs in again).

## What to watch

* **Build time.** The image installs numpy/scipy/pandas/pyproj; a cold build takes a
  few minutes. `.dockerignore` keeps the parquet shard store and the DEM cache out.
* **Worker memory.** Block models are numpy arrays; a very large project can outgrow
  a 1 GB worker. If a build dies with no error in the log, raise the `modeler`
  instance size (`apps-s-1vcpu-2gb`) — that is almost always what happened.
* **The public gallery is still built in GitHub Actions** (`mmp-daily-build`), which
  commits `site/` to `main`. Because the app builds from the public git url rather than
  a connected GitHub account, that commit does **not** deploy itself — trigger a
  deployment (above), or connect the repo through DO's GitHub integration once and
  deploys become automatic.
* **The landing page is `site/index.html` and is hand-written** — the daily build no
  longer copies the gallery over it (`models.html` is the gallery).
