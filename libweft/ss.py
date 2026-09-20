# SPDX-License-Identifier: LGPL-3.0-only
"""Weaver source syntax and object fields."""

import collections
import os
import re
import struct

KEYWORDS = {
    "defineSettings": None,
    "defineAnim": 3,
    "defineSound": 4,
    "serialAction": 6,
    "parallelAction": 7,
    "defineEvent": 8,
    "selectAction": 9,
    "defineStill": 10,
    "defineObject": 11,
}
PUNCT = set("{};=(),")


class SSError(Exception):
    def __init__(self, path, line, msg):
        super().__init__(f"{path}:{line}: {msg}")
        self.path, self.line = (path, line)


class Token:
    __slots__ = ("kind", "text", "trivia", "pos", "line")

    def __init__(self, kind, text, trivia, pos, line):
        self.kind, self.text, self.trivia, self.pos, self.line = (
            kind,
            text,
            trivia,
            pos,
            line,
        )

    def __repr__(self):
        return f"<{self.kind} {self.text!r}>"


def lex(src, path="<src>"):
    """Tokenize source, preserving whitespace and comments."""
    if isinstance(src, bytes):
        src = src.decode("latin1")
    toks, i, n, line = ([], 0, len(src), 1)
    while True:
        t0 = i
        while i < n:
            c = src[i]
            if c in " \t\r\n":
                line += c == "\n"
                i += 1
            elif src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j < 0 else j
            else:
                break
        trivia, start_line = (src[t0:i], line)
        if i >= n:
            toks.append(Token("eof", "", trivia, i, line))
            return toks
        c = src[i]
        if c == "#":
            j = src.find("\n", i)
            j = n if j < 0 else j
            if src[j - 1 : j] == "\r":
                j -= 1
            toks.append(Token("directive", src[i:j], trivia, i, start_line))
            i = j
            continue
        if c == '"':
            j = src.find('"', i + 1)
            if j < 0:
                raise SSError(path, line, "unterminated string")
            line += src.count("\n", i, j)
            toks.append(Token("string", src[i : j + 1], trivia, i, start_line))
            i = j + 1
            continue
        if c.isdigit() or (
            c in "+-." and i + 1 < n and (src[i + 1].isdigit() or src[i + 1] == ".")
        ):
            m = re.match("[+-]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?", src[i:])
            toks.append(Token("number", m.group(0), trivia, i, start_line))
            i += m.end()
            continue
        if c.isalpha() or c == "_":
            m = re.match("[A-Za-z_][A-Za-z0-9_]*", src[i:])
            toks.append(Token("ident", m.group(0), trivia, i, start_line))
            i += m.end()
            continue
        if c in PUNCT:
            toks.append(Token("punct", c, trivia, i, start_line))
            i += 1
            continue
        raise SSError(path, line, f"unexpected character {c!r}")


def unparse(toks):
    return "".join(t.trivia + t.text for t in toks)


class Value:
    """A property value and its source spelling."""

    __slots__ = ("kind", "value", "text")

    def __init__(self, kind, value, text):
        self.kind, self.value, self.text = (kind, value, text)

    def __repr__(self):
        return f"<{self.kind} {self.value!r}>"


class Item:
    """A directive or definition."""

    __slots__ = ("kind", "toks", "keyword", "name", "weave", "body", "line", "path")

    def __init__(self, kind, toks, line, path, **kw):
        self.kind, self.toks, self.line, self.path = (kind, toks, line, path)
        self.keyword = kw.get("keyword")
        self.name = kw.get("name")
        self.weave = kw.get("weave", False)
        self.body = kw.get("body", [])

    def __repr__(self):
        return f"<{self.kind} {self.keyword or ''} {self.name or self.toks[0].text}>"


class Entry:
    """A property or member inside a definition."""

    __slots__ = ("kind", "key", "value", "line")

    def __init__(self, kind, key, value, line):
        self.kind, self.key, self.value, self.line = (kind, key, value, line)

    def __repr__(self):
        return f"<{self.kind} {self.key}>"


