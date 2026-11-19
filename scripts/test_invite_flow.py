#!/usr/bin/env python3
"""Adding someone must be one field and one link.

The admin page asked for four things — email (marked optional), validity in
days, role, and a note — and then handed back a bare invite CODE under a
heading telling you to send it to the client. The person at the other end then
had to find the sign-in page, notice the "I have an invite code" toggle, switch
it to sign-up, and type the code in by hand: three chances to get it wrong
before anyone had an account.

The link that does all of that already existed in this page; it was just shown
as plain text under four fields nobody needed. So this drives the whole thing
end to end, as both people, because the question "is it actually one step now"
cannot be answered by reading the markup.

Needs a real Postgres and playwright:

    pg_ctlcluster 16 main start
    DATABASE_URL=postgresql://postgres@127.0.0.1:5432/mmptest \
    PYTHONPATH=src python3 scripts/test_invite_flow.py
"""
import os
import sys
import uuid
import time
import tempfile
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))
os.environ.setdefault("PGSSLMODE", "disable")
os.environ.setdefault("MMP_LOCAL_FILES", tempfile.mkdtemp(prefix="mmp-invite-"))

try:
    from playwright.sync_api import sync_playwright
except Exception:                                               # noqa: BLE001
    print("playwright is not installed — skipping")
    sys.exit(0)

from studio import store                                        # noqa: E402
import studio.server as S                                       # noqa: E402
import uvicorn                                                  # noqa: E402

d = store.db(); d.init_schema()
email = "admin-" + uuid.uuid4().hex[:6] + "@example.com"; pw = "pw-" + uuid.uuid4().hex[:10]
d.one("insert into users (email, pw, role) values (%s,%s,'admin') returning *", email, S.hash_pw(pw))
cfg = uvicorn.Config(S.app, host="127.0.0.1", port=8231, log_level="error")
srv = uvicorn.Server(cfg)
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(80):
    time.sleep(0.25)
    if srv.started: break
B = "http://127.0.0.1:8231"
client_email = "client-" + uuid.uuid4().hex[:6] + "@example.com"
OK, BAD = [], []
def check(n, c, d_=""):
    (OK if c else BAD).append(n)
    print(("  ok   " if c else "  FAIL ") + n + (("   " + str(d_)[:160]) if d_ and not c else ""))

with sync_playwright() as pw_:
    br = pw_.chromium.launch()
    p = br.new_page()
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    # sign in as admin
    p.goto(B + "/app/index.html", wait_until="networkidle")
    p.fill("[name=email]", email); p.fill("[name=password]", pw)
    p.click("#submit"); p.wait_for_timeout(1500)
    p.goto(B + "/app/admin.html", wait_until="networkidle"); p.wait_for_timeout(900)

    # how many fields does a person have to fill in?
    names = p.eval_on_selector_all("#inviteform input, #inviteform select",
                                   "els => els.map(e => e.name)")
    visible = [n for n in names
               if p.is_visible(f"#inviteform [name={n}]")]
    check("one visible field to fill in", visible == ["email"], f"visible={visible} of {names}")
    check("the extras are tucked away but still reachable",
          p.is_visible("#inviteform details summary") and set(names) >= {"days","role","note"},
          names)
    check("the button says what it does",
          p.inner_text("#inviteform button[type=submit]").strip() == "Create account",
          p.inner_text("#inviteform button[type=submit]"))

    p.fill("[name=email]", client_email)
    p.click("#inviteform button[type=submit]")
    p.wait_for_selector("#invlink", timeout=8000)
    url = p.input_value("#invlink")
    check("a ready-to-send link is produced", "/app/index.html?invite=" in url, url)
    check("the link carries their address", "email=" in url, url)
    check("and a Copy button is offered", p.is_visible("#copylink"))
    check("the form clears for the next one", p.input_value("[name=email]") == "",
          p.input_value("[name=email]"))

    # now be the client: open the link in a clean context
    c2 = br.new_context(); p2 = c2.new_page()
    e2 = []; p2.on("pageerror", lambda e: e2.append(str(e)))
    p2.goto(url, wait_until="networkidle"); p2.wait_for_timeout(800)
    check("the link opens straight into sign-up",
          p2.is_visible("[name=invite]"), "invite field hidden")
    check("the code is filled in for them",
          p2.input_value("[name=invite]") != "", p2.input_value("[name=invite]"))
    check("their email is filled in for them",
          p2.input_value("[name=email]").lower() == client_email.lower(),
          p2.input_value("[name=email]"))
    p2.fill("[name=password]", "a-good-password-123")
    p2.click("#submit"); p2.wait_for_timeout(2500)
    row = d.one("select id, email, role from users where lower(email) = lower(%s)", client_email)
    check("the account exists after one password", bool(row), row)
    check("and it is a client, not an admin", bool(row) and row["role"] == "client",
          row and row["role"])
    check("no page errors anywhere", not errs and not e2, (errs + e2)[:2])
    br.close()
print(f"\n{len(OK)} passed, {len(BAD)} failed")
for b in BAD: print("  failed:", b)
sys.exit(1 if BAD else 0)
