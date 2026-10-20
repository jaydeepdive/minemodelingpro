"""MMP Studio server — accounts, uploads, private models, and the public site.

One process serves everything at minemodelingpro.com:

    /                     the public gallery that `model3d.build_all` renders into site/
    /app/                 Studio: sign in, upload drill data, set parameters, 3D viewer
    /api/*                this module's JSON API (session cookie, no keys in the browser)

Accounts are **issued, not opened**: MMP runs on a client basis, so sign-up needs an
invite code that an admin creates. Every API query is scoped to the signed-in
account; object keys are prefixed with the owner's id. The modelling itself happens
in studio/worker.py, which picks jobs off the `jobs` table.

Environment (App Platform sets the first one for you):
    DATABASE_URL, SPACES_BUCKET, SPACES_REGION, SPACES_KEY, SPACES_SECRET
    MMP_SECRET          session signing key (required in production)
    MMP_ADMIN_EMAIL     bootstrap admin, created on first start
    MMP_ADMIN_PASSWORD
    MMP_SITE_DIR        default: ../site

Run locally:
    DATABASE_URL=postgresql://localhost/mmp MMP_SECRET=dev \\
      PYTHONPATH=src uvicorn studio.server:app --reload
"""
import os
import re
import sys
import json
import time
import uuid
import hmac
import base64
import hashlib
import secrets
import datetime
import mimetypes
import urllib.parse

from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from studio import store                                   # noqa: E402
from minemodelingpro import params as PARAMS               # noqa: E402
from minemodelingpro import regional as REG                # noqa: E402

SITE_DIR = os.environ.get("MMP_SITE_DIR") or os.path.join(_ROOT, "site")
SECRET = (os.environ.get("MMP_SECRET") or "").encode() or None
COOKIE = "mmp_session"
# https-only in production; plain http only for a local run
COOKIE_SECURE = (os.environ.get("MMP_COOKIE_SECURE", "")
                 or ("1" if os.environ.get("MMP_ENV") == "production" else "0")) not in ("0", "false", "")
SESSION_DAYS = float(os.environ.get("MMP_SESSION_DAYS", "14"))
MAX_UPLOAD_MB = float(os.environ.get("MMP_MAX_FILE_MB", "200"))
VERSION = os.environ.get("MMP_VERSION") or datetime.date.today().isoformat()
# The gallery is behind the sign-in by default: MMP runs on a client basis, so only
# the landing page is public. Set MMP_PUBLIC_GALLERY=1 to open /models* to everyone.
PUBLIC_GALLERY = (os.environ.get("MMP_PUBLIC_GALLERY", "0") or "0").lower() not in ("0", "false", "no", "")

app = FastAPI(title="MineModelingPro Studio", docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------- passwords
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_pw(pw, stored):
    try:
        kind, n, r, p, salt, want = stored.split("$")
        if kind != "scrypt":
            return False
        h = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                           p=int(p), dklen=len(base64.b64decode(want)))
        return hmac.compare_digest(h, base64.b64decode(want))
    except Exception:
        return False


# ----------------------------------------------------------------- sessions
def _sign(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(SECRET or b"dev-secret", body, hashlib.sha256).digest()[:16]
    return body.decode() + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def _unsign(token):
    try:
        body, sig = token.split(".", 1)
        want = hmac.new(SECRET or b"dev-secret", body.encode(), hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(base64.urlsafe_b64encode(want).rstrip(b"=").decode(), sig):
            return None
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + pad))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def set_session(resp, user):
    resp.set_cookie(COOKIE, _sign({"uid": user["id"] if isinstance(user["id"], str) else str(user["id"]),
                                   "tv": user.get("token_version", 1),
                                   "exp": time.time() + SESSION_DAYS * 86400}),
                    httponly=True, secure=COOKIE_SECURE, samesite="lax",
                    max_age=int(SESSION_DAYS * 86400), path="/")


def me(request: Request):
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    data = _unsign(tok)
    if not data:
        return None
    u = store.db().one("select * from users where id = %s", data["uid"])
    if not u or u["disabled"] or u["token_version"] != data.get("tv"):
        return None
    return u


def require(request: Request):
    u = me(request)
    if not u:
        raise HTTPException(401, "sign in first")
    return u


