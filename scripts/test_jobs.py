"""The job queue must not lose a job when its worker dies.

Needs a Postgres to talk to:
    DATABASE_URL=postgresql://postgres@/mmptest?host=/var/run/postgresql&port=5433 \\
    PYTHONPATH=src python3 scripts/test_jobs.py
"""
import os
import sys
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))
os.environ.setdefault("PGSSLMODE", "disable")

from studio import store                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""))


d = store.db()
d.init_schema()
d.run("delete from jobs")
d.run("delete from workers")
owner = d.one("insert into users (email, pw, role) values (%s,%s,%s) returning *",
              "t-" + uuid.uuid4().hex[:8] + "@example.com", "x", "admin")["id"]
proj = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
             owner, "t-" + uuid.uuid4().hex[:6], "T")["id"]


def queue(kind="import"):
    return d.one("insert into jobs (kind, owner, project_id) values (%s,%s,%s) returning *",
                 kind, owner, proj)


# --- a claimed job is running, and claiming again finds nothing
j = queue()
c = d.claim_job()
check("a queued job is claimed exactly once", c and c["id"] == j["id"] and d.claim_job() is None)
check("claiming starts the heartbeat", c["heartbeat_at"] is not None)

# --- a job that is still beating is left alone
req, dead = d.requeue_stale(timeout_min=20)
check("a beating job is not requeued", req == [] and dead == [])

# --- a job that stopped beating comes back to the queue
d.run("update jobs set heartbeat_at = now() - interval '45 minutes' where id = %s", j["id"])
req, dead = d.requeue_stale(timeout_min=20)
check("a job that stopped beating is requeued", len(req) == 1 and not dead, (req, dead))
again = d.claim_job()
check("the requeued job is claimable again", again and again["id"] == j["id"])
check("the attempt count carries", again["attempts"] == 2, again["attempts"])

# --- and after enough attempts it is given up on, out loud
d.run("update jobs set attempts = 3, heartbeat_at = now() - interval '45 minutes' where id = %s", j["id"])
req, dead = d.requeue_stale(timeout_min=20)
check("a job that keeps dying is abandoned with a reason", not req and len(dead) == 1)
row = d.one("select status, log from jobs where id = %s", j["id"])
check("the abandoned job says what happened",
      row["status"] == "error" and "memory" in (row["log"] or ""), row)
pr = d.one("select status, error from projects where id = %s", proj)
check("the project is taken out of 'parsing'", pr["status"] == "error", pr)

# --- a job with no heartbeat at all (queued before this column existed) still sweeps
d.run("delete from jobs")
j2 = queue("build")
d.claim_job()
d.run("update jobs set heartbeat_at = null, started_at = now() - interval '3 hours' where id = %s", j2["id"])
req, dead = d.requeue_stale(timeout_min=20)
check("a job from before the heartbeat existed is still rescued", len(req) == 1, (req, dead))

# --- the timeout may be a float: make_interval(mins => ...) takes an integer, and
#     the worker reads its timeout out of the environment as a float
d.run("delete from jobs")
j3 = queue("publish")
d.claim_job()
d.run("update jobs set heartbeat_at = now() - interval '2 hours' where id = %s", j3["id"])
req, dead = d.requeue_stale(timeout_min=20.0)
check("a fractional timeout works", len(req) == 1, (req, dead))

# --- the worker's own pulse
check("the worker has not been seen yet", d.worker_seen("modeler")[0] is None)
d.worker_beat("modeler", "polling")
age, note = d.worker_seen("modeler")
check("a worker beat is readable", age is not None and age < 5 and note == "polling", (age, note))
d.worker_beat("modeler", "again")
check("beating twice keeps one row",
      d.one("select count(*) as n from workers")["n"] == 1)

# --- an improvement reaches a model without anyone pressing a button
from studio import worker as W                                  # noqa: E402
from minemodelingpro import model3d as M3                       # noqa: E402

d.run("delete from jobs")
cur = M3.MODEL_VERSION


def ready(version, name="R"):
    pid = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
                owner, "r-" + uuid.uuid4().hex[:6], name)["id"]
    d.run("update projects set status='ready', model_key='k', stats=%s, updated_at=now() "
          "where id=%s", ('{"model_version": %d}' % version) if version is not None else "{}", pid)
    return pid


def build_jobs(pid):
    return d.one("select count(*) as n from jobs where project_id=%s and kind='build'", pid)["n"]


old_p = ready(cur - 1)
new_p = ready(cur)
never_p = ready(None)                       # built before the stamp existed
W.refresh_stale_models(verbose=False)
check("a model behind the code is queued for rebuild", build_jobs(old_p) == 1, build_jobs(old_p))
check("a model already on the current version is left alone", build_jobs(new_p) == 0)
check("a model with no version at all counts as behind", build_jobs(never_p) == 1)

W.refresh_stale_models(verbose=False)
check("a second sweep does not queue it twice", build_jobs(old_p) == 1, build_jobs(old_p))

d.run("delete from jobs")
d.run("update projects set status='error' where id=%s", old_p)
W.refresh_stale_models(verbose=False)
check("a project that is not ready is not rebuilt", build_jobs(old_p) == 0)

d.run("delete from jobs")
d.run("update projects set status='ready' where id=%s", old_p)
os.environ["MMP_AUTO_REFRESH"] = "0"
W.refresh_stale_models(verbose=False)
check("the refresh can be switched off", build_jobs(old_p) == 0)
os.environ["MMP_AUTO_REFRESH"] = "1"

d.run("delete from jobs")
many = [ready(cur - 1, "M%d" % i) for i in range(W.REFRESH_BATCH + 3)]
n = W.refresh_stale_models(verbose=False)
check("a version bump trickles rather than stampeding",
      n == W.REFRESH_BATCH and
      d.one("select count(*) as n from jobs where kind='build'")["n"] == W.REFRESH_BATCH, n)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
