"""The client's own drill data must be readable from the project page.

Asked for four times before it existed, because every earlier version lived in
the 3D viewer. This covers the endpoint behind it: the three tables, the hole
filter, the element filter, paging, and that a project you do not own is not
readable.

    DATABASE_URL=postgresql://postgres@/mmptest?host=/var/run/postgresql&port=5433 \\
    PYTHONPATH=src python3 scripts/test_data_api.py
"""
import os
import sys
import uuid
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))
os.environ.setdefault("PGSSLMODE", "disable")
os.environ.setdefault("MMP_LOCAL_FILES", tempfile.mkdtemp(prefix="mmp-files-"))

from studio import store                                        # noqa: E402
import studio.server as S                                       # noqa: E402
from fastapi.testclient import TestClient                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  — " + str(detail)) if detail and not cond else ""))


d = store.db()
d.init_schema()
email = "d-" + uuid.uuid4().hex[:8] + "@example.com"
pw = "pw-" + uuid.uuid4().hex[:10]
c = TestClient(S.app)
r = c.post("/api/auth/signup", json={"email": email, "password": pw, "invite": ""})
if r.status_code >= 400:                       # invite-only: make the user directly
    import studio.server as _s
    u = d.one("insert into users (email, pw, role) values (%s,%s,%s) returning *",
              email, _s.hash_pw(pw) if hasattr(_s, "hash_pw") else pw, "admin")
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
check("signed in", r.status_code < 400, r.text[:200])
me = c.get("/api/me").json()
uid = (me.get("user") or {}).get("id")
check("session is live", bool(uid), me)

proj = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
             uid, "data-" + uuid.uuid4().hex[:6], "Data test")

# a dataset in exactly the shape the importer writes
F = store.files()
key = f"{uid}/{proj['id']}/data.json"
F.put_json(key, {
    "collars": [{"hole": "DDH-001", "e": 500000.0, "n": 5500000.0, "z": 300.0,
                 "az": 45.0, "dip": -60.0, "depth": 120.0},
                {"hole": "DDH-002", "e": 500020.0, "n": 5500010.0, "z": 302.0,
                 "az": 45.0, "dip": -60.0, "depth": 140.0}],
    "surveys": [{"hole": "DDH-001", "md": 0.0, "az": 45.0, "dip": -60.0},
                {"hole": "DDH-001", "md": 50.0, "az": 46.0, "dip": -61.0}],
    # the key is "el", as ivtable.from_rows reads it — not "element"
    "intervals": [
        {"hole": "DDH-001", "from": 10.0, "to": 11.0, "el": "Ag", "grade": 120.0, "unit": "g/t"},
        {"hole": "DDH-001", "from": 11.0, "to": 12.0, "el": "Ag", "grade": 240.0, "unit": "g/t"},
        {"hole": "DDH-002", "from": 30.0, "to": 31.5, "el": "Ag", "grade": 15.0, "unit": "g/t"},
        {"hole": "DDH-002", "from": 30.0, "to": 31.5, "el": "Co", "grade": 0.21, "unit": "%"},
    ],
})
d.run("insert into datasets (project_id, owner, status, data_key) values (%s,%s,'ready',%s)",
      proj["id"], uid, key)

base = f"/api/projects/{proj['id']}/data"
r = c.get(base + "?table=assays").json()
check("assays come back", r.get("total") == 4, r)
check("assay columns are named", r.get("columns", [])[:3] == ["hole", "from_m", "to_m"], r.get("columns"))
check("length is computed", any(row[3] == 1.5 for row in r["rows"]), r["rows"])
check("both elements are offered", r.get("elements") == ["Ag", "Co"], r.get("elements"))

r = c.get(base + "?table=assays&el=Co").json()
check("the element filter narrows", r["total"] == 1 and r["rows"][0][4] == "Co", r)

r = c.get(base + "?table=assays&q=ddh-002").json()
check("the hole filter is case-insensitive", r["total"] == 2, r)

r = c.get(base + "?table=collars").json()
check("collars come back with easting/northing", r["total"] == 2
      and r["columns"][1] == "easting" and r["rows"][0][1] == 500000.0, r)