def require_admin(request: Request):
    u = require(request)
    if u["role"] != "admin":
        raise HTTPException(403, "admins only")
    return u


def event(uid, kind, **detail):
    try:
        store.db().run("insert into events (user_id, kind, detail) values (%s,%s,%s)",
                       uid, kind, json.dumps(detail))
    except Exception:
        pass


# ------------------------------------------------------------------- helpers
def slugify(s, fallback="project"):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48] or fallback


def public_user(u):
    return {"id": str(u["id"]), "email": u["email"], "name": u.get("name"),
            "role": u["role"], "quota_bytes": u["quota_bytes"]}


def project_or_404(u, pid):
    p = store.db().one("select * from projects where id = %s", pid)
    if not p or (str(p["owner"]) != str(u["id"]) and u["role"] != "admin"):
        raise HTTPException(404, "no such project")
    return p


def queue(kind, owner, project_id=None, dataset_id=None, payload=None):
    return store.db().one(
        "insert into jobs (kind, owner, project_id, dataset_id, payload) "
        "values (%s,%s,%s,%s,%s) returning *",
        kind, owner, project_id, dataset_id, json.dumps(payload or {}))


def jsonable(row):
    out = {}
    for k, v in dict(row or {}).items():
        if isinstance(v, (uuid.UUID,)):
            out[k] = str(v)
        elif isinstance(v, (datetime.datetime, datetime.date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


_LOGIN_TRIES = {}


def throttle(key, limit=8, window=300):
    now = time.time()
    hits = [t for t in _LOGIN_TRIES.get(key, []) if now - t < window]
    hits.append(now)
    _LOGIN_TRIES[key] = hits[-20:]
    if len(hits) > limit:
        raise HTTPException(429, "too many attempts — wait a few minutes")


# ------------------------------------------------------------------- startup
@app.on_event("startup")
def startup():
    if not SECRET and os.environ.get("MMP_ENV") == "production":
        raise SystemExit("MMP_SECRET must be set in production")
    d = store.wait_for_db()
    d.init_schema()
    em = (os.environ.get("MMP_ADMIN_EMAIL") or "").strip().lower()
    pw = os.environ.get("MMP_ADMIN_PASSWORD") or ""
    if em and pw:
        if not d.one("select id from users where lower(email) = %s", em):
            d.run("insert into users (email, pw, role, name, quota_bytes) "
                  "values (%s,%s,'admin',%s,%s)", em, hash_pw(pw), "Administrator", 21474836480)
            print(f"[server] created admin {em}", flush=True)
    # the published gallery comes from object storage when it is there, so a new
    # build reaches the site without a deployment
    try:
        from studio import sitesync

        def _ready(path, commit):
            if not sitesync.fresher_than(SITE_DIR, path):
                print(f"[server] mirrored site {str(commit)[:7]} is older than this build — "
                      f"serving the image's own site/", flush=True)
                LIVE["dir"] = SITE_DIR
                return
            LIVE["dir"] = path
            LIVE["commit"] = commit
            print(f"[server] serving mirrored site {str(commit)[:7]} from {path}", flush=True)

        c = sitesync.pull()
        if c:
            _ready(sitesync.LOCAL, c)
        sitesync.start_server(_ready)
    except Exception as e:
        print(f"[server] sitesync unavailable: {str(e)[:140]}", flush=True)
    print(f"[server] ready; site={LIVE['dir']}", flush=True)


@app.get("/api/health")
def health():
    try:
        store.db().one("select 1")
        ok = True
    except Exception:
        ok = False
    return {"ok": ok, "version": VERSION}


# ---------------------------------------------------------------------- auth
@app.post("/api/auth/signup")
async def signup(request: Request):
    b = await request.json()
    email = (b.get("email") or "").strip().lower()
    pw = b.get("password") or ""
    code = (b.get("invite") or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "that does not look like an email address")
    if len(pw) < 10:
        raise HTTPException(400, "use a password of at least 10 characters")
    throttle("signup:" + (request.client.host if request.client else "?"))
    d = store.db()
    inv = d.one("select * from invites where code = %s", code)
    if not inv or inv["used_by"] or (inv["expires_at"] and inv["expires_at"] < datetime.datetime.now(
            datetime.timezone.utc)):
        raise HTTPException(403, "that invite code is not valid")
    if inv["email"] and inv["email"].strip().lower() != email:
        raise HTTPException(403, "that invite was issued to a different address")
    if d.one("select id from users where lower(email) = %s", email):
        raise HTTPException(409, "there is already an account for that address")
    u = d.one("insert into users (email, pw, name, role) values (%s,%s,%s,%s) returning *",
              email, hash_pw(pw), (b.get("name") or "").strip() or None, inv["role"])
    d.run("update invites set used_by = %s, used_at = now() where code = %s", u["id"], code)
    event(u["id"], "signup", email=email)
    r = JSONResponse({"user": public_user(u)})
    set_session(r, u)
    return r


@app.post("/api/auth/login")
async def login(request: Request):
    b = await request.json()
    email = (b.get("email") or "").strip().lower()
    throttle("login:" + email)
    u = store.db().one("select * from users where lower(email) = %s", email)
    if not u or u["disabled"] or not check_pw(b.get("password") or "", u["pw"]):
        raise HTTPException(401, "wrong email or password")
    store.db().run("update users set last_seen = now() where id = %s", u["id"])
    event(u["id"], "login")
    r = JSONResponse({"user": public_user(u)})
    set_session(r, u)
    return r


@app.post("/api/auth/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


@app.get("/api/me")
def whoami(request: Request):
    u = me(request)
    if not u:
        return {"user": None}
    used = 0
    try:
        used = store.files().usage(f"{u['id']}/")
    except Exception:
        pass
    return {"user": public_user(u), "used_bytes": used}


@app.post("/api/auth/password")
async def change_password(request: Request, u=Depends(require)):
    b = await request.json()
    if not check_pw(b.get("current") or "", u["pw"]):
        raise HTTPException(403, "current password is wrong")
    new = b.get("password") or ""
    if len(new) < 10:
        raise HTTPException(400, "use a password of at least 10 characters")
    store.db().run("update users set pw = %s, token_version = token_version + 1 where id = %s",
                   hash_pw(new), u["id"])
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


# ------------------------------------------------------------------- invites
@app.get("/api/invites")
def list_invites(u=Depends(require_admin)):
    return {"invites": [jsonable(r) for r in store.db().all(
        "select * from invites order by created_at desc limit 200")]}


@app.post("/api/invites")
async def make_invite(request: Request, u=Depends(require_admin)):
    b = await request.json()
    code = secrets.token_urlsafe(9)
    days = float(b.get("days") or 30)
    row = store.db().one(
        "insert into invites (code, email, role, note, created_by, expires_at) "
        "values (%s,%s,%s,%s,%s, now() + (%s || ' days')::interval) returning *",
        code, (b.get("email") or "").strip().lower() or None,
        "admin" if b.get("role") == "admin" else "client",
        (b.get("note") or "").strip() or None, u["id"], str(days))
    event(u["id"], "invite", code=code, email=row["email"])
    return {"invite": jsonable(row)}


@app.get("/api/users")
def list_users(u=Depends(require_admin)):
    rows = store.db().all("select id, email, name, role, quota_bytes, disabled, created_at, last_seen "
                          "from users order by created_at")
    return {"users": [jsonable(r) for r in rows]}


# ------------------------------------------------------------------ projects
@app.get("/api/projects")
def list_projects(request: Request, u=Depends(require)):
    rows = store.db().all("select * from projects where owner = %s order by updated_at desc", u["id"])
    return {"projects": [jsonable(r) for r in rows]}


@app.post("/api/projects")
async def create_project(request: Request, u=Depends(require)):
    b = await request.json()
    name = (b.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "the project needs a name")
    clean, errors = PARAMS.validate(b.get("params") or {})
    slug = slugify(b.get("slug") or name)
    d = store.db()
    if d.one("select id from projects where owner = %s and slug = %s", u["id"], slug):
        slug = f"{slug}-{secrets.token_hex(2)}"
    p = d.one("insert into projects (owner, slug, name, company, attach_to, params, commodity) "
              "values (%s,%s,%s,%s,%s,%s,%s) returning *",
              u["id"], slug, name, (b.get("company") or "").strip() or None,
              (b.get("attach_to") or "").strip() or None, json.dumps(clean),
              (b.get("commodity") or "").strip() or None)
    event(u["id"], "project.create", project=str(p["id"]), name=name)
    return {"project": jsonable(p), "errors": errors}


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request, u=Depends(require)):
    p = project_or_404(u, pid)
    d = store.db()
    ds = d.all("select * from datasets where project_id = %s order by created_at", pid)
    jobs = d.all("select * from jobs where project_id = %s order by created_at desc limit 12", pid)
    return {"project": jsonable(p), "datasets": [jsonable(r) for r in ds],
            "jobs": [jsonable(r) for r in jobs]}


@app.patch("/api/projects/{pid}")
async def patch_project(pid: str, request: Request, u=Depends(require)):
    p = project_or_404(u, pid)
    b = await request.json()
    d = store.db()
    sets, args, errors = [], [], []
    if "name" in b and (b["name"] or "").strip():
        sets.append("name = %s"); args.append(b["name"].strip())
    for k in ("company", "attach_to", "commodity"):
        if k in b:
            sets.append(f"{k} = %s"); args.append((b[k] or "").strip() or None)
    if "params" in b:
        clean, errors = PARAMS.validate(b["params"] or {})
        sets.append("params = %s"); args.append(json.dumps(clean))
    if not sets:
        return {"project": jsonable(p), "errors": errors}
    sets.append("updated_at = now()")
    args.append(pid)
    row = d.one(f"update projects set {', '.join(sets)} where id = %s returning *", *args)
    rebuild = bool(b.get("rebuild")) and row["status"] in ("ready", "error")
    if rebuild:
        queue("build", row["owner"], row["id"])
    return {"project": jsonable(row), "errors": errors, "queued": rebuild}


@app.post("/api/projects/{pid}/build")
def build(pid: str, request: Request, u=Depends(require)):
    p = project_or_404(u, pid)
    if not p.get("attach_to") and not store.db().one(
            "select id from datasets where project_id = %s and status = 'ready'", pid):
        raise HTTPException(400, "upload and parse some data first")
    j = queue("build", p["owner"], p["id"])
    return {"job": jsonable(j)}


@app.post("/api/projects/{pid}/publish")
async def publish(pid: str, request: Request, u=Depends(require)):
    p = project_or_404(u, pid)
    b = await request.json()
    want = bool(b.get("public", True))
    j = queue("publish", p["owner"], p["id"], payload={"public": want})
    event(u["id"], "project.publish", project=pid, public=want)
    return {"job": jsonable(j)}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, request: Request, u=Depends(require)):
    p = project_or_404(u, pid)
    j = queue("delete", p["owner"], p["id"])
    store.db().run("update projects set status = 'deleting' where id = %s", pid)
    event(u["id"], "project.delete", project=pid)
    return {"job": jsonable(j)}


# ------------------------------------------------------------------- uploads
@app.post("/api/projects/{pid}/uploads")
async def upload(pid: str, request: Request, files: list[UploadFile] = File(...),
                 name: str = Form(None), epsg: str = Form(None), utm_zone: str = Form(None),
                 hemisphere: str = Form("N"), datum: str = Form("WGS84"),
                 crs_mode: str = Form("auto"), azimuth_ref: str = Form("auto"),
                 u=Depends(require)):
    p = project_or_404(u, pid)
    F = store.files()
    used = 0
    try:
        used = F.usage(f"{u['id']}/")
    except Exception:
        pass
    saved, total = [], 0
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD_MB:.0f} MB")
        total += len(data)
        if used + total > (u["quota_bytes"] or 0):
            raise HTTPException(413, "this account's storage quota is full")
        key = f"{u['id']}/{pid}/uploads/{uuid.uuid4().hex[:8]}-{os.path.basename(f.filename)}"
        F.put(key, data, f.content_type or "application/octet-stream")
        saved.append({"filename": os.path.basename(f.filename), "key": key, "bytes": len(data)})
    if not saved:
        raise HTTPException(400, "no files were uploaded")
    opts = {"epsg": int(epsg) if (epsg or "").strip().isdigit() else None,
            "utm_zone": int(utm_zone) if (utm_zone or "").strip().isdigit() else None,
            "hemisphere": (hemisphere or "N")[:1].upper(), "datum": datum or "WGS84",
            "crs_mode": (crs_mode or "auto").lower()
            if (crs_mode or "auto").lower() in ("auto", "utm", "epsg", "latlon", "local") else "auto",
            "azimuth_ref": (azimuth_ref or "auto").lower()
            if (azimuth_ref or "auto").lower() in ("auto", "magnetic", "true") else "auto"}
    d = store.db()
    ds = d.one("insert into datasets (project_id, owner, name, files, options, bytes) "
               "values (%s,%s,%s,%s,%s,%s) returning *",
               pid, u["id"], (name or "").strip() or ", ".join(s["filename"] for s in saved)[:120],
               json.dumps(saved), json.dumps(opts), total)
    d.run("update projects set status = 'parsing', error = null, updated_at = now() where id = %s", pid)
    j = queue("import", u["id"], pid, ds["id"])
    event(u["id"], "dataset.upload", project=pid, files=len(saved), bytes=total)
    return {"dataset": jsonable(ds), "job": jsonable(j)}


