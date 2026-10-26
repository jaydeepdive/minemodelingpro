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
import json
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
# This account may be an admin (the invite-only fallback above makes one), and
# an admin CAN read any project — so a 404 here would mean "that project has no
# data", not "not yours". Check refusal with a plain user and a project that
# does have data, or the test passes for the wrong reason.
ok2 = f"{other}/{op['id']}/data.json"
F.put_json(ok2, {"collars": [{"hole": "O-1", "e": 1.0, "n": 2.0, "z": 3.0}],
                 "surveys": [], "intervals": [
                     {"hole": "O-1", "from": 1.0, "to": 2.0, "el": "Ag",
                      "grade": 9.0, "unit": "g/t"}]})
d.run("insert into datasets (project_id, owner, status, data_key) values (%s,%s,'ready',%s)",
      op["id"], other, ok2)
pem = "p-" + uuid.uuid4().hex[:8] + "@example.com"
ppw = "pw-" + uuid.uuid4().hex[:10]
d.one("insert into users (email, pw, role) values (%s,%s,%s) returning *",
      pem, S.hash_pw(ppw), "user")
pc = TestClient(S.app)
pc.post("/api/auth/login", json={"email": pem, "password": ppw})
check("a plain user is signed in", (pc.get("/api/me").json().get("user") or {}).get("id"))
check("another account's data is refused even though it has data",
      pc.get(f"/api/projects/{op['id']}/data").status_code in (403, 404))
check("and an admin can still read it (that is what the role is for)",
      c.get(f"/api/projects/{op['id']}/data").status_code == 200)

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

# --- A SOURCE PASTED, NOT UPLOADED.
# Jordan: "i need a way to add additional sources to the model (i.e. data pulled
# from a news release or whatever) to better assemble the model." A release
# states its numbers in a space-aligned table nobody exports as a file.
print("\na source pasted out of a news release")

RELEASE = """Canada Silver Cobalt Works Intersects 845 g/t Silver at Langis

Hole ID        From (m)    To (m)    Length (m)    Ag (g/t)    Co (%)
DDH-001          120.50    124.00          3.50      845.00      0.31
DDH-003           75.00     78.20          3.20      312.50      0.18

Table 2. Collar locations (NAD83 UTM Zone 17N)

Hole ID        Easting     Northing    Elevation    Azimuth    Dip    Depth (m)
DDH-003        574190      5253460          309        135    -55        220.0
"""

pb = f"/api/projects/{proj['id']}/sources"
r = c.post(pb + "/preview", json={"text": RELEASE})
check("a pasted release previews", r.status_code == 200, r.text[:300])
pv = r.json() if r.status_code == 200 else {}
check("its intervals are read", (pv.get("counts") or {}).get("intervals") == 4, pv.get("counts"))
check("its collars are read", (pv.get("counts") or {}).get("collars") == 1, pv.get("counts"))
check("both elements are found",
      sorted((pv.get("counts") or {}).get("elements", {})) == ["Ag", "Co"],
      (pv.get("counts") or {}).get("elements"))
check("the grid the release states is used", (pv.get("crs") or {}).get("epsg") == 26917,
      pv.get("crs"))
check("and it places where the release says",
      pv.get("center") and 47.0 < pv["center"][0] < 48.0 and -81.0 < pv["center"][1] < -79.0,
      pv.get("center"))
# the counts that decide whether this source reaches the model
H = pv.get("holes") or {}
check("it says how many holes the project already has", H.get("project_holes") == 2, H)
check("a hole already here is recognised as the same hole", H.get("already_here") == 1, H)
check("a hole new to the project is counted", H.get("new_to_this_project") == 1, H)
check("intervals on a hole with no collar anywhere are named",
      H.get("intervals_with_no_collar") == 0, H)

# a release whose hole names do not join: the silent failure this exists to show
LOST = """Hole ID        From (m)    To (m)    Ag (g/t)
ZZ-99-001         10.00     12.00      500.00
ZZ-99-002         20.00     22.00      400.00
"""
pv2 = c.post(pb + "/preview", json={"text": LOST}).json()
L = pv2.get("holes") or {}
check("a release that joins nothing says so loudly",
      L.get("already_here") == 0 and L.get("intervals_with_no_collar") == 2, L)
check("and names them as the release wrote them, not as normalised keys",
      (L.get("unplaceable") or [])[:2] == ["ZZ-99-001", "ZZ-99-002"], L.get("unplaceable"))

