"""Load an authored IFC into Blender via Bonsai.

RUNS INSIDE BLENDER ONLY. The core archiagent package never imports this
module — Blender ships Python 3.13 while the pipeline targets 3.14.

    blender --python archiagent/blender/load.py -- /path/to/model.ifc
"""

import sys


def load(ifc_path: str) -> int:
    import bpy  # noqa: PLC0415 — Blender-only import, by design

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.bim.load_project(filepath=ifc_path)
    walls = [o for o in bpy.data.objects if o.name.startswith("IfcWall")]
    print(f"loaded {len(walls)} IfcWall objects from {ifc_path}")
    return len(walls)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender --python load.py -- <model.ifc>")
    load(argv[0])
