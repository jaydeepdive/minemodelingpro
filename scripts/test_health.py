#!/usr/bin/env python3
"""The health check must never be able to take the app down.

There is ONE web instance. App Platform polls /api/health, and an instance that
fails or is slow to answer is pulled out of rotation — with no replica behind
it, that is `no_healthy_upstream (503 UH)` on every route, for everybody.
Observed 2026-10-27 while a model page was loading, with the app otherwise
perfectly healthy and idle.

The endpoint used to make three database round trips, and the connection pool
waits up to thirty seconds for a connection. It returned 200 whatever the
database said — a lesson already learned once and written in the comment above
it — but a check that can HANG takes the app out just as surely as one that can
fail. So this pins the property that actually matters: the endpoint answers
from a snapshot, promptly, whatever the database is doing.

Needs no Postgres: the database is replaced by one that never answers, which is
the case under test.
"""
import os
import sys
import time
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("MMP_SECRET", "test-secret")
os.environ.setdefault("MMP_HEALTH_EVERY", "1")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


try:
    from studio import server, store
except Exception as e:                                       # noqa: BLE001
    print("studio.server not importable here (%s) — skipping" % type(e).__name__)
    sys.exit(0)


class Hanging:
    """A database that accepts the call and never comes back, like a pool at
    its limit. Thirty seconds is the pool's own timeout."""

    def __init__(self, secs=30.0):
        self.secs = secs
        self.calls = 0

    def one(self, *a, **k):
        self.calls += 1
        time.sleep(self.secs)
        return {}

    def worker_seen(self, *a, **k):
        time.sleep(self.secs)
        return (0, "")


def timed(fn, limit=5.0):
    """Call it on a thread so a BLOCKING health check fails the test instead of
    hanging it. The whole point here is a call that may never return."""
    box = {}

    def run():
        try:
            box["out"] = fn()
        except Exception as e:                               # noqa: BLE001
            box["err"] = e

    t0 = time.time()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(limit)
    if th.is_alive():
        return {"hung": True}, limit + 999            # never passes a < check
    if "err" in box:
        # an endpoint that THROWS is as broken as one that hangs, and it must
        # read as a failed check rather than as a crashed test run
        return {"raised": "%s: %s" % (type(box["err"]).__name__,
                                      str(box["err"])[:120])}, limit + 999
    return box["out"], time.time() - t0


print("== a database that answers")
server._FACTS.clear()
server._FACTS.update({"at": time.time(), "ok": True, "worker_seen_s": 3,
                      "worker_note": "mem 1048 MB", "queued": 0, "running": 1})
out, secs = timed(server.health)
check("health answers at once", secs < 0.05, round(secs, 3))
check("it reports the version", out.get("version") is not None, out)
check("it reports the database as reachable", out.get("ok") is True, out)
check("it carries the worker's pulse and the queue",
      out.get("worker_seen_s") == 3 and out.get("running") == 1, out)
check("and does not leak the snapshot's timestamp as a field", "at" not in out, out)

print("\n== a database that hangs")
hung = Hanging(30.0)
real_db = store.db
store.db = lambda: hung
try:
    # the refresher is what waits — the request path must not
    t = threading.Thread(target=server._facts_once, daemon=True)
    t.start()
    time.sleep(0.4)
    out, secs = timed(server.health)
    check("health STILL answers at once while the database is hanging",
          secs < 0.05, round(secs, 3))
    check("it serves the last snapshot rather than waiting for a new one",
          out.get("ok") is True, out)
    check("the hanging call is the refresher's, not the request's",
          hung.calls == 1 and t.is_alive(), (hung.calls, t.is_alive()))
finally:
    store.db = real_db

print("\n== a database that is down")
class Dead:
    def one(self, *a, **k):
        raise RuntimeError("could not connect to server")

    def worker_seen(self, *a, **k):
        raise RuntimeError("could not connect to server")

store.db = lambda: Dead()
try:
    server._facts_once()
    out, secs = timed(server.health)
    check("health answers at once with the database down", secs < 0.05, round(secs, 3))
    check("and says so rather than pretending", out.get("ok") is False
          and out.get("db") == "unreachable", out)
    check("the reason is carried, not swallowed", bool(out.get("db_error")), out)
finally:
    store.db = real_db

print("\n== a snapshot nobody has refreshed")
server._FACTS.clear()
server._FACTS.update({"at": time.time() - 3600, "ok": True})
out, _ = timed(server.health)
check("a stale snapshot is reported as stale", out.get("stale_s", 0) > 600, out)
server._FACTS.clear()
server._FACTS.update({"ok": False, "db": "not checked yet"})
out, _ = timed(server.health)
check("before the first refresh it claims nothing", out.get("ok") is False
      and "stale_s" in out, out)

print("\n== the endpoint does no database work of its own")
touched = []
store.db = lambda: touched.append(1) or Dead()
try:
    server._FACTS.clear()
    server._FACTS.update({"at": time.time(), "ok": True})
    server.health()
    check("health never calls store.db()", not touched, touched)
finally:
    store.db = real_db

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
