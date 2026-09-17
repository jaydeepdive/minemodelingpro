"""Scale-proof, multi-file storage for the MMP data model.

GitHub caps any single file at 100 MB, and the full working store (mmp.sqlite)
already exceeds that — and the assay table will grow far larger as NI 43-101
extraction lands. So the DURABLE store is NOT one database: it is per-source
parquet shards, each kept safely under the limit and auto-split into parts if a
single source ever exceeds it. No data is ever dropped for size — a new source
is a new file, a growing table just adds parts.

Layout (all committed under data/keep/mmp/):
  collars/<source>[.NNN].parquet
  survey/<source>[.NNN].parquet
  assays/<source>[.NNN].parquet
  lithology/<source>[.NNN].parquet
  deposit_model/<source>[.NNN].parquet
  manifest.json         # shard inventory: table -> [{file, source, rows, bytes}]

The working sqlite (data/keep/mmp.sqlite) is rebuilt from these shards on demand
(rebuild_sqlite) and is NOT committed. Consumers read shards directly via
load_table(); the modelling workflow never needs the monolith.
"""
import os
import glob
import json
import shutil
import sqlite3
import datetime

import pandas as pd

from minemodelingpro import store

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEEP = os.path.join(_ROOT, "data", "keep")
SHARD_DIR = os.path.join(KEEP, "mmp")
MANIFEST = os.path.join(SHARD_DIR, "manifest.json")
MAX_BYTES = 90 * 1024 * 1024          # keep every shard safely under GitHub's 100 MB
TABLES = ["collars", "survey", "assays", "lithology", "deposit_model", "model_method", "metallurgy", "economics"]


def _safe(source_id):
    return str(source_id).replace(":", "__").replace("/", "_")


def _write_one(path, df):
    df.to_parquet(path, index=False)
    return os.path.getsize(path)


def _write_shards(table, source_id, df, manifest):
    """Write df for one source, splitting into .NNN parts if it exceeds MAX_BYTES."""
    tdir = os.path.join(SHARD_DIR, table)
    os.makedirs(tdir, exist_ok=True)
    base = _safe(source_id)
    if df.empty:
        return
    # write whole; if too big, split by row count into N even parts (recursively safe)
    tmp = os.path.join(tdir, f"{base}.parquet")
    size = _write_one(tmp, df)
    if size <= MAX_BYTES:
        manifest.setdefault(table, []).append(
            {"file": os.path.relpath(tmp, KEEP), "source": source_id, "rows": len(df), "bytes": size})
        return
    os.remove(tmp)
    parts = max(2, (size // MAX_BYTES) + 1)
    rows_per = (len(df) + parts - 1) // parts
    for i in range(parts):
        chunk = df.iloc[i * rows_per:(i + 1) * rows_per]
        if chunk.empty:
            continue
        p = os.path.join(tdir, f"{base}.{i:03d}.parquet")
        b = _write_one(p, chunk)
        manifest.setdefault(table, []).append(
            {"file": os.path.relpath(p, KEEP), "source": source_id, "rows": len(chunk), "bytes": b})


def export_shards(db=None):
    """Full rewrite of the shard store from the working sqlite (which holds
    everything). Idempotent: clears the shard dirs and rewrites all sources."""
    db = db or store.DB_PATH
    if not os.path.exists(db):
        print("[shards] no working store to export"); return {}
    con = sqlite3.connect(db)
    manifest = {}
    for table in TABLES:
        tdir = os.path.join(SHARD_DIR, table)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir)
        try:
            srcs = [r[0] for r in con.execute(f"SELECT DISTINCT source_id FROM {table}")]
        except sqlite3.OperationalError:
            continue
        for s in srcs:
            df = pd.read_sql_query(f"SELECT * FROM {table} WHERE source_id=?", con, params=[s])
            _write_shards(table, s, df, manifest)
    con.close()
    os.makedirs(SHARD_DIR, exist_ok=True)
    meta = {"generated": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "max_bytes": MAX_BYTES, "tables": manifest,
            "totals": {t: sum(x["rows"] for x in manifest.get(t, [])) for t in TABLES},
            "shard_count": sum(len(v) for v in manifest.values())}
    json.dump(meta, open(MANIFEST, "w"), indent=2)
    biggest = max((x["bytes"] for v in manifest.values() for x in v), default=0)
    print(f"[shards] wrote {meta['shard_count']} shards across {len([t for t in manifest])} tables; "
          f"rows={meta['totals']}; largest shard {biggest/1e6:.1f} MB (limit {MAX_BYTES/1e6:.0f})")
    return meta


def load_table(table, source=None):
    """Read a whole table (or one source) back as a DataFrame by concatenating
    its shards. This is how the modelling workflow consumes MMP — no monolith."""
    tdir = os.path.join(SHARD_DIR, table)
    files = sorted(glob.glob(os.path.join(tdir, "*.parquet")))
    if source is not None:
        pref = _safe(source)
        files = [f for f in files if os.path.basename(f) == f"{pref}.parquet"
                 or os.path.basename(f).startswith(pref + ".")]
    if not files:
        return pd.DataFrame()
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def rebuild_sqlite(db=None):
    """Rebuild the working sqlite from the committed shards (fast, offline)."""
    db = db or store.DB_PATH
    con = store.connect(db)
    for table in TABLES:
        df = load_table(table)
        if df.empty:
            continue
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        df = df[[c for c in df.columns if c in cols]]
        con.execute(f"DELETE FROM {table}")
        df.to_sql(table, con, if_exists="append", index=False)
    con.commit()
    s = store.stats(con)
    con.close()
    print(f"[shards] rebuilt sqlite: {s['collars']} collars, {s['assays']} assays from shards")
    return s


if __name__ == "__main__":
    import sys
    if sys.argv[1:] and sys.argv[1] == "rebuild":
        rebuild_sqlite()
    else:
        export_shards()
