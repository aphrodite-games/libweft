# SPDX-License-Identifier: LGPL-3.0-only
"""Byte comparison and checks against original Weaver logs."""

import gzip
import re
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from . import decompile, rebuild, si
from .weave import _nodes

_CHUNK = re.compile(r"\(:stream:(\d+), time:(-?\d+), size:(\d+)\)( |  - end of stream)")
_OTHER = re.compile(
    r"(?:Created Object: .*|Opened file: .*|weaving object: .* \(stream:\d+\)|"
    r"\* (?:Parsing|Weaving) .*| \*\* data rate \d+ KBytes/second \*\*|"
    r" \*\* stream:\d+, time:-?\d+, size:\d+ \*\*|"
    r"INDEFINITE duration in serial action: .* |)"
)


def log_records(raw):
    """Yield (id, signed time, payload bytes, buffer, end) in emission order."""
    buffer = None
    for number, line in enumerate(raw.decode("latin1").splitlines(), 1):
        chunk = _CHUNK.fullmatch(line)
        boundary = re.fullmatch(r"buffer (\d+)", line)
        if boundary:
            buffer = int(boundary[1])
        elif chunk:
            if buffer is None:
                raise ValueError(f"log line {number}: emission before first buffer")
            yield (*map(int, chunk.group(1, 2, 3)), buffer, chunk[4] != " ")
        elif not _OTHER.fullmatch(line):
            raise ValueError(f"log line {number}: unrecognised line {line!r}")


def compare_log(raw, phys):
    """Check every physical chunk against the original compiler's emission record."""
    expected = list(log_records(raw))
    chunks = _nodes(phys, "mxch")
    if len(expected) != len(chunks):
        raise ValueError(
            f"log has {len(expected)} emissions; SI has {len(chunks)} chunks"
        )
    for index, (want, chunk) in enumerate(zip(expected, chunks)):
        fields = chunk.data
        time = fields["time"]
        if time >= 0x80000000:
            time -= 0x100000000
        got = (
            fields["object"],
            time,
            len(chunk.extra),
            chunk.offset // phys["buffer_size"],
            bool(fields["flags"] & 2),
        )
        if got != want:
            raise ValueError(
                f"emission {index} at SI offset {chunk.offset}: log {want}, SI {got}"
            )
    return len(chunks)


def roundtrip(path, log=None):
    """Write a project to disk, read it back, rebuild, and compare every byte."""
    path = Path(path)
    original = path.read_bytes()
    with TemporaryDirectory(prefix="libweft-") as temp:
        directory = Path(temp) / path.stem
        decompile(path).write(directory)
        rebuilt = rebuild(directory)
    if rebuilt != original:
        at = next(
            (i for i, (a, b) in enumerate(zip(original, rebuilt)) if a != b),
            min(len(original), len(rebuilt)),
        )
        raise ValueError(f"{path}: round trip differs at byte {at}")
    report = dict(
        file=path.name,
        bytes=len(original),
        original_sha256=sha256(original).hexdigest(),
        rebuilt_sha256=sha256(rebuilt).hexdigest(),
        exact=True,
    )
    if log is not None:
        log = Path(log)
        raw = (
            gzip.decompress(log.read_bytes())
            if log.suffix == ".gz"
            else log.read_bytes()
        )
        report["log"] = dict(
            file=log.name,
            sha256=sha256(raw).hexdigest(),
            emissions=compare_log(raw, si.loads(rebuilt)),
            exact=True,
        )
    return report