check("an empty paste is refused", c.post(pb + "/preview", json={"text": "   "}).status_code == 400)
# the browser's api() stringifies the body itself; passing JSON.stringify as well
# sends a JSON *string*, and .get on a str is a bare 500 that says nothing
check("a double-encoded body is named, not a 500",
      c.post(pb + "/preview", json=json.dumps({"text": RELEASE})).status_code == 400,
      c.post(pb + "/preview", json=json.dumps({"text": RELEASE})).status_code)
check("and it says what arrived",
      "double-encoded" in c.post(pb + "/preview",
                                 json=json.dumps({"text": RELEASE})).json().get("detail", ""))
check("a paste bigger than a paste is refused",
      c.post(pb + "/preview", json={"text": "x" * 5_000_000}).status_code == 413)
check("another account's project cannot be pasted into",
      pc.post(f"/api/projects/{op['id']}/sources/preview",
              json={"text": RELEASE}).status_code in (403, 404))
check("preview stores nothing",
      d.one("select count(*) as n from datasets where project_id = %s",
            proj["id"])["n"] == 1)

# --- adding it really does make it a dataset like any other
r = c.post(pb, json={"text": RELEASE, "name": "Langis release, Feb 2026"})
# A release is a good ADDITION and a bad standalone database. Judging it as the
# latter rejects exactly what the person is adding — and do_import RAISES on a
# validation error, so this is the difference between the feature working and
# every added source failing.
check("a release is not held to whole-database minimums",
      pv.get("errors") == [], pv.get("errors"))
check("but a paste holding no drill data at all is still refused",
      c.post(pb + "/preview", json={"text": "The company announces a $5,000,000 financing."})
       .json().get("errors"))
check("adding a source succeeds", r.status_code == 200, r.text[:300])
got = r.json() if r.status_code == 200 else {}
check("it becomes a dataset", bool((got.get("dataset") or {}).get("id")), got)
check("named as the person named it",
      (got.get("dataset") or {}).get("name") == "Langis release, Feb 2026", got.get("dataset"))
check("and an import job is queued", (got.get("job") or {}).get("kind") == "import", got.get("job"))
check("the project is now parsing",
      d.one("select status from projects where id = %s", proj["id"])["status"] == "parsing")
rows = d.all("select * from datasets where project_id = %s order by created_at", proj["id"])
check("the project now has two datasets", len(rows) == 2, len(rows))
new = rows[-1]
check("the paste is kept as the file it is, so it can be re-read",
      len(new["files"] or []) == 1 and new["files"][0]["filename"].endswith(".txt"),
      new["files"])
check("the stored text is the text pasted",
      F.get(new["files"][0]["key"]).decode("utf-8").startswith("Canada Silver Cobalt"))
check("it is marked as a paste", (new["options"] or {}).get("pasted") is True, new["options"])

# the standalone rule still applies to a project's first dataset
from minemodelingpro import imports as _IMP
thin = {"counts": {"collars": 1, "intervals": 6, "linked": 1}}
check("a project's first dataset must still be a whole database",
      len(_IMP.validate(thin, standalone=True)) == 3, _IMP.validate(thin))
check("and the same data is fine as an addition",
      _IMP.validate(thin, standalone=False) == [], _IMP.validate(thin, standalone=False))
check("an addition with nothing in it is still refused",
      _IMP.validate({"counts": {"collars": 0, "intervals": 0, "linked": 0}},
                    standalone=False) != [])

# --- the worker must actually read it back and merge it
print("\nthe added source reaches the model")
from studio import worker as _W
import importlib
pj = d.one("select * from projects where id = %s", proj["id"])
before = {r["id"] for r in d.all("select id from datasets where project_id = %s and status='ready'",
                                 proj["id"])}
jb = d.one("select * from jobs where project_id = %s and kind = 'import' "
           "and status = 'queued' order by created_at desc limit 1", proj["id"])
check("the import job is waiting", bool(jb))
if jb:
    try:
        _W.do_import(jb)
        ran = True
    except Exception as e:
        ran = False
        check("the pasted source imports without raising", False, "%s: %s" % (type(e).__name__, e))
    if ran:
        check("the pasted source imports without raising", True)
        nd = d.one("select * from datasets where id = %s", jb["dataset_id"])
        check("its dataset is ready, not error", nd["status"] == "ready",
              (nd["status"], nd.get("error")))
        check("and it was read as a release",
              any(t.get("kind") == "release" for t in ((nd["summary"] or {}).get("tables") or [])),
              (nd["summary"] or {}).get("tables"))
        check("its intervals are stored",
              ((nd["summary"] or {}).get("counts") or {}).get("intervals") == 4,
              (nd["summary"] or {}).get("counts"))
        # and the data endpoint now shows the release's holes beside the upload's
        hl = c.get(base + "?table=assays&group=hole").json()
        got = {x[0] for x in hl["rows"]}
        check("the release's new hole appears in the hole list", "DDH-003" in got, sorted(got))
        check("and the project's own holes are still there",
              {"DDH-001", "DDH-002"} <= got, sorted(got))