@app.delete("/api/datasets/{did}")
def delete_dataset(did: str, request: Request, u=Depends(require)):
    d = store.db()
    ds = d.one("select * from datasets where id = %s", did)
    if not ds or (str(ds["owner"]) != str(u["id"]) and u["role"] != "admin"):
        raise HTTPException(404, "no such dataset")
    F = store.files()
    try:
        F.remove([f["key"] for f in ds["files"] or [] if f.get("key")] +
                 ([ds["data_key"]] if ds.get("data_key") else []))
    except Exception:
        pass
    d.run("delete from datasets where id = %s", did)
    return {"ok": True}


# -------------------------------------------------------------------- models
def _stream(data, media="application/json", filename=None):
    headers = {"Cache-Control": "private, max-age=60"}
    if filename:
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return StreamingResponse(iter([data]), media_type=media, headers=headers)


@app.get("/api/projects/{pid}/model.json")
def project_model(pid: str, request: Request, u=Depends(require)):
    p = project_or_404(u, pid)
    if not p.get("model_key"):
        raise HTTPException(404, "this project has no model yet")
    try:
        return _stream(store.files().get(p["model_key"]))
    except Exception:
        raise HTTPException(404, "the model file is missing — rebuild the project")


@app.post("/api/adopt")
async def adopt(request: Request, u=Depends(require)):
    """Open one of MMP's own public models in Studio, so its parameters can be
    changed and the model rebuilt — the public pages themselves are static."""
    b = await request.json()
    slug = re.sub(r"[^a-z0-9-]", "", (b.get("slug") or "").lower())[:64]
    if not slug:
        raise HTTPException(400, "which model?")
    name = (b.get("name") or slug.replace("-", " ").title()).strip()[:120]
    d = store.db()
    mine = d.one("select * from projects where owner = %s and attach_to = %s", u["id"], slug)
    if mine:
        return {"project": jsonable(mine), "existing": True}
    base = slug if not d.one("select id from projects where owner = %s and slug = %s", u["id"], slug) else slug
    p = d.one("insert into projects (owner, slug, name, company, attach_to, params, commodity) "
              "values (%s,%s,%s,%s,%s,%s,%s) returning *",
              u["id"], f"{slug}-mine", name, (b.get("company") or "").strip() or None, base,
              json.dumps(PARAMS.validate(b.get("params") or {})[0]), (b.get("commodity") or "").strip() or None)
    queue("build", u["id"], p["id"])
    event(u["id"], "project.adopt", project=str(p["id"]), slug=slug)
    return {"project": jsonable(p), "existing": False}


