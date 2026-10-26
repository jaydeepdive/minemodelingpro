"""MMP Studio worker — turns uploaded drill data into models.

Postgres holds the accounts, projects and the job queue; Spaces (or a directory,
locally) holds the uploaded files and the finished models. This process does the
work that needs Python: parsing spreadsheets and Access databases, the desurvey,
the block model, the terrain.

    jobs.kind = "import"   parse a dataset's files -> dataset JSON + summary, then build
                "build"    (re)build the project's model with its current parameters
                "publish"  copy the model to the shared prefix (or unpublish)
                "delete"   remove a project's stored files and the project row

Run it anywhere that can reach the database:

    pip install -r studio/requirements.txt
    export DATABASE_URL=postgresql://...  SPACES_BUCKET=... SPACES_KEY=... SPACES_SECRET=...
    PYTHONPATH=src python studio/worker.py          # --once for a single pass

On App Platform it is the `modeler` worker component of the same app, so it shares
the image and the database with the web service.
"""
import os
import re
import sys
import json
import time
import shutil
import tempfile
import datetime
import threading
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)

from studio import store                              # noqa: E402
from minemodelingpro import imports as IMP             # noqa: E402
from minemodelingpro import ivtable as IVT             # noqa: E402
from minemodelingpro import projects as PROJ           # noqa: E402
from minemodelingpro import model3d as M3              # noqa: E402
from minemodelingpro import params as PARAMS           # noqa: E402

POLL = float(os.environ.get("MMP_POLL_SECONDS", "5"))
# a job that stops beating for this long is taken to have died with its worker
JOB_TIMEOUT_MIN = float(os.environ.get("MMP_JOB_TIMEOUT_MIN", "20"))
JOB_ATTEMPTS = int(os.environ.get("MMP_JOB_ATTEMPTS", "3"))
BEAT = float(os.environ.get("MMP_BEAT_SECONDS", "30"))
# how often to look for models the modelling code has moved on from, and how many
# to put back through the estimator at a time (one box, other people's jobs queue
# behind these, so a corpus-wide refresh trickles rather than stampedes)
REFRESH_MIN = float(os.environ.get("MMP_REFRESH_MIN", "10"))
REFRESH_BATCH = int(os.environ.get("MMP_REFRESH_BATCH", "3"))
MAX_FILE_MB = float(os.environ.get("MMP_MAX_FILE_MB", "200"))
WORKDIR = os.environ.get("MMP_WORKDIR", os.path.join(tempfile.gettempdir(), "mmp-studio"))


# --------------------------------------------------------------------- helpers
def log(job_id, line, status=None):
    print(f"[worker] {line}", flush=True)
    try:
        if status in ("done", "error"):
            store.db().run("update jobs set log = %s, status = %s, finished_at = now() where id = %s",
                           (line or "")[:4000], status, job_id)
        elif status:
            store.db().run("update jobs set log = %s, status = %s where id = %s",
                           (line or "")[:4000], status, job_id)
        else:
            store.db().run("update jobs set log = %s where id = %s", (line or "")[:4000], job_id)
    except Exception:
        pass


class Heartbeat:
    """Says a job is still alive while a handler works.

    The handler is one long synchronous call, so the beat has to come from
    beside it. Without it a job that takes longer than the timeout would be
    requeued while it was still running, and a job whose worker was killed —
    a deploy, or the out-of-memory killer — would stay `running` for ever and
    leave the project parsing with nothing on its way."""

    def __init__(self, job_id, every=BEAT):
        self.job_id, self.every, self.stop = job_id, every, threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(self.every):
            try:
                store.db().beat_job(self.job_id)
            except Exception:
                pass

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *e):
        self.stop.set()


def sweep(verbose=True):
    """Put jobs whose worker died back on the queue."""
    try:
        requeued, dead = store.db().requeue_stale(JOB_TIMEOUT_MIN, JOB_ATTEMPTS)
    except Exception as e:
        print(f"[worker] sweep failed: {str(e)[:160]}", flush=True)
        return 0
    if verbose:
        for r in requeued:
            print(f"[worker] requeued {r['kind']} job {str(r['id'])[:8]} "
                  f"(attempt {r['attempts']} stopped beating)", flush=True)
        for r in dead:
            print(f"[worker] gave up on {r['kind']} job {str(r['id'])[:8]} "
                  f"after {r['attempts']} attempts", flush=True)
    return len(requeued) + len(dead)


