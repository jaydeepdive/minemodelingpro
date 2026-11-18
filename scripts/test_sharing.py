#!/usr/bin/env python3
"""Showing one private model to one named account, and to nobody else.

`publish` already existed and is a different thing: it hands a model to anyone
holding the link. A client deliverable needs neither the whole internet nor a
second copy of the data — it needs one named account to be able to open one
project. So this covers the part that is easy to get wrong and expensive to get
wrong: who can read, who can write, and who can do neither.

The rules under test:
  * a share lets another account READ the project and its model
  * a read share does NOT let them change anything
  * an edit share lets them change settings and rebuild
  * nobody but the owner may publish or delete, edit share or not
  * nobody but the owner may widen the circle by adding further shares
  * a stranger still sees nothing at all
  * removing a share removes access
  * a person may remove their own access

    DATABASE_URL=postgresql://postgres@127.0.0.1:5432/mmptest \
    PYTHONPATH=src python3 scripts/test_sharing.py
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
          (("  — " + str(detail)[:160]) if detail and not cond else ""))


d = store.db()
d.init_schema()


def account(role="client"):
    """A signed-in client for a fresh account."""
    email = "s-" + uuid.uuid4().hex[:8] + "@example.com"
    pw = "pw-" + uuid.uuid4().hex[:10]
    d.one("insert into users (email, pw, role) values (%s,%s,%s) returning *",
          email, S.hash_pw(pw), role)
    cl = TestClient(S.app)
    r = cl.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code < 400, r.text[:200]
    uid = (cl.get("/api/me").json().get("user") or {}).get("id")
    return cl, uid, email


owner, oid, o_email = account()
mate, mid, m_email = account()
other, xid, x_email = account()

proj = d.one("insert into projects (owner, slug, name, status) "
             "values (%s,%s,%s,'ready') returning *",
             oid, "share-" + uuid.uuid4().hex[:6], "Share test")
pid = str(proj["id"])
P = f"/api/projects/{pid}"

check("the owner can read their own project", owner.get(P).status_code == 200)
check("a stranger cannot", other.get(P).status_code == 404)
check("and neither can the person it is about to be shared with",
      mate.get(P).status_code == 404)

# ----------------------------------------------------------------- read share
r = owner.post(P + "/shares", json={"email": m_email})
check("the owner can share it", r.status_code == 200, r.text)
check("sharing to an address with no account says so",
      owner.post(P + "/shares", json={"email": "nobody-" + uuid.uuid4().hex[:6]
                                      + "@example.com"}).status_code == 404)
check("and sharing it to its own owner is refused",
      owner.post(P + "/shares", json={"email": o_email}).status_code == 400)

check("the share lets them read it", mate.get(P).status_code == 200)
check("it tells them they are a viewer",
      mate.get(P).json().get("your_role") == "viewer",
      mate.get(P).json().get("your_role"))
check("and names who shared it", mate.get(P).json().get("owner_email") == o_email)
check("it appears in their project list",
      any(str(x.get("id")) == pid for x in mate.get("/api/projects").json()["projects"]))
check("marked as not theirs",
      [x for x in mate.get("/api/projects").json()["projects"]
       if str(x.get("id")) == pid][0].get("shared_with_me") is True)
check("the owner's own list does not call it shared with them",
      [x for x in owner.get("/api/projects").json()["projects"]
       if str(x.get("id")) == pid][0].get("shared_with_me") is False)
check("a stranger still sees nothing", other.get(P).status_code == 404)

# -------------------------------------------------------- read is read ONLY
check("a viewer cannot change settings",
      mate.patch(P, json={"params": {"density": 3.0}}).status_code == 403)
check("a viewer cannot rebuild", mate.post(P + "/build").status_code == 403)
check("a viewer cannot publish", mate.post(P + "/publish",
                                           json={"public": True}).status_code == 403)
check("a viewer cannot delete", mate.delete(P).status_code == 403)
check("a viewer cannot share it onward",
      mate.post(P + "/shares", json={"email": x_email}).status_code == 403)
check("and that did not quietly let the stranger in",
      other.get(P).status_code == 404)

# ----------------------------------------------------------------- edit share
r = owner.post(P + "/shares", json={"email": m_email, "can_edit": True})
check("the share can be upgraded to edit", r.status_code == 200, r.text)
check("which is reported as such", mate.get(P).json().get("your_role") == "editor")
check("an editor can change settings",
      mate.patch(P, json={"params": {"density": 3.0}}).status_code == 200)
check("an editor still cannot publish",
      mate.post(P + "/publish", json={"public": True}).status_code == 403)
check("an editor still cannot delete", mate.delete(P).status_code == 403)
check("an editor still cannot share it onward",
      mate.post(P + "/shares", json={"email": x_email}).status_code == 403)

# --------------------------------------------------------------- taking it back
check("the owner sees who it is shared with",
      [x["email"] for x in owner.get(P + "/shares").json()["shares"]] == [m_email])
check("a stranger cannot even list the shares",
      other.get(P + "/shares").status_code == 404)
check("the owner can take access away",
      owner.delete(P + f"/shares/{mid}").status_code == 200)
check("and it is gone", mate.get(P).status_code == 404)
check("and out of their list",
      not any(str(x.get("id")) == pid
              for x in mate.get("/api/projects").json()["projects"]))

# a person may always drop their own access
owner.post(P + "/shares", json={"email": m_email})
check("shared again", mate.get(P).status_code == 200)
check("a viewer may remove themselves",
      mate.delete(P + f"/shares/{mid}").status_code == 200)
check("and that worked", mate.get(P).status_code == 404)

owner.post(P + "/shares", json={"email": m_email})
check("but may not remove someone else",
      other.delete(P + f"/shares/{mid}").status_code == 403)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  failed:", f)
sys.exit(1 if FAIL else 0)