@app.get("/api/public/projects")
def public_projects():
    rows = store.db().all("select id, slug, name, company, region, commodity, stats, public_key, "
                          "updated_at from projects where is_public and public_key is not null "
                          "order by updated_at desc limit 500")
    return {"projects": [jsonable(r) for r in rows]}


@app.get("/api/public/model/{key}")
def public_model(key: str):
    if not re.match(r"^[A-Za-z0-9._-]{4,120}$", key):
        raise HTTPException(400, "bad key")
    p = store.db().one("select public_key from projects where is_public and public_key = %s", key)
    if not p:
        raise HTTPException(404, "not a shared model")
    try:
        return _stream(store.files().get("public/" + key))
    except Exception:
        raise HTTPException(404, "that model is no longer available")


# ----------------------------------------------------------------- defaults
@app.get("/api/defaults")
def defaults(region: str = None, commodity: str = None, attach_to: str = None):
    """Where a new project's parameters start: the most recent technical report for
    the public project it is built on, else what is typical for the region and
    commodity. The project page shows these as the starting values."""
    out, src = {}, {}
    if attach_to:
        try:
            from minemodelingpro import projects as PROJ
            from minemodelingpro import model3d as M3
            for d in PROJ.assemble():
                if d["slug"] == attach_to:
                    out, src = M3.auto_params(d)
                    region = region or d.get("region")
                    break
        except Exception:
            pass
    if not out:
        tp, label = REG.typical(region, commodity)
        out = tp
        src = {k: label for k in tp}
    return {"params": out, "sources": src,
            "fields": sorted(PARAMS.ALL - {"name", "company", "note"})}


