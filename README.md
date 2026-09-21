# libweft

LEGO Island SI ↔ Weaver SS, with a media exporter. Python 3.10+, standard library only.

**104 files rebuilt byte for byte:** 26 each from English, Japanese, Korean and Beta 9.0.
[Results and SHA-256 pairs](evidence/roundtrip.json) cover 2,110,816,256 bytes.

From a checkout, using the Korean `ISLE.SI`:

```sh
python -m libweft unweave ISLE.SI isle
python -m libweft weave isle rebuilt.SI
cmp ISLE.SI rebuilt.SI
shasum -a 256 ISLE.SI rebuilt.SI
```

```text
fc6144d606dec96e928faf69dff0792f533e22214a1d9e8687665f27a43cb1b6  ISLE.SI
fc6144d606dec96e928faf69dff0792f533e22214a1d9e8687665f27a43cb1b6  rebuilt.SI
```

The Korean CD also contains logs from the original Weaver compiler. An excerpt from
[`jukeboxw.Log`](evidence/jukeboxw.Log.gz):

```text
(:stream:6, time:0, size:43788)
buffer 1
(:stream:6, time:0, size:65514)
```

All **53,658 emissions** match the rebuilt files: 47,059 in ISLE, 6,531 in JUKEBOX,
68 in JUKEBOXW. Each comparison checks object ID, signed timestamp, payload size,
buffer index and end-of-stream flag.

```sh
python -m libweft verify ISLE.SI --log evidence/ISLE.Log.gz
# ISLE.SI: identical (159,186,944 bytes)
#   47,059 log emissions matched
```

The three logs are included as gzip files, preserving their original bytes.
[Disc paths and hashes](evidence/logs.json) identify the source.

## Format notes

- Records are scheduled by signed timestamp, with weave order breaking ties. A serial
  action advances by a composite child's absolute end time, reproducing the growing
  timestamps in `ACT2MAIN.SI`.
- Split chunks stop at buffer boundaries. Their `data_size` counts the logical bytes
  still outstanding. The observed pad/split threshold is **9827–9830**; this uses 9828.
- Padding retains the previous contents of a single write buffer, initially `0xCD`.
  Container sizes are patched later, leaving their earlier values in some padding.
- Source definitions can be woven more than once with different IDs. `ISLE.Log` creates
  `SkateArms_Mask_Bitmap` once, then weaves it as 236 and 237; `HelicopterArms_Mask_Bitmap`
  becomes 247 and 248. Definitions and emitted instances need separate identities.

## Using it

`unweave` writes SS, an object-ID header, raw `media/*.bin` records and a residue JSON.
Edit the SS, then `weave` the directory. Existing output files/directories are not replaced.
The source is canonical: original comments, macros and includes cannot be recovered.
Properties marked `[inferred]` use reconstructed syntax.

This lets you:

- Change placement, timing, looping and action order in SS, then rebuild the SI.
- Assemble a new SI from source, object IDs and raw media. The tests include a synthetic
  sound file built without an original SI.
- Export the contained pictures, sounds and films for inspection, with their action IDs
  and original source paths retained in an index.

Scheduling, chunk boundaries, offsets and padding are rebuilt. The residue keeps container-size
placeholders exposed in stale buffer contents: 207 words across the corpus, 100 of them zero.
Byte identity applies to an unchanged project with its raw media and residue.

```sh
python -m libweft extract ISLE.SI media
python -m libweft verify /path/to/LEGO --json > results.json
python -m unittest discover -s tests
```

`extract` writes WAV, BMP, FLC and Smacker files, plus native model, animation and other payloads.
Its index records IDs, original paths and hashes. Incomplete trailing PCM frames are kept in
that index as hex; WAVs contain complete frames. Use `unweave` for the exact raw media.

For Python callers (optionally `pip install .`, which also adds `weft`):

```python
from libweft import decompile, rebuild, extract

project = decompile("ISLE.SI")
project.write("isle")
si_bytes = rebuild("isle")
extract("ISLE.SI", "media")
```

## Credits and license

Built with the [isledecomp/isle](https://github.com/isledecomp/isle/tree/31bd20de79df0a2d2d26b63f734e155ddd17e8ae/LEGO1/omni) decompilation as a reference
for SI records and [SIEdit/libweaver](https://github.com/isledecomp/SIEdit/blob/52083bec8e9f413d005a272ae1bdba00641f33b7/lib/interleaf.cpp) for interleaving.
The Korean disc's Weaver sources and logs supplied the original compiler evidence.
Thank you to the isledecomp contributors for making that research possible.

Code: [LGPL-3.0-only](LICENSE), with the incorporated [GPLv3 terms](COPYING).
The historical logs are Mindscape material, outside the code license. Game assets are not included.
