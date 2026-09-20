# SPDX-License-Identifier: LGPL-3.0-only
"""SI to canonical SS and back."""

import collections
import json
import os
import re
import struct
from pathlib import Path

from . import layout, schedule, si, ss

CH_END = 2
MXST_HEADER_TAIL = 12
UNINIT = 0xCD  # MSVC debug-heap fill, retained in Weaver's skipped write-buffer spans.


class Refused(Exception):
    """Unsupported source or file layout."""


class Project:
    """Source, object IDs, raw media, and buffer residue for one SI."""

    def __init__(self, base, source, actions, media, residue):
        self.base, self.source, self.actions = (base, source, actions)
        self.media = media
        self.residue = residue

    def write(self, outdir):
        """Write an editable project; refuse to replace an existing directory."""
        if not _ident(self.base):
            raise Refused("invalid project name")
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=False)
        (outdir / "media").mkdir()
        (outdir / (self.base + ".SS")).write_text(
            self.source, encoding="utf-8", newline=""
        )
        (outdir / self.actions_name).write_text(
            self.actions, encoding="utf-8", newline=""
        )
        for name, blob in self.media.values():
            if Path(name).name != name or name in (".", "..") or "\\" in name:
                raise Refused(f"invalid media filename: {name}")
            (outdir / "media" / name).write_bytes(blob)
        (outdir / (self.base + ".residue.json")).write_text(
            json.dumps(self.residue, indent=2) + "\n", encoding="utf-8"
        )
        return outdir

    @property
    def actions_name(self):
        return self.base.title() + "_Actions.h"

    @classmethod
    def read(cls, outdir):
        outdir = Path(outdir)
        sources = list(outdir.glob("*.SS"))
        if len(sources) != 1:
            raise Refused(f"{outdir}: expected exactly one .SS file")
        source = sources[0]
        base = source.stem
        residue = json.loads(
            (outdir / (base + ".residue.json")).read_text(encoding="utf-8")
        )
        media = {}
        for key, name in residue["media"].items():
            if Path(name).name != name or name in (".", "..") or "\\" in name:
                raise Refused(f"invalid media filename: {name}")
            media[key] = (name, (outdir / "media" / name).read_bytes())
        actions = (outdir / (base.title() + "_Actions.h")).read_text(encoding="utf-8")
        return cls(base, source.read_text(encoding="utf-8"), actions, media, residue)


def _ident(name):
    return bool(re.fullmatch("[A-Za-z_]\\w*", name))


def _num(x):
    if x != x or x in (float("inf"), float("-inf")):
        raise Refused(f"non-finite coordinate {x!r}")
    return repr(x)


def _vec(v):
    return "(" + ", ".join(_num(x) for x in v) + ")"


def _quote(s):
    if '"' in s:
        raise Refused(f"a quote inside a string value: {s!r}")
    return '"' + s + '"'


def _looping(flags):
    for word, bit in (("CACHE", 1), ("STREAM", 4)):
        if flags & bit:
            return word
    return None


