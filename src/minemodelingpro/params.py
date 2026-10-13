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
  power            inverse-distance power (default 2)
  method           "idw" (default) | "nn" (nearest neighbour) | "mean" (equal weights)
  top_cut          grade cap applied to composites (default: 99.5th percentile)
  top_cut_pct      percentile to cap at, when top_cut is not given
  density          tonnes per m³ (default: the report's, else by commodity)
  gap_fill         True (default): unreported hole length is composited as zero grade
  default_cutoff   cut-off the model page opens at
  elements         restrict/order the elements modelled
  max_elements     how many elements to model (default 3)
  bins             grade-legend breaks for the primary element

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
           "density", "default_cutoff"}
INTEGER = {"min_holes", "min_samples", "max_elements"}
BOOLEAN = {"gap_fill"}
LISTS = {"block", "elements", "bins"}
STRINGS = {"method", "name", "company", "note"}
ALL = NUMERIC | INTEGER | BOOLEAN | LISTS | STRINGS

LIMITS = {"block": (0.5, 200.0), "composite_m": (0.25, 50.0), "radius_m": (5.0, 2000.0),
          "max_gap_m": (1.0, 1000.0), "spacing_m": (1.0, 2000.0), "power": (0.0, 8.0),
          "top_cut": (0.0, 1e6), "top_cut_pct": (50.0, 100.0), "density": (1.0, 8.0),
          "min_holes": (1, 20), "min_samples": (1, 100), "max_elements": (1, 8),
          "default_cutoff": (0.0, 1e6), "bins": (0.0, 1e6)}


def load(path=PATH):
    try:
        return json.load(open(path))
    except Exception:
        return {}


def save(data, path=PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w"), indent=1, sort_keys=True)
    return path


def for_project(slug, path=PATH, extra=None):
    """Merged parameters for one project: file defaults < file entry < `extra`
    (e.g. settings carried on an imported dataset)."""
    data = load(path)
    out = copy.deepcopy(data.get("_defaults") or {})
    out.update(copy.deepcopy(data.get(slug) or {}))
    if extra:
        out.update({k: v for k, v in extra.items() if v is not None})
    return validate(out)[0]


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
            if k in LISTS:
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
