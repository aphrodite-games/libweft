# SPDX-License-Identifier: LGPL-3.0-only
"""RIFF/OMNI records and physical layout."""

import struct
from pathlib import Path

COMPOSITE = {6, 7, 9}
ACT_, RAND = (0x00746341, 0x444E4152)
CONTAINERS = (b"RIFF", b"LIST", b"MxSt", b"MxDa")
FORMED = (b"RIFF", b"LIST")


class Unmodelled(Exception):
    """Unsupported or incomplete SI data."""


def _cstr(b, o):
    e = b.index(b"\x00", o)
    return (b[o:e], e + 1)


def unpack_object(b, o, end):
    r = {}
    r["type"] = struct.unpack_from("<H", b, o)[0]
    o += 2
    r["presenter"], o = _cstr(b, o)
    r["unk0x14"] = struct.unpack_from("<I", b, o)[0]
    o += 4
    r["name"], o = _cstr(b, o)
    r["id"], r["flags"], r["start_time"], r["duration"], r["loop_count"] = (
        struct.unpack_from("<IIiii", b, o)
    )
    o += 20
    r["location"] = struct.unpack_from("<3d", b, o)
    o += 24
    r["direction"] = struct.unpack_from("<3d", b, o)
    o += 24
    r["up"] = struct.unpack_from("<3d", b, o)
    o += 24
    xl = struct.unpack_from("<H", b, o)[0]
    o += 2
    r["extra"] = b[o : o + xl]
    o += xl
    if r["type"] not in COMPOSITE:
        r["media_src_path"], o = _cstr(b, o)
        (
            r["unk0x9c0"],
            r["unk0x9c4"],
            r["frames_per_second"],
            r["media_format"],
            r["palette_management"],
            r["sustain_time"],
        ) = struct.unpack_from("<IIiIii", b, o)
        o += 24
        if r["media_format"] == int.from_bytes(b" WAV", "little"):
            r["volume"] = struct.unpack_from("<i", b, o)[0]
            o += 4
    return (r, o)


def pack_object(r):
    out = [
        struct.pack("<H", r["type"]),
        r["presenter"],
        b"\x00",
        struct.pack("<I", r["unk0x14"]),
        r["name"],
        b"\x00",
        struct.pack(
            "<IIiii",
            r["id"],
            r["flags"],
            r["start_time"],
            r["duration"],
            r["loop_count"],
        ),
        struct.pack("<3d", *r["location"]),
        struct.pack("<3d", *r["direction"]),
        struct.pack("<3d", *r["up"]),
        struct.pack("<H", len(r["extra"])),
        r["extra"],
    ]
    if r["type"] not in COMPOSITE:
        out += [
            r["media_src_path"],
            b"\x00",
            struct.pack(
                "<IIiIii",
                r["unk0x9c0"],
                r["unk0x9c4"],
                r["frames_per_second"],
                r["media_format"],
                r["palette_management"],
                r["sustain_time"],
            ),
        ]
        if r["media_format"] == int.from_bytes(b" WAV", "little"):
            out.append(struct.pack("<i", r["volume"]))
    return b"".join(out)


class Node:
    __slots__ = ("kind", "tag", "form", "children", "offset", "length", "data", "extra")

    def __init__(self, kind, offset, **kw):
        self.kind = kind
        self.offset = offset
        self.tag = kw.get("tag")
        self.form = kw.get("form")
        self.children = kw.get("children")
        self.data = kw.get("data")
        self.extra = kw.get("extra")
        self.length = kw.get("length", 0)

    def __repr__(self):
        return f"<{self.kind} @{self.offset}+{self.length}>"


def parse(path):
    """Parse an SI file into records."""
    return loads(Path(path).read_bytes(), str(path))


