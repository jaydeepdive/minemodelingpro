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
import shutil
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
    """Is the app up — and is the worker that does the modelling still breathing?

    The worker line is here because an app whose background worker has died looks
    perfectly healthy from the outside, and that is exactly the failure that leaves
    an upload saying "parsing" for three hours."""
    d = store.db()
    try:
        d.one("select 1")
        ok = True
    except Exception:
        ok = False
    out = {"ok": ok, "version": VERSION}
    if ok:
        age, note = d.worker_seen("modeler")
        out["worker_seen_s"] = round(age) if age is not None else None
        out["worker_note"] = note or None
        try:
            q = d.one("select count(*) filter (where status = 'queued') as queued, "
                      "count(*) filter (where status = 'running') as running from jobs")
            out["queued"], out["running"] = int(q["queued"]), int(q["running"])
        except Exception:
            pass
    return out


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
    age, _note = d.worker_seen("modeler")
    return {"project": jsonable(p), "datasets": [jsonable(r) for r in ds],
            "jobs": [jsonable(r) for r in jobs],
            # so a page waiting on a job can say whether anything is listening
            "worker": {"seen_s": round(age) if age is not None else None,
                       "timeout_min": float(os.environ.get("MMP_JOB_TIMEOUT_MIN", "20"))}}


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


# ------------------------------------------------- a source pasted, not uploaded
# A drill database is not the only way drilling arrives. A news release states
# its results in a table nobody exports as a file, and a property is better
# modelled WITH those holes than without them. Pasted text becomes an ordinary
# dataset, so it merges by normalised hole id exactly like an upload and shows in
# the import log and the hole list — there is no second kind of source in the
# model, only a second way in.
MAX_PASTE_BYTES = 4_000_000


def _clean_url(u):
    """A source link we are willing to store and show as a link."""
    u = (u or "").strip()[:600]
    if not u:
        return None
    if not re.match(r"^https?://[^\s<>\"']+$", u, re.I):
        raise HTTPException(400, "that does not look like a link — it should start http:// or https://")
    return u


def _parse_pasted(text, project_name=None, company=None, opts=None, pid=None):
    """What a paste holds, without storing anything."""
    import tempfile
    from minemodelingpro import imports as IMP
    o = opts or {}
    tmp = tempfile.mkdtemp(prefix="mmp-paste-")
    path = os.path.join(tmp, "pasted-source.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        data = IMP.build_dataset(
            [path], project=project_name, company=company,
            epsg=o.get("epsg"), utm_zone=o.get("utm_zone"),
            hemisphere=o.get("hemisphere", "N"), datum=o.get("datum", "WGS84"),
            crs_mode=o.get("crs_mode", "auto"), source_url=o.get("source_url"))
        # a paste is an addition when the project already has drilling
        has = store.db().all(
            "select id from datasets where project_id = %s and status = 'ready'", pid) \
            if pid else []
        return data, IMP.validate(data, standalone=not has)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _known_holes(pid):
    """Normalised hole ids this project already has, so a paste can say whether
    its holes JOIN. A release naming LM-26-101 against a collar table that says
    LM26101 lands nowhere, and silently."""
    from minemodelingpro import holeid
    d = store.db()
    out = set()
    for r in d.all("select data_key from datasets where project_id = %s and status = 'ready'", pid):
        if not r.get("data_key"):
            continue
        try:
            t, collars, _s = _dataset(r["data_key"])
        except Exception:
            continue
        for c in collars or []:
            h = c.get("hole") or c.get("id")
            if h:
                out.add(holeid.key(str(h)))
    return out


async def _body(request):
    """The JSON object a POST should carry. A client that stringifies its body
    twice sends a JSON *string*, and reading .get on it is a bare 500 that says
    nothing — this says what arrived instead."""
    try:
        b = await request.json()
    except Exception:
        raise HTTPException(400, "this endpoint takes a JSON object")
    if not isinstance(b, dict):
        raise HTTPException(400, "expected a JSON object, got %s — the body looks "
                                 "double-encoded" % type(b).__name__)
    return b


