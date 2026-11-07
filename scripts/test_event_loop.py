#!/usr/bin/env python3
"""Blocking work must never run on the web process's event loop.

This process runs ONE asyncio event loop. An `async def` endpoint runs on it,
so every millisecond it spends in Postgres, in scrypt or in object storage is a
millisecond in which the process answers no other request at all — not the
dashboard, not a static file, not /api/health. The edge waits and gives up with
a **504** while the container is up, the logs are quiet and health is green the
moment it is finally asked. That is the failure this file exists to prevent, and
it was live for weeks across eleven endpoints and the auth gate.

Measured before the fix: 24 concurrent logins stalled /api/health for 952 ms;
the same 24 through a sync endpoint stalled it 18 ms.

The guard is static and covers the whole class, so an endpoint added next year
cannot reintroduce it: an `async def` route handler may await the request body
and `off_loop`, and must touch nothing blocking itself.
"""
import ast
import asyncio
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "studio", "server.py")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- the static guard
# What "blocking" looks like in this server. Each is a call that waits on
# Postgres, on a password hash, on object storage, or on the importer.
BLOCKING = [
    (r"\bstore\.db\(\)", "a Postgres query"),
    (r"(?<![\w.])d\.(one|all|run)\(", "a Postgres query"),
    (r"\bworker_seen\(", "a Postgres query"),
    (r"\b(hash_pw|check_pw)\(", "scrypt"),
    (r"\bstore\.files\(\)\.", "object storage"),
    (r"(?<![\w.])F\.(put|usage|get|delete)\(", "object storage"),
    (r"\b_parse_pasted\(", "the importer"),
    (r"\bproject_or_404\(", "a Postgres query"),
    (r"\b_known_holes\(", "a Postgres query"),
    (r"\b_attachable_or_400\(", "a Postgres query"),
    (r"\bqueue\(", "a Postgres query"),
    (r"\bevent\(", "a Postgres query"),
]
# An async handler is allowed to await these.
ALLOWED_AWAIT = ("off_loop(", "call_next(", "request.json()", "_body(request)",
                 "f.read()", "request.form()", "request.body()")

src = open(SERVER).read()
tree = ast.parse(src)
lines = src.split("\n")


def seg(node):
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def is_route(node):
    for d in node.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "app":
            if f.attr in ("get", "post", "patch", "put", "delete"):
                return True
    return False


