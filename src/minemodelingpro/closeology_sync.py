"""Mirror ALL data Closeology collects into MineModelingPro at build time.

MMP is entitled to every dataset the group collects, now and in the future. This
enumerates Closeology's committed ``data/keep`` and mirrors the data files into
``data/closeology/`` (gitignored) so MMP code can read any of them without the
two repos being coupled at the code level. Because it enumerates the directory,
datasets Closeology adds later are picked up automatically — no per-file wiring.

Two sources, tried in order:
  1. Local sibling repo   -- set CLOSEOLOGY_REPO=/path/to/closeology  (the Mac
     collector; always the full set, instant).
  2. GitHub               -- pulls jaydeepdive/closeology data/keep over the API
     (CI). Public repo needs no token; a private one needs CLOSEOLOGY_TOKEN.

As a convenience, the drill-news bank (drillbank.sqlite) is also dropped into
data/keep/ where export.py / model3d.py read it, UNLESS the MiningNewsTerminal API
is configured (drill_sync owns it then).

Run:  PYTHONPATH=src python -m minemodelingpro.closeology_sync
"""
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEST = os.path.join(_ROOT, "data", "closeology")
KEEP = os.path.join(_ROOT, "data", "keep")
OWNER_REPO = os.environ.get("CLOSEOLOGY_REPO_SLUG", "jaydeepdive/closeology")

# Directories under closeology/data/keep that are NOT data we want to mirror
_SKIP_DIRS = {"mmp", "sedar_pdfs", "sedar_profile", "ocr_cache", "_to_delete"}
# Only mirror real data files
_DATA_EXT = (".json", ".sqlite", ".parquet", ".csv", ".geojson")
_SKIP_SUFFIX = (".sqlite-journal",)


def _want(name):
    if name.endswith(_SKIP_SUFFIX):
        return False
    low = name.lower()
    if low.startswith("mmp_") or low.startswith("sedar_") or "reextract" in low:
        return False  # MMP owns these already
    return low.endswith(_DATA_EXT)


def _place_drillbank():
    """Make the mirrored drill bank visible where MMP reads it, unless the API owns it."""
    if os.environ.get("MNT_API_URL") and os.environ.get("MNT_API_KEY"):
        return
    src = os.path.join(DEST, "drillbank.sqlite")
    if os.path.exists(src):
        os.makedirs(KEEP, exist_ok=True)
        shutil.copy2(src, os.path.join(KEEP, "drillbank.sqlite"))
        print("[closeology_sync] placed drillbank.sqlite for MMP modelling")


def sync_local(repo):
    src_keep = os.path.join(repo, "data", "keep")
    if not os.path.isdir(src_keep):
        print(f"[closeology_sync] local repo has no data/keep: {src_keep}")
        return {"error": True}
    os.makedirs(DEST, exist_ok=True)
    n = 0
    for name in sorted(os.listdir(src_keep)):
        sp = os.path.join(src_keep, name)
        if os.path.isdir(sp) and name not in _SKIP_DIRS:
            # shallow-mirror one level of data files (e.g. facts subdirs)
            dd = os.path.join(DEST, name); os.makedirs(dd, exist_ok=True)
            for f in os.listdir(sp):
                if _want(f) and os.path.isfile(os.path.join(sp, f)):
                    shutil.copy2(os.path.join(sp, f), os.path.join(dd, f)); n += 1
        elif os.path.isfile(sp) and _want(name):
            shutil.copy2(sp, os.path.join(DEST, name)); n += 1
    print(f"[closeology_sync] local: mirrored {n} data file(s) from {repo} -> data/closeology/")
    _place_drillbank()
    return {"files": n}


def sync_github():
    import requests
    tok = os.environ.get("CLOSEOLOGY_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if tok:
        headers["Authorization"] = f"token {tok}"
    api = f"https://api.github.com/repos/{OWNER_REPO}/contents/data/keep"
    r = requests.get(api, headers=headers, timeout=45)
    if r.status_code != 200:
        print(f"[closeology_sync] github list failed HTTP {r.status_code}: {r.text[:120]}")
        return {"error": True}
    os.makedirs(DEST, exist_ok=True)
    n = 0
    for item in r.json():
        if item.get("type") == "file" and _want(item["name"]):
            dl = item.get("download_url")
            if not dl:
                continue
            resp = requests.get(dl, headers=headers, timeout=120)
            if resp.ok:
                open(os.path.join(DEST, item["name"]), "wb").write(resp.content); n += 1
    print(f"[closeology_sync] github: mirrored {n} data file(s) from {OWNER_REPO} -> data/closeology/")
    _place_drillbank()
    return {"files": n}


def sync():
    repo = os.environ.get("CLOSEOLOGY_REPO")
    if repo and os.path.isdir(repo):
        return sync_local(repo)
    try:
        return sync_github()
    except Exception as e:
        print(f"[closeology_sync] no source available ({str(e)[:80]}); skipping")
        return {"noop": True}


if __name__ == "__main__":
    out = sync()
    sys.exit(0 if not out.get("error") else 1)
