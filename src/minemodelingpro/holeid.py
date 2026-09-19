"""Drill-hole ID normalisation so the same hole matches across sources.

Reports, news releases and collar tables write the same hole many ways:
``MB-10-15`` / ``MB-2010-15`` / ``MB10-015`` / ``mb 10 15``. ``key()`` reduces
an ID to a canonical token string: alpha runs upper-cased, numeric runs as
integers (leading zeros dropped), 4-digit years 19xx/20xx folded to 2 digits,
separators removed. Used to join collars <-> intervals within a report and to
merge the same holes arriving from a report and from news releases.
"""
import re

_TOK = re.compile(r"[A-Za-z]+|\d+")


def key(hole_id):
    s = str(hole_id or "").strip()
    if not s:
        return ""
    toks = _TOK.findall(s)
    out = []
    for i, t in enumerate(toks):
        if t.isdigit():
            n = int(t)
            # a 4-digit year in a non-final position (MB-2010-15) -> 2 digits
            if len(t) == 4 and (1950 <= n <= 2099) and i < len(toks) - 1:
                n = n % 100
            out.append(str(n))
        else:
            out.append(t.upper())
    return "-".join(out)


def looks_like_hole(s):
    """A plausible hole id: short, has a digit, not just a number/decimal."""
    s = str(s or "").strip()
    if not s or len(s) > 24 or not re.search(r"\d", s):
        return False
    if re.fullmatch(r"[-+]?\d[\d,]*(\.\d+)?", s):          # a bare number
        return False
    if re.fullmatch(r"[\d.,\s/%-]+", s):
        return False
    low = s.lower()
    if low.startswith(("table", "figure", "page", "section", "zone ", "note")):
        return False
    return True