@app.get("/api/stats")
def stats():
    """Aggregate counts for the landing page. Numbers only — no project names — so
    the gallery itself can stay behind the sign-in."""
    out = {"models": 0, "holes": 0, "reports": 0, "generated": None, "public_gallery": PUBLIC_GALLERY}
    try:
        idx = json.load(open(os.path.join(LIVE["dir"], "models_index.json")))
        ms = idx.get("models") or []
        out.update({"models": len(ms),
                    "holes": sum(int(m.get("holes") or 0) for m in ms),
                    "reports": sum(1 for m in ms if "report" in str(m.get("source") or "")),
                    "generated": (idx.get("generated") or "")[:10] or None})
    except Exception:
        pass
    try:
        out["shared"] = store.db().one(
            "select count(*) as n from projects where is_public")["n"]
    except Exception:
        pass
    return out


@app.get("/api/regional")
def regional_table():
    return REG.table()


# --------------------------------------------------------------- access control
# What anyone may fetch without signing in: the landing page and what it needs, the
# sign-in page itself, and the auth endpoints. Everything else — the gallery, the
# model viewers, the model JSON — needs a session, unless PUBLIC_GALLERY is on.
_OPEN_EXACT = {"/", "/index.html", "/favicon.ico", "/robots.txt",
               # the landing page shows one real model, built from public filings
               "/hero.json", "/hero.js", "/models/three.min.js",
               "/app/", "/app/index.html", "/app/app.js", "/app/app.css", "/app/config.js"}