r = c.get(base + "?table=surveys").json()
check("surveys come back", r["total"] == 2 and r["rows"][1][1] == 50.0, r)

r = c.get(base + "?table=assays&limit=2&offset=0").json()
r2 = c.get(base + "?table=assays&limit=2&offset=2").json()
check("paging returns different rows and the same total",
      len(r["rows"]) == 2 and len(r2["rows"]) == 2 and r["rows"] != r2["rows"]
      and r["total"] == r2["total"] == 4, (r["rows"], r2["rows"]))

check("the limit is bounded", c.get(base + "?limit=999999").json()["limit"] <= 1000)

# someone else's project is not readable
other = d.one("insert into users (email, pw, role) values (%s,%s,%s) returning *",
              "x-" + uuid.uuid4().hex[:8] + "@example.com", "x", "user")["id"]
op = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
           other, "other-" + uuid.uuid4().hex[:6], "Not mine")
check("another account's data is refused",
      c.get(f"/api/projects/{op['id']}/data").status_code in (403, 404))

# a project with no parsed data says so rather than erroring
empty = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
              uid, "empty-" + uuid.uuid4().hex[:6], "Empty")
check("a project with no data says so",
      c.get(f"/api/projects/{empty['id']}/data").status_code == 404)

# --- at a real client's scale. The first version of this endpoint built a Row
# per interval to count and slice them, which on a 919,152-row assay file is a
# million objects in a web process and a 500 on the page. Nothing here may grow
# with the file; only with the page.
import time                                                      # noqa: E402
import resource                                                  # noqa: E402

N_HOLES, N_EL, PER = 600, 20, 60                                 # 720,000 intervals
big = []
for h in range(N_HOLES):
    hid = "LM-26-%03d" % h
    for i in range(PER):
        for e in range(N_EL):
            big.append({"hole": hid, "from": float(i), "to": float(i) + 1.0,
                        "el": "E%02d" % e, "grade": 1.0 + i, "unit": "g/t"})
bigp = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
             uid, "big-" + uuid.uuid4().hex[:6], "Big")
bk = f"{uid}/{bigp['id']}/data.json"
F.put_json(bk, {"collars": [], "surveys": [], "intervals": big})
d.run("insert into datasets (project_id, owner, status, data_key) values (%s,%s,'ready',%s)",
      bigp["id"], uid, bk)
del big

bb = f"/api/projects/{bigp['id']}/data"
m0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t0 = time.time()
r = c.get(bb + "?table=assays&limit=200")
dt = time.time() - t0
m1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
check("a 720,000-row assay file does not 500", r.status_code == 200, r.status_code)
j = r.json() if r.status_code == 200 else {}
check("it reports the full row count", j.get("total") == N_HOLES * N_EL * PER,
      j.get("total"))
check("it returns only the page asked for", len(j.get("rows") or []) == 200,
      len(j.get("rows") or []))
check("the first page is quick", dt < 20, "%.1fs" % dt)
check("every element is listed", len(j.get("elements") or []) == N_EL,
      len(j.get("elements") or []))

t0 = time.time()
r2 = c.get(bb + "?table=assays&offset=700000&limit=200").json()
check("a deep page works and costs no more",
      len(r2["rows"]) == 200 and r2["total"] == j["total"] and time.time() - t0 < 20)
check("a deep page is a different page", r2["rows"] != j["rows"])

r3 = c.get(bb + "?table=assays&q=LM-26-005&limit=50").json()
check("the hole filter works at scale",
      r3["total"] == N_EL * PER and all(x[0] == "LM-26-005" for x in r3["rows"]),
      (r3["total"], r3["rows"][:1]))
r4 = c.get(bb + "?table=assays&el=E03&limit=10").json()
check("the element filter works at scale",
      r4["total"] == N_HOLES * PER and all(x[4] == "E03" for x in r4["rows"]),
      r4["total"])
