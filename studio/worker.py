"""MMP Studio worker — turns uploaded drill data into models.

Supabase holds the accounts, the uploaded files and the job queue; this process
does the work that needs Python (parsing spreadsheets/Access databases, the
desurvey, the block model, the terrain) and writes the finished model back.

    jobs.kind = "import"   parse a dataset's files  -> dataset JSON + summary, then build
                "build"    (re)build the project's model with its current parameters
                "publish"  copy the model into the public bucket (or unpublish)
                "delete"   remove a project's stored files

Run it anywhere that can reach Supabase and has this repo installed:

    pip install -r requirements.txt supabase
    export SUPABASE_URL=https://<ref>.supabase.co
    export SUPABASE_SERVICE_KEY=<service-role key>     # server-side only, never in the browser
    PYTHONPATH=src python studio/worker.py             # add --once for a single pass

On the Mac it belongs in a launchd job beside the SEDAR collector; in CI or on a
small VM it can simply stay running.
"""
import os
import re
import io
import sys
import json
import time
import shutil
import tempfile
import datetime
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from minemodelingpro import imports as IMP            # noqa: E402
from minemodelingpro import projects as PROJ          # noqa: E402
from minemodelingpro import model3d as M3             # noqa: E402
from minemodelingpro import params as PARAMS          # noqa: E402

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
POLL = float(os.environ.get("MMP_POLL_SECONDS", "5"))
MAX_FILE_MB = float(os.environ.get("MMP_MAX_FILE_MB", "200"))
WORKDIR = os.environ.get("MMP_WORKDIR", os.path.join(tempfile.gettempdir(), "mmp-studio"))


