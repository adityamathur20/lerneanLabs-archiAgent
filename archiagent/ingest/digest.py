"""Source identity for a DXF, stable across reconversion of the same DWG.

The ODA File Converter is deterministic apart from two HEADER variables, the
"last updated" Julian dates `$TDUPDATE` and `$TDUUPDATE`. Converting one DWG
twice (measured 2026-09-29 on `Aiims Road 3BHK Flats-vk.dwg`) produced files
differing in exactly those two values and nothing else -- not even `$TDINDWG`,
the cumulative editing time.

A plain byte hash therefore calls the same drawing a different drawing every
time it is converted, which fails freeze/replay for a reason that has nothing
to do with the drawing. Neutralising those two values, and only those two,
keeps the digest an honest content fingerprint: any real edit still changes it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

#: HEADER variables whose values are wall-clock noise, not content.
VOLATILE_HEADER_VARS: frozenset[bytes] = frozenset({b"$TDUPDATE", b"$TDUUPDATE"})

#: Binary DXF sentinel. Such a file has no line structure to walk, so it is
#: hashed whole; ODA writes ASCII, so this is a guard, not a supported path.
_BINARY_SENTINEL = b"AutoCAD Binary DXF"

_PLACEHOLDER = b"0\n"


def dxf_source_digest(path) -> str:
    """SHA-256 of a DXF with the volatile "last updated" timestamps neutralised.

    A DXF is a flat sequence of (group code, value) line pairs, so the value
    following the `$TDUPDATE`/`$TDUUPDATE` name pair is the one to replace. The
    names occur only as header variables, so no section tracking is needed.
    """
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        if stream.read(len(_BINARY_SENTINEL)) == _BINARY_SENTINEL:
            stream.seek(0)
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                h.update(chunk)
            return h.hexdigest()
        stream.seek(0)

        expect_code = True
        neutralise_next_value = False
        for line in stream:
            if expect_code:
                h.update(line)
                expect_code = False
                continue
            if neutralise_next_value:
                # Fixed bytes, so the replaced line's own ending cannot leak in.
                h.update(_PLACEHOLDER)
                neutralise_next_value = False
            else:
                h.update(line)
                neutralise_next_value = line.strip() in VOLATILE_HEADER_VARS
            expect_code = True
    return h.hexdigest()