@app.post("/api/projects/{pid}/sources/preview")
async def preview_source(pid: str, request: Request, u=Depends(require)):
    """Read a paste and say what is in it — before anything is stored.

    The counts that matter are not the row counts: they are how many of this
    source's holes JOIN the ones already here, and how many intervals arrive on
    holes with no collar, because those cannot be placed in 3D at all."""
    from minemodelingpro import holeid
    p = project_or_404(u, pid)
    b = await _body(request)
    text = (b.get("text") or "")
    if len(text.encode("utf-8")) > MAX_PASTE_BYTES:
        raise HTTPException(413, "that is more text than a paste should carry — upload it as a file")
    if not text.strip():
        raise HTTPException(400, "nothing pasted")
    try:
        opts = dict(b.get("options") or {})
        opts["source_url"] = _clean_url(b.get("url"))
        data, errors = _parse_pasted(text, p["name"], p.get("company"), opts, pid=pid)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(400, "could not read that: %s: %s" % (type(e).__name__, str(e)[:200]))
    known = _known_holes(pid)
    new_c = {holeid.key(str(c.get("hole") or "")) for c in (data.get("collars") or [])}
    # keep the name the release wrote beside the normalised key: a person
    # cannot recognise "ZZ-99-1" as their own hole ZZ-99-001
    names = {}
    for r in (data.get("intervals") or []):
        nm = str(r.get("hole") or "")
        if nm:
            names.setdefault(holeid.key(nm), nm)
    iv_h = set(names)
    placed = new_c | known
    return {
        "counts": data.get("counts"),
        "source_url": data.get("source_url"),
        "source_kind": data.get("source_kind"),
        "crs": {k: v for k, v in (data.get("crs") or {}).items()
                if k in ("kind", "epsg", "located", "zone")},
        "center": data.get("center"),
        "notes": data.get("notes"),
        "tables": data.get("tables"),
        "errors": errors,
        "holes": {
            "collars": len(new_c),
            "with_intervals": len(iv_h),
            "already_here": len(iv_h & known),
            "new_to_this_project": len(iv_h - known),
            # the one that decides whether this source reaches the model
            "intervals_with_no_collar": len(iv_h - placed),
            "unplaceable": sorted(names[k] for k in (iv_h - placed))[:12],
            "project_holes": len(known),
        },
    }


@app.post("/api/projects/{pid}/sources")
async def add_source(pid: str, request: Request, u=Depends(require)):
    """Keep a pasted source and model with it. It is stored as the text file it
    is, so "Read the files again" re-reads it like any other upload."""
    p = project_or_404(u, pid)
    b = await _body(request)
    text = (b.get("text") or "")
    if len(text.encode("utf-8")) > MAX_PASTE_BYTES:
        raise HTTPException(413, "that is more text than a paste should carry — upload it as a file")
    if not text.strip():
        raise HTTPException(400, "nothing pasted")
    name = (b.get("name") or "").strip()[:120] or "pasted source"
    o = b.get("options") or {}
    opts = {"epsg": o.get("epsg"), "utm_zone": o.get("utm_zone"),
            "hemisphere": (o.get("hemisphere") or "N")[:1].upper(),
            "datum": o.get("datum") or "WGS84",
            "crs_mode": (o.get("crs_mode") or "auto").lower(),
            "azimuth_ref": "auto", "pasted": True,
            "source_url": _clean_url(b.get("url"))}
    F = store.files()
    raw = text.encode("utf-8")
    key = f"{u['id']}/{pid}/uploads/{uuid.uuid4().hex[:8]}-{slugify(name, 'source')}.txt"
    F.put(key, raw, "text/plain")
    saved = [{"filename": slugify(name, "source") + ".txt", "key": key, "bytes": len(raw)}]
    d = store.db()
    ds = d.one("insert into datasets (project_id, owner, name, files, options, bytes) "
               "values (%s,%s,%s,%s,%s,%s) returning *",
               pid, u["id"], name, json.dumps(saved), json.dumps(opts), len(raw))
    d.run("update projects set status = 'parsing', error = null, updated_at = now() where id = %s", pid)
    j = queue("import", u["id"], pid, ds["id"])
    event(u["id"], "dataset.paste", project=pid, bytes=len(raw))
    return {"dataset": jsonable(ds), "job": jsonable(j)}