print("    (peak RSS %d MB -> %d MB across the page requests)"
      % (m0 // 1024, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024))

# --- THE SHAPE THE WORKER ACTUALLY WRITES.
# do_import stores IVT.to_store(dataset), whose "intervals" is a dict of deflated
# base64 buffers, not a list of rows. Every fixture above is a list, so the
# endpoint shipped reading rows it never gets and 500'd on the real file. A test
# that does not use the production writer is not testing production.
from minemodelingpro import ivtable as IVT                       # noqa: E402

stored = IVT.to_store({
    "collars": [{"hole": "SM-001", "e": 1.0, "n": 2.0, "z": 3.0,
                 "az": 10.0, "dip": -50.0, "depth": 90.0}],
    "surveys": [{"hole": "SM-001", "md": 0.0, "az": 10.0, "dip": -50.0}],
    "intervals": [
        {"hole": "SM-001", "from": 1.0, "to": 2.0, "el": "Ag", "grade": 310.0, "unit": "g/t"},
        {"hole": "SM-001", "from": 2.0, "to": 3.5, "el": "Ag", "grade": 42.0, "unit": "g/t"},
        {"hole": "SM-001", "from": 2.0, "to": 3.5, "el": "Co", "grade": 0.4, "unit": "%"},
    ],
})
check("to_store really produces columns, not rows",
      isinstance(stored["intervals"], dict), type(stored["intervals"]).__name__)

sp = d.one("insert into projects (owner, slug, name) values (%s,%s,%s) returning *",
           uid, "stored-" + uuid.uuid4().hex[:6], "Stored shape")
sk = f"{uid}/{sp['id']}/data.json"
F.put_json(sk, stored)
d.run("insert into datasets (project_id, owner, status, data_key) values (%s,%s,'ready',%s)",
      sp["id"], uid, sk)

sb = f"/api/projects/{sp['id']}/data"
r = c.get(sb + "?table=assays")
check("the stored column shape does not 500", r.status_code == 200, r.text[:300])
j = r.json() if r.status_code == 200 else {}
check("its rows read back correctly", j.get("total") == 3, j)
check("its grades survive the round trip",
      any(abs((row[5] or 0) - 310.0) < 1e-9 for row in (j.get("rows") or [])), j.get("rows"))
check("its elements are listed", j.get("elements") == ["Ag", "Co"], j.get("elements"))
check("filters work on the stored shape",
      c.get(sb + "?table=assays&el=Co").json()["total"] == 1)
check("collars read from the stored shape",
      c.get(sb + "?table=collars").json()["total"] == 1)

# --- ONE LINE PER DRILL HOLE.
# Jordan, reading 144,774 rows of 1 m assays 200 at a time: "this is impossible
# to read. i need one line per drill hole, with the ability to expand the data on
# each hole." A metre of core assayed for twenty elements is ONE interval with
# twenty numbers on it, not twenty rows.
print("\none line per drill hole, expandable")

r = c.get(base + "?table=assays&group=hole").json()
check("the hole list has one row per hole", r.get("total") == 2 and len(r["rows"]) == 2, r)
check("it says it is grouped", r.get("group") == "hole", r.get("group"))
byh = {x[0]: x for x in r["rows"]}
check("holes come back in order", [x[0] for x in r["rows"]] == ["DDH-001", "DDH-002"],
      [x[0] for x in r["rows"]])
check("the sample count is the hole's own", byh["DDH-001"][1] == 2 and byh["DDH-002"][1] == 2,
      {k: v[1] for k, v in byh.items()})
check("a hole assayed for two elements reports two", byh["DDH-002"][2] == 2, byh["DDH-002"])
check("the depth range is the hole's own",
      byh["DDH-001"][3] == 10.0 and byh["DDH-001"][4] == 12.0, byh["DDH-001"])
# DDH-002 has ONE 1.5 m interval assayed for two elements: 1.5 m of core, not 3
check("assayed metres count core, not analyses", abs(byh["DDH-002"][5] - 1.5) < 1e-9,
      byh["DDH-002"])
check("and a hole with two separate metres reports two",
      abs(byh["DDH-001"][5] - 2.0) < 1e-9, byh["DDH-001"])

r = c.get(base + "?table=assays&group=hole&el=Ag").json()
check("picking an element adds its best and mean",
      r["columns"][-2:] == ["max Ag", "mean Ag"], r["columns"])
a1 = [x for x in r["rows"] if x[0] == "DDH-001"][0]
check("max is the hole's highest grade", a1[5] == 240.0, a1)
check("mean is the hole's average", abs(a1[6] - 180.0) < 1e-9, a1)
check("a hole without that element is left out of the list",
      all(x[0] != "NOPE" for x in r["rows"]))
check("the hole filter still applies when grouped",
      c.get(base + "?table=assays&group=hole&q=ddh-002").json()["total"] == 1)

# --- expanding one hole: wide, one row per interval
r = c.get(base + "?table=assays&hole=DDH-002&wide=1").json()
check("expanding a hole returns its intervals", r.get("total") == 1, r)
check("it is wide: one row for an interval assayed twice", len(r["rows"]) == 1, r["rows"])
check("each element is a column with its unit",
      r["columns"] == ["from (m)", "to (m)", "length (m)", "Ag (g/t)", "Co (%)"],
      r["columns"])
check("both grades sit on the one row", r["rows"][0][3] == 15.0 and r["rows"][0][4] == 0.21,
      r["rows"][0])
check("the length is the interval's", r["rows"][0][2] == 1.5, r["rows"][0])

r = c.get(base + "?table=assays&hole=ddh-001&wide=1").json()
check("the hole name is matched case-insensitively", r.get("total") == 2, r)
check("intervals come back in depth order",
      [x[0] for x in r["rows"]] == [10.0, 11.0], [x[0] for x in r["rows"]])
check("an element the hole lacks is still a column or absent, never a wrong number",
      all(len(x) == len(r["columns"]) for x in r["rows"]))
check("a hole that does not exist says so",
      c.get(base + "?table=assays&hole=NOT-A-HOLE&wide=1").status_code == 404)

r = c.get(base + "?table=assays&hole=DDH-002&wide=1&el=Co").json()
check("expanding with an element filter shows only it",
      r["columns"] == ["from (m)", "to (m)", "length (m)", "Co (%)"], r["columns"])

# surveys group and expand the same way
r = c.get(base + "?table=surveys&group=hole").json()
check("surveys group by hole too", r["total"] == 1 and r["rows"][0][1] == 2, r)
r = c.get(base + "?table=surveys&hole=DDH-001").json()
check("a hole's survey stations expand", r["total"] == 2 and r["rows"][1][0] == 50.0, r)

# --- and at a client's scale, where the point of this is
t0 = time.time()
r = c.get(bb + "?table=assays&group=hole&limit=200")
dt = time.time() - t0
check("the hole list does not 500 on 720,000 rows", r.status_code == 200, r.status_code)
j = r.json() if r.status_code == 200 else {}
check("it is 600 holes, not 720,000 rows", j.get("total") == N_HOLES, j.get("total"))
check("the first page is quick", dt < 25, "%.1fs" % dt)
check("a cached second call is immediate",
      (lambda t: t < 3)(
          (lambda s: (c.get(bb + "?table=assays&group=hole&limit=200"), time.time() - s)[1])
          (time.time())))
row = j["rows"][0]
check("each hole reports its own sample count", row[1] == N_EL * PER, row)
check("assayed metres are core metres, not analyses", abs(row[5] - PER) < 1e-6,
      (row[5], PER))
check("and all 20 elements are counted once", row[2] == N_EL, row)

t0 = time.time()
r = c.get(bb + "?table=assays&hole=LM-26-005&wide=1").json()
check("expanding one hole out of 600 is quick", time.time() - t0 < 10,
      "%.1fs" % (time.time() - t0))
check("it is PER rows, not PER x 20", r["total"] == PER, r["total"])
check("with a column for every element", len(r["columns"]) == 3 + N_EL,
      len(r["columns"]))

check("the stored column shape groups too",
      c.get(sb + "?table=assays&group=hole").json()["total"] == 1)
sw = c.get(sb + "?table=assays&hole=SM-001&wide=1").json()
check("and expands on the stored shape", sw["total"] == 2, sw)
check("a missing grade in a wide row is empty, not shifted",
      sw["rows"][0][3] == 310.0 and sw["rows"][0][4] is None, sw["rows"][0])

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