def loads(b, path="<memory>"):
    if len(b) < 32:
        raise Unmodelled("truncated SI header")
    if b[0:4] != b"RIFF" or b[8:12] != b"OMNI" or b[12:16] != b"MxHd":
        raise Unmodelled(f"{path}: not a RIFF/OMNI file beginning with MxHd")
    buffer_size = struct.unpack_from("<I", b, 24)[0]
    if buffer_size < 32 or buffer_size % 1024:
        raise Unmodelled(f"invalid buffer size: {buffer_size}")
    root = Node("file", 0, children=[], length=len(b))
    spans = []
    _walk(b, 0, len(b), buffer_size, root.children, spans)
    covered = _merge(spans)
    if covered != [(0, len(b))]:
        raise Unmodelled(f"{path}: coverage is {covered[:3]}, expected [(0, {len(b)})]")
    return dict(
        path=path, bytes=b, size=len(b), buffer_size=buffer_size, root=root, spans=spans
    )


def _walk(b, o, end, buffer_size, out, spans):
    while o + 8 <= end:
        nb = (o // buffer_size + 1) * buffer_size
        if 0 < nb - o < 8:
            out.append(Node("gap", o, data=b[o:nb], length=nb - o))
            spans.append((o, nb))
            o = nb
            continue
        tag = b[o : o + 4]
        size = struct.unpack_from("<I", b, o + 4)[0]
        body, bend = (o + 8, o + 8 + size)
        if bend > end:
            raise Unmodelled(f"chunk {tag!r} at {o} claims {size} bytes, past {end}")
        if tag in CONTAINERS:
            form = b[body : body + 4] if tag in FORMED else None
            n = Node("container", o, tag=tag, form=form, children=[])
            p = body + (4 if form else 0)
            if tag == b"LIST" and form == b"MxCh":
                cnt = struct.unpack_from("<I", b, p)[0]
                sel = None
                if cnt in (ACT_, RAND):
                    q = p
                    selector, q = _cstr(b, q)
                    vcount = struct.unpack_from("<I", b, q)[0]
                    q += 4
                    vals = []
                    for _ in range(vcount):
                        v, q = _cstr(b, q)
                        vals.append(v)
                    sel = dict(selector=selector, values=vals, raw=b[p:q])
                    p = q
                else:
                    p += 4
                n.extra = dict(count=cnt, select=sel)
            spans.append((o, p))
            _walk(b, p, bend, buffer_size, n.children, spans)
            n.length = size + 8
            out.append(n)
        elif tag == b"MxOb":
            rec, p = unpack_object(b, body, bend)
            if pack_object(rec) != b[body:p]:
                raise Unmodelled(
                    f"MxOb at {o} does not re-serialise from its decoded fields"
                )
            n = Node("mxob", o, tag=tag, data=rec, children=[], length=size + 8)
            spans.append((o, p))
            _walk(b, p, bend, buffer_size, n.children, spans)
            out.append(n)
        elif tag == b"MxCh":
            fl, oid, ms, dsz = struct.unpack_from("<HIII", b, body)
            out.append(
                Node(
                    "mxch",
                    o,
                    tag=tag,
                    length=size + 8,
                    data=dict(flags=fl, object=oid, time=ms, data_size=dsz),
                    extra=b[body + 14 : bend],
                )
            )
            spans.append((o, bend))
        elif tag == b"MxHd":
            mi, ma, bs, bn, rv = struct.unpack_from("<HHIHH", b, body)
            out.append(
                Node(
                    "mxhd",
                    o,
                    tag=tag,
                    length=size + 8,
                    data=dict(
                        ver_minor=mi,
                        ver_major=ma,
                        buffer_size=bs,
                        buffers_num=bn,
                        reserved=rv,
                    ),
                )
            )
            spans.append((o, bend))
        elif tag == b"MxOf":
            count = struct.unpack_from("<I", b, body)[0]
            slots = list(struct.unpack_from("<%dI" % ((size - 4) // 4), b, body + 4))
            out.append(
                Node(
                    "mxof",
                    o,
                    tag=tag,
                    length=size + 8,
                    data=dict(count=count, slots=slots),
                )
            )
            spans.append((o, bend))
        elif tag == b"pad ":
            out.append(Node("pad", o, tag=tag, data=b[body:bend], length=size + 8))
            spans.append((o, bend))
        else:
            raise Unmodelled(f"unknown tag {tag!r} at {o}")
        o = bend
        if size & 1:
            if b[o : o + 1] != b"\x00":
                raise Unmodelled(
                    f"RIFF pad byte at {o} is {b[o : o + 1]!r}, expected NUL"
                )
            spans.append((o, o + 1))
            o += 1
    if o != end:
        raise Unmodelled(f"{end - o} bytes unclaimed before {end}")


def _merge(spans):
    """Merge adjacent byte ranges; reject overlaps."""
    spans = sorted(spans)
    merged = []
    for s, e in spans:
        if merged and s == merged[-1][1]:
            merged[-1] = (merged[-1][0], e)
        elif merged and s < merged[-1][1]:
            raise Unmodelled(f"overlapping ranges at {s}")
        else:
            merged.append((s, e))
    return merged


def emit(phys, patch=None):
    """Serialize records and rebuild stream offsets."""
    layout = {}
    _emit(phys["root"].children, 0, layout, None, patch)
    slots = _slots_from_layout(phys, layout)
    return _emit(phys["root"].children, 0, {}, slots, patch)


def find(phys, kind):
    """Find the first record of the requested kind."""
    stack = list(phys["root"].children)
    while stack:
        n = stack.pop(0)
        if n.kind == kind:
            return n
        if n.children:
            stack = list(n.children) + stack
    return None


def _slots_from_layout(phys, layout):
    """Rebuild the MxOf table from emitted stream offsets."""
    node = find(phys, "mxof")
    if node is None:
        return None
    slots = [0] * len(node.data["slots"])
    for root_id, offset in layout.items():
        if root_id < len(slots):
            slots[root_id] = offset
    return slots


def _emit(nodes, base, layout, slots, patch):
    """Emit records at absolute offsets."""
    parts, pos = ([], base)
    for n in nodes:
        if patch:
            patch(n)
        if n.kind == "gap":
            chunk = n.data
        else:
            if n.kind == "pad":
                body = n.data
            elif n.kind == "mxhd":
                d = n.data
                body = struct.pack(
                    "<HHIHH",
                    d["ver_minor"],
                    d["ver_major"],
                    d["buffer_size"],
                    d["buffers_num"],
                    d["reserved"],
                )
            elif n.kind == "mxof":
                use = slots if slots is not None else n.data["slots"]
                body = struct.pack("<I", n.data["count"]) + struct.pack(
                    "<%dI" % len(use), *use
                )
            elif n.kind == "mxch":
                d = n.data
                body = (
                    struct.pack(
                        "<HIII", d["flags"], d["object"], d["time"], d["data_size"]
                    )
                    + n.extra
                )
            elif n.kind == "mxob":
                fields = pack_object(n.data)
                body = fields + _emit(
                    n.children, pos + 8 + len(fields), layout, slots, patch
                )
            elif n.kind == "container":
                head = n.form or b""
                if n.extra:
                    head += (
                        n.extra["select"]["raw"]
                        if n.extra["select"]
                        else struct.pack("<I", n.extra["count"])
                    )
                if n.tag == b"MxSt":
                    root = next((c for c in n.children if c.kind == "mxob"), None)
                    if root is not None:
                        layout[root.data["id"]] = pos
                body = head + _emit(
                    n.children, pos + 8 + len(head), layout, slots, patch
                )
            else:
                raise Unmodelled(f"cannot emit {n.kind}")
            chunk = (
                n.tag
                + struct.pack("<I", len(body))
                + body
                + (b"\x00" if len(body) & 1 else b"")
            )
        parts.append(chunk)
        pos += len(chunk)
    return b"".join(parts)


def streams(phys):
    """(MxSt node, its root MxOb, its MxDa list) for every stream, in file order."""
    lst = next(c for c in phys["root"].children[0].children if c.form == b"MxSt")
    out = []
    for st in lst.children:
        if st.kind != "container" or st.tag != b"MxSt":
            continue
        root = next((c for c in st.children if c.kind == "mxob"), None)
        mxda = next((c for c in reversed(st.children) if c.kind == "container"), None)
        out.append((st, root, mxda))
    return out