@app.post("/api/datasets/{did}/reimport")
def reimport_dataset(did: str, request: Request, u=Depends(require)):
    """Read the same uploaded files again.

    The files are kept, so when the importer learns something — a column it was
    reading wrongly, a coordinate reference it can now work out — the fix should
    not cost the client another upload."""
    d = store.db()
    ds = d.one("select * from datasets where id = %s", did)
    if not ds or (str(ds["owner"]) != str(u["id"]) and u["role"] != "admin"):
        raise HTTPException(404, "no such dataset")
    if not (ds["files"] or []):
        raise HTTPException(400, "this dataset has no stored files to read again")
    d.run("update datasets set status = 'queued', error = null where id = %s", did)
    d.run("update projects set status = 'parsing', error = null, updated_at = now() where id = %s",
          ds["project_id"])
    j = queue("import", ds["owner"], str(ds["project_id"]), did)
    event(u["id"], "dataset.reimport", project=str(ds["project_id"]), dataset=did)
    return {"job": jsonable(j)}


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


@app.get("/api/projects/{pid}/data")
def project_data(pid: str, request: Request, u=Depends(require),
                 table: str = "assays", q: str = "", el: str = "",
                 limit: int = 200, offset: int = 0,
                 group: str = "", hole: str = "", wide: int = 0):
    """The client's own drill data, as rows, from the project page.

    **Never materialises more than the page asked for.** The first version built
    a Row object per interval to count and slice them, which on a 919,152-row
    assay file is a million objects in a web process and a 500. The columns are
    numpy; selecting, filtering and counting all happen there, and only the rows
    on screen are ever turned into values."""
    project_or_404(u, pid)
    d, F = store.db(), store.files()
    keys = [r["data_key"] for r in
            d.all("select * from datasets where project_id = %s and status = 'ready' "
                  "order by created_at", pid) if r.get("data_key")]
    if not keys:
        raise HTTPException(404, "this project has no parsed data yet")
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    ql = (q or "").strip().lower()
    problems = []
    try:
        if group == "hole":
            return _read_holes(keys, table, ql, el, limit, offset, problems)
        if hole:
            return _read_one_hole(keys, table, hole, el, bool(wide), problems)
        return _read_project_data(keys, table, ql, el, limit, offset, problems)
    except HTTPException:
        raise
    except Exception as e:
        # An opaque 500 costs a round trip every time it happens. This is the
        # person's own project and their own file: say what went wrong.
        import traceback
        traceback.print_exc()
        raise HTTPException(500, "could not read this project's data: %s: %s"
                                 % (type(e).__name__, str(e)[:200]))


# A dataset is read once and kept decoded. Without this every page click
# re-downloaded the stored file and rebuilt 919,152 intervals' columns to show
# 200 rows — which is why the table sat on "Loading…". One project's worth is
# tens of megabytes; two is a cache, a dozen is the memory bug this modeller
# spent a week escaping.
_DATA_CACHE = {}
_DATA_CACHE_CAP = int(os.environ.get("MMP_DATA_CACHE", "2"))


def _dataset(key):
    """The parsed dataset for a stored key: intervals as columns, collars and
    surveys as they were written. Cached by key; the file is immutable once the
    parse has written it, so a stale read is not possible — a re-import writes a
    new key."""
    hit = _DATA_CACHE.get(key)
    if hit is not None:
        return hit
    from minemodelingpro import ivtable as IVT
    raw = store.files().get_json(key)
    iv = raw.get("intervals")
    t = (IVT.Intervals.from_json(iv) if isinstance(iv, dict)
         else IVT.Intervals.coerce(iv or []))
    t.index()                                    # build the per-element index once
    val = (t, raw.get("collars") or raw.get("holes") or [], raw.get("surveys") or [])
    _DATA_CACHE[key] = val
    while len(_DATA_CACHE) > max(1, _DATA_CACHE_CAP):
        _DATA_CACHE.pop(next(iter(_DATA_CACHE)))
    return val


# One row per hole, so a 144,774-row assay table opens as the 600 holes it
# actually is. Cached per (stored file, element) because it is a few passes over
# every row and the answer cannot change — the parse writes a new key.
_HOLE_CACHE = {}


