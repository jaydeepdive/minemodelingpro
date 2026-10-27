"""Keep the running app on the latest main, without anyone pressing deploy.

The app builds from the public GitHub URL, which DigitalOcean cannot receive push
webhooks for, and the source of an existing component cannot be switched to the
GitHub integration from the console. So the app watches the repository itself: the
worker runs this in a background thread, and when main moves — a code push, or the
nightly gallery build committing `site/` — it asks the Apps API for a new
deployment and records the commit it asked for.

Needs, as app-level environment variables:
    DO_DEPLOY_TOKEN   DigitalOcean API token with app read/write   (SECRET)
    DO_APP_ID         this app's id
    MMP_REPO          owner/repo to watch (default jaydeepdive/minemodelingpro)
    MMP_AUTODEPLOY    "0" to switch the whole thing off

This is the FALLBACK, not the primary. `.github/workflows/do-deploy.yml` asks for
a deployment within seconds of a push; this exists for the case that job cannot
cover — a push that never reaches Actions, or an Actions outage. Both deploying
at once is actively harmful: DigitalOcean cancels an in-progress deployment when
a newer one is created for the same app, so whichever asked second silently
killed the other's deployment and the loser reported a failure for an app that
had deployed fine (ab370d8, 2026-10-27). So this waits MMP_AUTODEPLOY_GRACE
seconds after a commit appears before acting on it, by which time the workflow
has either deployed it or has not.

Safeguards: it only ever deploys when the watched commit differs from the one it
last asked for, never while a deployment is already running, never until the
commit has had its grace period to be deployed by anyone else, and at most once
per MMP_AUTODEPLOY_MIN minutes.
"""
import os
import json
import time
import threading
import datetime
import urllib.request

REPO = os.environ.get("MMP_REPO", "jaydeepdive/minemodelingpro")
BRANCH = os.environ.get("MMP_BRANCH", "main")
APP_ID = os.environ.get("DO_APP_ID", "")
TOKEN = os.environ.get("DO_DEPLOY_TOKEN", "")
EVERY = float(os.environ.get("MMP_AUTODEPLOY_SECONDS", "300"))
MIN_GAP = float(os.environ.get("MMP_AUTODEPLOY_MIN", "10")) * 60
# How long a commit gets to be deployed by the workflow before this steps in.
GRACE = float(os.environ.get("MMP_AUTODEPLOY_GRACE", "900"))
API = "https://api.digitalocean.com/v2/apps"


def _get(url, token=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "mmp-autodeploy"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# content-only paths: the gallery is mirrored to object storage by studio.sitesync,
# so a commit that only touches these does not need the app rebuilt
CONTENT = ("site/", "data/")


def _age(d):
    """Seconds since the commit was made, or 0.0 if the date cannot be read."""
    t = (((d.get("commit") or {}).get("committer") or {}).get("date")
         or ((d.get("commit") or {}).get("author") or {}).get("date"))
    if not t:
        return 0.0
    try:
        when = datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 0.0
    return max(0.0, (datetime.datetime.utcnow() - when).total_seconds())


def head_commit():
    """The tip of the watched branch: (sha, subject, needs_deploy, age_s)."""
    d = _get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    sha = d.get("sha")
    msg = ((d.get("commit") or {}).get("message") or "").split("\n")[0][:120]
    files = [f.get("filename", "") for f in d.get("files") or []]
    code = [f for f in files if not f.startswith(CONTENT)]
    # no file list (very large commits) -> deploy, rather than miss a code change
    return sha, msg, (bool(code) or not files), _age(d)


def app_state():
    """(in-progress?, the commit the live deployment was built from)."""
    a = _get(f"{API}/{APP_ID}", TOKEN)["app"]
    busy = bool(a.get("in_progress_deployment"))
    live = a.get("active_deployment") or {}
    sha = ""
    for part in ("services", "workers", "static_sites", "jobs"):
        for c in live.get(part) or []:
            sha = c.get("source_commit_hash") or sha
    return busy, sha


def deploy():
    req = urllib.request.Request(f"{API}/{APP_ID}/deployments", data=b"{}", method="POST",
                                 headers={"Authorization": "Bearer " + TOKEN,
                                          "Content-Type": "application/json",
                                          "User-Agent": "mmp-autodeploy"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["deployment"]["id"]


def _mark(db, sha):
    db.run("insert into events (kind, detail) values ('autodeploy', %s)",
           json.dumps({"sha": sha, "at": datetime.datetime.utcnow().isoformat(timespec="seconds")}))


def _last_mark(db):
    r = db.one("select detail from events where kind = 'autodeploy' order by at desc limit 1")
    return ((r or {}).get("detail") or {}).get("sha") or ""


def loop(db):
    print(f"[autodeploy] watching {REPO}@{BRANCH} every {EVERY:g}s, "
          f"acting on a commit after {GRACE:g}s if nothing else has", flush=True)
    last_try = 0.0
    while True:
        time.sleep(EVERY)
        try:
            sha, msg, needs, age = head_commit()
            if not sha:
                continue
            busy, live = app_state()
            asked = _last_mark(db)
            if busy or sha in (live, asked):
                continue
            if not needs:
                _mark(db, sha)            # content only — sitesync carries it
                print(f"[autodeploy] {sha[:7]} touches only published content — no deploy", flush=True)
                continue
            if age and age < GRACE:
                # The workflow deploys within seconds of a push. Asking as well
                # cancels whichever deployment started first, which is how a
                # healthy deploy came to be reported as a failure.
                continue
            if time.time() - last_try < MIN_GAP:
                continue
            last_try = time.time()
            _mark(db, sha)
            dep = deploy()
            print(f"[autodeploy] main moved to {sha[:7]} ({msg}) — deployment {dep[:8]}", flush=True)
        except Exception as e:                      # never take the worker down
            print(f"[autodeploy] {type(e).__name__}: {str(e)[:160]}", flush=True)


def start(db):
    if (os.environ.get("MMP_AUTODEPLOY", "1") or "1").lower() in ("0", "false", "no"):
        print("[autodeploy] disabled", flush=True)
        return None
    if not (APP_ID and TOKEN):
        print("[autodeploy] DO_APP_ID / DO_DEPLOY_TOKEN not set — not watching", flush=True)
        return None
    t = threading.Thread(target=loop, args=(db,), daemon=True, name="autodeploy")
    t.start()
    return t
