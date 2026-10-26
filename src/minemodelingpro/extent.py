"""How big the deposit is, as the report itself describes it.

MMP's largest remaining error is not grade but reach: across the projects with a
filed statement, the grade-matched error correlates at +0.91 with how much of the
deposit's volume the model covers at all. Goldboro's model covers 0.2% of its
filed tonnage — not because the grades are wrong, but because the report publishes
eighty-four intercepts of a deposit drilled with fifteen hundred holes, and no
amount of interpolation between eighty-four points builds sixteen million tonnes.

What the report does publish, in prose, is the body's shape: "traced over a strike
length of 1,200 m, to a vertical depth of 450 m, with true widths of 2 to 15 m".
That is the qualified person's wireframe, stated in words instead of drawn — the
one piece of the model that cannot be recovered from the published assays. Reading
it gives two things MMP never had:

  * a bound on where the body may be carried between holes, so the domain can
    follow the vein along its strike for as far as the report says it goes and no
    further; and
  * an independent volumetric check — strike x depth x width x density — against
    which both MMP's tonnage and the filed statement can be judged, which is how a
    model that covers a twentieth of its deposit becomes visible as such rather
    than merely wrong.

Only stated numbers are used. Where a report says nothing, this says nothing.
"""
import re

_M_PER = {"m": 1.0, "metre": 1.0, "metres": 1.0, "meter": 1.0, "meters": 1.0,
          "km": 1000.0, "kilometre": 1000.0, "kilometres": 1000.0,
          "kilometer": 1000.0, "kilometers": 1000.0,
          "ft": 0.3048, "feet": 0.3048, "foot": 0.3048}
_U = r"(m|metres?|meters?|km|kilometres?|kilometers?|ft|feet)"
_N = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"


def _m(val, unit):
    try:
        return float(str(val).replace(",", "")) * _M_PER[unit.lower().rstrip(".")]
    except Exception:
        return None


# Each pattern names one dimension. Ranges ("2 to 15 m") keep both ends; a single
# number keeps one. Written to be linear: no lazy spans between optional groups,
# which is what made the coordinate reader take minutes on a page of degree signs.
_PATS = [
    ("strike", re.compile(r"strike\s+length\s+of\s+(?:approximately\s+|about\s+|some\s+|over\s+|~)?"
                          + _N + r"\s*" + _U, re.I)),
    ("strike", re.compile(r"(?:traced|defined|delineated|identified|extends?|extending|"
                          r"continuous)\s+(?:\w+\s+){0,4}?(?:over|for|along)\s+"
                          r"(?:approximately\s+|about\s+|some\s+|~)?" + _N + r"\s*" + _U
                          + r"\s+(?:of\s+)?(?:along\s+)?strike", re.I)),
    ("strike", re.compile(r"(?:over|along)\s+(?:a\s+)?strike\s+(?:length\s+|extent\s+)?of\s+"
                          r"(?:approximately\s+|about\s+|some\s+|~)?" + _N + r"\s*" + _U, re.I)),
    ("strike", re.compile(_N + r"\s*" + _U + r"\s+(?:of\s+)?strike\s+(?:length|extent)", re.I)),
    ("depth", re.compile(r"(?:to|at)\s+a\s+(?:vertical\s+|downhole\s+|maximum\s+)?depth\s+of\s+"
                         r"(?:approximately\s+|about\s+|some\s+|~)?" + _N + r"\s*" + _U, re.I)),
    ("depth", re.compile(r"(?:to|reaching)\s+(?:approximately\s+|about\s+|some\s+|~)?" + _N
                         + r"\s*" + _U + r"\s+(?:below\s+(?:surface|the\s+surface)|"
                         r"vertical\s+depth|depth\s+below\s+surface)", re.I)),
    ("dip", re.compile(r"(?:down[- ]?dip|dip)\s+(?:extent|length|continuity)\s+of\s+"
                       r"(?:approximately\s+|about\s+|some\s+|~)?" + _N + r"\s*" + _U, re.I)),
    ("dip", re.compile(r"(?:extends?|traced|defined|continuous)\s+(?:\w+\s+){0,3}?down[- ]?dip\s+"
                       r"(?:for\s+|over\s+|to\s+)?(?:approximately\s+|about\s+|some\s+|~)?" + _N
                       + r"\s*" + _U, re.I)),
    ("dip", re.compile(r"dip\s+length\s+of\s+(?:approximately\s+|about\s+|some\s+|~)?"
                       + _N + r"\s*" + _U, re.I)),
    ("dip", re.compile(r"(?:down[- ]?dip|dip)\s+(?:for|over)\s+(?:at\s+least\s+)?"
                       r"(?:approximately\s+|about\s+|some\s+|~)?" + _N + r"\s*" + _U, re.I)),
    ("dip", re.compile(_N + r"\s*" + _U + r"\s+down[- ]?dip", re.I)),
    ("depth", re.compile(r"vertical\s+(?:extent|continuity|dimension)\s+of\s+"
                         r"(?:approximately\s+|about\s+|some\s+|~)?" + _N + r"\s*" + _U, re.I)),
    ("width", re.compile(r"(?:true\s+)?(?:width|thickness)(?:es)?\s+(?:ranging\s+)?"
                         r"(?:from\s+|of\s+|between\s+)" + _N + r"\s*(?:" + _U
                         + r")?\s*(?:to|-|–|and)\s*" + _N + r"\s*" + _U, re.I)),
    ("width", re.compile(r"average\s+(?:true\s+)?(?:width|thickness)\s+of\s+"
                         r"(?:approximately\s+|about\s+|~)?" + _N + r"\s*" + _U, re.I)),
    ("width", re.compile(_N + r"\s*" + _U + r"\s+(?:average\s+)?true\s+(?:width|thickness)", re.I)),
]

