"""Per-project modelling parameters.

Every number the estimator uses has a default derived from the data (the technical
report's own parent block size, drill spacing, a 99.5th-percentile top-cut …).
This module lets any of them be overridden per project — from the web app, from
an imported dataset's settings, or by hand — and keeps those overrides in one
committed file, ``data/keep/mmp_model_params.json``:

    {"_defaults": {"power": 2},
     "murray-brook": {"block": [3, 3, 3], "radius_m": 60, "density": 3.6,
                      "elements": ["Cu", "Zn", "Pb"], "default_cutoff": 0.2}}

Keys (all optional; anything absent falls back to the data-driven default):

  block            [x, y, z] block size in metres (the report's parent block by default)
  composite_m      composite length down the hole (default: block height, max 5 m)
  radius_m         search radius (default ~1.6 x drill spacing)
  max_gap_m        furthest a block may sit from a real composite (default ~0.75 x spacing)
  spacing_m        override the measured drill spacing that drives the two above
  min_holes        distinct holes needed to estimate a block (default 2)
  min_samples      composites needed within the radius (default 3)
  max_per_hole     most samples one drill hole may contribute to a block (default 4) —
                   declustering: without it a single hole can supply every neighbour
  power            inverse-distance power (default 2)
  method           "idw" (default) | "nn" (nearest neighbour) | "mean" (equal weights)
  top_cut          grade cap applied to composites (default: 99.5th percentile)
  top_cut_pct      percentile to cap at, when top_cut is not given
  density          tonnes per m³ (default: the report's, else by commodity)
  gap_fill         True (default): unreported hole length is composited as zero grade
  gap_fill_margin_m  how far those zeros run past a reported interval (default: one
                   drill spacing) — they bound the deposit, they should not dilute it
  default_cutoff   cut-off the model page opens at
  value_cutoff     dollars-per-tonne cut-off (the report's NSR cut-off by default)
  elements         restrict/order the elements modelled
  max_elements     how many elements to model (default 3)
  bins             grade-legend breaks for the primary element
  deposit_style    "vein" | "vms" | "porphyry" | "orogenic" | "skarn" | "sedex" |
                   "iron" | "pegmatite" | "laterite" | "phosphate" | "bulk" — sets the
                   density, how far mineralisation carries, and the block size
  nsr              net-smelter-return formula {"factors": {"Cu": 81, ...}, "units": …},
                   so a polymetallic deposit can be cut in dollars per tonne rather
                   than on one metal's grade (the report's own formula by default)

CLI:
    python -m minemodelingpro.params --list
    python -m minemodelingpro.params murray-brook block=3,3,3 radius_m=60 density=3.6
    python -m minemodelingpro.params murray-brook --clear
"""
import os
import json
import copy

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(_ROOT, "data", "keep", "mmp_model_params.json")

NUMERIC = {"composite_m", "radius_m", "max_gap_m", "spacing_m", "power", "top_cut", "top_cut_pct",
           "density", "default_cutoff", "value_cutoff", "gap_fill_margin_m"}
INTEGER = {"min_holes", "min_samples", "max_elements", "max_per_hole"}
BOOLEAN = {"gap_fill"}
LISTS = {"block", "elements", "bins"}
STRINGS = {"method", "name", "company", "note", "deposit_style"}
# the net-smelter-return formula: {"factors": {el: $/t per unit grade}, "units": ...}.
# Not a number and not a list, so it travels as an object of its own.
OBJECTS = {"nsr", "metallurgy"}
ALL = NUMERIC | INTEGER | BOOLEAN | LISTS | STRINGS | OBJECTS

LIMITS = {"block": (0.5, 200.0), "composite_m": (0.25, 50.0), "radius_m": (5.0, 2000.0),
          "max_gap_m": (1.0, 1000.0), "spacing_m": (1.0, 2000.0), "power": (0.0, 8.0),
          "top_cut": (0.0, 1e6), "top_cut_pct": (50.0, 100.0), "density": (1.0, 8.0),
          "min_holes": (1, 20), "min_samples": (1, 100), "max_elements": (1, 8),
          "max_per_hole": (1, 24),
          "default_cutoff": (0.0, 1e6), "value_cutoff": (0.0, 1e5), "bins": (0.0, 1e6),
          "gap_fill_margin_m": (1.0, 1000.0)}


def load(path=PATH):
    try:
        return json.load(open(path))
    except Exception:
        return {}