def object_properties(r):
    """Convert MxOb fields to SS properties."""
    out = []
    known = ss.ENABLED | 1 | 2 | 4 | 8
    if r["flags"] & ~known or not r["flags"] & ss.ENABLED:
        raise Refused(
            f"{r['name']!r}: flags {r['flags']:#x} outside the measured encoding"
        )
    if r["unk0x14"]:
        raise Refused(f"{r['name']!r}: unk0x14 is {r['unk0x14']}")
    if r["presenter"]:
        out.append(
            ("handlerClass", _quote(r["presenter"].decode("latin1")), ss.ATTESTED)
        )
    if r["type"] not in ss.COMPOSITE:
        out.append(
            ("fileName", _quote(r["media_src_path"].decode("latin1")), ss.ATTESTED)
        )
    if r["duration"] == -1:
        out.append(("duration", "INDEFINITE", ss.ATTESTED))
    elif r["duration"]:
        out.append(("duration", str(r["duration"]), ss.ATTESTED))
    if r["loop_count"] != 1:
        out.append(("loopCount", str(r["loop_count"]), ss.ATTESTED))
    if r["start_time"]:
        out.append(("startTime", str(r["start_time"]), ss.ATTESTED))
    for key in ("location", "direction", "up"):
        if tuple(r[key]) != ss.DEFAULTS[key]:
            out.append((key, _vec(r[key]), ss.ATTESTED))
    method = _looping(r["flags"])
    if method:
        out.append(("loopingMethod", method, ss.ATTESTED))
    if r["flags"] & ss.TRANSPARENT:
        out.append(("transparency", "TRUE", ss.ATTESTED))
    if r["type"] not in ss.COMPOSITE:
        if r["palette_management"] == 0:
            out.append(("paletteManagement", "NONE", ss.ATTESTED))
        elif r["palette_management"] != 1:
            raise Refused(f"{r['name']!r}: paletteManagement {r['palette_management']}")
        if r["frames_per_second"] != 1:
            out.append(("framesPerSecond", str(r["frames_per_second"]), ss.INFERRED))
        if r["sustain_time"]:
            out.append(
                (
                    "sustainTime",
                    "INDEFINITE" if r["sustain_time"] == -1 else str(r["sustain_time"]),
                    ss.INFERRED,
                )
            )
        if r["unk0x9c0"] or r["unk0x9c4"]:
            raise Refused(f"{r['name']!r}: unk0x9c is non-zero")
        ext = os.path.splitext(r["media_src_path"].decode("latin1"))[1].lower()
        if ss.FOURCC.get(ext, b" OBJ") != struct.pack("<I", r["media_format"]):
            out.append(
                (
                    "mediaFormat",
                    _quote(struct.pack("<I", r["media_format"]).decode("latin1")),
                    ss.INFERRED,
                )
            )
        if "volume" in r and r["volume"] != ss.DEFAULT_VOLUME:
            out.append(("volume", str(r["volume"]), ss.ATTESTED))
    if r["extra"]:
        if not r["extra"].endswith(b"\x00") or r["extra"].count(0) != 1:
            raise Refused(f"{r['name']!r}: extra is not one NUL-terminated string")
        out.append(("extra", _quote(r["extra"][:-1].decode("latin1")), ss.ATTESTED))
    return out


def _select_property(sel):
    parts = [_quote(sel["selector"].decode("latin1"))] + [
        _quote(v.decode("latin1")) for v in sel["values"]
    ]
    return ("select", "mxSelect(" + ", ".join(parts) + ")", ss.INFERRED)