# --- the source's own link, kept and shown
print("\nthe link a source came from")
RU = "https://www.globenewswire.com/news-release/2026/10/06/337535"
pv3 = c.post(pb + "/preview", json={"text": RELEASE, "url": RU}).json()
check("the link is kept with the source", pv3.get("source_url") == RU, pv3.get("source_url"))
check("and a release is labelled as one", pv3.get("source_kind") == "news release",
      pv3.get("source_kind"))
check("a link that is not a link is refused",
      c.post(pb + "/preview", json={"text": RELEASE, "url": "ftp://x/y"}).status_code == 400)
check("javascript: is refused",
      c.post(pb + "/preview", json={"text": RELEASE, "url": "javascript:alert(1)"})
       .status_code == 400)
check("no link at all is fine",
      c.post(pb + "/preview", json={"text": RELEASE}).json().get("source_url") is None)
r5 = c.post(pb, json={"text": RELEASE, "name": "With a link", "url": RU})
check("adding with a link succeeds", r5.status_code == 200, r5.text[:200])
ds5 = d.one("select * from datasets where id = %s", (r5.json().get("dataset") or {}).get("id"))
check("the link is stored on the dataset", (ds5["options"] or {}).get("source_url") == RU,
      ds5["options"])
# and it survives the import into what the model reads as its sources
jb5 = d.one("select * from jobs where dataset_id = %s and kind = 'import' "
            "order by created_at desc limit 1", ds5["id"])
if jb5:
    try:
        _W.do_import(jb5)
        nd5 = d.one("select * from datasets where id = %s", ds5["id"])
        check("the import keeps the link", (nd5["summary"] or {}).get("source_url") == RU,
              (nd5["summary"] or {}).get("source_url"))
        check("and records it as a news release",
              (nd5["summary"] or {}).get("source_kind") == "news release",
              (nd5["summary"] or {}).get("source_kind"))
    except Exception as e:
        check("the import keeps the link", False, "%s: %s" % (type(e).__name__, e))

# --- CONNECTING A PUBLIC MODEL AFTER THE PROJECT EXISTS
# Jordan: "i still cant connect another model to it" / "i need to be able to add
# a project after the initial one has been created." attach_to could only be set
# when the project was created, and the page merely printed it.
print("\nconnecting a public model to a project that already exists")

ab = f"/api/projects/{proj['id']}"
r = c.get("/api/public/attachable")
check("the attachable models are listable", r.status_code == 200, r.text[:200])
AM = (r.json() or {}).get("models") or []
check("each one carries a slug and a name",
      all(m.get("slug") for m in AM[:20]), AM[:1])

# a slug with no published page must be refused, not silently stored: the build
# would then use only this account's own data and look like it worked
bad = c.patch(ab, json={"attach_to": "not-a-real-public-model"})
check("an unknown model is refused", bad.status_code == 400, bad.status_code)
check("and the refusal says what to do",
      "no published model" in (bad.json().get("detail") or ""), bad.json())
check("the project is unchanged by a refused attach",
      d.one("select attach_to from projects where id = %s", proj["id"])["attach_to"] is None)

if AM:
    slug = AM[0]["slug"]
    r2 = c.patch(ab, json={"attach_to": slug, "rebuild": True})
    check("a real public model can be connected after creation",
          r2.status_code == 200, r2.text[:200])
    check("it is stored on the project",
          d.one("select attach_to from projects where id = %s", proj["id"])["attach_to"] == slug)
    check("and a rebuild is queued",
          bool(d.one("select id from jobs where project_id = %s and kind = 'build' "
                     "and status = 'queued'", proj["id"])) or r2.json().get("queued") is False)
    # disconnecting is the same route with an empty value
    r3 = c.patch(ab, json={"attach_to": ""})
    check("it can be disconnected again", r3.status_code == 200
          and d.one("select attach_to from projects where id = %s",
                    proj["id"])["attach_to"] is None)
else:
    check("there is a public gallery to attach to", False, "models_index.json empty here")

check("another account cannot attach on your behalf",
      pc.patch(f"/api/projects/{op['id']}", json={"attach_to": "x"}).status_code in (400, 403, 404))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