class SourceFile:
    __slots__ = ("path", "src", "toks", "items")

    def __init__(self, path, src, toks, items):
        self.path, self.src, self.toks, self.items = (path, src, toks, items)

    def text(self):
        return unparse(self.toks)

    def definitions(self):
        return [i for i in self.items if i.kind == "definition"]


def parse(src, path="<src>"):
    toks = lex(src, path)
    return SourceFile(path, src, toks, _Parser(toks, path).file())


class _Parser:
    def __init__(self, toks, path):
        self.t, self.i, self.path = (toks, 0, path)

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self):
        t = self.t[self.i]
        self.i += 1
        return t

    def expect(self, kind, text=None):
        t = self.peek()
        if t.kind != kind or (text is not None and t.text != text):
            raise SSError(
                self.path, t.line, f"expected {text or kind}, got {t.kind} {t.text!r}"
            )
        return self.next()

    def file(self):
        items = []
        while self.peek().kind != "eof":
            start, t = (self.i, self.peek())
            if t.kind == "directive":
                self.next()
                items.append(
                    Item("directive", self.t[start : self.i], t.line, self.path)
                )
                continue
            items.append(self.definition())
        return items

    def definition(self):
        start = self.i
        kw = self.expect("ident")
        if kw.text not in KEYWORDS:
            raise SSError(self.path, kw.line, f"unknown definition keyword {kw.text!r}")
        name = self.expect("ident")
        weave = self.peek().kind == "ident" and self.peek().text == "Weave"
        if weave:
            self.next()
        self.expect("punct", "{")
        body = []
        while not (self.peek().kind == "punct" and self.peek().text == "}"):
            if self.peek().kind == "eof":
                raise SSError(self.path, kw.line, f"unterminated body of {name.text}")
            body.append(self.entry())
        self.expect("punct", "}")
        return Item(
            "definition",
            self.t[start : self.i],
            kw.line,
            self.path,
            keyword=kw.text,
            name=name.text,
            weave=weave,
            body=body,
        )

    def entry(self):
        key = self.expect("ident")
        if self.peek().kind == "punct" and self.peek().text == ";":
            self.next()
            return Entry("member", key.text, None, key.line)
        self.expect("punct", "=")
        v = self.value()
        self.expect("punct", ";")
        return Entry("property", key.text, v, key.line)

    def value(self):
        t = self.peek()
        if t.kind == "string":
            self.next()
            return Value("string", t.text[1:-1].replace("\r\n", "\n"), t.text)
        if t.kind == "number":
            self.next()
            return Value("number", _number(t.text), t.text)
        if t.kind == "punct" and t.text == "(":
            start = self.i
            self.next()
            parts = [self.value()]
            while self.peek().kind == "punct" and self.peek().text == ",":
                self.next()
                parts.append(self.value())
            self.expect("punct", ")")
            return Value(
                "vec", tuple(_f32(float(p.value)) for p in parts), self._span(start)
            )
        if t.kind == "ident":
            self.next()
            if self.peek().kind == "punct" and self.peek().text == "(":
                start = self.i
                self.next()
                args = []
                if not (self.peek().kind == "punct" and self.peek().text == ")"):
                    args.append(self.value())
                    while self.peek().kind == "punct" and self.peek().text == ",":
                        self.next()
                        args.append(self.value())
                self.expect("punct", ")")
                return Value(
                    "call",
                    (t.text, [a.value for a in args]),
                    t.text + self._span(start),
                )
            return Value("name", t.text, t.text)
        raise SSError(self.path, t.line, f"expected a value, got {t.kind} {t.text!r}")

    def _span(self, start):
        return "".join(x.trivia + x.text for x in self.t[start : self.i]).strip()


def _number(text):
    return int(text) if re.fullmatch("[+-]?\\d+", text) else float(text)