def unweave(phys):
    """Recover canonical SS, media, and buffer residue."""
    base = os.path.splitext(os.path.basename(phys["path"]))[0]
    if not _ident(base):
        raise Refused("SI filename must be an identifier, e.g. ISLE.SI")
    hd, of = (si.find(phys, "mxhd"), si.find(phys, "mxof"))
    if (hd.data["ver_major"], hd.data["ver_minor"]) != (2, 2):
        raise Refused(
            f"version {hd.data['ver_major']}.{hd.data['ver_minor']}, expected 2.2"
        )
    lines, emitted, ids, media = ([], {}, [], {})
    dup = collections.Counter()
    sidx = [0]

    def walk(node, top):
        r = node.data
        kids = [c for c in node.children or [] if c.kind == "container"]
        childobs = [c for c in (kids[0].children if kids else []) if c.kind == "mxob"]
        for c in childobs:
            walk(c, False)
        name = r["name"].decode("latin1")
        if not _ident(name):
            raise Refused(f"object name {name!r} is not an identifier")
        dup[name] += 1
        key = name if dup[name] == 1 else f"{name}__{dup[name]}"
        ids.append((key, r["id"]))
        if r["type"] not in ss.COMPOSITE:
            ext = (
                ".bin"  # SI media records; extract() supplies standalone file wrappers.
            )
            media[f"{sidx[0]}:{r['id']}"] = (
                f"{sidx[0]:03d}_{r['id']:05d}_{name}{ext}",
                None,
            )
        if name in emitted:
            if emitted[name] != _shape(r, childobs):
                raise Refused(f"{name!r} is woven twice with different fields")
            return
        emitted[name] = _shape(r, childobs)
        props = object_properties(r)
        if kids and kids[0].extra and kids[0].extra["select"]:
            props.append(_select_property(kids[0].extra["select"]))
        kw = next((k for k, v in ss.KEYWORDS.items() if v == r["type"]))
        lines.append(f"{kw} {name}{(' Weave' if top else '')}")
        lines.append("{")
        for k, v, status in props:
            lines.append(
                f"    {k} = {v};"
                + ("" if status == ss.ATTESTED else "   // [inferred]")
            )
        for c in childobs:
            lines.append(f"    {c.data['name'].decode('latin1')};")
        lines.append("}")
        lines.append("")

    for i, (st, root, _mxda) in enumerate(si.streams(phys)):
        if root is None:
            raise Refused("a stream with no root object")
        sidx[0] = i
        walk(root, True)
    blobs = _media_blobs(phys)
    for key in media:
        if key not in blobs:
            raise Refused(f"{key} has no media in the file")
        media[key] = (media[key][0], blobs[key])
    settings = [
        f"    bufferSizeKB = {phys['buffer_size'] // 1024};",
        f"    buffersNum = {hd.data['buffers_num']};",
        f"    idSpace = {of.data['count']};   // [inferred] MxOf.count",
        f"    slotCount = {len(of.data['slots'])};   // [inferred] MxOf table length",
    ]
    head = (
        [
            HEADER % base,
            f'#include "{base.title()}_Actions.h"',
            "",
            "defineSettings Configuration",
            "{",
        ]
        + settings
        + ["}", ""]
    )
    source = "\r\n".join(head + lines) + "\r\n"
    hdr = [
        f"// Object IDs for {base}.SS. Repeated instances use __2, __3, ...",
        f"#ifndef {base.upper()}_ACTIONS_H",
        f"#define {base.upper()}_ACTIONS_H",
        "",
    ]
    for key, oid in ids:
        hdr.append(f"#define c_{key:<44s} {oid}")
    hdr += ["", "#endif", ""]
    residue = dict(
        source=os.path.basename(phys["path"]),
        size=phys["size"],
        version=[hd.data["ver_major"], hd.data["ver_minor"], hd.data["reserved"]],
        prepatch={str(k): v for k, v in prepatch_map(phys).items()},
        gaps=len(_nodes(phys, "gap")),
        media={k: v[0] for k, v in media.items()},
    )
    return Project(base, source, "\r\n".join(hdr), media, residue)


HEADER = """// %s.SS — canonical Weaver source generated by libweft.
// Original comments/includes cannot be recovered from SI.
// [inferred] properties use reconstructed syntax."""


def _shape(r, childobs):
    return (
        tuple(sorted(((k, v) for k, v in r.items() if k != "id"))),
        tuple(c.data["name"] for c in childobs),
    )


def _nodes(phys, kind):
    out = []

    def visit(ns):
        for n in ns:
            if n.kind == kind:
                out.append(n)
            if n.children:
                visit(n.children)

    visit(phys["root"].children)
    return sorted(out, key=lambda n: n.offset)


def _media_blobs(phys):
    """Join media payloads, keyed by stream index and object ID."""
    out = collections.defaultdict(list)
    for i, (_st, _root, mxda) in enumerate(si.streams(phys)):
        if any(c.kind == "gap" for c in mxda.children):
            for c in mxda.children:
                if c.kind == "mxch" and (not c.data["flags"] & CH_END):
                    out[f"{i}:{c.data['object']}"].append(c.extra)
            continue
        for r in layout.reassemble(mxda):
            if not r.flags & CH_END:
                out[f"{i}:{r.object}"].append(r.payload)
    return {k: b"".join(v) for k, v in out.items()}


def _skipped(phys):
    return sorted(_nodes(phys, "pad") + _nodes(phys, "gap"), key=lambda n: n.offset)


def prepatch_map(phys):
    """Recover container-size placeholders preserved in padding."""
    data, bs, out = (phys["bytes"], phys["buffer_size"], {})
    for n in _skipped(phys):
        body, o = (n.data, n.offset + (8 if n.kind == "pad" else 0))
        s = o - bs
        if s < 0:
            continue
        prev, i, ln = (data[s : s + len(body)], 0, len(body))
        if prev == body:
            continue
        while i < ln:
            if prev[i] == body[i]:
                i += 1
                continue
            j = i
            while j < ln and prev[j] != body[j]:
                j += 1
            for st in range(i, max(-1, i - 4), -1):
                if data[s + st - 4 : s + st] in (b"LIST", b"MxSt", b"RIFF", b"MxDa"):
                    out[s + st] = struct.unpack_from("<I", bytes(body), st)[0]
                    break
            else:
                raise Refused(
                    f"pad at {n.offset}: bytes {s + i}..{s + j} differ from the previous buffer and sit in no container header"
                )
            i = j
    return out


