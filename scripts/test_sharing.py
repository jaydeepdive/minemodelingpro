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
# An address with no account used to be refused outright. It now WAITS — see
# the pending-share section below for why that is safe.
_nobody = "nobody-" + uuid.uuid4().hex[:6] + "@example.com"
_r = owner.post(P + "/shares", json={"email": _nobody})
check("sharing to an address with no account is held, not refused",
      _r.status_code == 200 and (_r.json().get("share") or {}).get("pending") is True,
      _r.text[:160])
owner.delete(P + f"/shares/{_nobody}")          # tidy up for the count below
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
      [x["email"] for x in owner.get(P + "/shares").json()["shares"]] == [m_email],
      owner.get(P + "/shares").json()["shares"])
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

# ------------------------------------------- a share that predates the account
# Inviting someone and then giving them a model was two trips, because a share
# attaches to a user id and there is no user until they accept. A share may now
# name an ADDRESS instead. The property that makes that safe is structural: every
# access check matches on user_id, and a pending row has none, so it grants
# nothing at all until the address becomes an account.
pend_email = "later-" + uuid.uuid4().hex[:8] + "@example.com"
r = owner.post(P + "/shares", json={"email": pend_email})
check("sharing with an address that has no account is accepted", r.status_code == 200, r.text)
check("and is reported as pending", (r.json().get("share") or {}).get("pending") is True,
      r.json())
check("the owner can see it waiting",
      any(x["email"] == pend_email and x["pending"] for x in
          owner.get(P + "/shares").json()["shares"]),
      owner.get(P + "/shares").json()["shares"])

# nothing is granted by a row with no account behind it
rows = d.all("select user_id, email from project_shares where project_id = %s "
             "and lower(email) = %s", pid, pend_email)
check("the pending row carries no user id", rows and rows[0]["user_id"] is None, rows)

later, lid, _le = account()          # a DIFFERENT account, same project
check("a stranger still cannot read it while a share waits for someone else",
      later.get(P).status_code == 404)

# now that address signs up, through the invite flow
inv = d.one("insert into invites (code, email, role, created_by) "
            "values (%s,%s,'client',%s) returning *",
            "c-" + uuid.uuid4().hex[:10], pend_email, oid)
newc = TestClient(S.app)
r = newc.post("/api/auth/signup", json={"email": pend_email, "password": "a-good-password-1",
                                        "invite": inv["code"]})
check("the invited address can sign up", r.status_code < 400, r.text[:200])
check("and the model is THERE when they first sign in",
      newc.get(P).status_code == 200)
check("it is in their project list straight away",
      any(str(x.get("id")) == pid for x in newc.get("/api/projects").json()["projects"]))
check("as a viewer, not an editor", newc.get(P).json().get("your_role") == "viewer")
check("the claimed row now names the account",
      (lambda rr: rr and rr[0]["user_id"] is not None and rr[0]["email"] is None)(
          d.all("select user_id, email from project_shares where project_id = %s "
                "and user_id = (select id from users where lower(email) = %s)",
                pid, pend_email)),
      "not claimed")
check("and it is no longer listed as pending",
      not any(x["email"] == pend_email and x["pending"]
              for x in owner.get(P + "/shares").json()["shares"]))

# a pending share can be taken back before it is ever claimed
gone_email = "gone-" + uuid.uuid4().hex[:8] + "@example.com"
owner.post(P + "/shares", json={"email": gone_email})
check("a waiting share can be removed by the address",
      owner.delete(P + f"/shares/{gone_email}").status_code == 200)
check("and it is gone", not any(x["email"] == gone_email
                                for x in owner.get(P + "/shares").json()["shares"]))

# an invite can carry the models it should land on
giv_email = "giv-" + uuid.uuid4().hex[:8] + "@example.com"
r = owner.post("/api/invites", json={"email": giv_email, "projects": [pid]})
if r.status_code == 403:
    check("inviting with a model attached (admin only here)", True, "owner is not admin")
else:
    check("an invite can carry a model", r.status_code == 200, r.text[:200])
    check("and says which", [g["id"] for g in (r.json().get("shared") or [])] == [pid],
          r.json().get("shared"))

# and a model the inviter does not own cannot be given away
other_proj = d.one("insert into projects (owner, slug, name, status) "
                   "values (%s,%s,%s,'ready') returning *",
                   xid, "notmine-" + uuid.uuid4().hex[:6], "Not mine")
r = owner.post("/api/invites", json={"email": "x-" + uuid.uuid4().hex[:6] + "@example.com",
                                     "projects": [str(other_proj["id"])]})
if r.status_code == 200:
    check("a model you do not own is refused, not quietly shared",
          [str(other_proj["id"])] == (r.json().get("refused") or []),
          r.json())
    check("and no share row was written for it",
          not d.all("select 1 from project_shares where project_id = %s",
                    other_proj["id"]))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  failed:", f)
sys.exit(1 if FAIL else 0)
