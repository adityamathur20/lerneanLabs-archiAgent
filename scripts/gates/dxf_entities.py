"""Dump what archiAgent's own loader sees in a DXF, for the handle-join gate.

Plan: docs/superpowers/plans/2026-10-07-mlightcad-cad-viewer.md, Task 2.

    python scripts/gates/dxf_entities.py PLAN.dxf OUT.json

Writes every top-level `SourceEntity` (the ones a user can select: geometry
expanded out of an INSERT has a synthesised id and is excluded, see
docs/superpowers/notes/2026-10-07-handle-join-spike.md) as

    {"file": ..., "nested": N, "entities": {id: {"kind", "layer", "coords"}}}

The viewer-side half (archiViewer gates/handle-join.mjs) loads the same DXF in
mlightcad and checks each id resolves to the same entity there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from archiagent.ingest.dxf_vector import load_dxf


def dump(dxf: Path) -> dict:
    # A unit ratio only rescales geometry; 1.0 keeps coordinates in source units,
    # which is what the viewer reads, and accepts a header with no $INSUNITS.
    ps, _ = load_dxf(dxf, units_per_foot=1.0)
    top = [e for e in ps.entities if not e.parent_id]
    return {
        "file": dxf.name,
        "nested": len(ps.entities) - len(top),
        "entities": {
            e.id: {"kind": e.kind, "layer": e.layer, "coords": [list(p) for p in e.coords]}
            for e in top
        },
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    Path(sys.argv[2]).write_text(json.dumps(dump(Path(sys.argv[1]))))
