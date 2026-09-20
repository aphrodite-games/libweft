# SPDX-License-Identifier: LGPL-3.0-only
"""Chunk splitting and buffer boundaries."""

from . import si

CH_END, CH_SPLIT = (2, 16)
MXCH_HEADER = 14
CHUNK_OVERHEAD = 8 + MXCH_HEADER
CHUNK_HEADER_ONLY = 8
# The observed layouts constrain this threshold to 9827–9830.
MAX_PADDING = 9828


class Record:
    """A logical media record, before buffer splitting."""

    __slots__ = ("flags", "object", "time", "payload")

    def __init__(self, flags, object, time, payload):
        self.flags, self.object, self.time, self.payload = (
            flags,
            object,
            time,
            payload,
        )

    def __repr__(self):
        return f"<rec obj={self.object} t={self.time} {len(self.payload)}B flags={self.flags:#x}>"


def reassemble(mxda):
    """Join split chunks into logical records."""
    recs, i, kids = ([], 0, mxda.children)
    while i < len(kids):
        c = kids[i]
        if c.kind in ("pad", "gap"):
            i += 1
            continue
        d = c.data
        if not d["flags"] & CH_SPLIT:
            recs.append(Record(d["flags"], d["object"], d["time"], c.extra))
            i += 1
            continue
        parts, total, obj, t, flags = (
            [],
            d["data_size"],
            d["object"],
            d["time"],
            d["flags"],
        )
        got = 0
        while (
            i < len(kids)
            and kids[i].kind == "mxch"
            and kids[i].data["flags"] & CH_SPLIT
            and (got < total)
        ):
            parts.append(kids[i].extra)
            got += len(kids[i].extra)
            i += 1
        payload = b"".join(parts)
        if len(payload) != total:
            raise ValueError(
                f"split run for object {obj} at t={t} reassembles to {len(payload)} bytes but its first chunk claims {total}"
            )
        recs.append(Record(flags & ~CH_SPLIT, obj, t, payload))
    return recs


def layout(
    recs, start, buffer_size, max_padding=MAX_PADDING, pad_source=None, fill_gaps=False
):
    """Place records in streaming buffers."""
    out, pos = ([], start)
    for r in recs:
        data, first = (r.payload, True)
        while True:
            remaining = buffer_size - pos % buffer_size
            need = CHUNK_OVERHEAD + len(data)
            need += need & 1
            if need <= remaining:
                flags = r.flags if first else r.flags | CH_SPLIT
                out.append(_chunk(pos, flags, r.object, r.time, len(data), data))
                pos += need
                break
            if remaining < CHUNK_HEADER_ONLY:
                if not fill_gaps:
                    raise ValueError(
                        f"{remaining} bytes left at {pos}: too few for a pad chunk"
                    )
                out.append(
                    si.Node("gap", pos, data=b"\x00" * remaining, length=remaining)
                )
                pos += remaining
                continue
            if remaining < max_padding:
                out.append(_pad(pos, remaining, pad_source))
                pos += remaining
                continue
            take = remaining - CHUNK_OVERHEAD
            take -= take & 1
            out.append(
                _chunk(
                    pos, r.flags | CH_SPLIT, r.object, r.time, len(data), data[:take]
                )
            )
            pos += CHUNK_OVERHEAD + take
            data = data[take:]
            first = False
    return (out, pos)


def _chunk(offset, flags, object_id, time, data_size, payload):
    n = si.Node(
        "mxch",
        offset,
        tag=b"MxCh",
        data=dict(flags=flags, object=object_id, time=time, data_size=data_size),
        length=8 + MXCH_HEADER + len(payload),
    )
    n.extra = payload
    return n


def _pad(offset, total, pad_source=None):
    body = (pad_source or {}).get((offset, total))
    return si.Node(
        "pad",
        offset,
        tag=b"pad ",
        data=body if body is not None else b"\x00" * (total - 8),
        length=total,
    )