def _hole_summary(key, el):
    """Per-hole aggregates over the interval columns, vectorised.

    Never builds a row: the lesson from the first version of this endpoint, which
    made a Row per interval to count them and died on a real file."""
    import numpy as np
    ck = (key, el or "")
    hit = _HOLE_CACHE.get(ck)
    if hit is not None:
        return hit
    t, _c, _s = _dataset(key)
    idx = t.index()
    if el:
        sel = idx.get(el)
        sel = sel if sel is not None else np.zeros(0, np.int64)
    else:
        sel = np.arange(len(t.hole_i), dtype=np.int64)
    nH = len(t.holes)
    hi = t.hole_i[sel]
    frm, to, gr, eli = t.frm[sel], t.to[sel], t.grade[sel], t.el_i[sel]
    samples = np.bincount(hi, minlength=nH)
    f_lo = np.full(nH, np.inf); np.minimum.at(f_lo, hi, frm)
    t_hi = np.full(nH, -np.inf); np.maximum.at(t_hi, hi, to)
    # assayed metres: distinct intervals only, or a 19-element package counts
    # every metre of core nineteen times
    ok = np.isfinite(frm) & np.isfinite(to) & (to > frm)
    if ok.any():
        trip = np.unique(np.c_[hi[ok], frm[ok], to[ok]], axis=0)
        length = np.bincount(trip[:, 0].astype(np.int64),
                             weights=trip[:, 2] - trip[:, 1], minlength=nH)
    else:
        length = np.zeros(nH)
    pair = np.unique(np.c_[hi, eli], axis=0) if len(hi) else np.zeros((0, 2), np.int64)
    nels = np.bincount(pair[:, 0].astype(np.int64), minlength=nH)
    gmax = gmean = None
    if el and len(sel):
        g_ok = np.isfinite(gr)
        gmax = np.full(nH, -np.inf); np.maximum.at(gmax, hi[g_ok], gr[g_ok])
        gsum = np.bincount(hi[g_ok], weights=gr[g_ok], minlength=nH)
        gcnt = np.bincount(hi[g_ok], minlength=nH)
        gmean = np.divide(gsum, np.maximum(gcnt, 1))
    val = {"samples": samples, "from": f_lo, "to": t_hi, "length": length,
           "nels": nels, "max": gmax, "mean": gmean, "holes": list(t.holes),
           "unit": (t.units[t.unit_i[sel[0]]] if el and len(sel) else "")}
    _HOLE_CACHE[ck] = val
    while len(_HOLE_CACHE) > 8:
        _HOLE_CACHE.pop(next(iter(_HOLE_CACHE)))
    return val


def _f(v):
    import numpy as np
    return None if v is None or not np.isfinite(v) else round(float(v), 4)


def _read_holes(keys, table, ql, el, limit, offset, problems):
    """The hole list: one line per drill hole, which is how a person reads a
    drill database. The intervals come when a hole is opened."""
    import numpy as np
    rows, total, elements = [], 0, set()
    if el:
        cols = ["hole", "samples", "from (m)", "to (m)", "assayed (m)",
                "max " + el, "mean " + el]
    else:
        cols = ["hole", "samples", "elements", "from (m)", "to (m)", "assayed (m)"]
    if table == "surveys":
        cols = ["hole", "stations", "from (m)", "to (m)"]
    for k in keys:
        try:
            t, collars, surveys = _dataset(k)
        except Exception as e:
            problems.append("could not read a stored file: %s" % str(e)[:120])
            continue
        if table == "surveys":
            byh = {}
            for v in surveys:
                h = str(v.get("hole") or v.get("id") or "")
                md = v.get("md", v.get("depth"))
                a = byh.setdefault(h, [0, None, None])
                a[0] += 1
                if isinstance(md, (int, float)):
                    a[1] = md if a[1] is None else min(a[1], md)
                    a[2] = md if a[2] is None else max(a[2], md)
            names = sorted(h for h in byh if not ql or ql in h.lower())
            total += len(names)
            for h in names[offset:offset + limit]:
                a = byh[h]
                rows.append([h, a[0], a[1], a[2]])
            continue
        S = _hole_summary(k, el)
        elements |= set(t.index())
        order = [i for i, h in enumerate(S["holes"])
                 if S["samples"][i] > 0 and (not ql or ql in str(h or "").lower())]
        order.sort(key=lambda i: str(S["holes"][i] or ""))
        total += len(order)
        for i in order[offset:offset + limit]:
            base = [S["holes"][i] or "", int(S["samples"][i])]
            if el:
                base += [_f(S["from"][i]), _f(S["to"][i]), _f(S["length"][i]),
                         _f(S["max"][i] if S["max"] is not None else None),
                         _f(S["mean"][i] if S["mean"] is not None else None)]
            else:
                base += [int(S["nels"][i]), _f(S["from"][i]), _f(S["to"][i]),
                         _f(S["length"][i])]
            rows.append(base)
    if problems and not rows and not total:
        raise HTTPException(500, "; ".join(problems[:3]))
    return {"table": table, "group": "hole", "columns": cols, "rows": rows,
            "total": total, "offset": offset, "limit": limit,
            "elements": sorted(e for e in elements if e), "problems": problems or None}


