"""Drill assay intervals as columns rather than as rows of dictionaries.

A client's multi-element database is 900,000 assay intervals. As a list of
dictionaries that is about 500 MB of Python objects — 360 bytes of dictionary
and three boxed floats for 26 bytes of actual measurement — and it is carried
twice, once as the stored dataset and once as the project's. That single fact is
what made a gigabyte of worker memory tight, and what made a model take a minute
to start.

Here the same data is five numpy arrays and a few short string tables: about
26 bytes a row, read from disk as raw buffers rather than parsed number by
number. Nothing about the data changes — the same values, at the same precision,
in the same order — so a model built from this table is identical, byte for
byte, to one built from the rows it replaces. `scripts/test_ivtable.py` holds
that to the real corpus.

The modeller reads intervals one element at a time (composite this metal, then
that one), so `rows_for(el)` hands back that element's rows and nothing else.
Each row is a `Row`: a slots object that answers to `x["grade"]` and `x.get(...)`
exactly as the dictionary it replaces did, so the estimator, the compositing and
the wireframe did not have to learn a new shape.
"""
import base64
import json

import numpy as np

# The column layout. Depths and grades stay float64: a model must come out of
# this table identical to one built from the rows, and float32 would round the
# fifth decimal of a grade.
_F = np.float64
_FIELDS = ("key", "hole", "from", "to", "el", "grade", "unit", "sub", "src")


class Row:
    """One interval, addressed like the dictionary it replaces.

    `__slots__` and no dictionary of its own: 120 bytes against 360, and only
    the rows actually being worked on exist at any moment."""

    __slots__ = ("key", "hole", "frm", "to", "el", "grade", "unit", "sub", "src")

    def __init__(self, key, hole, frm, to, el, grade, unit, sub, src):
        self.key, self.hole, self.frm, self.to = key, hole, frm, to
        self.el, self.grade, self.unit, self.sub, self.src = el, grade, unit, sub, src

    def __getitem__(self, k):
        try:
            return getattr(self, "frm" if k == "from" else k)
        except AttributeError:
            raise KeyError(k)

    def __setitem__(self, k, v):
        setattr(self, "frm" if k == "from" else k, v)

    def get(self, k, default=None):
        v = getattr(self, "frm" if k == "from" else k, default)
        return default if v is None else v

    def __contains__(self, k):
        return k in _FIELDS

    def keys(self):
        return _FIELDS

    def items(self):
        return [(k, self[k]) for k in _FIELDS]

    def as_dict(self):
        return {k: self[k] for k in _FIELDS}

    def __repr__(self):
        return "Row(%s %s-%s %s %s)" % (self.hole, self.frm, self.to, self.el, self.grade)


def _none(v):
    """NaN is how a missing depth or grade is held in a float column."""
    return None if (v is None or v != v) else float(v)


