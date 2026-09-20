# SPDX-License-Identifier: LGPL-3.0-only
"""Media records and Weaver emission order."""

import struct

NEVER = 0xFFFFFFFF
MASK = 0xFFFFFFFF
CH_END = 2
SERIAL, SELECT = (6, 9)
COMPOSITE = (5, 6, 7, 9)
WAV, FLC, SMK, STL, EVT, OBJ = (b" WAV", b" FLC", b" SMK", b" STL", b" EVT", b" OBJ")
CACHED_SOUND = b"LegoLoadCacheSoundPresenter"


def signed(t):
    return t - 0x100000000 if t >= 0x80000000 else t


def fourcc(v):
    return struct.pack("<I", v) if v else None


class Uncuttable(Exception):
    """Unsupported or incomplete media data."""


def cut(fields, blob):
    """One leaf's media -> (header_size, [(size, time_offset), ...], end_offset)."""
    fmt = fourcc(fields.get("media_format"))
    if fmt == OBJ:
        return (len(blob), [], fields["duration"])
    if fmt == WAV:
        if len(blob) < 24:
            raise Uncuttable("WAV shorter than its 24-byte header")
        rate = struct.unpack_from("<I", blob, 8)[0]
        rest = blob[24:]
        if not rest:
            return (24, [], fields["duration"])
        if rate <= 0:
            raise Uncuttable("WAV byte rate must be positive")
        cached = fields.get("presenter") == CACHED_SOUND
        if cached:
            sizes = [len(rest)]
        else:
            sizes = [min(rate, len(rest) - o) for o in range(0, len(rest), rate)]
        out, t = ([], 0)
        for s in sizes:
            out.append((s, t))
            t += int(s * 1000.0 / rate + 0.5)
        return (24, out, fields["duration"] if cached else t)
    if fmt == FLC:
        if len(blob) < 128:
            raise Uncuttable("FLC shorter than its 128-byte header")
        frames, speed = (
            struct.unpack_from("<H", blob, 6)[0],
            struct.unpack_from("<I", blob, 16)[0],
        )
        out, o, i = ([], 128, 0)
        while o < len(blob):
            n = struct.unpack_from("<i", blob, o)[0]
            if not 0 <= n <= 64 or o + 4 + 16 * n + 6 > len(blob):
                raise Uncuttable(f"frame {i}: rect count {n} at offset {o}")
            fsz, ftype = struct.unpack_from("<IH", blob, o + 4 + 16 * n)
            if ftype != 0xF1FA:
                raise Uncuttable(f"frame {i}: chunk type {ftype:#06x}, expected 0xf1fa")
            rec = 4 + 16 * n + fsz
            if o + rec > len(blob):
                raise Uncuttable(f"frame {i}: chunk of {fsz} overruns the media")
            out.append((rec, i * speed))
            o += rec
            i += 1
        if i != frames:
            raise Uncuttable(f"{i} frames cut, header says {frames}")
        return (128, out, i * speed)
    if fmt == SMK:
        if len(blob) < 104:
            raise Uncuttable("SMK shorter than its 104-byte header")
        n = struct.unpack_from("<I", blob, 12)[0]
        rate = struct.unpack_from("<i", blob, 16)[0]
        trees = struct.unpack_from("<I", blob, 52)[0]
        head = 104 + trees + 5 * n
        if head > len(blob):
            raise Uncuttable(f"header {head} overruns the media")
        fps = 1000 // rate if rate > 0 else 100000 // -rate if rate < 0 else 10
        if fps <= 0:
            raise Uncuttable("SMK frame rate must be positive")
        sizes = [struct.unpack_from("<I", blob, 104 + 4 * i)[0] & ~3 for i in range(n)]
        if sum(sizes) != len(blob) - head:
            raise Uncuttable(
                f"frame table sums to {sum(sizes)}, body is {len(blob) - head}"
            )
        return (head, [(sizes[i], i * 1000 // fps) for i in range(n)], n * 1000 // fps)
    if fmt == STL:
        if len(blob) < 1064:
            raise Uncuttable("STL shorter than its 1064-byte header")
        return (1064, [(len(blob) - 1064, 0)], fields["duration"])
    if fmt == EVT:
        if len(blob) < 16:
            raise Uncuttable("EVT shorter than its 16-byte header")
        return (16, [(len(blob) - 16, 0)], fields["duration"])
    raise Uncuttable(f"no cutting rule for media format {fmt!r}")


class Node:
    __slots__ = (
        "id",
        "type",
        "name",
        "start",
        "duration",
        "fields",
        "children",
        "weave",
        "parent",
        "body",
        "abs_start",
        "index",
        "end_time",
        "done",
        "ready",
    )

    def __init__(self, rec, weave):
        self.id = rec["id"]
        self.type = rec["type"]
        self.name = rec["name"]
        self.start = rec["start_time"]
        self.duration = rec["duration"]
        self.fields = rec
        self.children = []
        self.weave = weave
        self.parent = None
        self.body = None
        self.abs_start = self.index = self.end_time = None
        self.done = self.ready = False

    @property
    def composite(self):
        return self.type in COMPOSITE

    @property
    def indefinite(self):
        """Indefinite duration propagates through composite actions."""
        if not self.composite:
            return self.duration == -1
        return self.type == SELECT or any(c.indefinite for c in self.children)


def tree(st):
    """Build the stream object tree in weave order."""
    counter = [0]

    def build(container, parent):
        for c in container.children or []:
            if c.kind == "mxob":
                n = Node(c.data, counter[0])
                counter[0] += 1
                n.parent = parent
                if parent is not None:
                    parent.children.append(n)
                build(c, n)
                if parent is None:
                    return n
            elif c.kind == "container":
                r = build(c, parent)
                if r is not None:
                    return r
        return None

    return build(st, None)


def simulate(root, media):
    """Schedule records for one stream."""
    out, warn = ([], [])
    pending = []
    serial = {}
    hi = {}

    def emit(node, time, size, end=False):
        out.append((node.id, time & MASK, size, end))

    def raise_hi(node, t):
        p = node.parent
        while p is not None:
            if unsigned_lt(hi.get(id(p), 0), t):
                hi[id(p)] = t
            p = p.parent

    def activate(node, base):
        node.abs_start = NEVER if base == NEVER else base + node.start & MASK
        if node.composite:
            hi.setdefault(id(node), 0)
            if node.type == SERIAL:
                serial[id(node)] = [0, node.abs_start]
                if node.children:
                    activate(node.children[0], node.abs_start)
            else:
                for c in list(node.children):
                    activate(c, node.abs_start)
            if not node.children:
                node.ready = True
                node.end_time = node.abs_start + node.duration & MASK
                pending.append(node)
            return
        hdr, body, end_off = media[node.id]
        base = NEVER if node.abs_start == NEVER else node.abs_start
        node.body = [(hdr, NEVER)] + [(sz, base + off & MASK) for sz, off in body]
        node.index = 0
        node.end_time = base + end_off & MASK
        pending.append(node)

    def clock(node):
        if node.index < len(node.body):
            return node.body[node.index][1]
        return NEVER if node.duration == -1 else node.end_time

    def ended(node):
        node.done = True
        pending.remove(node)
        parent = node.parent
        if parent is None:
            return
        if parent.type == SERIAL:
            state = serial[id(parent)]
            if node.indefinite:
                warn.append((len(out), node.name))
                state[1] = NEVER
            elif state[1] != NEVER:
                if node.composite:
                    state[1] = state[1] + node.end_time & MASK
                else:
                    state[1] = node.end_time
            state[0] += 1
            if state[0] < len(parent.children):
                activate(parent.children[state[0]], state[1])
                return
        if all(c.done for c in parent.children):
            parent.ready = True
            parent.end_time = composite_clock(parent)
            raise_hi(parent, parent.end_time)
            pending.append(parent)

    def composite_clock(node):
        if node.type == SELECT:
            return NEVER
        t = hi.get(id(node), 0)
        for p in pending:
            if not p.composite and unsigned_lt(t, p.abs_start):
                t = p.abs_start
        return t

    activate(root, 0)
    while pending:
        eligible = [p for p in pending if not p.composite or p.ready]
        n = min(
            eligible,
            key=lambda x: (signed(x.end_time if x.composite else clock(x)), x.weave),
        )
        if not n.composite and n.index < len(n.body):
            size, at = n.body[n.index]
            emit(n, at, size)
            n.index += 1
            raise_hi(n, clock(n))
            continue
        emit(n, n.end_time, 0, True)
        ended(n)
    return (out, warn)


def unsigned_lt(a, b):
    return a & MASK < b & MASK