def _read_one_hole(keys, table, hole, el, wide, problems):
    """One hole's intervals. Wide by default: a metre of core assayed for
    nineteen elements is ONE interval with nineteen numbers on it, not nineteen
    rows — which is what made the flat table unreadable."""
    import numpy as np
    hl = str(hole).strip().lower()
    for k in keys:
        try:
            t, collars, surveys = _dataset(k)
        except Exception as e:
            problems.append("could not read a stored file: %s" % str(e)[:120])
            continue
        if table == "surveys":
            src = [v for v in surveys
                   if str(v.get("hole") or v.get("id") or "").lower() == hl]
            if not src:
                continue
            return {"table": table, "hole": hole,
                    "columns": ["depth (m)", "azimuth", "dip"],
                    "rows": [[v.get("md", v.get("depth")), v.get("az"), v.get("dip")]
                             for v in src],
                    "total": len(src), "problems": problems or None}
        hits = [i for i, h in enumerate(t.holes) if str(h or "").lower() == hl]
        if not hits:
            continue
        sel = np.flatnonzero(np.isin(t.hole_i, np.array(hits, np.int64)))
        if el:
            idx = t.index().get(el)
            if idx is not None:
                sel = sel[np.isin(sel, idx)]
        if not len(sel):
            continue
        frm, to = t.frm[sel], t.to[sel]
        if not wide:
            out = []
            for j in sel[np.lexsort((t.el_i[sel], t.frm[sel]))]:
                j = int(j)
                a, b = _f(t.frm[j]), _f(t.to[j])
                out.append([a, b, (round(b - a, 2) if a is not None and b is not None else None),
                            t.els[t.el_i[j]] or "", _f(t.grade[j]),
                            t.units[t.unit_i[j]] or ""])
            return {"table": table, "hole": hole,
                    "columns": ["from (m)", "to (m)", "length (m)", "element", "grade", "unit"],
                    "rows": out, "total": len(out), "problems": problems or None}
        # one row per interval, one column per element
        iv, inv = np.unique(np.c_[frm, to], axis=0, return_inverse=True)
        els_here = sorted({t.els[int(e)] or "" for e in np.unique(t.el_i[sel])})
        unit = {e: "" for e in els_here}
        pos = {e: n for n, e in enumerate(els_here)}
        grid = [[None] * len(els_here) for _ in range(len(iv))]
        for n, j in enumerate(sel):
            e = t.els[t.el_i[int(j)]] or ""
            grid[int(inv[n])][pos[e]] = _f(t.grade[int(j)])
            if not unit[e]:
                unit[e] = t.units[t.unit_i[int(j)]] or ""
        cols = ["from (m)", "to (m)", "length (m)"] + [
            (e + (" (" + unit[e] + ")" if unit[e] else "")) for e in els_here]
        out = []
        for n in range(len(iv)):
            a, b = _f(iv[n][0]), _f(iv[n][1])
            out.append([a, b, (round(b - a, 2) if a is not None and b is not None else None)]
                       + grid[n])
        out.sort(key=lambda r: (r[0] is None, r[0] or 0))
        return {"table": table, "hole": hole, "wide": True, "columns": cols,
                "rows": out, "total": len(out), "elements": els_here,
                "problems": problems or None}
    raise HTTPException(404, "no rows for hole %s" % hole)


