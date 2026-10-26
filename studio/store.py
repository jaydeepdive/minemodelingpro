"""Storage for MMP Studio: Postgres + object storage, with no third party.

Two small facades so the server and the worker never talk to a driver directly:

    DB    - Postgres (psycopg3). `one`/`all`/`run` take %s placeholders, return dicts.
            `claim_job()` takes the next queued job with FOR UPDATE SKIP LOCKED, so
            several workers can run without stepping on each other.
    Files - object storage. DigitalOcean Spaces (S3 API) when SPACES_* is set,
            otherwise a directory on disk, which is what the tests and a local run use.

Environment:
    DATABASE_URL        postgres://…      (App Platform injects ${db.DATABASE_URL})
    SPACES_BUCKET       mmp-studio
    SPACES_REGION       nyc3              (endpoint derived unless SPACES_ENDPOINT is set)
    SPACES_KEY/SECRET   access key id + secret
    MMP_FILES_DIR       fallback directory when SPACES_* is unset (default ./data/private/studio)
"""
import os
import io
import json
import time
import threading

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_pg.sql")


# --------------------------------------------------------------------- Postgres
class DB:
    def __init__(self, url=None, minconn=1, maxconn=4):
        self.url = url or os.environ.get("DATABASE_URL") or ""
        if not self.url:
            raise SystemExit("DATABASE_URL is not set")
        if self.url.startswith("postgres://"):            # psycopg wants postgresql://
            self.url = "postgresql://" + self.url[len("postgres://"):]
        import psycopg
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row
        self._dict_row = dict_row
        kw = {}
        if "sslmode=" not in self.url:
            kw["sslmode"] = os.environ.get("PGSSLMODE", "require")
        self.pool = ConnectionPool(self.url, min_size=minconn, max_size=maxconn,
                                   kwargs=kw, open=True, timeout=30)

    def _cur(self, conn):
        return conn.cursor(row_factory=self._dict_row)

    def all(self, sql, *args):
        with self.pool.connection() as c, self._cur(c) as cur:
            cur.execute(sql, args or None)
            return cur.fetchall()

    def one(self, sql, *args):
        rows = self.all(sql, *args)
        return rows[0] if rows else None

    def run(self, sql, *args):
        with self.pool.connection() as c, self._cur(c) as cur:
            cur.execute(sql, args or None)
            return cur.rowcount

    def init_schema(self, path=SCHEMA):
        with open(path) as f:
            sql = f.read()
        with self.pool.connection() as c, c.cursor() as cur:
            cur.execute(sql)
        return True

    def claim_job(self):
        """Next queued job, marked running, atomically."""
        with self.pool.connection() as c, self._cur(c) as cur:
            cur.execute("""
                with j as (
                  select id from jobs where status = 'queued'
                  -- a person waiting on their own build goes before any
                  -- automatic refresh, however long the refresh has queued
                  order by (source = 'refresh'), created_at
                  limit 1 for update skip locked)
                update jobs set status = 'running', started_at = now(),
                       heartbeat_at = now(), attempts = attempts + 1
                 where id in (select id from j)
             returning *""")
            return cur.fetchone()

    def beat_job(self, job_id):
        """Say the job is still alive. A job that stops beating is taken to have
        died with its worker — on App Platform that is a deploy or the kernel's
        out-of-memory killer, neither of which gets to tell us."""
        try:
            return self.run("update jobs set heartbeat_at = now() where id = %s", job_id)
        except Exception:
            return 0

    def requeue_stale(self, timeout_min=20, max_attempts=3):
        """Put jobs whose worker stopped beating back on the queue, and give up
        out loud on the ones that have failed too often.

        Without this a worker that dies mid-job leaves the job `running` and the
        project `parsing` for ever: nothing retries it and the page spins. Returns
        (requeued, abandoned) as lists of (id, kind, project_id, attempts)."""
        # a float number of minutes: make_interval(mins => ...) takes an integer only
        cut = "(now() - (%s::double precision * interval '1 minute'))"
        with self.pool.connection() as c, self._cur(c) as cur:
            cur.execute(f"""
                select id, kind, project_id, dataset_id, attempts from jobs
                 where status = 'running'
                   and coalesce(heartbeat_at, started_at, created_at) < {cut}
                 order by created_at
                 for update skip locked""", (timeout_min,))
            rows = cur.fetchall() or []
            requeued, dead = [], []
            for r in rows:
                if (r["attempts"] or 0) >= max_attempts:
                    dead.append(r)
                    cur.execute("update jobs set status = 'error', finished_at = now(), "
                                "log = %s where id = %s",
                                ("the modeler stopped partway through, %d times — the files are "
                                 "probably too large for it to hold in memory at once"
                                 % (r["attempts"] or 0), r["id"]))
                    if r["dataset_id"]:
                        cur.execute("update datasets set status = 'error', error = %s where id = %s",
                                    ("the modeler stopped partway through this file three times",
                                     r["dataset_id"]))
                    if r["project_id"]:
                        cur.execute("update projects set status = 'error', error = %s where id = %s",
                                    ("the modeler stopped partway through, three times",
                                     r["project_id"]))
                else:
                    requeued.append(r)
                    # say so, and say it somewhere the successful retry will not
                    # overwrite: otherwise a build that died once and worked the
                    # second time reads as "attempt 2" with no explanation, which
                    # is indistinguishable from it failing for ever
                    cur.execute(
                        "update jobs set status = 'queued', heartbeat_at = null, "
                        "started_at = null, notes = concat_ws(chr(10), notes, %s::text) "
                        "where id = %s",
                        # attempts counts the one that just died, not the next one
                        ("attempt %d stopped responding after %g minutes and was "
                         "restarted. The build itself reports its own peak memory "
                         "in the log below — if that is well under the limit, the "
                         "worker was interrupted (a deploy or a restart), not starved"
                         % (max(1, r["attempts"] or 1), timeout_min), r["id"]))
            return requeued, dead

    def worker_beat(self, wid="modeler", note=None):
        try:
            return self.run("insert into workers (id, at, note) values (%s, now(), %s) "
                            "on conflict (id) do update set at = now(), note = excluded.note",
                            wid, (note or "")[:200])
        except Exception:
            return 0

    def worker_seen(self, wid="modeler"):
        """Seconds since the worker last said anything, or None if it never has."""
        try:
            r = self.one("select extract(epoch from (now() - at)) as age, note "
                         "from workers where id = %s", wid)
            return (float(r["age"]), r.get("note")) if r else (None, None)
        except Exception:
            return (None, None)


