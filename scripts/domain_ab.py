"""Does the deposit domain help? Measured, per project, both ways.

The skeleton is meant to fix one specific failure: a model that covers a fraction
of its deposit because every block had to sit within a search distance of a
composite, so the model was a string of bubbles around individual intercepts
rather than a body. Three defects in it have already been found by measurement
and fixed, and after each it still scored exactly neutral — which is a reason to
keep measuring rather than a reason to ship it.

This builds the same project twice, with the domain off and on, and scores both
the same way the validation harness does: at the cut-off that reproduces the
filed grade, is the filed tonnage there? Run it on the projects whose data spans
a real part of the filed body, because those are the only ones where linking
intercepts is interpolation rather than invention.

    PYTHONPATH=src python scripts/domain_ab.py kinsley-copaur-minerals reid ...
    PYTHONPATH=src python scripts/domain_ab.py --spanning      # pick them by data
"""
import os
import sys
import json
import time
import pickle

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DS_PKL = os.environ.get("MMP_DS_PKL", "")


def datasets():
    if DS_PKL and os.path.exists(DS_PKL):
        return pickle.load(open(DS_PKL, "rb"))
    from minemodelingpro import projects
    return projects.assemble(verbose=False)


def score_one(m):
    """The filed statement, MMP at their grade, and the ratio."""
    from validate import published, at_their_grade, filed_element
    p = published(m)
    if not p:
        return None
    filed = float(p.get("mi_tonnes") or p.get("tonnes") or 0)
    cats = p.get("categories") or {}
    fg = None
    for k in ("M+I", "Total", "Measured", "Indicated", "Inferred"):
        v = (cats.get(k) or {}).get("grade")
        if v:
            fg = float(v)
            break
    el = filed_element(m, p) or m.get("primary")
    if not (filed and fg and el):
        return None
    got = at_their_grade(m, fg, el)
    if not got or not got.get("tonnes"):
        return None
    p = {"tonnes": filed, "grade": fg}
    return {"el": el, "filed_t": float(p["tonnes"]), "filed_g": float(p["grade"]),
            "mmp_t": float(got["tonnes"]), "cut": got["cut"],
            "clamped": got.get("clamped"),
            "ratio": float(got["tonnes"]) / float(p["tonnes"])}


def build(ds, domain):
    from minemodelingpro import model3d
    st = {"domain": True} if domain else {"domain": False}
    return model3d.build_project(ds, settings=st, verbose=False)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ds = datasets()
    by = {d["slug"]: d for d in ds}
    if "--spanning" in sys.argv:
        # the domain's own case: a wide, deep footprint tested by few holes, where
        # the ground between holes is most of the deposit
        want = []
        for d in ds:
            H = d.get("holes") or {}
            hs = list(H.values()) if isinstance(H, dict) else list(H)
            hs = [h for h in hs if isinstance(h, dict)
                  and h.get("x") is not None and h.get("y") is not None]
            if len(hs) < 6 or len(hs) > 400:
                continue
            xs = [h["x"] for h in hs]
            ys = [h["y"] for h in hs]
            span = max(max(xs) - min(xs), max(ys) - min(ys))
            if span > 600 and span / max(len(hs), 1) > 8:
                want.append(d["slug"])
        args = want
    if not args:
        print(__doc__)
        return
    rows = []
    for slug in args:
        d = by.get(slug)
        if not d:
            print(f"{slug}: no dataset")
            continue
        out = {"slug": slug, "holes": len(d.get("holes") or ())}
        for name, dom in (("off", False), ("on", True)):
            t0 = time.time()
            try:
                m = build(d, dom)
            except Exception as e:
                out[name] = {"error": f"{type(e).__name__}: {str(e)[:90]}"}
                continue
            s = score_one(m)
            el = m.get("primary")
            bl = ((m.get("elements") or {}).get(el) or {}).get("blocks") or {}
            dinfo = ((bl.get("params") or {}).get("domain")) or {}
            out[name] = {"blocks": bl.get("n_total"), "secs": round(time.time() - t0, 1),
                         "score": s, "domain": dinfo,
                         "coverage": (m.get("coverage") or {}).get("volume_ratio")}
        a = ((out.get("off") or {}).get("score") or {}).get("ratio")
        b = ((out.get("on") or {}).get("score") or {}).get("ratio")
        out["moved"] = (round(b - a, 3) if (a is not None and b is not None) else None)
        rows.append(out)
        print(f"{slug[:28]:28} off={_f(a)} on={_f(b)} "
              f"blocks {(out.get('off') or {}).get('blocks')} -> "
              f"{(out.get('on') or {}).get('blocks')} "
              f"links={(out.get('on') or {}).get('domain', {}).get('links')}", flush=True)
    good = [r for r in rows if r["moved"] is not None]
    if good:
        import statistics
        def closeness(x):
            return abs(1.0 - x) if x else 1.0
        off = statistics.median(closeness(r["off"]["score"]["ratio"]) for r in good)
        on = statistics.median(closeness(r["on"]["score"]["ratio"]) for r in good)
        print(f"\nscored {len(good)} project(s): median distance from 1.0 "
              f"off {off:.3f} -> on {on:.3f}  ({'better' if on < off else 'worse' if on > off else 'neutral'})")
    out_f = os.environ.get("MMP_AB_OUT", "/tmp/domain_ab.json")
    json.dump(rows, open(out_f, "w"), indent=1, default=str)
    print("wrote", out_f)


def _f(v):
    return "   -  " if v is None else f"{v:6.3f}"


if __name__ == "__main__":
    main()
