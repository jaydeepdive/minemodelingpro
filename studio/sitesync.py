"""Serve the published site from Spaces, so new models don't need a deployment.

The gallery is rebuilt nightly by GitHub Actions, which commits `site/` to the repo.
Baking that into the container image means every refresh needs a build and a restart.
Instead:

    worker   mirror()  GitHub tarball ──► Spaces  published/…   (when main's site/ moves)
    web      pull()    Spaces ──► /tmp/mmp-site, served in place of the baked site/

Both run as threads, both are safe to run on several instances at once, and both fall
back to the image's own `site/` if Spaces is empty or unreachable.
"""
import os
import io
import json
import time
import hashlib
import tarfile
import threading
import urllib.request

from studio import store

REPO = os.environ.get("MMP_REPO", "jaydeepdive/minemodelingpro")
BRANCH = os.environ.get("MMP_BRANCH", "main")
PREFIX = os.environ.get("MMP_SITE_PREFIX", "published")
MANIFEST = PREFIX + "/manifest.json"
LOCAL = os.environ.get("MMP_SITE_CACHE", "/tmp/mmp-site")
PULL_EVERY = float(os.environ.get("MMP_SITE_PULL_SECONDS", "180"))
MIRROR_EVERY = float(os.environ.get("MMP_SITE_MIRROR_SECONDS", "600"))
SKIP = (".DS_Store",)


def _md5(b):
    return hashlib.md5(b).hexdigest()


def _manifest():
    try:
        return store.files().get_json(MANIFEST)
    except Exception:
        return None


# ------------------------------------------------------------------ publishing
def publish(site_dir="site", commit=None, verbose=True):
    """Upload everything under `site_dir` that Spaces doesn't already hold."""
    F = store.files()
    old = (_manifest() or {}).get("files") or {}
    files, sent, kept = {}, 0, 0
    for root, _dirs, names in os.walk(site_dir):
        for n in names:
            if n in SKIP:
                continue
            full = os.path.join(root, n)
            rel = os.path.relpath(full, site_dir).replace(os.sep, "/")
            with open(full, "rb") as f:
                body = f.read()
            h = _md5(body)
            files[rel] = {"md5": h, "bytes": len(body)}
            if old.get(rel, {}).get("md5") == h:
                kept += 1
                continue
            F.put(f"{PREFIX}/{rel}", body, _ctype(rel))
            sent += 1
    F.put_json(MANIFEST, {"commit": commit, "files": files,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    for rel in old:
        if rel not in files:
            try:
                F.remove(f"{PREFIX}/{rel}")
            except Exception:
                pass
    if verbose:
        print(f"[sitesync] published {len(files)} files ({sent} new/changed, {kept} unchanged)", flush=True)
    return len(files), sent


def _ctype(rel):
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    return {"html": "text/html; charset=utf-8", "js": "application/javascript", "css": "text/css",
            "json": "application/json", "svg": "image/svg+xml", "png": "image/png",
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "ico": "image/x-icon",
            "txt": "text/plain; charset=utf-8", "xml": "application/xml",
            }.get(ext, "application/octet-stream")


# ------------------------------------------------------------------- mirroring
def head_sha():
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                                 headers={"User-Agent": "mmp-sitesync"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode()).get("sha")


def mirror(sha=None, verbose=True):
    """Pull `site/` out of the repository tarball and publish it to Spaces."""
    sha = sha or head_sha()
    url = f"https://codeload.github.com/{REPO}/tar.gz/{sha}"
    req = urllib.request.Request(url, headers={"User-Agent": "mmp-sitesync"})
    if verbose:
        print(f"[sitesync] mirroring site/ at {sha[:7]}", flush=True)
    with urllib.request.urlopen(req, timeout=300) as r:
        blob = r.read()
    tmp = os.path.join("/tmp", "mmp-mirror")
    os.makedirs(tmp, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        root = tf.getnames()[0].split("/")[0]
        want = root + "/site/"
        members = [m for m in tf.getmembers() if m.name.startswith(want) and m.isfile()]
        for m in members:
            m.name = m.name[len(want):]
            tf.extract(m, tmp)
    n, sent = publish(tmp, commit=sha, verbose=verbose)
    return sha, n, sent


# -------------------------------------------------------------------- pulling
def _generated(path):
    """When a site directory's gallery was built (models_index.json), or ''."""
    try:
        return str(json.load(open(os.path.join(path, "models_index.json"))).get("generated") or "")
    except Exception:
        return ""


def fresher_than(baked, dest=LOCAL):
    """Is the mirrored copy at least as new as the one baked into the image? A newly
    deployed image can carry a gallery the mirror has not caught up with yet, and
    serving the stale one would look like the site going backwards."""
    a, b = _generated(dest), _generated(baked)
    return bool(a) and (not b or a >= b)


def pull(dest=LOCAL, verbose=False):
    """Bring the local copy in line with what Spaces holds. Returns the commit, or None."""
    man = _manifest()
    if not man or not man.get("files"):
        return None
    F = store.files()
    os.makedirs(dest, exist_ok=True)
    have = {}
    hp = os.path.join(dest, ".manifest.json")
    try:
        have = (json.load(open(hp)) or {}).get("files") or {}
    except Exception:
        have = {}
    got = 0
    for rel, meta in man["files"].items():
        local = os.path.join(dest, rel)
        if have.get(rel, {}).get("md5") == meta["md5"] and os.path.exists(local):
            continue
        try:
            body = F.get(f"{PREFIX}/{rel}")
        except Exception:
            continue
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as f:
            f.write(body)
        got += 1
    for rel in have:
        if rel not in man["files"]:
            try:
                os.remove(os.path.join(dest, rel))
            except OSError:
                pass
    json.dump(man, open(hp, "w"))
    if got and verbose:
        print(f"[sitesync] pulled {got} file(s) at {str(man.get('commit'))[:7]}", flush=True)
    return man.get("commit")


# -------------------------------------------------------------------- threads
def serve_loop(on_ready=None):
    """web: keep /tmp/mmp-site in step with Spaces."""
    last = None
    while True:
        try:
            c = pull(verbose=True)
            if c and c != last:
                last = c
                if on_ready:
                    on_ready(LOCAL, c)
        except Exception as e:
            print(f"[sitesync] pull: {type(e).__name__}: {str(e)[:140]}", flush=True)
        time.sleep(PULL_EVERY)


def mirror_loop():
    """worker: when the repo's site/ moves, mirror it to Spaces."""
    last = (_manifest() or {}).get("commit")
    while True:
        try:
            sha = head_sha()
            if sha and sha != last:
                mirror(sha)
                last = sha
        except Exception as e:
            print(f"[sitesync] mirror: {type(e).__name__}: {str(e)[:140]}", flush=True)
        time.sleep(MIRROR_EVERY)          # check first, then wait: a restart should not
                                          # delay the gallery by a whole interval


def start_server(on_ready=None):
    if not store.files().s3:
        print("[sitesync] no object storage — serving the image's own site/", flush=True)
        return None
    t = threading.Thread(target=serve_loop, args=(on_ready,), daemon=True, name="sitesync-pull")
    t.start()
    return t


def start_worker():
    if not store.files().s3:
        return None
    t = threading.Thread(target=mirror_loop, daemon=True, name="sitesync-mirror")
    t.start()
    return t


if __name__ == "__main__":
    import sys
    if "--mirror" in sys.argv:
        print(mirror())
    elif "--pull" in sys.argv:
        print(pull(verbose=True))
    else:
        print(publish(sys.argv[1] if len(sys.argv) > 1 else "site"))