# Plausible bounds, in metres. A "strike length" of eight metres is a drill
# intercept described loosely; one of eighty kilometres is a regional trend, not a
# deposit. Rejecting those is most of what makes a stated dimension usable.
_BOUNDS = {"strike": (40.0, 25_000.0), "depth": (20.0, 2_500.0),
           "dip": (20.0, 2_500.0), "width": (0.3, 300.0)}
_MINERALISED = re.compile(r"minerali[sz]|deposit|vein|lens|zone|orebody|ore body|"
                          r"shoot|horizon|lode|reef|stockwork|breccia", re.I)


# ---------------------------------------------------------------- attitude
# The body's ORIENTATION, which is a different question from its size. MMP has
# `dip`, `dip_direction` and `plunge` as parameters -- they orient the wireframe
# and stretch the search along strike and down dip -- and until now NOTHING ever
# filled them from a report. They were manual-only, so a model of a vein system
# took its orientation from the principal axes of the drill pattern, which is
# the orientation of the DRILLING, not of the rock.
#
# Note the collision with `stated()` above: there, "dip" is the down-dip EXTENT
# in metres (bounds 20-2,500 m). Here it is an angle in degrees. Two different
# quantities, same word, and conflating them is how a 2,042 m down-dip extent
# reads as a 2,042-degree dip.
_DEG = r"(\d{1,2}(?:\.\d)?)\s*(?:°|&deg;|deg\.?|degrees?)"
_COMPASS = {"n": 0, "north": 0, "nne": 22, "ne": 45, "northeast": 45, "north-east": 45,
            "ene": 67, "e": 90, "east": 90, "ese": 112, "se": 135, "southeast": 135,
            "south-east": 135, "sse": 157, "s": 180, "south": 180, "ssw": 202,
            "sw": 225, "southwest": 225, "south-west": 225, "wsw": 247,
            "w": 270, "west": 270, "wnw": 292, "nw": 315, "northwest": 315,
            "north-west": 315, "nnw": 337}