def derive_pads(data, buffer_size, pad_spans, prepatch):
    """Reproduce skipped bytes using the previous contents of the write buffer."""
    stream = bytearray(data)
    for off, val in prepatch.items():
        struct.pack_into("<I", stream, int(off), val)
    shadow = bytearray(bytes([UNINIT]) * buffer_size)
    out, pos = ({}, 0)

    def write(lo, hi):
        while lo < hi:
            p = lo % buffer_size
            take = min(hi - lo, buffer_size - p)
            shadow[p : p + take] = stream[lo : lo + take]
            lo += take

    for off, total, header in pad_spans:
        write(pos, off + header)
        st = (off + header) % buffer_size
        out[off] = bytes(shadow[st : st + total - header])
        pos = off + total
    return out


def _body_len(n):
    if n.kind == "pad":
        return len(n.data)
    if n.kind == "mxhd":
        return 12
    if n.kind == "mxof":
        return 4 + 4 * len(n.data["slots"])
    if n.kind == "mxch":
        return 14 + len(n.extra)
    if n.kind == "mxob":
        return len(si.pack_object(n.data)) + sum(_chunk_len(c) for c in n.children)
    if n.kind == "container":
        head = len(n.form or b"")
        if n.extra:
            head += len(n.extra["select"]["raw"]) if n.extra["select"] else 4
        return head + sum(_chunk_len(c) for c in n.children)
    raise Refused(f"cannot size {n.kind}")


def _chunk_len(n):
    if n.kind == "gap":
        return len(n.data)
    b = _body_len(n)
    return 8 + b + (b & 1)


def _mxob_node(rec, children, select=None):
    n = si.Node("mxob", 0, tag=b"MxOb", data=rec, children=[])
    if children:
        lst = si.Node("container", 0, tag=b"LIST", form=b"MxCh", children=children)
        lst.extra = dict(count=len(children), select=select)
        n.children.append(lst)
    return n


def _select_raw(prop):
    name, args = prop.value
    if name != "mxSelect" or len(args) < 2:
        raise Refused('select must be mxSelect("selector", "value", ...)')
    sel = args[0].encode("latin1")
    vals = [a.encode("latin1") for a in args[1:]]
    return dict(
        selector=sel,
        values=vals,
        raw=sel
        + b"\x00"
        + struct.pack("<I", len(vals))
        + b"".join(v + b"\x00" for v in vals),
    )


def read_actions(text):
    ids = {}
    for m in re.finditer("^#define\\s+c_(\\w+)\\s+(-?\\d+)", text, re.M):
        ids[m.group(1)] = int(m.group(2))
    return ids


def build_objects(prog, ids):
    """Build object trees in weave order."""
    dup, macros = (collections.Counter(), prog.unit.macros)
    roots = []

    def build(name, top):
        o = prog.resolve(name)
        if o is None:
            raise Refused(f"{name!r} is used but never defined")
        kids = [build(m, False) for m in o.members]
        dup[name] += 1
        key = name if dup[name] == 1 else f"{name}__{dup[name]}"
        if key not in ids:
            raise Refused(f"no id for {key} in the action table")
        rec = ss.record(o, ids[key], macros)
        sel = _select_raw(o.props["select"]) if "select" in o.props else None
        if sel and len(sel["values"]) != len(kids):
            raise Refused(
                f"{name}: {len(sel['values'])} select values for {len(kids)} members"
            )
        return _mxob_node(rec, kids, sel)

    for r in prog.roots:
        roots.append(build(r.name, True))
    return roots