def _pulse():
    """What the worker wants anyone debugging it to know, on every beat.

    The memory limit above all: this container is sized in the DO console, not in
    the repo, and a build that dies at 1 GB and survives at 2 GB looks identical
    from the page. /api/health reports this, so "how much memory does the modeler
    actually have" is answerable without opening a console."""
    try:
        return "mem %d MB · model v%d" % (int(M3.mem_budget_mb()), M3.MODEL_VERSION)
    except Exception:
        return "polling"


def refresh_stale_models(verbose=True):
    """Rebuild the models an improvement has not reached yet.

    A model is a stored artifact. Improving the estimator does not change a block
    model that was written last week, so "we improved the modelling" and "your
    model is better" were two different events with a button press in between —
    and the client had no way to know the button was there, or that it mattered.

    The public gallery never had this problem: CI rebuilds it nightly from the
    current code. Private models are rebuilt here, on the same principle, by
    comparing the version stamped on each stored model against the code's own.
    A few at a time, newest first, and never for a project that already has a
    build queued, so a version bump does not become a thundering herd.
    """
    if (os.environ.get("MMP_AUTO_REFRESH", "1") or "1").lower() in ("0", "false", "no"):
        return 0
    d = store.db()
    try:
        rows = d.all(
            "select p.id, p.name, p.owner, "
            "       coalesce((p.stats->>'model_version')::int, 0) as v "
            "  from projects p "
            " where p.status = 'ready' and p.model_key is not null "
            "   and coalesce((p.stats->>'model_version')::int, 0) < %s "
            "   and not exists (select 1 from jobs j "
            "                    where j.project_id = p.id and j.kind = 'build' "
            "                      and j.status in ('queued','running')) "
            " order by p.updated_at desc nulls last limit %s",
            M3.MODEL_VERSION, REFRESH_BATCH)
    except Exception as e:
        print(f"[worker] refresh sweep failed: {str(e)[:160]}", flush=True)
        return 0
    for r in rows:
        try:
            d.run("insert into jobs (kind, owner, project_id, source) "
                  "values ('build',%s,%s,'refresh')", r["owner"], r["id"])
        except Exception as e:
            print(f"[worker] could not queue refresh for {r['id']}: {str(e)[:120]}", flush=True)
            continue
        if verbose:
            print(f"[worker] refresh: {r['name']} is on model v{r['v']}, "
                  f"code is v{M3.MODEL_VERSION} — rebuilding", flush=True)
    return len(rows)


def queue_build(job, project_id):
    """Put a build on the queue for this project."""
    return store.db().one(
        "insert into jobs (kind, owner, project_id) values ('build',%s,%s) returning *",
        job.get("owner"), project_id)


def _slug(s, fallback="project"):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48] or fallback