_DIP_ANGLE = [
    re.compile(r"dipp?(?:ing|s)?\s+(?:steeply\s+|shallowly\s+|moderately\s+)?"
               r"(?:at\s+)?(?:approximately\s+|about\s+|~)?" + _DEG, re.I),
    re.compile(r"dips?\s+of\s+(?:approximately\s+|about\s+|~)?" + _DEG, re.I),
    re.compile(_DEG + r"\s+dip(?:ping)?\b", re.I),
]
# "dips steeply towards the west" — an adverb may sit between the verb and the
# direction, as may the angle itself ("dipping 75° to the north")
_DIP_DIRN = re.compile(
    r"dipp?(?:ing|s)?\s+"
    r"(?:(?:steeply|shallowly|moderately|gently|sub[- ]?vertically|"
    r"sub[- ]?horizontally)\s+)?"
    r"(?:[\d.]+\s*(?:°|deg\.?|degrees?)?\s*)?"
    r"(?:to|towards?)\s+(?:the\s+)?"
    r"(north[- ]?east|north[- ]?west|south[- ]?east|south[- ]?west|"
    r"north|south|east|west|nne|ene|ese|sse|ssw|wsw|wnw|nnw|ne|nw|se|sw|n|s|e|w)\b", re.I)
_DIP_AZ = re.compile(r"dip\s+(?:direction|azimuth)\s+(?:of\s+)?(?:approximately\s+)?"
                     r"(\d{1,3})\s*(?:°|deg\.?|degrees?)?", re.I)
_STRIKE_DIRN = re.compile(
    r"strik(?:es?|ing)\s+(?:approximately\s+|about\s+)?"
    r"(north[- ]?east|north[- ]?west|south[- ]?east|south[- ]?west|"
    r"north|south|east|west|ne|nw|se|sw|n|s|e|w)\b", re.I)
_STRIKE_AZ = re.compile(r"strik(?:es?|ing)\s+(?:at\s+)?(?:approximately\s+)?"
                        r"(\d{1,3})\s*(?:°|deg\.?|degrees?)", re.I)
# a qualitative statement is worth something when no number is given
_QUAL = [(re.compile(r"sub[- ]?vertical", re.I), 85.0),
         (re.compile(r"steeply\s+dipping|steep\s+dip", re.I), 70.0),
         (re.compile(r"moderately\s+dipping", re.I), 45.0),
         (re.compile(r"shallowly\s+dipping|shallow\s+dip", re.I), 25.0),
         (re.compile(r"sub[- ]?horizontal|flat[- ]?lying", re.I), 10.0)]


