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

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