class Intervals:
    """Assay intervals in columns, read and written a whole element at a time."""

    __slots__ = ("keys", "holes", "els", "units", "srcs",
                 "key_i", "hole_i", "el_i", "unit_i", "src_i", "frm", "to", "grade", "sub",
                 "_idx", "_cache")

    def __init__(self):
        self.keys, self.holes, self.els, self.units, self.srcs = [], [], [], [], []
        for a in ("key_i", "hole_i", "el_i", "unit_i", "src_i", "sub"):
            setattr(self, a, np.empty(0, np.int32))
        for a in ("frm", "to", "grade"):
            setattr(self, a, np.empty(0, _F))
        self._idx = None          # element -> row indices
        self._cache = (None, None)

    # ------------------------------------------------------------------ build
    @classmethod
    def from_rows(cls, rows, consume=False):
        """Build from the list-of-dictionaries shape (a report, or a parse).

        `consume` drops each row as it is read, so a parse that produced a
        million dictionaries does not have to hold them beside the columns."""
        t = cls()
        n = len(rows)
        keys, holes, els, units, srcs = {}, {}, {}, {}, {}
        ki = np.empty(n, np.int32); hi = np.empty(n, np.int32)
        ei = np.empty(n, np.int32); ui = np.empty(n, np.int32); si = np.empty(n, np.int32)
        fr = np.empty(n, _F); to = np.empty(n, _F); gr = np.empty(n, _F)
        sb = np.empty(n, np.int32)

        def put(table, lst, v):
            if v is None:
                v = ""
            i = table.get(v)
            if i is None:
                i = table[v] = len(lst)
                lst.append(v)
            return i

        for j, x in enumerate(rows):
            ki[j] = put(keys, t.keys, x.get("key"))
            hi[j] = put(holes, t.holes, x.get("hole"))
            ei[j] = put(els, t.els, x.get("el"))
            ui[j] = put(units, t.units, x.get("unit"))
            si[j] = put(srcs, t.srcs, x.get("src"))
            f_, t_, g_ = x.get("from"), x.get("to"), x.get("grade")
            fr[j] = np.nan if f_ is None else f_
            to[j] = np.nan if t_ is None else t_
            gr[j] = np.nan if g_ is None else g_
            sb[j] = int(x.get("sub") or 0)
            if consume:
                rows[j] = None
        t.key_i, t.hole_i, t.el_i, t.unit_i, t.src_i = ki, hi, ei, ui, si
        t.frm, t.to, t.grade, t.sub = fr, to, gr, sb
        return t

    @classmethod
    def coerce(cls, v):
        """A table, a list of rows, or nothing -> a table."""
        if isinstance(v, cls):
            return v
        return cls.from_rows(v or [])

    # ------------------------------------------------------------------ size
    def __len__(self):
        return len(self.frm)

    def __bool__(self):
        return len(self.frm) > 0

    def nbytes(self):
        return int(sum(getattr(self, a).nbytes for a in
                       ("key_i", "hole_i", "el_i", "unit_i", "src_i", "sub", "frm", "to", "grade")))

    # ------------------------------------------------------------- row access
    def row(self, j):
        # a string column holds "" where the row had nothing; it reads back as
        # None, because that is what the dictionary it replaces gave
        return Row(self.keys[self.key_i[j]] or None, self.holes[self.hole_i[j]] or None,
                   _none(self.frm[j]), _none(self.to[j]), self.els[self.el_i[j]] or None,
                   _none(self.grade[j]), self.units[self.unit_i[j]] or None,
                   int(self.sub[j]), self.srcs[self.src_i[j]] or None)

    def __iter__(self):
        """One row at a time, built on the way past and dropped behind."""
        for j in range(len(self.frm)):
            yield self.row(j)

    def __getitem__(self, j):
        if isinstance(j, slice):
            return [self.row(k) for k in range(*j.indices(len(self.frm)))]
        return self.row(j)

    # --------------------------------------------------------- by element
    def index(self):
        if self._idx is None:
            idx = {}
            for e_i, el in enumerate(self.els):
                w = np.flatnonzero(self.el_i == e_i)
                if len(w):
                    idx[el] = w
            self._idx = idx
        return self._idx

    def elements(self):
        return list(self.index())

    def count(self, el):
        return int(len(self.index().get(el, ())))

    def rows_for(self, el):
        """This element's rows. Cached for the element last asked for, because
        the modeller works one metal at a time and asking twice in a row is the
        normal pattern; holding every element at once is what this class exists
        to avoid."""
        if self._cache[0] == el:
            return self._cache[1]
        w = self.index().get(el)
        rows = [self.row(j) for j in w] if w is not None else []
        self._cache = (el, rows)
        return rows

    def unit_of(self, el):
        w = self.index().get(el)
        if w is None or not len(w):
            return None
        return self.units[self.unit_i[w[0]]] or None

    def grades(self, el):
        w = self.index().get(el)
        return self.grade[w] if w is not None else np.empty(0, _F)

    def keys_of(self, el):
        """The distinct hole keys this element was assayed on."""
        w = self.index().get(el)
        if w is None:
            return set()
        return {self.keys[i] for i in np.unique(self.key_i[w])}

    def ore_fraction(self, el):
        """The share of this element's assays that reach a grade worth mining."""
        from minemodelingpro import ore
        return ore.significance(el, self.grades(el))

    def mean(self, el):
        g = self.grades(el)
        g = g[np.isfinite(g)]
        return float(g.mean()) if len(g) else None

    # ------------------------------------------------------------- mutation
    def set_grades(self, el, values):
        """Write an element's grades back (sanity repairs a unit mistake)."""
        w = self.index().get(el)
        if w is None:
            return
        v = np.asarray([np.nan if x is None else x for x in values], _F)
        self.grade[w] = v
        self._cache = (None, None)

    def keep(self, el, mask):
        """Keep only the rows of this element the mask marks."""
        w = self.index().get(el)
        if w is None:
            return
        drop = w[~np.asarray(mask, bool)]
        if len(drop):
            self._delete(drop)

    def drop_element(self, el):
        w = self.index().get(el)
        if w is not None and len(w):
            self._delete(w)

    def _delete(self, rows):
        m = np.ones(len(self.frm), bool)
        m[rows] = False
        for a in ("key_i", "hole_i", "el_i", "unit_i", "src_i", "sub", "frm", "to", "grade"):
            setattr(self, a, getattr(self, a)[m])
        self._idx = None
        self._cache = (None, None)

    def extend(self, other):
        """Add another table's rows (or a list of row dicts) to this one."""
        other = Intervals.coerce(other)
        if not len(other):
            return self
        if not len(self):
            for a in ("keys", "holes", "els", "units", "srcs"):
                setattr(self, a, list(getattr(other, a)))
            for a in ("key_i", "hole_i", "el_i", "unit_i", "src_i", "sub", "frm", "to", "grade"):
                setattr(self, a, getattr(other, a).copy())
            self._idx = None
            self._cache = (None, None)
            return self

        def remap(mine, theirs):
            pos = {v: i for i, v in enumerate(mine)}
            out = np.empty(len(theirs), np.int32)
            for i, v in enumerate(theirs):
                p = pos.get(v)
                if p is None:
                    p = pos[v] = len(mine)
                    mine.append(v)
                out[i] = p
            return out

        for a, tbl in (("key_i", "keys"), ("hole_i", "holes"), ("el_i", "els"),
                       ("unit_i", "units"), ("src_i", "srcs")):
            m = remap(getattr(self, tbl), getattr(other, tbl))
            setattr(self, a, np.concatenate([getattr(self, a), m[getattr(other, a)]]))
        for a in ("sub", "frm", "to", "grade"):
            setattr(self, a, np.concatenate([getattr(self, a), getattr(other, a)]))
        self._idx = None
        self._cache = (None, None)
        return self

    def relabel(self, keyfn=None, src=None):
        """Fill in the hole key (from the hole id) and the source, in place.

        Done over the string table rather than the rows: 900,000 intervals name
        a few hundred holes, so the key function runs a few hundred times."""
        if keyfn is not None:
            pos, keys = {}, []
            remap = np.empty(len(self.holes), np.int32)
            for i, h in enumerate(self.holes):
                k = keyfn(h)
                j = pos.get(k)
                if j is None:
                    j = pos[k] = len(keys)
                    keys.append(k)
                remap[i] = j
            self.keys = keys
            self.key_i = remap[self.hole_i] if len(self) else np.empty(0, np.int32)
        if src is not None:
            self.srcs = [src]
            self.src_i = np.zeros(len(self), np.int32)
        self._idx = None
        self._cache = (None, None)
        return self

    def restrict_keys(self, wanted):
        """Keep only the rows whose hole key is one of these."""
        if not len(self):
            return self
        ok = np.array([k in wanted for k in self.keys], bool)
        m = ok[self.key_i] if len(self.keys) else np.zeros(len(self), bool)
        if not m.all():
            self._delete(np.flatnonzero(~m))
        return self

    def max_to_by_key(self):
        """Deepest assayed depth per hole key."""
        out = {}
        if not len(self):
            return out
        t = np.where(np.isfinite(self.to), self.to, 0.0)
        order = np.argsort(self.key_i, kind="stable")
        ki, tt = self.key_i[order], t[order]
        start = 0
        while start < len(ki):
            end = start + int(np.searchsorted(ki[start:], ki[start], "right"))
            out[self.keys[ki[start]]] = float(tt[start:end].max())
            start = end
        return out

    def dedupe(self):
        """One row per hole, interval and element, first occurrence kept — the
        same rule the row version applied, decided on the columns instead of in
        a set of a million tuples."""
        if not len(self):
            return self
        f = np.round(self.frm, 1)
        t = np.round(self.to, 1)
        ok = np.isfinite(f) & np.isfinite(t) & (t > f)
        named = np.array([bool(k) for k in self.keys], bool)
        if len(named):
            ok &= named[self.key_i]
        else:
            ok[:] = False
        w = np.flatnonzero(ok)
        if not len(w):
            self._delete(np.arange(len(self)))
            return self
        stack = np.stack([self.key_i[w].astype(np.int64),
                          np.round(f[w] * 10).astype(np.int64),
                          np.round(t[w] * 10).astype(np.int64),
                          self.el_i[w].astype(np.int64)], 1)
        _, first = np.unique(stack, axis=0, return_index=True)
        keep = np.zeros(len(self), bool)
        keep[w[first]] = True              # first occurrence, original order kept
        if not keep.all():
            self._delete(np.flatnonzero(~keep))
        return self

    # ------------------------------------------------------------ storage
    # narrow dtypes for storage: a few hundred holes do not need 32 bits, and a
    # sub-interval flag does not need any. The depths and grades stay float64 —
    # a model built from this table has to come out identical.
    _STORE_DTYPE = {"key_i": "int32", "hole_i": "int32", "el_i": "int16", "unit_i": "int8",
                    "src_i": "int16", "sub": "int8", "frm": "float64", "to": "float64",
                    "grade": "float64"}

    def to_json(self, compress=True):
        """The stored form: short string tables as text, columns as buffers.

        Not a list of numbers per column — a number written as text costs about
        eight bytes and is parsed one at a time, while a buffer is one allocation
        and one memcpy. Deflated before encoding, because a column of depths or
        of element indices is extremely repetitive and the saving is most of the
        file and most of the time spent reading it."""
        cols, kinds = {}, {}
        for a, dt in self._STORE_DTYPE.items():
            v = getattr(self, a)
            want = np.dtype(dt)
            if v.dtype != want:
                hi = np.iinfo(want).max if want.kind in "iu" else None
                if hi is not None and len(v) and (int(v.max(initial=0)) > hi or int(v.min(initial=0)) < np.iinfo(want).min):
                    want = v.dtype                      # more distinct values than expected
                v = v.astype(want)
            raw = np.ascontiguousarray(v).tobytes()
            if compress:
                import zlib
                raw = zlib.compress(raw, 6)
            cols[a] = base64.b64encode(raw).decode("ascii")
            kinds[a] = str(want)
        return {"format": "columns/1", "n": int(len(self)),
                "codec": "zlib+b64" if compress else "b64",
                "tables": {"keys": self.keys, "holes": self.holes, "els": self.els,
                           "units": self.units, "srcs": self.srcs},
                "dtypes": kinds, "cols": cols}

    @classmethod
    def from_json(cls, d):
        """Read either shape: the columns above, or a plain list of rows."""
        if isinstance(d, list):
            return cls.from_rows(d)
        if isinstance(d, cls):
            return d
        if not isinstance(d, dict) or d.get("format") != "columns/1":
            return cls.from_rows(d or [])
        t = cls()
        tb = d.get("tables") or {}
        for a in ("keys", "holes", "els", "units", "srcs"):
            setattr(t, a, list(tb.get(a) or []))
        dt = d.get("dtypes") or {}
        zipped = (d.get("codec") or "b64").startswith("zlib")
        for a, raw in (d.get("cols") or {}).items():
            buf = base64.b64decode(raw)
            if zipped:
                import zlib
                buf = zlib.decompress(buf)
            setattr(t, a, np.frombuffer(buf, dtype=np.dtype(dt.get(a, "float64"))))
        return t