# ---------------------------------------------------------------- object storage
class Files:
    """Private object storage. Keys are paths like `<user>/<project>/model.json`."""

    def __init__(self):
        self.bucket = os.environ.get("SPACES_BUCKET") or ""
        key = os.environ.get("SPACES_KEY") or ""
        secret = os.environ.get("SPACES_SECRET") or ""
        region = os.environ.get("SPACES_REGION", "nyc3")
        self.endpoint = os.environ.get("SPACES_ENDPOINT") or f"https://{region}.digitaloceanspaces.com"
        self.region = region
        self.s3 = None
        if self.bucket and key and secret:
            import boto3
            from botocore.config import Config
            self.s3 = boto3.client("s3", endpoint_url=self.endpoint, region_name=region,
                                   aws_access_key_id=key, aws_secret_access_key=secret,
                                   config=Config(signature_version="s3v4",
                                                 retries={"max_attempts": 3, "mode": "standard"}))
        else:
            self.dir = os.environ.get("MMP_FILES_DIR") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "private", "studio")
            os.makedirs(self.dir, exist_ok=True)

    # -- basics
    def put(self, key, data, content_type="application/octet-stream", public=False):
        if isinstance(data, str):
            data = data.encode()
        if self.s3:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type,
                               ACL="public-read" if public else "private")
        else:
            p = os.path.join(self.dir, key)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        return key

    def put_json(self, key, obj, public=False):
        return self.put(key, json.dumps(obj, separators=(",", ":")).encode(),
                        "application/json", public=public)

    def get(self, key):
        if self.s3:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        with open(os.path.join(self.dir, key), "rb") as f:
            return f.read()

    def get_json(self, key):
        return json.loads(self.get(key).decode())

    def exists(self, key):
        try:
            if self.s3:
                self.s3.head_object(Bucket=self.bucket, Key=key)
                return True
            return os.path.exists(os.path.join(self.dir, key))
        except Exception:
            return False

    def remove(self, keys):
        keys = [keys] if isinstance(keys, str) else list(keys)
        if not keys:
            return 0
        if self.s3:
            self.s3.delete_objects(Bucket=self.bucket,
                                   Delete={"Objects": [{"Key": k} for k in keys]})
        else:
            for k in keys:
                try:
                    os.remove(os.path.join(self.dir, k))
                except OSError:
                    pass
        return len(keys)

    def list(self, prefix):
        out = []
        if self.s3:
            tok = None
            while True:
                kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
                if tok:
                    kw["ContinuationToken"] = tok
                r = self.s3.list_objects_v2(**kw)
                out += [{"key": o["Key"], "bytes": o["Size"]} for o in r.get("Contents") or []]
                if not r.get("IsTruncated"):
                    break
                tok = r.get("NextContinuationToken")
        else:
            root = os.path.join(self.dir, prefix)
            for dp, _dn, fn in os.walk(root):
                for f in fn:
                    p = os.path.join(dp, f)
                    out.append({"key": os.path.relpath(p, self.dir), "bytes": os.path.getsize(p)})
        return out

    def remove_prefix(self, prefix):
        return self.remove([o["key"] for o in self.list(prefix)])

    def usage(self, prefix):
        return sum(o["bytes"] for o in self.list(prefix))

    def signed_url(self, key, ttl=900):
        """A time-limited URL, when the backing store can make one."""
        if self.s3:
            return self.s3.generate_presigned_url("get_object",
                                                  Params={"Bucket": self.bucket, "Key": key},
                                                  ExpiresIn=int(ttl))
        return None


# ------------------------------------------------------------------- singletons
_LOCK = threading.Lock()
_DB = None
_FILES = None


def db():
    global _DB
    with _LOCK:
        if _DB is None:
            _DB = DB()
    return _DB


def files():
    global _FILES
    with _LOCK:
        if _FILES is None:
            _FILES = Files()
    return _FILES


def wait_for_db(tries=30, delay=2):
    """App Platform can start the app before the database finishes attaching."""
    last = None
    for _ in range(tries):
        try:
            d = db()
            d.one("select 1 as ok")
            return d
        except Exception as e:                 # noqa: BLE001
            last = e
            global _DB
            _DB = None
            time.sleep(delay)
    raise SystemExit(f"database unreachable: {last}")