_OPEN_PREFIX = ("/api/auth/", "/api/health", "/assets/", "/static/", "/.well-known/")
_GALLERY_PREFIX = ("/models", "/models/")


def _is_open(path):
    if path in _OPEN_EXACT or path.startswith(_OPEN_PREFIX):
        return True
    if path == "/models_index.json":
        return PUBLIC_GALLERY
    if path.startswith("/models"):
        return PUBLIC_GALLERY
    return False


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if _is_open(path) or path.startswith("/api/"):
        return await call_next(request)          # /api/* enforces its own auth
    if me(request):
        return await call_next(request)
    if path.endswith((".json", ".js", ".css", ".map")):
        return JSONResponse({"detail": "sign in first"}, status_code=401)
    nxt = path + (("?" + request.url.query) if request.url.query else "")
    return RedirectResponse("/app/index.html?next=" + urllib.parse.quote(nxt, safe=""), status_code=302)


# ------------------------------------------------------------------ the site
class SiteFiles(StaticFiles):
    """The static gallery, with index.html for directories and no caching of HTML."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        # the renderer and the gallery data change with every build and are small —
        # caching them is how a fix ends up invisible in someone's browser
        if path.endswith((".html", "/", ".js", ".css", ".json")) or path in ("", "."):
            r.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.endswith((".svg", ".png", ".jpg", ".webp")):
            r.headers["Cache-Control"] = "public, max-age=3600"
        return r


LIVE = {"dir": SITE_DIR, "commit": None}


class LiveSite(SiteFiles):
    """Serve the mirrored site from object storage when it is present, else the
    copy baked into the image. Swapping the directory needs no deployment."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

    async def get_response(self, path, scope):
        d = LIVE["dir"]
        if d != self.directory:
            self.directory = d
        return await super().get_response(path, scope)


if os.path.isdir(SITE_DIR):
    app.mount("/", LiveSite(directory=SITE_DIR, html=True), name="site")
else:                                            # a bare API deployment
    @app.get("/")
    def root():
        return {"ok": True, "note": "site/ is not present in this deployment"}