handlers = [n for n in ast.walk(tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and is_route(n)]
check("the route table was found", len(handlers) >= 25, f"found {len(handlers)}")

async_handlers = [n for n in handlers if isinstance(n, ast.AsyncFunctionDef)]
check("there are async handlers to check", len(async_handlers) >= 9,
      f"found {len(async_handlers)}")

# Every module-level sync function, so a call out of a handler can be followed.
SYNC_FNS = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def on_loop_text(fn, seen=None):
    """The code an async handler runs ON the loop.

    Not just its own body: `return _login(b)` runs _login's Postgres query and
    scrypt on the loop exactly as inlining them would, so a guard that reads
    only the handler is fooled by moving the work one call deep — which is how
    this was verified, by putting the fault back that way and watching the test
    pass. Calls are followed; what goes through `off_loop` is not.
    """
    seen = seen if seen is not None else set()
    if fn.name in seen or len(seen) > 40:
        return ""
    seen.add(fn.name)
    body = seg(fn)
    threaded = set()
    for node in ast.walk(fn):
        # off_loop(target, ...) -> target runs in a thread, not here
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "off_loop" and node.args:
            a = node.args[0]
            if isinstance(a, ast.Name):
                threaded.add(a.id)
            elif isinstance(a, ast.Attribute):
                threaded.add(a.attr)
    stripped = re.sub(r"await\s+off_loop\(.*?\n", "\n", body, flags=re.S)
    stripped = re.sub(r"await\s+off_loop\([^\n]*\)", "", stripped)
    out = [stripped]
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in threaded or name == "off_loop":
                continue
            callee = SYNC_FNS.get(name)
            if callee is not None and f"off_loop({name}" not in body:
                out.append(on_loop_text(callee, seen))
    return "\n".join(out)


for fn in async_handlers:
    stripped = on_loop_text(fn)
    found = [why for pat, why in BLOCKING if re.search(pat, stripped)]
    check(f"async {fn.name} does no blocking work on the loop",
          not found, "blocks on " + ", ".join(sorted(set(found))))

# every await in an async handler is one of the permitted kinds
for fn in async_handlers:
    for node in ast.walk(fn):
        if isinstance(node, ast.Await):
            txt = seg(node).strip()
            check(f"await in {fn.name} is permitted ({txt.split(chr(10))[0][:46]})",
                  any(a in txt for a in ALLOWED_AWAIT), txt[:90])

# the auth gate runs on every page view of every signed-in session
gate = [n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "gate"]
check("the auth gate was found", len(gate) == 1)
if gate:
    g = seg(gate[0])
    check("the gate calls me() through off_loop", "await off_loop(me, request)" in g)
    check("the gate never calls me() on the loop",
          not re.search(r"(?<!off_loop\()\bif me\(request\)", g))

# the helper is what it claims to be
check("off_loop is run_in_threadpool",
      re.search(r"^off_loop = run_in_threadpool", src, re.M) is not None)


# ------------------------------------------------------------- and it really threads
# A guard that trusts the name would pass if off_loop were `async def f(fn,*a):
# return fn(*a)`. So run a blocking call through it and watch the loop tick.
def _runtime():
    from studio import server

    async def ticker(stop, gaps):
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    async def main():
        out = {}
        for label, run in (("on the loop", None), ("through off_loop", server.off_loop)):
            stop, gaps = asyncio.Event(), []
            t = asyncio.ensure_future(ticker(stop, gaps))
            await asyncio.sleep(0.05)
            gaps.clear()
            if run is None:
                time.sleep(0.4)                       # a blocking call, on the loop
            else:
                await run(time.sleep, 0.4)            # the same call, threaded
            stop.set()
            await t
            out[label] = max(gaps) if gaps else 0.0
        return out

    return asyncio.new_event_loop().run_until_complete(main())


try:
    g = _runtime()
    print(f"\n  longest loop stall  on the loop {g['on the loop'] * 1000:.0f} ms"
          f"   through off_loop {g['through off_loop'] * 1000:.0f} ms")
    check("a blocking call on the loop does stall it (the fault, pinned)",
          g["on the loop"] > 0.3, f"{g['on the loop'] * 1000:.0f} ms")
    check("the same call through off_loop does not stall it",
          g["through off_loop"] < 0.1, f"{g['through off_loop'] * 1000:.0f} ms")
except Exception as e:                                           # noqa: BLE001
    check("the runtime check ran", False, f"{type(e).__name__}: {e}")


# -------------------------------------------- and the instrument that sees it
# /api/health never touches the database, so through every one of these 504s it
# answered 200 in a millisecond and polling it reported the site healthy. Loop
# lag is what makes the failure visible, so it is checked the only way worth
# checking: block the loop and see whether the number moves.
def _instrument():
    from studio import server

    async def main():
        server._LAG.clear()
        server.LOOP_LAG.update({"ms": 0.0, "worst_ms": 0.0, "worst_at": None})
        task = asyncio.ensure_future(server._loop_lag())
        await asyncio.sleep(0.6)
        quiet = server.loop_lag()["loop_lag_worst_5m_ms"]
        time.sleep(0.8)                                  # the fault, deliberately
        await asyncio.sleep(0.4)
        busy = server.loop_lag()
        task.cancel()
        return quiet, busy

    return asyncio.new_event_loop().run_until_complete(main())


try:
    quiet, busy = _instrument()
    print(f"\n  loop lag reported   quiet {quiet:.0f} ms"
          f"   after an 800 ms block {busy['loop_lag_worst_5m_ms']:.0f} ms")
    check("health reports loop lag", "loop_lag_ms" in busy)
    check("an idle loop reports near-zero lag", quiet < 100, f"{quiet:.0f} ms")
    check("a blocked loop is reported", busy["loop_lag_worst_5m_ms"] > 500,
          f"{busy['loop_lag_worst_5m_ms']:.0f} ms")
    check("the worst is stamped with when it happened", bool(busy["loop_lag_worst_at"]))
    check("the health endpoint carries the number",
          "loop_lag()" in open(SERVER).read().split("def health()")[1][:900])
except Exception as e:                                           # noqa: BLE001
    check("the instrument check ran", False, f"{type(e).__name__}: {e}")

print(f"\n{len(OK)} passed, {len(BAD)} failed")
if BAD:
    for b in BAD:
        print("  failed:", b)
sys.exit(1 if BAD else 0)