def save(data, path=PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w"), indent=1, sort_keys=True)
    return path


ORIGINS = ("auto", "defaults", "project", "dataset")


def layered(slug, path=PATH, extra=None, base=None):
    """Merged parameters and where each one came from.

    Precedence, lowest first:
      ``base``   - derived automatically (the project's most recent technical
                   report, else what is typical for its region + commodity)
      defaults   - the ``_defaults`` entry in mmp_model_params.json
      project    - the per-slug entry in that file
      ``extra``  - settings carried on the dataset / sent by the web app

    Returns ``(merged, origin)`` where ``origin[key]`` is one of ORIGINS. Anything
    not tagged "auto" is a human choice, which is what the model page reports.
    """
    data = load(path)
    out, origin = {}, {}
    for src, layer in (("auto", base), ("defaults", data.get("_defaults")),
                       ("project", data.get(slug)), ("dataset", extra)):
        for k, v in copy.deepcopy(layer or {}).items():
            if v is None:
                continue
            out[k] = v
            origin[k] = src
    clean = validate(out)[0]
    return clean, {k: origin.get(k) for k in clean}


def for_project(slug, path=PATH, extra=None, base=None):
    """Merged parameters for one project (see `layered`)."""
    return layered(slug, path=path, extra=extra, base=base)[0]


def set_for(slug, values, path=PATH):
    data = load(path)
    clean, errors = validate(values)
    if errors:
        raise ValueError("; ".join(errors))
    entry = data.get(slug) or {}
    entry.update(clean)
    data[slug] = {k: v for k, v in entry.items() if v is not None}
    save(data, path)
    return data[slug]


def clear(slug, path=PATH):
    data = load(path)
    data.pop(slug, None)
    save(data, path)


def validate(values):
    """Coerce + range-check a parameter dict. Returns (clean, errors); unknown or
    out-of-range keys are dropped with an error message rather than raising, so a
    bad field from the web form can be reported back without losing the rest."""
    clean, errors = {}, []
    for k, v in (values or {}).items():
        if k.startswith("_"):
            continue
        if k not in ALL:
            errors.append(f"unknown parameter '{k}'")
            continue
        try:
            if k == "metallurgy":
                if not isinstance(v, dict):
                    errors.append("metallurgy: expected an object")
                    continue
            elif k in OBJECTS:
                if not isinstance(v, dict) or not isinstance(v.get("factors"), dict):
                    errors.append(f"{k}: expected an object with a 'factors' map")
                    continue
                fac = {}
                for el, f in v["factors"].items():
                    try:
                        f = float(f)
                    except (TypeError, ValueError):
                        continue
                    if 0 < f < 1e6:
                        fac[str(el)[:6]] = f
                if len(fac) < 2:
                    errors.append(f"{k}: needs at least two priced metals")
                    continue
                v = {**v, "factors": fac}
            elif k in LISTS:
                if isinstance(v, str):
                    v = [p for p in v.replace(";", ",").split(",") if p.strip()]
                v = list(v)
                if k == "elements":
                    v = [str(x).strip() for x in v if str(x).strip()]
                else:
                    v = [float(x) for x in v]
                    lo, hi = LIMITS[k]
                    if any(not (lo <= x <= hi) for x in v):
                        errors.append(f"{k}: values must be between {lo} and {hi}")
                        continue
                    if k == "block":
                        if len(v) == 1:
                            v = v * 3
                        if len(v) != 3:
                            errors.append("block: needs 1 or 3 numbers (x,y,z)")
                            continue
            elif k in BOOLEAN:
                v = str(v).strip().lower() not in ("0", "false", "no", "off", "")
            elif k in INTEGER:
                v = int(float(v))
            elif k in NUMERIC:
                v = float(v)
            else:
                v = str(v).strip()
                if k == "method" and v not in ("idw", "nn", "mean"):
                    errors.append("method: use idw, nn or mean")
                    continue
        except (TypeError, ValueError):
            errors.append(f"{k}: '{v}' is not valid")
            continue
        if k in LIMITS and k not in LISTS:
            lo, hi = LIMITS[k]
            if not (lo <= v <= hi):
                errors.append(f"{k}: must be between {lo} and {hi}")
                continue
        clean[k] = v
    return clean, errors


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if not a or a[0] in ("--list", "-l"):
        d = load()
        for k, v in sorted(d.items()):
            print(f"{k}: {json.dumps(v)}")
        if not d:
            print("(no project parameter overrides set)")
    elif len(a) >= 2 and a[1] in ("--clear", "-c"):
        clear(a[0]); print(f"cleared {a[0]}")
    else:
        slug, kv = a[0], dict(x.split("=", 1) for x in a[1:] if "=" in x)
        print(slug, json.dumps(set_for(slug, kv)))