def _download(key, dest):
    data = store.files().get(key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


# ------------------------------------------------------------------ job: import
def do_import(job):
    d, F = store.db(), store.files()
    ds_row = d.one("select * from datasets where id = %s", job["dataset_id"])
    if not ds_row:
        raise RuntimeError("dataset not found")
    d.run("update datasets set status = 'parsing', error = null where id = %s", ds_row["id"])
    tmp = os.path.join(WORKDIR, "in", str(ds_row["id"]))
    shutil.rmtree(tmp, ignore_errors=True)
    paths = []
    for f in ds_row["files"] or []:
        if (f.get("bytes") or 0) > MAX_FILE_MB * 1024 * 1024:
            raise RuntimeError(f"{f.get('filename')} is larger than {MAX_FILE_MB:.0f} MB")
        paths.append(_download(f["key"], os.path.join(tmp, f["filename"])))
    if not paths:
        raise RuntimeError("no files on this dataset")
    proj = d.one("select * from projects where id = %s", ds_row["project_id"])
    user = d.one("select quota_bytes from users where id = %s", ds_row["owner"])
    try:                                        # per-account storage quota
        used = F.usage(f"{ds_row['owner']}/")
        if user and used > (user["quota_bytes"] or 0):
            raise RuntimeError(f"account storage quota reached ({used / 1e6:.0f} MB used) — "
                               f"delete a project or ask for more space")
    except RuntimeError:
        raise
    except Exception:
        pass
    o = ds_row["options"] or {}
    log(job["id"], f"parsing {len(paths)} file(s)...")
    data = IMP.build_dataset(
        paths, project=proj["name"], company=proj.get("company"),
        epsg=o.get("epsg"), utm_zone=o.get("utm_zone"), hemisphere=o.get("hemisphere", "N"),
        datum=o.get("datum", "WGS84"), attach_to=proj.get("attach_to"),
        settings=proj.get("params") or {}, dataset_id="import:" + str(ds_row["id"]),
        owner=str(ds_row["owner"]), private=not proj.get("is_public"),
        source_name=ds_row.get("name") or ", ".join(os.path.basename(p) for p in paths),
        crs_mode=o.get("crs_mode", "auto"), azimuth_ref=o.get("azimuth_ref", "auto"))
    # A source ADDED to a project that already has drilling is not required to
    # be a whole drill database on its own: a release of six intervals on three
    # holes is a good addition and a bad standalone dataset. Judge it as what it
    # is, or adding a source fails on the minimums every time.
    others = d.all("select id from datasets where project_id = %s and status = 'ready' "
                   "and id <> %s", ds_row["project_id"], ds_row["id"])
    errors = IMP.validate(data, standalone=not others)
    summary = {"counts": data["counts"], "tables": data["tables"], "notes": data["notes"],
               "crs": data["crs"], "center": data["center"], "errors": errors}
    key = f"{ds_row['owner']}/{ds_row['project_id']}/datasets/{ds_row['id']}.json"
    F.put_json(key, IVT.to_store(data))
    d.run("update datasets set status = %s, summary = %s, data_key = %s, error = %s where id = %s",
          "error" if errors else "ready", json.dumps(summary), key,
          "; ".join(errors) or None, ds_row["id"])
    shutil.rmtree(tmp, ignore_errors=True)
    if errors:
        raise RuntimeError("; ".join(errors))
    n_coll, n_iv = data["counts"]["collars"], data["counts"]["intervals"]
    # Hand the build to its own job rather than calling it here. Parsing a large
    # multi-element database leaves several hundred megabytes of parsed rows alive
    # in this frame, and the build then has to fit the block model around them —
    # on a worker with a gigabyte that is the difference between a model and a
    # process the kernel kills.
    del data, summary
    d.run("update projects set status = 'parsing', error = null where id = %s", ds_row["project_id"])
    queue_build(job, ds_row["project_id"])
    return f"parsed: {n_coll} collars, {n_iv} intervals — queued the model build"


# ------------------------------------------------------------------- job: build
def do_build(job):
    d, F = store.db(), store.files()
    proj = d.one("select * from projects where id = %s", job["project_id"])
    rows = d.all("select * from datasets where project_id = %s and status = 'ready'", proj["id"])
    if not rows and not proj.get("attach_to"):
        raise RuntimeError("no parsed data in this project yet")
    d.run("update projects set status = 'building', error = null where id = %s", proj["id"])
    datasets = [IVT.from_store(IMP.intern_rows(F.get_json(r["data_key"])))
                for r in rows if r.get("data_key")]
    ds = assemble_user_project(datasets, proj)
    settings = PARAMS.validate(proj.get("params") or {})[0]
    model = M3.build_project(ds, settings=settings)
    model["private"] = not proj.get("is_public")
    model["owner_project"] = str(proj["id"])
    key = f"{proj['owner']}/{proj['id']}/model.json"
    F.put_json(key, model)
    pe = model["elements"][model["primary"]]
    gt = pe.get("gt") or [[0, 0, 0, 0]]
    want = pe.get("default_cutoff") or (pe["bins"][1] if len(pe["bins"]) > 1 else pe["bins"][0])
    cut = next((r for r in gt if r[0] >= want), gt[0])
    whole = next((r for r in gt if r[0] <= 0), gt[0])    # every block the model holds
    stats = {"holes": model["counts"]["holes"], "intervals": model["counts"]["intervals"],
             "blocks": model["counts"]["blocks"], "element": model["primary"],
             "unit": pe["unit"], "cutoff": cut[0], "tonnes": cut[1], "grade": cut[2],
             "contained": cut[3], "contained_unit": pe.get("contained_unit"),
             # a cut-off tonnage on its own has misled every reader of this card:
             # it is a slice of the deposit, and the deposit's own size belongs
             # beside it
             "tonnes_all": whole[1], "grade_all": whole[2],
             "elements": list(model.get("elements") or {}),
             "region": model.get("region"), "sources": len(model.get("sources") or []),
             "params": (pe.get("blocks") or {}).get("params", {}),
             # which modelling code made this. The refresh sweep reads it to find
             # the models an improvement has not reached yet.
             "model_version": model.get("model_version"),
             "built": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    d.run("update projects set status = 'ready', model_key = %s, stats = %s, region = %s, "
          "updated_at = now() where id = %s", key, json.dumps(stats), model.get("region"), proj["id"])
    if proj.get("is_public"):
        do_publish({**job, "project_id": proj["id"], "payload": {"public": True}}, model=model)
    # say where the time went: "why is a rebuild nine minutes" should be
    # answerable from the card, not from a profiler
    where = model.get("build_summary") or ""
    peak = model.get("peak_mb")
    if peak:
        where = (where + " · " if where else "") + f"peak {peak} MB"
    return (f"built {stats['holes']} holes, {stats['intervals']} intervals, "
            f"{stats['blocks']:,} blocks" + (f" — {where}" if where else ""))


_SYNCED = False


def _ensure_public_data():
    """The public drill bank (Closeology's) is synced at build time in CI, so a
    fresh container has only the 43-101 report JSON. Fetch it once, on demand,
    when a client project asks to be built on top of a public MMP project."""
    global _SYNCED
    if _SYNCED:
        return
    _SYNCED = True
    bank = os.path.join(_ROOT, "data", "keep", "drillbank.sqlite")
    if os.path.exists(bank):
        return
    try:
        from minemodelingpro import closeology_sync
        closeology_sync.sync()
        print("[worker] synced public drill bank", flush=True)
    except Exception as e:
        print(f"[worker] public drill bank unavailable: {str(e)[:150]}", flush=True)


def assemble_user_project(datasets, proj):
    """One project dataset from this account's imported datasets — and, when the
    project names a public project in `attach_to`, that project's data as well, so
    private drilling can be modelled together with what MMP already holds."""
    reports = list(datasets)
    base = None
    if proj.get("attach_to"):
        _ensure_public_data()
        try:
            for d in PROJ.assemble():
                if d["slug"] == proj["attach_to"]:
                    base = d
                    break
        except Exception as e:
            print(f"[worker] attach_to {proj['attach_to']} unavailable: {str(e)[:100]}")
    p = {"news": [], "reports": reports,
         "company": proj.get("company") or (base or {}).get("company"),
         "companies": {proj.get("company")} - {None}, "project": proj["name"],
         "lat": None, "lon": None, "keys": set(), "country": (base or {}).get("region")}
    merged = PROJ._materialise(p, [], {}, {}, consume=True)
    if base is not None and merged:
        # fold the public project's holes/intervals/sources in alongside the import, so
        # the client's own drilling is modelled WITH the public data, not instead of it
        merged["holes"].update({k: v for k, v in base["holes"].items() if k not in merged["holes"]})
        merged["intervals"].extend(base["intervals"])
        merged["sources"] += base["sources"]
        merged["resources"] = (merged.get("resources") or []) + (base.get("resources") or [])
        merged["report_params"] = merged.get("report_params") or base.get("report_params")
        merged["report_params_from"] = merged.get("report_params_from") or base.get("report_params_from")
        ds = merged
    elif base is not None:
        ds = base
    else:
        ds = merged
    if ds is None:
        raise RuntimeError("the data could not be placed in 3D — check the coordinate system")
    ds["slug"] = proj["slug"]
    ds["name"] = proj["name"]
    ds["company"] = proj.get("company")
    ds["params"] = PARAMS.validate(proj.get("params") or {})[0]
    if proj.get("commodity") and not ds.get("commodities"):
        ds["commodities"] = [proj["commodity"]]
    return ds


# ----------------------------------------------------------------- job: publish
def do_publish(job, model=None):
    d, F = store.db(), store.files()
    proj = d.one("select * from projects where id = %s", job["project_id"])
    payload = job.get("payload") or {}
    want = bool(payload.get("public", proj.get("is_public")))
    pub = proj.get("public_key") or f"{proj['slug']}-{str(proj['id'])[:8]}.json"
    if not want:
        try:
            F.remove("public/" + pub)
        except Exception:
            pass
        d.run("update projects set is_public = false, public_key = null where id = %s", proj["id"])
        return "unpublished"
    if model is None:
        if not proj.get("model_key"):
            raise RuntimeError("build the model before publishing it")
        model = F.get_json(proj["model_key"])
    model = dict(model)
    model["private"] = False
    F.put_json("public/" + pub, model)            # the model only — never the source files
    d.run("update projects set is_public = true, public_key = %s where id = %s", pub, proj["id"])
    return f"shared as {pub}"


# ------------------------------------------------------------------ job: delete
def do_delete(job):
    d, F = store.db(), store.files()
    proj = d.one("select * from projects where id = %s", job["project_id"])
    if proj:
        try:
            F.remove_prefix(f"{proj['owner']}/{proj['id']}/")
            if proj.get("public_key"):
                F.remove("public/" + proj["public_key"])
        except Exception:
            pass
        d.run("delete from projects where id = %s", proj["id"])
    return "deleted"


HANDLERS = {"import": do_import, "build": do_build, "publish": do_publish, "delete": do_delete}


# ------------------------------------------------------------------- main loop
def run_once():
    job = store.db().claim_job()
    if not job:
        return False
    print(f"[worker] job {str(job['id'])[:8]} {job['kind']}", flush=True)
    try:
        with Heartbeat(job["id"]):
            out = HANDLERS[job["kind"]](job)
        log(job["id"], out or "done", status="done")
    except MemoryError:
        msg = ("the files are too large for the modeler to hold in memory at once — "
               "split them, or send them as one zip and ask for more memory")
        print("[worker] MemoryError", flush=True)
        log(job["id"], msg, status="error")
        for tbl, col in (("datasets", "dataset_id"), ("projects", "project_id")):
            if job.get(col):
                try:
                    store.db().run(f"update {tbl} set status = 'error', error = %s where id = %s",
                                   msg[:500], job[col])
                except Exception:
                    pass
        return True
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        print(tb, flush=True)
        msg = f"{type(e).__name__}: {e}"
        log(job["id"], msg[:1000], status="error")
        if job.get("dataset_id"):
            try:
                store.db().run("update datasets set status = 'error', error = %s where id = %s",
                               msg[:500], job["dataset_id"])
            except Exception:
                pass
        if job.get("project_id"):
            try:
                store.db().run("update projects set status = 'error', error = %s where id = %s",
                               msg[:500], job["project_id"])
            except Exception:
                pass
    return True


def main():
    d = store.wait_for_db()
    try:                                   # keep the live app on the latest main
        from studio import autodeploy
        autodeploy.start(d)
    except Exception as e:
        print(f"[worker] autodeploy unavailable: {str(e)[:120]}", flush=True)
    try:                                   # publish the nightly gallery without a deploy
        from studio import sitesync
        sitesync.start_worker()
    except Exception as e:
        print(f"[worker] sitesync unavailable: {str(e)[:120]}", flush=True)
    try:
        store.db().init_schema()
    except Exception as e:
        print(f"[worker] schema: {str(e)[:200]}", flush=True)
    once = "--once" in sys.argv
    print(f"[worker] polling every {POLL:g}s, job timeout {JOB_TIMEOUT_MIN:g} min", flush=True)
    # anything left running by the previous process died with it — a deploy
    # restarts this worker mid-job, and nothing else will ever pick those up
    sweep()
    last_sweep = time.time()
    last_refresh = 0.0                     # look for stale models on the first idle tick
    while True:
        try:
            store.db().worker_beat("modeler", _pulse())
            busy = run_once()
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"[worker] poll failed: {str(e)[:200]}", flush=True)
            busy = False
        if once:
            return
        if time.time() - last_sweep > 60:
            sweep()
            last_sweep = time.time()
        # only when the queue is empty: a client's own upload always goes first
        if not busy and time.time() - last_refresh > REFRESH_MIN * 60:
            refresh_stale_models()
            last_refresh = time.time()
        if not busy:
            time.sleep(POLL)


if __name__ == "__main__":
    main()
