"""What the report says about the ground between the holes, and about the rock.

Two things a technical report carries that MMP read nothing of.

GEOPHYSICS. Drilling tests points; geophysics covers the ground between and
beyond them, and a report says which surveys were flown or walked, over how much
ground, and — the part that matters — where the anomalies sit relative to the
mineralisation that was then drilled. None of that is an assay, so none of it
reaches the estimator directly, and pretending otherwise would be inventing
rock. What it does do is answer the question the block model cannot: whether the
body is likely to continue past the last hole, and in which direction. So the
surveys are catalogued, and any statement tying an anomaly to mineralisation or
to an untested target is kept with the sentence it came from, for the model page
to show beside the shape MMP drew from the drilling alone.

ROCK PROPERTIES. The geotechnical and physical-property work is more directly
useful, because two of its numbers are model inputs:

  * bulk density or specific gravity, which is the tonnage factor — a model
    carrying an assumed 2.7 against a report's measured 2.43-2.63 is wrong by
    the same ratio, on every tonne; and
  * the depth of overburden or weathering, which is where the body starts.

The rest — RQD, GSI, rock mass rating, UCS, pit slope angle — says how the rock
would be mined rather than where it is, and is kept as stated for the page.

Only stated numbers are read. Where a report says nothing, this says nothing.

The traps are real and are guarded for by name. "GSI" is the Geological Strength
Index and is also Geochemical Services Inc. and GSI Environmental Inc., both of
which appear in the same reports; a bare "2.7" near the word density may be a
table of anything; and a report's history section describes surveys flown over a
regional belt, not over this deposit.
"""
import re

_N = r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"
_SP = r"[\s ]*"


def _clip(s):
    """A quote that starts at a word and, where it can, at a sentence.

    Cutting a fixed number of characters either side of a match put "rete high
    chargeability zones" on the page, which reads as a transcription error
    rather than as the report's own words."""
    s = " ".join(str(s or "").split())
    if not s:
        return s
    cut = s.find(". ")
    if 0 <= cut < int(len(s) * 0.55):
        s = s[cut + 2:]
    elif s[:1].islower() or s[:1].isdigit():
        sp = s.find(" ")
        if 0 <= sp < 30:
            s = s[sp + 1:]
    end = s.rfind(". ")
    if end > int(len(s) * 0.5):
        s = s[:end + 1]
    return s.strip()