def _read_project_data(keys, table, ql, el, limit, offset, problems):
    out, total, cols, elements = [], 0, [], set()

    def _page(seq, make):
        nonlocal total
        for item in seq:
            total += 1
            if offset < total <= offset + limit:
                out.append(make(item))

    for k in keys:
        try:
            t, collars, surveys = _dataset(k)
        except Exception as e:
            problems.append("could not read a stored file: %s" % str(e)[:120])
            continue
        if table == "collars":
            cols = ["hole", "easting", "northing", "elevation", "azimuth", "dip", "depth_m"]
            src = [h for h in collars
                   if not ql or ql in str(h.get("hole") or h.get("id") or "").lower()]
            _page(src, lambda h: [str(h.get("hole") or h.get("id") or ""),
                                  h.get("e", h.get("x")), h.get("n", h.get("y")),
                                  h.get("z"), h.get("az"), h.get("dip"), h.get("depth")])
        elif table == "surveys":
            cols = ["hole", "depth_m", "azimuth", "dip"]
            src = [v for v in surveys
                   if not ql or ql in str(v.get("hole") or v.get("id") or "").lower()]
            _page(src, lambda v: [str(v.get("hole") or v.get("id") or ""),
                                  v.get("md", v.get("depth")), v.get("az"), v.get("dip")])
        else:
            import numpy as np
            cols = ["hole", "from_m", "to_m", "length_m", "element", "grade", "unit"]
            idx = t.index()
            elements |= set(idx)
            want = [el] if el else sorted(idx)
            sel = [idx[e] for e in want if e in idx]
            sel = np.concatenate(sel) if sel else np.zeros(0, np.int64)
            if ql and len(sel):
                # match on the hole NAME TABLE, not per row: a few hundred strings
                hits = np.array([i for i, h in enumerate(t.holes)
                                 if ql in str(h or "").lower()], dtype=np.int64)
                sel = sel[np.isin(t.hole_i[sel], hits)] if len(hits) else sel[:0]
            n = int(len(sel))
            lo, hi = min(offset, n), min(offset + limit, n)
            total += n
            for j in sel[lo:hi]:
                j = int(j)
                fr = float(t.frm[j]) if np.isfinite(t.frm[j]) else None
                to = float(t.to[j]) if np.isfinite(t.to[j]) else None
                gr = float(t.grade[j]) if np.isfinite(t.grade[j]) else None
                out.append([t.holes[t.hole_i[j]] or "", fr, to,
                            (round(to - fr, 2) if fr is not None and to is not None else None),
                            t.els[t.el_i[j]] or "", gr, t.units[t.unit_i[j]] or ""])
    if problems and not out and not total:
        raise HTTPException(500, "; ".join(problems[:3]))
    return {"table": table, "columns": cols, "rows": out, "total": total,
            "offset": offset, "limit": limit, "elements": sorted(e for e in elements if e),
            "problems": problems or None}


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
# Every asset the landing page loads has to be here, or the page renders without
# it and says nothing: nav.js was added when the menu bar was unified and was not,
# so minemodelingpro.com served a signed-out visitor a page with no way to sign in.
# scripts/test_public_assets.py walks the public pages and fails if one is missing.
_OPEN_EXACT = {"/", "/index.html", "/favicon.ico", "/robots.txt",
               "/nav.js",                        # the menu bar, on every page
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


# --------------------------------------------------- the renderer, never stale
# Studio's private viewer fetches ../models/viewer.js and ../models/_shell.html.
# Those are build artifacts committed into site/, refreshed only by the nightly
# gallery build — so a renderer improvement reached a client's private model a
# day late, or not at all if the build failed. The same staleness that made
# models need a button press, one layer down. Served from the package instead,
# which is this deployment's own code.
def _pkg_file(name):
    from minemodelingpro import model3d as _m3
    return os.path.join(os.path.dirname(os.path.abspath(_m3.__file__)), name)


@app.get("/models/viewer.js")
def viewer_js():
    p = _pkg_file("viewer.js")
    if not os.path.exists(p):
        raise HTTPException(404, "viewer.js is not in this deployment")
    return FileResponse(p, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/models/_shell.html")
def viewer_shell():
    """The viewer's markup, placeholders and all — which is exactly what
    write_viewer copies to site/models/_shell.html, and what model.html fills in."""
    p = _pkg_file("model3d_viewer.html")
    if not os.path.exists(p):
        raise HTTPException(404, "the viewer shell is not in this deployment")
    return FileResponse(p, media_type="text/html",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


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
