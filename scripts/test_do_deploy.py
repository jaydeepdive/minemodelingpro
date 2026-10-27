#!/usr/bin/env python3
"""The deploy workflow, run for real against a stand-in DigitalOcean API.

Two things deploy this app: this workflow, and the app's own watcher
(studio/autodeploy.py). DigitalOcean cancels an in-progress deployment when a
newer one is created for the same app, so when the two overlapped the loser saw
its own deployment go CANCELED and emailed a failure for an app that had
deployed perfectly well. That is what happened to ab370d8 on 2026-10-27, and it
is indistinguishable from a real failure unless the job asks what the app is
actually serving.

A workflow is shell inside YAML and neither half is checked by anything. This
extracts the step as the YAML parser sees it, points it at a fake Apps API, and
runs it under the flags Actions uses (bash -e -o pipefail) — which is how the
first draft was caught writing `attempt; rc=$?`, where -e ends the step before
the assignment ever runs.
"""
import os
import sys
import json
import time
import random
import socket
import subprocess
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "do-deploy.yml")
PASS, FAIL = [], []
SHA = "abc1234"

FAKE = textwrap.dedent('''
    import http.server, json, os
    SCEN = os.environ["SCEN"]; SHA = os.environ["SHA"]
    state = {"posts": 0}
    def app_doc():
        busy = SCEN == "busy_already" or (SCEN == "superseded_busy" and state["posts"] >= 1)
        sha = {"superseded_done": SHA}.get(SCEN, "0000000")
        return {"app": {"in_progress_deployment": ({"phase": "BUILDING"} if busy else None),
                        "active_deployment": {"services": [{"source_commit_hash": sha}]}}}
    def phase():
        return {"superseded_busy": "CANCELED", "superseded_done": "CANCELED",
                "cancelled_lost": "CANCELED", "plain_ok": "ACTIVE",
                "error_twice": "ERROR",
                "error_then_ok": "ERROR" if state["posts"] == 1 else "ACTIVE"}[SCEN]
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, o):
            b = json.dumps(o).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
        def do_GET(self):
            self._send({"deployment": {"phase": phase()}} if "/deployments/" in self.path
                       else app_doc())
        def do_POST(self):
            state["posts"] += 1
            self._send({"deployment": {"id": "d1"}})
    http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
''')


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


try:
    import yaml
except ImportError:
    print("pyyaml not available — skipping the deploy-workflow check")
    sys.exit(0)

wf = yaml.safe_load(open(WF, encoding="utf-8"))
step = wf["jobs"]["deploy"]["steps"][0]["run"]
check("the workflow is valid YAML with a deploy step", bool(step))

tmp = os.path.join("/tmp", "mmp-do-deploy-test")
os.makedirs(tmp, exist_ok=True)
open(os.path.join(tmp, "fake.py"), "w").write(FAKE)
# the stub stands in for the API, and the poll runs at test speed
script = (step.replace("https://api.digitalocean.com/v2/apps/$APP_ID",
                       "http://127.0.0.1:$PORT/v2/apps/$APP_ID")
              .replace("sleep 20", "sleep 0.05"))
open(os.path.join(tmp, "step.sh"), "w").write(script)
check("the step talks to the real API in the committed file",
      "api.digitalocean.com" in step, "the stub was committed by mistake")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run(scen):
    port = free_port()
    env = dict(os.environ, SCEN=scen, SHA=SHA, PORT=str(port))
    srv = subprocess.Popen([sys.executable, "fake.py"], cwd=tmp, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        r = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail",
                            "step.sh"], cwd=tmp, capture_output=True, text=True,
                           timeout=120,
                           env=dict(env, TOKEN="t", APP_ID="x", GITHUB_SHA=SHA))
        return r.returncode, (r.stdout + r.stderr).strip()
    finally:
        srv.kill()
        srv.wait()


CASES = [
    ("plain_ok", 0, "an ordinary deployment passes"),
    ("busy_already", 0, "a deployment already running is left alone"),
    ("superseded_busy", 0, "being superseded while another deployment runs is not a failure"),
    ("superseded_done", 0, "being superseded after the app reached our commit is not a failure"),
    ("cancelled_lost", 1, "a cancellation that left the app on another commit IS a failure"),
    ("error_then_ok", 0, "a build that errors once is retried"),
    ("error_twice", 1, "a build that errors twice is a real failure"),
]
for scen, want, name in CASES:
    rc, out = run(scen)
    check(name, rc == want, "exit %s, wanted %s\n%s" % (rc, want, out))

# ---- and the other half of the race: the app's own watcher stands down
sys.path.insert(0, os.path.join(ROOT, "studio"))
try:
    import autodeploy as AD
except Exception as e:                                   # psycopg absent, etc.
    print("  (studio.autodeploy not importable here: %s)" % type(e).__name__)
    AD = None
if AD is not None:
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    def doc(mins, field="committer"):
        t = (now - datetime.timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"commit": {field: {"date": t}}}

    check("the watcher holds off on a commit the workflow is still deploying",
          AD._age(doc(1)) < AD.GRACE)
    check("and acts on one the workflow has clearly not deployed",
          AD._age(doc(20)) >= AD.GRACE)
    check("the grace period is 15 minutes unless told otherwise", AD.GRACE == 900,
          AD.GRACE)
    # A commit whose date cannot be read must DEPLOY, not stall for ever: the
    # watcher is the fallback, and a fallback that never fires is not one.
    check("a commit with no readable date is never held back",
          AD._age({}) == 0.0 and
          AD._age({"commit": {"committer": {"date": "yesterday"}}}) == 0.0)
    check("the author date stands in when there is no committer date",
          AD._age(doc(30, "author")) >= AD.GRACE)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
