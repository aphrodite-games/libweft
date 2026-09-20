# SPDX-License-Identifier: LGPL-3.0-only
import gzip
import json
import struct
import tempfile
import unittest
import wave
from hashlib import sha256
from pathlib import Path

from libweft import Project, decompile, extract, rebuild, si
from libweft.assets import convert
from libweft.layout import Record, layout, reassemble
from libweft.verify import compare_log, log_records, roundtrip

SOURCE = """defineSettings Configuration
{
    bufferSizeKB = 64;
    buffersNum = 2;
    idSpace = 1;
    slotCount = 1;
}
defineSound Tone Weave
{
    fileName = "tone.wav";
    duration = 1000;
    volume = 50;
}
"""
PCM = bytes(range(256)) * 32
WAV = struct.pack("<HHIIHH", 1, 1, 8192, 8192, 1, 8) + bytes(8) + PCM


def project():
    return Project(
        "TONE",
        SOURCE,
        "#define c_Tone 0\n",
        {"0:0": ("tone.bin", WAV)},
        {"version": [2, 2, 0], "prepatch": {}, "media": {"0:0": "tone.bin"}},
    )


class WeftTests(unittest.TestCase):
    def test_new_file_and_disk_roundtrip(self):
        data = rebuild(project())
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"OMNI")
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data) - 8)
        self.assertEqual(len(data) % 65536, 0)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "TONE.SI"
            path.write_bytes(data)
            self.assertTrue(roundtrip(path)["exact"])
            directory = Path(temp) / "source"
            decompile(path).write(directory)
            path.unlink()
            self.assertEqual(rebuild(directory), data)
            with self.assertRaises(FileExistsError):
                project().write(directory)

    def test_source_and_media_edits_change_output(self):
        p = project()
        original = rebuild(p)
        p.source = p.source.replace("volume = 50", "volume = 25")
        edited = rebuild(p)
        self.assertNotEqual(original, edited)
        self.assertEqual(si.find(si.loads(edited), "mxob").data["volume"], 25)
        p.media["0:0"] = ("tone.bin", WAV[:-1] + b"\0")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "TONE.SI"
            path.write_bytes(rebuild(p))
            self.assertEqual(decompile(path).media["0:0"][1], WAV[:-1] + b"\0")

    def test_duplicate_ids_in_separate_streams(self):
        p = project()
        p.source += SOURCE[SOURCE.index("defineSound") :].replace("Tone", "Other")
        p.actions += "#define c_Other 0\n"
        p.media["1:0"] = ("other.bin", WAV[:-1] + b"\0")
        p.residue["media"]["1:0"] = "other.bin"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "TONE.SI"
            path.write_bytes(rebuild(p))
            recovered = decompile(path)
            self.assertNotEqual(recovered.media["0:0"][1], recovered.media["1:0"][1])
            self.assertTrue(roundtrip(path)["exact"])

    def test_split_chunks_and_countdown(self):
        payload = bytes(range(256)) * 600
        chunks, _ = layout([Record(0, 7, 123, payload)], 65500, 65536, fill_gaps=True)
        physical = [c for c in chunks if c.kind == "mxch"]
        remaining = len(payload)
        for c in physical:
            self.assertEqual(c.data["data_size"], remaining)
            self.assertLessEqual(c.offset % 65536 + c.length, 65536)
            remaining -= len(c.extra)
        self.assertEqual(remaining, 0)
        records = reassemble(si.Node("container", 0, children=chunks))
        self.assertEqual(records[0].payload, payload)
        self.assertEqual(records[0].time, 123)

    def test_wave_export(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "TONE.SI"
            path.write_bytes(rebuild(project()))
            output = Path(temp) / "media"
            index = extract(path, output)
            with wave.open(str(output / index[0]["file"]), "rb") as sound:
                self.assertEqual(sound.getparams()[:4], (1, 1, 8192, len(PCM)))
                self.assertEqual(sound.readframes(len(PCM)), PCM)

    def test_bitmap_wrapper(self):
        header = struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, 8, 0, 4, 0, 0, 256, 0)
        blob = header + bytes(1024) + b"\1\2\0\0"
        fields = {"media_format": int.from_bytes(b" STL", "little"), "duration": -1}
        extension, data = convert(fields, blob)
        self.assertEqual(extension, ".bmp")
        self.assertEqual(data[:2], b"BM")
        self.assertEqual(struct.unpack_from("<I", data, 10)[0], 1078)
        self.assertEqual(data[14:], blob)

    def test_partial_pcm_frame(self):
        p = project()
        header = struct.pack("<HHIIHHII", 1, 1, 8192, 16384, 2, 16, 5, 0)
        p.media["0:0"] = ("tone.bin", header + b"\1\2\3\4\5")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "TONE.SI"
            path.write_bytes(rebuild(p))
            out = Path(temp) / "media"
            entry = extract(path, out)[0]
            self.assertEqual(entry["trailing_bytes"], "05")
            with wave.open(str(out / entry["file"]), "rb") as sound:
                self.assertEqual(sound.getnframes(), 2)
                self.assertEqual(sound.readframes(2), b"\1\2\3\4")
            self.assertTrue(roundtrip(path)["exact"])

    def test_flags_and_duration_edits(self):
        for value, bit in [("TRUE", 8), ("FALSE", 0)]:
            p = project()
            p.source = p.source.replace(
                "duration = 1000;", f"duration = 900; transparency = {value};"
            )
            fields = si.find(si.loads(rebuild(p)), "mxob").data
            self.assertEqual(fields["flags"] & 8, bit)
            self.assertEqual(fields["duration"], 900)

    def test_flic_frame_wrapper(self):
        header = bytearray(128)
        struct.pack_into("<HHHH", header, 4, 0xAF12, 1, 2, 2)
        struct.pack_into("<I", header, 16, 100)
        frame = struct.pack("<IHH8x", 16, 0xF1FA, 0)
        blob = bytes(header) + struct.pack("<i4i", 1, 0, 0, 2, 2) + frame
        fields = {"media_format": int.from_bytes(b" FLC", "little")}
        extension, data = convert(fields, blob)
        self.assertEqual(extension, ".flc")
        self.assertEqual(struct.unpack_from("<I", data)[0], 144)
        self.assertEqual(struct.unpack_from("<II", data, 80), (128, 0))
        self.assertEqual(data[128:], frame)

    def test_media_format_override(self):
        p = project()
        p.source = p.source.replace("tone.wav", "tone.raw").replace(
            "volume = 50;", 'volume = 50; mediaFormat = " WAV";'
        )
        fields = si.find(si.loads(rebuild(p)), "mxob").data
        self.assertEqual(fields["volume"], 50)
        self.assertEqual(fields["media_src_path"], b"tone.raw")

    def test_invalid_si(self):
        for blob in (b"", b"RIFF" + bytes(100), bytes(32)):
            with self.assertRaises(si.Unmodelled):
                si.loads(blob)


