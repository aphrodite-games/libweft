# SPDX-License-Identifier: LGPL-3.0-only
"""Standalone media files from SI payloads. No decoding or resampling."""

import json
import re
import struct
from hashlib import sha256
from pathlib import Path

from . import schedule, si
from .weave import _media_blobs


def convert(fields, blob):
    """Return (extension, bytes), adding WAV/BMP wrappers and removing FLC dirty rectangles."""
    fmt = struct.pack("<I", fields["media_format"])
    header, records, _ = schedule.cut(fields, blob)
    if fmt == b" WAV":
        align = struct.unpack_from("<H", blob, 12)[0]
        if not align:
            raise ValueError("WAV block alignment must be positive")
        pcm = blob[24 : 24 + (len(blob) - 24) // align * align]
        body = b"WAVEfmt " + struct.pack("<I", 16) + blob[:16]
        body += b"data" + struct.pack("<I", len(pcm)) + pcm
        body += b"\0" * (len(pcm) & 1)
        return ".wav", b"RIFF" + struct.pack("<I", len(body)) + body
    if fmt == b" STL":
        return ".bmp", struct.pack(
            "<2sIHHI", b"BM", len(blob) + 14, 0, 0, 14 + 1064
        ) + blob
    if fmt == b" FLC":
        result, at, offsets = bytearray(blob[:header]), header, []
        for size, _ in records:
            count = struct.unpack_from("<i", blob, at)[0]
            offsets.append(len(result))
            result.extend(blob[at + 4 + 16 * count : at + size])
            at += size
        # The SI dirty-rectangle prefixes aren't part of an Autodesk FLC frame.
        struct.pack_into("<I", result, 0, len(result))
        struct.pack_into("<II", result, 80, *(offsets + [0, 0])[:2])
        return ".flc", bytes(result)
    if fmt == b" SMK":
        return ".smk", blob
    source = fields["media_src_path"].decode("latin1").replace("\\", "/")
    ext = Path(source).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        ext = ".bin"
    return ext, blob


def extract(path, outdir):
    """Export each leaf once per stream, with an index of IDs and original filenames."""
    phys = si.parse(path)
    blobs = _media_blobs(phys)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=False)
    index = []
    for stream, (_, root, _) in enumerate(si.streams(phys)):
        todo = [root]
        while todo:
            node = todo.pop()
            todo.extend(reversed(node.children or []))
            if node.kind != "mxob" or node.data["type"] in si.COMPOSITE:
                continue
            fields = node.data
            name = fields["name"].decode("latin1")
            blob = blobs[f"{stream}:{fields['id']}"]
            ext, data = convert(fields, blob)
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)
            filename = f"{stream:03d}_{fields['id']:05d}_{safe}{ext}"
            (outdir / filename).write_bytes(data)
            index.append(
                dict(
                    stream=stream,
                    id=fields["id"],
                    name=name,
                    file=filename,
                    source=fields["media_src_path"].decode("latin1"),
                    sha256=sha256(data).hexdigest(),
                )
            )
            if fields["media_format"] == int.from_bytes(b" WAV", "little"):
                # Some retail sounds end with an incomplete PCM frame.
                align = struct.unpack_from("<H", blob, 12)[0]
                tail = (len(blob) - 24) % align
                if tail:
                    index[-1]["trailing_bytes"] = blob[-tail:].hex()
    (outdir / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    return index