class ByElement:
    """`by_el[el]` over a column table: the same mapping the modeller had, except
    that only the element being worked on is ever built into rows."""

    __slots__ = ("table",)

    def __init__(self, table):
        self.table = table

    def __getitem__(self, el):
        return self.table.rows_for(el)

    def get(self, el, default=None):
        return self.table.rows_for(el) if el in self else default

    def __iter__(self):
        return iter(self.table.elements())

    def __len__(self):
        return len(self.table.elements())

    def __bool__(self):
        return bool(self.table.elements())

    def __contains__(self, el):
        return el in self.table.index()

    def keys(self):
        return self.table.elements()

    def items(self):
        for e in self.table.elements():
            yield e, self.table.rows_for(e)

    def count(self, el):
        return self.table.count(el)

    def keys_of(self, el):
        return self.table.keys_of(el)

    def pop(self, el, default=None):
        self.table.drop_element(el)
        return default


def to_store(ds):
    """A dataset ready to be written: its intervals in column form."""
    iv = ds.get("intervals")
    if iv is not None and not isinstance(iv, dict):
        ds = dict(ds)
        ds["intervals"] = Intervals.coerce(iv).to_json()
    return ds


def from_store(ds):
    """A dataset as read back: its intervals as a table."""
    if isinstance(ds, dict) and "intervals" in ds:
        ds["intervals"] = Intervals.from_json(ds["intervals"])
    return ds


def rows_json(iv):
    """The row form, for anything that still wants to read a dataset as text."""
    t = Intervals.coerce(iv)
    return [r.as_dict() for r in t]


if __name__ == "__main__":
    import sys
    src = json.load(open(sys.argv[1]))
    t = Intervals.coerce(src.get("intervals") or src)
    print("%d intervals, %d elements, %.1f MB in columns"
          % (len(t), len(t.elements()), t.nbytes() / 1e6))