def attitude(pages_text, max_pages=None):
    """The body's orientation as the report states it, in degrees.

    Returns {"dip": {...}, "dip_direction": {...}} where each carries the median,
    the count, and the sentence it came from — the same shape `stated` uses, so a
    reader can check the number against the words. A dip must be 0-90 and a
    direction 0-360; anything else is a different quantity wearing the same word.
    """
    pages = list(pages_text or [])
    if max_pages:
        pages = pages[:max_pages]
    dips, dirs, qual = [], [], []
    for pno, txt in enumerate(pages):
        t = str(txt or "")
        if not t or not _MINERALISED.search(t):
            continue
        for rx in _DIP_ANGLE:
            for m in rx.finditer(t):
                try:
                    v = float(m.group(1))
                except (TypeError, ValueError):
                    continue
                if 0.0 < v <= 90.0:
                    dips.append((v, pno, _snippet(t, m.start())))
        for m in _DIP_AZ.finditer(t):
            v = float(m.group(1))
            if 0.0 <= v <= 360.0:
                dirs.append((v % 360.0, pno, _snippet(t, m.start())))
        for m in _DIP_DIRN.finditer(t):
            v = _COMPASS.get(m.group(1).replace(" ", "").replace("-", "").lower())
            if v is not None:
                dirs.append((float(v), pno, _snippet(t, m.start())))
        if not dips:
            for rx, v in _QUAL:
                m = rx.search(t)
                if m:
                    qual.append((v, pno, _snippet(t, m.start())))
                    break
    out = {}
    if not dips and qual:
        dips = qual
        out["dip_is_qualitative"] = True
    for name, rows in (("dip", dips), ("dip_direction", dirs)):
        if not rows:
            continue
        vals = sorted(r[0] for r in rows)
        out[name] = {"n": len(rows), "median": vals[len(vals) // 2],
                     "min": vals[0], "max": vals[-1],
                     "page": rows[0][1], "text": rows[0][2]}
    return out


def _snippet(t, at, span=100):
    a = max(0, at - span // 3)
    return " ".join(t[a:a + span].split())


def stated(pages_text, max_pages=None):
    """Every stated dimension of the mineralised body, in metres.

    Returns {"strike": [...], "depth": [...], "dip": [...], "width": [...]} with
    the values found, plus "n_pages" and one example sentence per dimension. A
    sentence is only read when it is about mineralisation: "a strike length of
    1,200 m" in a description of a regional fault is not the deposit."""
    out = {k: [] for k in _BOUNDS}
    why = {}
    for pno, t in enumerate(pages_text or []):
        if max_pages and pno >= max_pages:
            break
        if not t or not _MINERALISED.search(t):
            continue
        flat = re.sub(r"\s+", " ", t)
        for key, rx in _PATS:
            for m in rx.finditer(flat):
                gs = [g for g in m.groups() if g is not None]
                vals = []
                # groups alternate number, unit — a range leaves the first unit out
                nums = [g for g in gs if re.match(r"^[\d,.]+$", g)]
                units = [g for g in gs if not re.match(r"^[\d,.]+$", g)]
                unit = units[-1] if units else "m"
                for i, nv in enumerate(nums):
                    u = units[i] if i < len(units) - 0 and len(units) == len(nums) else unit
                    v = _m(nv, u)
                    if v is not None:
                        vals.append(v)
                lo, hi = _BOUNDS[key]
                vals = [v for v in vals if lo <= v <= hi]
                if not vals:
                    continue
                # the sentence it came from, for the model page to quote
                a = max(0, m.start() - 110)
                sent = flat[a:m.end() + 60].strip()
                if not _MINERALISED.search(sent):
                    continue
                if key == "width":
                    if not re.search(r"vein|lens|zone|deposit|minerali[sz]|horizon|lode|reef",
                                     sent, re.I):
                        continue
                    if len(vals) > 1 and max(vals) > 30 * max(min(vals), 1e-9):
                        continue           # "1 to 250 m" is two unrelated numbers
                out[key].extend(vals)
                why.setdefault(key, {"page": pno + 1, "text": sent[:240]})
    res = {}
    for k, vs in out.items():
        if not vs:
            continue
        vs = sorted(vs)
        res[k] = {"n": len(vs), "min": round(vs[0], 1), "max": round(vs[-1], 1),
                  "median": round(vs[len(vs) // 2], 1), **why.get(k, {})}
    return res


def volume(ext, style=None):
    """The body's volume implied by its stated dimensions, in cubic metres.

    Deliberately crude: strike x down-dip (or vertical depth) x true width, which
    is what a slab of that shape holds. A real body fills some fraction of its own
    bounding slab, so this is an upper bound on one lens, not a resource — its use
    is to say whether a model covers a tenth of its deposit or most of it."""
    if not ext:
        return None
    s = (ext.get("strike") or {}).get("median")
    d = (ext.get("dip") or {}).get("median") or (ext.get("depth") or {}).get("median")
    w = (ext.get("width") or {}).get("median")
    if not (s and d and w):
        return None
    return float(s) * float(d) * float(w)


def tonnes(ext, density):
    v = volume(ext)
    return None if not v or not density else v * float(density)


def describe(ext):
    if not ext:
        return None
    bits = []
    for k, lbl in (("strike", "strike"), ("dip", "down dip"),
                   ("depth", "depth"), ("width", "true width")):
        e = ext.get(k)
        if not e:
            continue
        if e["min"] == e["max"]:
            bits.append(f"{e['median']:.0f} m {lbl}")
        else:
            bits.append(f"{e['min']:.0f}–{e['max']:.0f} m {lbl}")
    return ", ".join(bits) or None
