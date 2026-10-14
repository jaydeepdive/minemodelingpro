"""Typical modelling parameters by region and commodity.

A project's starting parameters come from its own most recent NI 43-101 technical
report (``projects.assemble`` carries them on the dataset as ``report_params``).
When a project has no report — a news-release-only deposit, or a client's own
upload — the fallback is what is *typical for comparable deposits*: the same
commodity in the same region, measured across every report MMP has extracted.

    >>> from minemodelingpro import regional
    >>> regional.typical("Quebec", "Au")
    ({'block': 5.0, ...}, 'typical for Au in Quebec (18 reports)')

The table is cheap to build (it reads the already-extracted report JSON) and is
written to ``data/keep/mmp_regional_params.json`` so the web app and the Studio
worker can prefill a form with the same numbers without re-reading the corpus.
"""
import os
import json
from collections import defaultdict

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(_ROOT, "data", "keep", "mmp_regional_params.json")

# the parameters worth generalising: the geometry and material properties of a
# deposit style. Search radius and composite length follow drill spacing, which
# is measured per project, so they are advisory only.
KEYS = ("block", "density", "composite_m", "radius_m")
_RANGE = {"block": (1.0, 50.0), "density": (1.5, 6.0),
          "composite_m": (0.25, 20.0), "radius_m": (10.0, 400.0)}
_SNAP = [1, 2, 2.5, 3, 4, 5, 6, 6.25, 7.5, 8, 10, 12, 12.5, 15, 20, 25, 30]
MIN_N = {"region+commodity": 2, "commodity": 3, "region": 3, "global": 5}
# density and the spacing-derived parameters are noisier than block geometry,
# so they need more reports in a group before they count as "typical"
MIN_K = {"block": 2, "density": 4, "composite_m": 3, "radius_m": 3}


def _snap(v):
    return float(min(_SNAP, key=lambda s: abs(s - v)))


def _params_of(r):
    """Parameters a report states, oldest key names included."""
    p = dict(r.get("model_params") or {})
    if r.get("block_size") and not p.get("block"):
        p["block"] = r["block_size"]
    if r.get("density") and not p.get("density"):
        p["density"] = r["density"]
    return {k: v for k, v in p.items() if k in KEYS and v is not None}


def _region_of(r):
    reg = r.get("region")
    if reg:
        return reg
    c = r.get("center") or r.get("text_center")
    if not c:
        return None
    try:
        from minemodelingpro import terrain
        return terrain.region_of(float(c[0]), float(c[1]))
    except Exception:
        return None


def _commodity_of(r):
    return (str(r.get("commodity") or "").split("-")[0] or None)


def build(reports=None, verbose=False):
    """Measure typical parameters from the extracted 43-101 corpus."""
    if reports is None:
        from minemodelingpro import projects as P
        reports = P.load_reports()
    bags = defaultdict(lambda: defaultdict(list))
    for r in reports:
        if r.get("source") == "import":
            continue                      # client data is not evidence of a norm
        p = _params_of(r)
        if not p:
            continue
        reg, el = _region_of(r), _commodity_of(r)
        for scope in (("region+commodity", (reg, el)), ("commodity", (el,)),
                      ("region", (reg,)), ("global", ())):
            kind, parts = scope
            if any(x is None for x in parts):
                continue
            bags[(kind, "|".join(parts))]["_n"].append(1)
            for k, v in p.items():
                bags[(kind, "|".join(parts))][k].append(v)
    out = {}
    for (kind, key), vals in bags.items():
        n = len(vals["_n"])
        if n < MIN_N[kind]:
            continue
        entry = {"n": n}
        for k in KEYS:
            got = [v for v in vals.get(k, []) if v is not None]
            if len(got) < max(MIN_K[k], MIN_N[kind]):
                continue
            lo, hi = _RANGE[k]
            if k == "block":
                arr = np.asarray([b for b in got if len(b) == 3 and
                                  all(lo <= float(x) <= hi for x in b)], float)
                if len(arr) < MIN_K[k]:
                    continue
                entry[k] = [_snap(v) for v in np.median(arr, 0)]
            else:
                arr = [float(v) for v in got if lo <= float(v) <= hi]
                if len(arr) < MIN_K[k]:
                    continue
                entry[k] = round(float(np.median(arr)), 2)
        if len(entry) > 1:
            out.setdefault(kind, {})[key] = entry
    _sane_density(out)
    if verbose:
        for kind in ("region+commodity", "commodity", "region", "global"):
            print(f"{kind}: {len(out.get(kind, {}))} groups")
    return out