def _snip(t, at, span=230):
    a = max(0, at - span // 3)
    return _clip(t[a:a + span])


# ---------------------------------------------------------------- surveys

_METHOD = [
    ("IP/resistivity", re.compile(r"induced\s+polari[sz]ation|\bIP\s*/\s*resistivit|"
                                  r"\bIP\s+survey|chargeabilit|resistivit", re.I)),
    ("magnetics", re.compile(r"aeromagnetic|magnetometer|magnetic\s+survey(?:ing)?|"
                             r"ground\s+magnetics?\b", re.I)),
    ("electromagnetics", re.compile(r"electromagnetic|\bVTEM\b|\bZTEM\b|\bTDEM\b|\bVLF\b|"
                                    r"\bEM\s+survey|\bAEM\b|\bHeliTEM\b|\bSkyTEM\b", re.I)),
    ("gravity", re.compile(r"gravity\s+survey|ground\s+gravity|gravimetric\s+survey", re.I)),
    ("seismic", re.compile(r"seismic\s+(?:survey|line|reflection|refraction)", re.I)),
    ("radiometrics", re.compile(r"radiometric", re.I)),
    ("magnetotellurics", re.compile(r"magnetotelluric|\bMT\s+survey", re.I)),
    # a "down-hole survey" is the hole's own azimuth and dip, which is why
    # `survey` is not one of the words that makes this geophysics
    ("borehole geophysics", re.compile(r"(?:down[- ]?hole|borehole)\s+(?:geophysic|"
                                       r"EM\b|IP\b|logging|probe|conductivity)", re.I)),
]
_AIR = re.compile(r"airborne|heli(?:copter)?[- ]?borne|aeromagnetic|flown|flew|flight\s+line|"
                  r"\bVTEM\b|\bZTEM\b|\bSkyTEM\b|\bHeliTEM\b", re.I)
_GROUND = re.compile(r"ground[- ](?:based\s+)?|walked|road[- ]based|line[- ]cutting|"
                     r"ground\s+magnetic|ground\s+gravity", re.I)
_HOLE = re.compile(r"down[- ]?hole|borehole", re.I)

# how much ground the survey covered, which is the one number that says whether
# it was a pass over the belt or a grid over the deposit
# reports write it "102-line km" as often as "102 line km", and a hyphen is not
# whitespace: missing that gave a survey the grid-cutting length from the next
# sentence instead of its own
_HY = r"[\s\u00a0-]*"
_LINE_KM = re.compile(
    r"(?:(\d{1,3}(?:,\d{3})*(?:\.\d+)?)" + _HY + r"line[-\s]?(?:km|kilometres?|kilometers?)\b"
    r"|(\d{1,3}(?:,\d{3})*(?:\.\d+)?)" + _SP + r"(?:km|kilometres?|kilometers?)" + _SP
    + r"of" + _SP + r"(?:line|survey|grid|road[-\s]?based|induced|IP\b|magnetic|gravity|"
    r"electromagnetic|seismic)"
    r"|(\d{1,3}(?:,\d{3})*(?:\.\d+)?)" + _SP + r"(?:km|kilometres?|kilometers?)" + _SP
    + r"(?:of" + _SP + r")?(?:line[-\s]?cutting|lines)\b)", re.I)
_STATIONS = re.compile(r"(?:a\s+total\s+of\s+)?" + _N + _SP + r"(?:gravity\s+|survey\s+)?stations", re.I)
_SPACING = re.compile(r"(?:flight\s+lines?|lines?|stations?)\s+(?:spaced\s+|at\s+|on\s+)"
                      r"(?:approximately\s+|about\s+|~)?" + _N + r"\s*m(?:etre|eter)?s?"
                      r"(?:\s+(?:intervals?|spacing|centres?|centers?|apart))?", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
# something somebody did over ground, as opposed to a line in a list of acronyms
# an author citation: Surname, A.B., 2012,
_CITED = re.compile(r"[A-Z][a-z]{2,},\s*[A-Z]\.\s*[A-Z]?\.?,\s*(?:19|20)\d{2}")
# a page header's date is the report's, not the survey's
_MONTH = re.compile(r"(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|"
                    r"OCTOBER|NOVEMBER|DECEMBER)\s*$", re.I)
_DID = re.compile(r"survey(?:s|ed|ing)?\b|flown|flew|carried\s+out|completed|conducted|"
                  r"undertaken|covered|grid\b|line[-\s]?km|anomal|high\w*\b|chargeabilit|"
                  r"response", re.I)


def surveys(pages_text, max_pages=None):
    """Every geophysical survey the report describes, with what it covered."""
    out, seen = [], set()
    for pno, t in enumerate(pages_text or []):
        if max_pages and pno >= max_pages:
            break
        flat = re.sub(r"\s+", " ", str(t or ""))
        if len(flat) < 40:
            continue
        for name, rx in _METHOD:
            m = rx.search(flat)
            if not m:
                continue
            sent = _snip(flat, m.start())
            if sent.count(".") > 14 or "......" in sent:
                continue                     # table of contents dot leaders
            # "IP Induced Polarisation K Thousand" is a glossary, not a survey
            if not _DID.search(sent):
                continue
            # "Fritz, F.P., 2012, ... Aeromagnetic Survey Interpretation" is a
            # reference list: the survey is somebody else's report, not work done
            if _CITED.search(flat[max(0, m.start() - 90):m.start() + 20]):
                continue
            rec = {"method": name, "page": pno + 1, "text": sent[:240]}
            plat = ("airborne" if _AIR.search(sent) else
                    "borehole" if _HOLE.search(sent) else
                    "ground" if _GROUND.search(sent) else None)
            if plat:
                rec["platform"] = plat
            # the length that belongs to THIS survey, read beside the method's own
            # words rather than anywhere in the sentence: a paragraph listing a
            # magnetic grid and an IP grid states two lengths
            near = flat[max(0, m.start() - 30):m.end() + 120]
            km = _LINE_KM.search(near)
            if km:
                try:
                    v = float(next(g for g in km.groups() if g).replace(",", ""))
                    if 0.1 <= v <= 100000:
                        rec["line_km"] = v
                except (TypeError, ValueError):
                    pass
            # a station count is unambiguous, so it may be read from further off:
            # "A ground gravity survey was carried out in 2022 by ... A total of
            # 1,168 stations were" puts it two sentences away
            st = _STATIONS.search(flat[max(0, m.start() - 30):m.end() + 300])
            if st:
                try:
                    rec["stations"] = int(float(st.group(1).replace(",", "")))
                except ValueError:
                    pass
            sp = _SPACING.search(near)
            if sp:
                try:
                    v = float(sp.group(1).replace(",", ""))
                    if 1 <= v <= 2000:
                        rec["line_spacing_m"] = v
                except ValueError:
                    pass
            # the year usually opens the sentence — "In 1987 the company
            # completed an airborne magnetic survey" — so it is looked for
            # further back than the measurements are
            yw = flat[max(0, m.start() - 130):m.end() + 100]
            yr = _YEAR.search(yw)
            if yr and not _MONTH.search(yw[:yr.start()]):
                rec["year"] = int(yr.group(1))
            key = (name, rec.get("platform"), rec.get("line_km"), rec.get("year"))
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    return out


# ------------------------------------------------- anomalies worth drilling

_ANOM = re.compile(r"\b(?:chargeabilit\w*|resistivit\w*|magnetic|conductor|conductive|"
                   r"geophysical|IP)\b[^.]{0,120}?\b(?:anomal\w+|high\w*|low\w*|target\w*|"
                   r"response)\b", re.I)
_TIES = re.compile(r"coincid\w+|correlat\w+|associated\s+with|underlies|underlain|beneath|"
                   r"over\s+the\s+(?:sulphide|sulfide|minerali[sz]|deposit|zone)|"
                   r"explained\s+by|reflects?\s+the|interpreted\s+to\s+(?:be|represent|reflect)",
                   re.I)
_ORE = re.compile(r"minerali[sz]ation|minerali[sz]ed|sulphide|sulfide|ore\b|deposit|vein|lens|"
                  r"lode|massive\s+sulphide|zone\s+of\s+minerali", re.I)
_UNTESTED = re.compile(r"untested|not\s+been\s+(?:drill|test)|remains?\s+(?:open|untested)|"
                       r"warrants?\s+(?:drill|follow)|recommend\w*\s+(?:drill|follow)|"
                       r"follow[-\s]?up\s+drill|drill\s+target", re.I)


def targets(pages_text, max_pages=None, cap=40):
    """Where the report ties a geophysical response to rock worth drilling.

    Two kinds, kept apart: an anomaly the drilling has already explained, which
    says the method works on this deposit; and one it has not, which is the
    report's own statement of where the body might continue. Neither is data the
    estimator may use — they are sentences, not samples — so they are kept as
    sentences, with their page, for a reader to weigh against the shape the
    drilling alone supports."""
    out = []
    for pno, t in enumerate(pages_text or []):
        if max_pages and pno >= max_pages:
            break
        flat = re.sub(r"\s+", " ", str(t or ""))
        if "......" in flat:
            continue
        for m in _ANOM.finditer(flat):
            a = max(0, m.start() - 120)
            sent = flat[a:m.end() + 220]
            if not (_ORE.search(sent) or _UNTESTED.search(sent)):
                continue
            tied = bool(_TIES.search(sent) and _ORE.search(sent))
            open_ = bool(_UNTESTED.search(sent))
            if not (tied or open_):
                continue
            out.append({"page": pno + 1, "explained": tied, "untested": open_,
                        "text": _clip(sent)[:260]})
            break                            # one per page is enough to make the point
        if len(out) >= cap:
            break
    return out


# ------------------------------------------------------- rock properties

# "GSI" is also Geochemical Services Inc. and GSI Environmental Inc., which both
# appear in these reports; the index is only read when a number sits with it.
# Only ever read with a linking word between the term and the number. A bare gap
# of forty characters swallowed "sent to GSI for 30 g fire assay" and a variable
# table's "RQDCD 0 10 1 code RQD" — in both the number belongs to something else.
_LINK = r"(?:\s*(?:of|=|:|is|was|were|are|values?|averag\w+|mean|ranges?|rang\w+|from|" \
        r"between|to|about|approximately|~|around|typically|generally|and|the|a|an|" \
        r"index|rating|,|\()\s*){1,5}"
_GSI = re.compile(r"\bGSI\b(?![\s,]*(?:Environmental|Inc|Ltd|LLC|Consult|for\b))"
                  + _LINK + _N + r"(?!\s*(?:g\b|kg|ppm|ppb|g/t|holes|samples))", re.I)
_GSI2 = re.compile(r"geological\s+strength\s+index" + _LINK + _N, re.I)
_RQD = re.compile(r"\bRQD\b" + _LINK + _N + r"\s*%|"
                  r"rock\s+quality\s+designation[^.\d]{0,30}" + _LINK + _N, re.I)
_RMR = re.compile(r"rock\s+mass\s+rating" + _LINK + _N + r"|\bRMR\b" + _LINK + _N, re.I)
_UCS = re.compile(r"(?:uniaxial\s+compressive\s+strength|\bUCS\b)[^.\d]{0,60}" + _N
                  + r"\s*(?:MPa|mpa)", re.I)
_SLOPE = re.compile(r"(?:overall\s+)?(?:pit\s+)?slope\s+angle" + _LINK + _N
                    + r"\s*(?:°|degrees?|deg\b)", re.I)
_OVB = re.compile(r"overburden[^.\d]{0,60}" + _N + r"\s*m(?:etre|eter)?s?\b"
                  r"|" + _N + r"\s*m(?:etre|eter)?s?\s+of\s+overburden", re.I)
_WEATH = re.compile(r"(?:depth\s+of\s+(?:oxidation|weathering)|(?:oxidation|weathering)\s+"
                    r"(?:extends?|reaches?|to\s+a\s+depth\s+of))[^.\d]{0,50}" + _N
                    + r"\s*m(?:etre|eter)?s?\b", re.I)
# density: a range is the useful form, because a single number in a table may be
# anything. Both are read; the range wins where both are present.
_DENS_RANGE = re.compile(r"(?:bulk\s+densit\w*|specific\s+gravit\w*|dry\s+densit\w*|\bSG\b)"
                         r"[^.]{0,80}?(?:ranges?\s+(?:from|between)|varies?\s+(?:from|between)|"
                         r"of)\s*" + _N + r"\s*(?:t/m3|t/m³|g/cm3|g/cm³)?\s*"
                         r"(?:to|-|–|and)\s*" + _N + r"\s*(?:t/m3|t/m³|g/cm3|g/cm³)?", re.I)
# the UNIT is what makes this safe, so the gap may be wide: the first figure
# after "bulk density" that is given in t/m3 is the bulk density
_DENS_ONE = re.compile(r"(?:bulk\s+densit\w*|specific\s+gravit\w*|dry\s+densit\w*)"
                       r"[^.]{0,90}?" + _N + r"\s*(?:t/m3|t/m³|g/cm3|g/cm³)", re.I)
_DENS_N = re.compile(r"(?:a\s+total\s+of\s+)?" + _N + r"\s*(?:bulk\s+density|\bBD\b|"
                     r"specific\s+gravity)\s*(?:\(BD\)\s*)?(?:values|determinations|"
                     r"measurements|samples)", re.I)

_GEOTECH_CTX = re.compile(r"geotechnic|rock\s+mass|slope|pit\s+design|core\s+log|density|"
                          r"specific\s+gravity|strength", re.I)

_FIELDS = [
    ("rqd", _RQD, 0.0, 100.0, "%"),
    ("gsi", _GSI, 5.0, 100.0, ""),
    ("gsi", _GSI2, 5.0, 100.0, ""),
    ("rmr", _RMR, 5.0, 100.0, ""),
    ("ucs_mpa", _UCS, 1.0, 500.0, "MPa"),
    ("slope_angle_deg", _SLOPE, 10.0, 80.0, "°"),
    ("overburden_m", _OVB, 0.2, 300.0, "m"),
    ("weathering_m", _WEATH, 0.5, 600.0, "m"),
]


def rock(pages_text, max_pages=None):
    """Physical and geotechnical properties, as the report states them."""
    vals, why = {}, {}

    def add(key, v, pno, sent):
        vals.setdefault(key, []).append(float(v))
        why.setdefault(key, {"page": pno + 1, "text": sent[:240]})

    for pno, t in enumerate(pages_text or []):
        if max_pages and pno >= max_pages:
            break
        flat = re.sub(r"\s+", " ", str(t or ""))
        if "......" in flat:
            continue
        for key, rx, lo, hi, _u in _FIELDS:
            for m in rx.finditer(flat):
                for g in m.groups():
                    if g is None:
                        continue
                    try:
                        v = float(str(g).replace(",", ""))
                    except ValueError:
                        continue
                    if lo <= v <= hi:
                        add(key, v, pno, _snip(flat, m.start()))
                    break
        for m in _DENS_RANGE.finditer(flat):
            try:
                a, b = float(m.group(1)), float(m.group(2))
            except (TypeError, ValueError):
                continue
            if 1.2 <= a <= 8.0 and 1.2 <= b <= 8.0 and a <= b:
                add("density_low", a, pno, _snip(flat, m.start()))
                add("density_high", b, pno, _snip(flat, m.start()))
        for m in _DENS_ONE.finditer(flat):
            try:
                v = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if 1.2 <= v <= 8.0:
                add("density", v, pno, _snip(flat, m.start()))
        for m in _DENS_N.finditer(flat):
            try:
                v = int(float(m.group(1).replace(",", "")))
            except (TypeError, ValueError):
                continue
            if 1 <= v <= 100000:
                add("density_samples", v, pno, _snip(flat, m.start()))

    out = {}
    for k, vs in vals.items():
        vs = sorted(vs)
        out[k] = {"n": len(vs), "min": round(vs[0], 3), "max": round(vs[-1], 3),
                  "median": round(vs[len(vs) // 2], 3), **why.get(k, {})}
    return out


def density_from(rk):
    """The tonnage factor the report's own measurements imply, or None.

    A stated range is used at its midpoint, which is what a model carrying one
    density for the body should carry. A lone figure is used only when there is
    no range, because a single number in a table of many is the weaker reading."""
    if not rk:
        return None
    lo, hi = rk.get("density_low"), rk.get("density_high")
    if lo and hi:
        v = (float(lo["median"]) + float(hi["median"])) / 2.0
        return round(v, 3) if 1.2 <= v <= 8.0 else None
    one = rk.get("density")
    if one and 1.2 <= float(one["median"]) <= 8.0:
        return round(float(one["median"]), 3)
    return None


def describe(gp, rk):
    """One line for the model page."""
    bits = []
    if gp:
        kinds = []
        for s in gp:
            k = s["method"]
            if k not in kinds:
                kinds.append(k)
        # one flight counted once: an airborne magnetic AND electromagnetic survey
        # is a single 277 line-km pass, not 554
        flights = {}
        for s in gp:
            if s.get("line_km"):
                flights[(s.get("line_km"), s.get("year"), s.get("platform"))] = s["line_km"]
        km = sum(flights.values())
        bits.append(", ".join(kinds[:5]) + (" (%.0f line-km stated)" % km if km else ""))
    d = density_from(rk)
    if d:
        bits.append("density %.2f t/m3 from the report" % d)
    for k, lbl in (("overburden_m", "overburden"), ("weathering_m", "weathering"),
                   ("slope_angle_deg", "pit slope")):
        e = (rk or {}).get(k)
        if e:
            bits.append("%s %g%s" % (lbl, e["median"], "°" if "slope" in k else " m"))
    return "; ".join(bits) or None