def client():
    if not (URL and KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (service-role key) first.")
    from supabase import create_client
    return create_client(URL, KEY)


# --------------------------------------------------------------------- helpers
def log(job_id, sb, line, status=None):
    print(f"[worker] {line}", flush=True)
    patch = {"log": (line or "")[:4000]}
    if status:
        patch["status"] = status
        if status in ("done", "error"):
            patch["finished_at"] = datetime.datetime.utcnow().isoformat()
    try:
        sb.table("jobs").update(patch).eq("id", job_id).execute()
    except Exception:
        pass


def _download(sb, bucket, path, dest):
    data = sb.storage.from_(bucket).download(path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _upload_json(sb, bucket, path, obj):
    body = json.dumps(obj, separators=(",", ":")).encode()
    sb.storage.from_(bucket).upload(path, body,
                                    {"content-type": "application/json", "upsert": "true"})
    return path


def _slug(s, fallback="project"):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48] or fallback


# ------------------------------------------------------------------ job: import
def do_import(sb, job):
    ds_row = sb.table("datasets").select("*").eq("id", job["dataset_id"]).single().execute().data
    if not ds_row:
        raise RuntimeError("dataset not found")
    sb.table("datasets").update({"status": "parsing", "error": None}).eq("id", ds_row["id"]).execute()
    tmp = os.path.join(WORKDIR, "in", ds_row["id"])
    shutil.rmtree(tmp, ignore_errors=True)
    paths = []
    for f in ds_row.get("files") or []:
        if (f.get("bytes") or 0) > MAX_FILE_MB * 1024 * 1024:
            raise RuntimeError(f"{f.get('filename')} is larger than {MAX_FILE_MB:.0f} MB")
        paths.append(_download(sb, "uploads", f["path"], os.path.join(tmp, f["filename"])))
    if not paths:
        raise RuntimeError("no files on this dataset")
    proj = sb.table("projects").select("*").eq("id", ds_row["project_id"]).single().execute().data
    try:                                    # per-account storage quota
        prof = sb.table("profiles").select("quota_bytes").eq("id", ds_row["owner"]).single().execute().data
        used = sb.rpc("account_usage", {"uid": ds_row["owner"]}).execute().data or 0
        if prof and used and used > (prof.get("quota_bytes") or 0):
            raise RuntimeError(f"account storage quota reached ({used/1e6:.0f} MB used) — "
                               f"delete a project or ask for more space")
    except RuntimeError:
        raise
    except Exception:
        pass
    o = ds_row.get("options") or {}
    log(job["id"], sb, f"parsing {len(paths)} file(s)…")
    data = IMP.build_dataset(
        paths, project=proj["name"], company=proj.get("company"),
        epsg=o.get("epsg"), utm_zone=o.get("utm_zone"), hemisphere=o.get("hemisphere", "N"),
        datum=o.get("datum", "WGS84"), attach_to=proj.get("attach_to"),
        settings=proj.get("params") or {}, dataset_id="import:" + ds_row["id"],
        owner=ds_row["owner"], private=not proj.get("is_public"),
        source_name=ds_row.get("name") or ", ".join(os.path.basename(p) for p in paths))
    errors = IMP.validate(data)
    summary = {"counts": data["counts"], "tables": data["tables"], "notes": data["notes"],
               "crs": data["crs"], "center": data["center"], "errors": errors}
    path = f"{ds_row['owner']}/{ds_row['project_id']}/datasets/{ds_row['id']}.json"
    _upload_json(sb, "models", path, data)
    sb.table("datasets").update({"status": "error" if errors else "ready", "summary": summary,
                                 "data_path": path, "error": "; ".join(errors) or None}
                                ).eq("id", ds_row["id"]).execute()
    shutil.rmtree(tmp, ignore_errors=True)
    if errors:
        raise RuntimeError("; ".join(errors))
    log(job["id"], sb, f"parsed: {data['counts']['collars']} collars, "
                       f"{data['counts']['intervals']} intervals — building the model")
    return do_build(sb, {**job, "project_id": ds_row["project_id"]})


# ------------------------------------------------------------------- job: build
def do_build(sb, job):
    proj = sb.table("projects").select("*").eq("id", job["project_id"]).single().execute().data
    rows = sb.table("datasets").select("*").eq("project_id", proj["id"]).eq("status", "ready").execute().data
    if not rows:
        raise RuntimeError("no parsed data in this project yet")
    sb.table("projects").update({"status": "building", "error": None}).eq("id", proj["id"]).execute()
    datasets = []
    for r in rows:
        if r.get("data_path"):
            datasets.append(json.loads(sb.storage.from_("models").download(r["data_path"]).decode()))
    ds = assemble_user_project(datasets, proj)
    settings = PARAMS.validate(proj.get("params") or {})[0]
    model = M3.build_project(ds, settings=settings)
    model["private"] = not proj.get("is_public")
    model["owner_project"] = proj["id"]
    path = f"{proj['owner']}/{proj['id']}/model.json"
    _upload_json(sb, "models", path, model)
    pe = model["elements"][model["primary"]]
    gt = (pe.get("gt") or [[0, 0, 0, 0]])
    cut = next((r for r in gt if r[0] >= (pe.get("default_cutoff") or (pe["bins"][1] if len(pe["bins"]) > 1 else pe["bins"][0]))), gt[0])
    stats = {"holes": model["counts"]["holes"], "intervals": model["counts"]["intervals"],
             "blocks": model["counts"]["blocks"], "element": model["primary"],
             "unit": pe["unit"], "cutoff": cut[0], "tonnes": cut[1], "grade": cut[2],
             "contained": cut[3], "contained_unit": pe.get("contained_unit"),
             "region": model.get("region"), "sources": len(model.get("sources") or []),
             "built": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    sb.table("projects").update({"status": "ready", "model_path": path, "stats": stats,
                                 "region": model.get("region")}).eq("id", proj["id"]).execute()
    if proj.get("is_public"):
        do_publish(sb, {**job, "project_id": proj["id"], "payload": {"public": True}}, model=model)
    return (f"built {stats['holes']} holes, {stats['intervals']} intervals, "
            f"{stats['blocks']:,} blocks")


def assemble_user_project(datasets, proj):
    """One project dataset from this account's imported datasets — and, when the
    project names a public project in `attach_to`, that project's data as well, so
    private drilling can be modelled together with what MMP already holds."""
    reports = list(datasets)
    base = None
    if proj.get("attach_to"):
        try:
            for d in PROJ.assemble():
                if d["slug"] == proj["attach_to"]:
                    base = d
                    break
        except Exception as e:
            print(f"[worker] attach_to {proj['attach_to']} unavailable: {str(e)[:100]}")
    if base is not None:
        p = {"news": [], "reports": reports, "company": proj.get("company") or base.get("company"),
             "companies": {proj.get("company")} - {None}, "project": proj["name"],
             "lat": None, "lon": None, "keys": set(), "country": base.get("region")}
        merged = PROJ._materialise(p, [], {}, {})
        # fold the public project's holes/intervals/sources in alongside the import
        if merged:
            merged["holes"].update({k: v for k, v in base["holes"].items() if k not in merged["holes"]})
            merged["intervals"] += base["intervals"]
            merged["sources"] += base["sources"]
            merged["resources"] = (merged.get("resources") or []) + (base.get("resources") or [])
            ds = merged
        else:
            ds = base
    else:
        p = {"news": [], "reports": reports, "company": proj.get("company"),
             "companies": {proj.get("company")} - {None}, "project": proj["name"],
             "lat": None, "lon": None, "keys": set(), "country": None}
        ds = PROJ._materialise(p, [], {}, {})
    if ds is None:
        raise RuntimeError("the data could not be placed in 3D — check the coordinate system")
    ds["slug"] = proj["slug"]
    ds["name"] = proj["name"]
    ds["company"] = proj.get("company")
    ds["params"] = PARAMS.validate(proj.get("params") or {})[0]
    return ds


# ----------------------------------------------------------------- job: publish
def do_publish(sb, job, model=None):
    proj = sb.table("projects").select("*").eq("id", job["project_id"]).single().execute().data
    want = bool((job.get("payload") or {}).get("public", proj.get("is_public")))
    pub_path = f"{proj['slug']}-{proj['id'][:8]}.json"
    if not want:
        try:
            sb.storage.from_("public-models").remove([pub_path])
        except Exception:
            pass
        sb.table("projects").update({"is_public": False}).eq("id", proj["id"]).execute()
        return "unpublished"
    if model is None:
        if not proj.get("model_path"):
            raise RuntimeError("build the model before publishing it")
        model = json.loads(sb.storage.from_("models").download(proj["model_path"]).decode())
    model = dict(model)
    model["private"] = False
    _upload_json(sb, "public-models", pub_path, model)
    sb.table("projects").update({"is_public": True}).eq("id", proj["id"]).execute()
    return f"published to public-models/{pub_path}"


# ------------------------------------------------------------------ job: delete
def do_delete(sb, job):
    proj_id = job["project_id"]
    owner = job["owner"]
    for bucket, prefix in (("uploads", f"{owner}/{proj_id}"), ("models", f"{owner}/{proj_id}")):
        try:
            files = sb.storage.from_(bucket).list(prefix)
            paths = [f"{prefix}/{f['name']}" for f in files or []]
            if paths:
                sb.storage.from_(bucket).remove(paths)
        except Exception:
            pass
    sb.table("projects").delete().eq("id", proj_id).execute()
    return "deleted"


HANDLERS = {"import": do_import, "build": do_build, "publish": do_publish, "delete": do_delete}


# ------------------------------------------------------------------- main loop
def run_once(sb):
    q = (sb.table("jobs").select("*").eq("status", "queued").order("created_at")
         .limit(1).execute().data)
    if not q:
        return False
    job = q[0]
    sb.table("jobs").update({"status": "running", "started_at": datetime.datetime.utcnow().isoformat(),
                             "attempts": (job.get("attempts") or 0) + 1}).eq("id", job["id"]).execute()
    print(f"[worker] job {job['id'][:8]} {job['kind']}", flush=True)
    try:
        out = HANDLERS[job["kind"]](sb, job)
        log(job["id"], sb, out or "done", status="done")
    except Exception as e:
        tb = traceback.format_exc(limit=3)
        print(tb, flush=True)
        msg = f"{type(e).__name__}: {e}"
        log(job["id"], sb, msg[:1000], status="error")
        if job.get("project_id"):
            try:
                sb.table("projects").update({"status": "error", "error": msg[:500]}
                                            ).eq("id", job["project_id"]).execute()
            except Exception:
                pass
    return True


def main():
    sb = client()
    once = "--once" in sys.argv
    print(f"[worker] polling {URL} every {POLL:g}s", flush=True)
    while True:
        try:
            busy = run_once(sb)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"[worker] poll failed: {str(e)[:200]}", flush=True)
            busy = False
        if once:
            return
        if not busy:
            time.sleep(POLL)


if __name__ == "__main__":
    main()