class LogTests(unittest.TestCase):
    RAW = b"* Weaving sample.ss\r\nbuffer 0\r\n(:stream:7, time:-1, size:3) \r\n(:stream:7, time:0, size:0)  - end of stream\r\n"

    def phys(self):
        chunks = [
            si.Node(
                "mxch", 100, data=dict(object=7, time=0xFFFFFFFF, flags=0), extra=b"abc"
            ),
            si.Node("mxch", 126, data=dict(object=7, time=0, flags=2), extra=b""),
        ]
        return dict(root=si.Node("file", 0, children=chunks), buffer_size=65536)

    def test_alignment(self):
        self.assertEqual(compare_log(self.RAW, self.phys()), 2)

    def test_log_mutations_rejected(self):
        mutations = [
            self.RAW + b"unknown\r\n",
            self.RAW.replace(b"stream:7", b"stream:8", 1),
            self.RAW.replace(b"time:-1", b"time:-2"),
            self.RAW.replace(b"size:3", b"size:4"),
            self.RAW.replace(b"buffer 0", b"buffer 1"),
            self.RAW.replace(b"buffer 0\r\n", b""),
            self.RAW.replace(b"  - end of stream", b" "),
            b"\r\n".join(self.RAW.split(b"\r\n")[:-2]),
        ]
        for raw in mutations:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                compare_log(raw, self.phys())

    def test_original_logs(self):
        evidence = Path(__file__).resolve().parents[1] / "evidence"
        counts = {"ISLE.Log.gz": 47059, "jukebox.Log.gz": 6531, "jukeboxw.Log.gz": 68}
        for entry in json.loads((evidence / "logs.json").read_text())["logs"]:
            raw = gzip.decompress((evidence / entry["file"]).read_bytes())
            self.assertEqual(sha256(raw).hexdigest(), entry["sha256"])
            self.assertEqual(len(raw), entry["bytes"])
            self.assertEqual(len(list(log_records(raw))), counts[entry["file"]])


if __name__ == "__main__":
    unittest.main()