def _f32(x):
    """Weaver reads coordinates as float32, then stores doubles."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


ATTESTED, INFERRED = ("attested", "inferred")
ENABLED = 32
LOOPING = {"CACHE": 1, None: 2, "STREAM": 4}
TRANSPARENT = 8
FOURCC = {
    ".flc": b" FLC",
    ".smk": b" SMK",
    ".wav": b" WAV",
    ".evt": b" EVT",
    ".bmp": b" STL",
    ".ani": b" OBJ",
    ".gph": b" OBJ",
    ".mod": b" OBJ",
    "": b" OBJ",
}
DEFAULT_EXT = {8: ".evt"}
COMPOSITE = {6, 7, 9}
DEFAULT_VOLUME = 79
DEFAULTS = dict(
    unk0x14=0,
    unk0x9c0=0,
    unk0x9c4=0,
    frames_per_second=1,
    palette_management=1,
    sustain_time=0,
    loop_count=1,
    start_time=0,
    duration=0,
    location=(0.0, 0.0, 0.0),
    direction=(0.0, 0.0, 1.0),
    up=(0.0, 1.0, 0.0),
)


class Unit:
    """Definitions, macros, and include paths."""

    def __init__(self):
        self.items = []
        self.macros = {}
        self.includes = []
        self.missing = []

    @property
    def unresolved(self):
        return [spelled for _, spelled, got in self.includes if got is None]


_INCLUDE = re.compile('#\\s*include\\s*"([^"]*)"')
_DEFINE = re.compile("#\\s*define\\s+([A-Za-z_]\\w*)\\s*(.*?)\\s*$")


def resolve_include(spelled, from_path, search):
    """Resolve Windows include paths by basename, ignoring case."""
    base = spelled.replace("\\", "/").rsplit("/", 1)[-1].lower()
    roots = [os.path.dirname(from_path)] + list(search)
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        for entry in os.listdir(r):
            if entry.lower() == base:
                return os.path.join(r, entry)
    return None


def preprocess(path, search=(), unit=None, seen=None, depth=0):
    """Parse a source file and its available includes."""
    unit = unit or Unit()
    seen = seen if seen is not None else set()
    if depth > 32 or os.path.abspath(path) in seen:
        return unit
    seen.add(os.path.abspath(path))
    f = parse(open(path, "rb").read(), path)
    for item in f.items:
        if item.kind == "directive":
            text = item.toks[0].text
            m = _INCLUDE.match(text)
            if m:
                got = resolve_include(m.group(1), path, search)
                unit.includes.append((path, m.group(1), got))
                if got:
                    preprocess(got, search, unit, seen, depth + 1)
                else:
                    unit.missing.append(m.group(1))
                continue
            m = _DEFINE.match(text)
            if m:
                unit.macros.setdefault(m.group(1), m.group(2))
            continue
        unit.items.append((f, item))
    return unit


class Obj:
    """A definition with its properties and members."""

    __slots__ = ("name", "keyword", "type", "weave", "props", "members", "path", "line")

    def __init__(self, item):
        self.name, self.keyword = (item.name, item.keyword)
        self.type = KEYWORDS[item.keyword]
        self.weave = item.weave
        self.props = {}
        self.members = []
        self.path, self.line = (item.path, item.line)
        for e in item.body:
            if e.kind == "member":
                self.members.append(e.key)
            else:
                self.props[e.key] = e.value

    @property
    def composite(self):
        return self.type in COMPOSITE

    def __repr__(self):
        return f"<{self.keyword} {self.name}{(' Weave' if self.weave else '')}>"


class Program:
    """Definitions, woven roots, and file settings."""

    def __init__(self):
        self.objects = collections.OrderedDict()
        self.roots = []
        self.settings = {}
        self.unit = None
        self.duplicates = []

    def resolve(self, name):
        return self.objects.get(name)


def elaborate(unit):
    p = Program()
    p.unit = unit
    for _f, item in unit.items:
        if item.keyword == "defineSettings":
            for e in item.body:
                if e.kind == "property":
                    p.settings[e.key] = e.value.value
            continue
        o = Obj(item)
        if o.name in p.objects:
            p.duplicates.append(o.name)
        p.objects[o.name] = o
        if o.weave:
            p.roots.append(o)
    return p


def load(path, search=()):
    return elaborate(preprocess(path, search))


def flags_of(o):
    method = o.props.get("loopingMethod")
    bit = LOOPING[method.value if method else None]
    transparency = o.props.get("transparency")
    value = transparency.value if transparency else "FALSE"
    if value not in ("TRUE", "YES", "FAST", "FALSE", "NO"):
        raise ValueError(f"unsupported transparency value: {value}")
    trans = TRANSPARENT if value in ("TRUE", "YES", "FAST") else 0
    return ENABLED | bit | trans


def extra_of(o, macros):
    if "extra" in o.props:
        s = o.props["extra"].value
    elif "entityName" in o.props:
        s = o.props["entityName"].value
    elif "siFile" in o.props:
        action = o.props.get("startAction")
        n = macros.get(action.value, action.value) if action else "0"
        s = f"Action:{o.props['siFile'].value};{n}"
    else:
        return b""
    return s.encode("latin1") + b"\x00"


def presenter_of(o):
    if "handlerClass" in o.props:
        return o.props["handlerClass"].value.encode("latin1")
    if o.type == 4 and "entityName" in o.props:
        return b"Lego3DWavePresenter"
    return b""


def media_path_of(o):
    fn = o.props.get("fileName")
    if fn is None:
        return b""
    p = fn.value
    ext = os.path.splitext(p)[1].lower()
    if not ext and o.type in DEFAULT_EXT:
        p += DEFAULT_EXT[o.type]
    return p.encode("latin1")


def duration_of(o, resolve=None, depth=0):
    d = o.props.get("duration")
    if d is None:
        return DEFAULTS["duration"]
    if d.kind == "name":
        return -1 if d.value == "INDEFINITE" else 0
    if d.kind == "call":
        fn, args = d.value
        other = resolve(args[0]) if resolve and fn == "durationOf" and args else None
        if other is None or depth > 8:
            return DEFAULTS["duration"]
        return duration_of(other, resolve, depth + 1)
    return int(d.value)


def record(o, oid, macros=None, resolve=None):
    """Build MxOb fields from an SS definition."""
    macros = macros or {}

    def num(k, d):
        if k not in o.props:
            return d
        v = o.props[k]
        return -1 if v.kind == "name" and v.value == "INDEFINITE" else int(v.value)

    def vec(k):
        return (
            tuple(float(x) for x in o.props[k].value) if k in o.props else DEFAULTS[k]
        )

    r = dict(
        type=o.type,
        presenter=presenter_of(o),
        unk0x14=0,
        name=o.name.encode("latin1"),
        id=oid,
        flags=flags_of(o),
        start_time=num("startTime", 0),
        duration=duration_of(o, resolve),
        loop_count=num("loopCount", 1),
        location=vec("location"),
        direction=vec("direction"),
        up=vec("up"),
        extra=extra_of(o, macros),
    )
    if o.composite:
        return r
    path = media_path_of(o)
    ext = os.path.splitext(path.decode("latin1"))[1].lower()
    r.update(
        media_src_path=path,
        unk0x9c0=0,
        unk0x9c4=0,
        frames_per_second=num("framesPerSecond", 1),
        media_format=struct.unpack("<I", FOURCC.get(ext, b" OBJ"))[0],
        palette_management=0 if o.props.get("paletteManagement") else 1,
        sustain_time=num("sustainTime", 0),
    )
    if "mediaFormat" in o.props:
        r["media_format"] = int.from_bytes(
            o.props["mediaFormat"].value.encode("latin1"), "little"
        )
    if r["media_format"] == int.from_bytes(b" WAV", "little"):
        r["volume"] = num("volume", DEFAULT_VOLUME)
    return r


def preprocess_text(text, path="<memory>", search=()):
    """Preprocess source held in memory."""
    unit = Unit()
    f = parse(text, path)
    for item in f.items:
        if item.kind == "directive":
            m = _INCLUDE.match(item.toks[0].text)
            if m:
                got = resolve_include(m.group(1), path, search)
                unit.includes.append((path, m.group(1), got))
                if got:
                    preprocess(got, search, unit, set(), 1)
                else:
                    unit.missing.append(m.group(1))
                continue
            m = _DEFINE.match(item.toks[0].text)
            if m:
                unit.macros.setdefault(m.group(1), m.group(2))
            continue
        unit.items.append((f, item))
    return unit
