"""How far does a vein continue? Measured against the filed statements.

`veins.build` splits a set of contacts into separate veins, and supports each
node, at one distance. It was being given the *link* reach — the tight distance
the old triangulation was allowed to close a face over, which exists to stop a
triangulated sheet spanning untested ground. The vein builder does not close
faces that way, so the tight reach bought nothing and cost continuity: at Enchi
it cut a 121-hole vein into bodies the zone finder had already decided were one.

The alternative is the zone finder's own continuity distance — the distance at
which two holes are called the same body. Loosening a reach is exactly what put
Blue Moon at 121x its filed tonnage once before, so it is not a judgement call:
this builds every project that has a filed statement both ways and scores both.

    PYTHONPATH=src MMP_DS_PKL=/tmp/mmp-ds.pkl python3 scripts/vein_reach_ab.py
"""
import os
import sys
import json
import time
import math
import pickle
import statistics

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

# Three arms, because the reach turned out to carry a passenger. `reach` was
# being used for two unrelated things: how far a vein continues, and how finely
# it is drawn (the lattice step has a floor of reach/12). Raising the first
# therefore coarsened the second, and on the first twenty projects the triangle
# count roughly halved — a longer vein drawn more crudely, which is not the
# trade anyone asked for. So: the old reach; the new reach with that passenger
# still attached; and the new reach with the lattice sized by the data instead.
ARMS = (("ball", {"MMP_VEINSPLIT": "ball"}),
        ("planes", {}))


def datasets():
    p = os.environ.get("MMP_DS_PKL", "")
    if p and os.path.exists(p):
        return pickle.load(open(p, "rb"))
    from minemodelingpro import projects
    return projects.assemble(verbose=False)


def score_one(m):
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
    return {"filed_t": filed, "mmp_t": float(got["tonnes"]),
            "ratio": float(got["tonnes"]) / filed}


def shape(m):
    """What the surface builder actually produced, so a tonnage move can be read."""
    el = m.get("primary")
    bl = ((m.get("elements") or {}).get(el) or {}).get("blocks") or {}
    d = ((bl.get("params") or {}).get("domain")) or {}
    sf = ((m.get("elements") or {}).get(el) or {}).get("surface") or {}
    return {"bodies": d.get("bodies"), "no_surface": d.get("no_surface"),
            "method": sf.get("method"), "tris": int(sf.get("tris") or 0) // 3,
            "groups": int(sf.get("bodies") or 0),
            "from_veins": sf.get("from_veins"),
            "thick_med": ((sf.get("thickness_m") or {}).get("median")
                          or (sf.get("thickness_m") or {}).get("p50")),
            "blocks": bl.get("n_total")}


def build(ds):
    from minemodelingpro import model3d
    return model3d.build_project(ds, verbose=False)


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    ds = datasets()
    by = {d["slug"]: d for d in ds}
    if only:
        slugs = [s for s in only if s in by]
    else:
        v = json.load(open(os.path.join(_HERE, "..", "data", "keep",
                                        "mmp_validation.json")))
        slugs = [r["slug"] for r in v["projects"] if r.get("filed_tonnes")
                 and r["slug"] in by]
    print(f"{len(slugs)} project(s) with a filed statement\n", flush=True)
    rows = []
    for slug in slugs:
        row = {"slug": slug}
        for name, env in ARMS:
            for k in ("MMP_VEINREACH", "MMP_VEINRES", "MMP_VEINSPLIT"):
                os.environ.pop(k, None)
            os.environ.update(env)
            t0 = time.time()
            try:
                m = build(by[slug])
            except Exception as e:
                row[name] = {"error": f"{type(e).__name__}: {str(e)[:90]}"}
                continue
            row[name] = {"score": score_one(m), "shape": shape(m),
                         "secs": round(time.time() - t0, 1)}
        rows.append(row)
        def _r(n):
            return ((row.get(n) or {}).get("score") or {}).get("ratio")
        def _s(n, k):
            return ((row.get(n) or {}).get("shape") or {}).get(k)
        last = ARMS[-1][0]
        print(f"{slug[:28]:28} " + " ".join(f"{n}={_f(_r(n))}" for n, _ in ARMS)
              + f"   tris {_s('ball','tris')}->{_s(last,'tris')}"
                f"   no_surface {_s('ball','no_surface')}"
                f"->{_s(last,'no_surface')}", flush=True)
    summarise(rows)
    out = os.environ.get("MMP_AB_OUT", "/tmp/vein_reach_ab.json")
    json.dump(rows, open(out, "w"), indent=1, default=str)
    print("wrote", out)


def _f(v):
    return "   -  " if v is None else f"{v:6.3f}"


def summarise(rows):
    names = [n for n, _ in ARMS]
    got = [r for r in rows
           if all(((r.get(n) or {}).get("score") or {}).get("ratio") for n in names)]
    if not got:
        print("\nnothing scored")
        return
    print(f"\nscored {len(got)} project(s), every arm")
    for name in names:
        rs = [r[name]["score"]["ratio"] for r in got]
        err = [abs(math.log(x)) for x in rs if x > 0]
        print(f"  {name:5} median ratio {statistics.median(rs):6.3f}   "
              f"median |log error| {statistics.median(err):5.3f}   "
              f"within 2x {sum(1 for x in rs if 0.5 <= x <= 2):2d}/{len(rs)}   "
              f"worse than 10x {sum(1 for x in rs if x > 10 or x < 0.1):2d}")
    last = names[-1]
    tr = [(r[n]["shape"] or {}).get("tris") or 0 for r in got for n in (last,)]
    tl = [(r["ball"]["shape"] or {}).get("tris") or 0 for r in got]
    print(f"  triangles, link -> {last}: {sum(tl):,} -> {sum(tr):,}")
    moved = [(r["slug"], r["ball"]["score"]["ratio"], r[last]["score"]["ratio"])
             for r in got
             if abs(r[last]["score"]["ratio"] - r["ball"]["score"]["ratio"]) > 0.02]
    print(f"  {len(moved)} project(s) moved at all")
    for s, a, b in sorted(moved, key=lambda t: -abs(t[2] - t[1]))[:12]:
        worse = abs(1 - b) > abs(1 - a)
        print(f"    {s[:30]:30} {a:7.3f} -> {b:7.3f}  {'worse' if worse else 'better'}")


if __name__ == "__main__":
    main()