def _sane_density(out):
    """Drop a group's density when it disagrees with the commodity as a whole.

    Reports quote mineral specific gravities alongside bulk density, so a small
    group can land on 4.1 t/m3 for a gold deposit. The commodity-wide median (or
    the global one) is built from many more reports, so it arbitrates."""
    gl = ((out.get("global") or {}).get("") or {}).get("density")
    com = {k: e.get("density") for k, e in (out.get("commodity") or {}).items()}
    for kind in ("region+commodity", "region", "commodity"):
        for key, e in list((out.get(kind) or {}).items()):
            d = e.get("density")
            if d is None:
                continue
            ref = com.get(key.split("|")[-1]) if kind == "region+commodity" else gl
            ref = ref or gl
            if ref and not (0.78 * ref <= d <= 1.28 * ref):
                e.pop("density", None)
                if len(e) <= 1:
                    out[kind].pop(key, None)


_TABLE = None


def table(refresh=False, reports=None):
    """The regional table, built once per process (cached file if present)."""
    global _TABLE
    if _TABLE is not None and not refresh:
        return _TABLE
    if not refresh:
        try:
            _TABLE = json.load(open(PATH))
            return _TABLE
        except Exception:
            pass
    _TABLE = build(reports=reports)
    try:
        save(_TABLE)
    except Exception:
        pass
    return _TABLE


def save(tbl=None, path=PATH):
    tbl = tbl if tbl is not None else table()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(tbl, open(path, "w"), indent=1, sort_keys=True)
    return path


def _norm(s):
    """Match region names loosely: case and accents vary between sources
    ("Quebec" from a form, "Québec" from the admin boundaries)."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s or ""))
                   if unicodedata.category(c) != "Mn").strip().lower()


def _lookup(tbl, kind, key):
    group = tbl.get(kind) or {}
    if key in group:
        return group[key], key
    want = _norm(key)
    for k, v in group.items():
        if _norm(k) == want:
            return v, k
    return None, key


def typical(region=None, commodity=None, tbl=None):
    """Typical parameters for a region + commodity, narrowest group that has
    enough evidence. Returns ``(params, label)``; label says what it is based on,
    for the model page. ``({}, None)`` when the corpus has nothing to offer."""
    tbl = tbl if tbl is not None else table()
    tries = []
    if region and commodity:
        tries.append(("region+commodity", f"{region}|{commodity}",
                      "typical for {c} in {r}"))
    if commodity:
        tries.append(("commodity", commodity, "typical for {c} deposits"))
    if region:
        tries.append(("region", region, "typical for deposits in {r}"))
    tries.append(("global", "", "typical across all 43-101 reports"))
    for kind, key, shape in tries:
        e, found = _lookup(tbl, kind, key)
        if e:
            parts = found.split("|")
            label = shape.format(r=parts[0], c=parts[-1] if len(parts) > 1 else commodity)
            p = {k: v for k, v in e.items() if k in KEYS}
            if p:
                return p, f"{label} ({e['n']} reports)"
    return {}, None


if __name__ == "__main__":
    import sys
    if "--build" in sys.argv:
        t = build(verbose=True)
        print("wrote", save(t))
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        p, label = typical(*(args + [None])[:2])
        print(label or "(nothing comparable)", json.dumps(p))
    elif "--build" not in sys.argv:
        t = table()
        for kind in ("region+commodity", "commodity", "region", "global"):
            for key, e in sorted((t.get(kind) or {}).items()):
                print(f"{kind:18} {key:28} {json.dumps(e)}")