def weave(project, media=None, fill_skipped=True):
    """Compile a project to SI bytes."""
    prog = ss.elaborate(ss.preprocess_text(project.source, project.base + ".SS"))
    ids = read_actions(project.actions)
    media = media if media is not None else {k: v[1] for k, v in project.media.items()}
    st = project.residue
    bs = int(prog.settings["bufferSizeKB"]) * 1024
    if bs < 16 * 1024:
        raise Refused("bufferSizeKB must be at least 16")
    hd = si.Node(
        "mxhd",
        0,
        tag=b"MxHd",
        data=dict(
            ver_minor=st["version"][1],
            ver_major=st["version"][0],
            buffer_size=bs,
            buffers_num=int(prog.settings["buffersNum"]),
            reserved=st["version"][2],
        ),
    )
    of = si.Node(
        "mxof",
        0,
        tag=b"MxOf",
        data=dict(
            count=int(prog.settings["idSpace"]),
            slots=[0] * int(prog.settings["slotCount"]),
        ),
    )
    roots = build_objects(prog, ids)
    pos = 8 + 4
    pos += _chunk_len(hd) + _chunk_len(of)
    pos += 8 + 4
    kids, kids_streams = ([], [])
    for root in roots:
        head = 8 + _chunk_len(root) + MXST_HEADER_TAIL
        room = bs - pos % bs
        if head > bs:
            raise Refused("object tree does not fit in one stream header buffer")
        if head > room:
            kids.append(_pad(pos, room))
            pos += room
        st_node = si.Node("container", pos, tag=b"MxSt", children=[root])
        start = pos + 8 + _chunk_len(root) + MXST_HEADER_TAIL
        recs = _records(root, media, len(kids_streams), bs)
        kids_streams.append(root)
        nodes, end = layout.layout(recs, start, bs, fill_gaps=True)
        mxda = si.Node(
            "container",
            start - MXST_HEADER_TAIL,
            tag=b"LIST",
            form=b"MxDa",
            children=nodes,
        )
        st_node.children.append(mxda)
        pos = end
        kids.append(st_node)
    tail = bs - pos % bs if pos % bs else 0
    if tail:
        if tail < 8:
            raise Refused(
                f"{tail} bytes left in the last buffer, too few for a pad chunk"
            )
        kids.append(_pad(pos, tail))
        pos += tail
    lst = si.Node("container", 0, tag=b"LIST", form=b"MxSt", children=kids)
    riff = si.Node("container", 0, tag=b"RIFF", form=b"OMNI", children=[hd, of, lst])
    phys = dict(root=si.Node("file", 0, children=[riff]), buffer_size=bs)
    data = si.emit(phys)
    if not fill_skipped:
        return data
    skipped = _flat_pads(kids)
    spans = [(n.offset, n.length, 8 if n.kind == "pad" else 0) for n in skipped]
    bodies = derive_pads(
        data, bs, spans, {int(k): v for k, v in st.get("prepatch", {}).items()}
    )
    for n in skipped:
        n.data = bodies[n.offset]
    return si.emit(phys)


def _pad(offset, total):
    return si.Node("pad", offset, tag=b"pad ", data=b"\x00" * (total - 8), length=total)


def _flat_pads(kids):
    out = []
    for n in kids:
        if n.kind in ("pad", "gap"):
            out.append(n)
        elif n.children:
            out.extend(_flat_pads(n.children))
    return sorted(out, key=lambda n: n.offset)


def _records(root, media, sidx, buffer_size):
    """Cut media into records, then schedule them."""
    tree = schedule.tree(si.Node("container", 0, tag=b"MxSt", children=[root]))
    blobs, cuts = ({}, {})
    stack = [tree]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.composite:
            continue
        key = f"{sidx}:{n.id}"
        if key not in media:
            raise Refused(f"no media for {key} ({n.name.decode('latin1')})")
        blobs[n.id] = media[key]
        cuts[n.id] = schedule.cut(n.fields, media[key])
    sched, _warn = schedule.simulate(tree, cuts)
    taken = collections.defaultdict(int)
    out = []
    for oid, time, size, end in sched:
        if end:
            out.append(layout.Record(CH_END, oid, time, b""))
            continue
        o = taken[oid]
        out.append(layout.Record(0, oid, time, blobs[oid][o : o + size]))
        taken[oid] = o + size
    return out
