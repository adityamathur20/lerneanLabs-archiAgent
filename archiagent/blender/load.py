"""Load an authored IFC into Blender via Bonsai.

RUNS INSIDE BLENDER ONLY. The core archiagent package never imports this
module — Blender ships Python 3.13 while the pipeline targets 3.14.

    blender --python archiagent/blender/load.py -- /path/to/model.ifc
"""

import sys


def load(ifc_path: str) -> int:
    import bpy  # noqa: PLC0415 — Blender-only import, by design
    if not hasattr(bpy.types.Scene, "BIMProperties"):
        import addon_utils
        if addon_utils.enable("bonsai", default_set=True, persistent=False) is None:
            raise RuntimeError("Installed Bonsai could not be enabled")
    from bonsai.bim.ifc import IfcStore

    if IfcStore.get_file() is not None:
        raise RuntimeError("Bonsai already has an IFC loaded; use a fresh Blender session")
    if bpy.context.window is None:
        raise RuntimeError("No Blender window context; use the standalone mesh package builder")
    scene = bpy.data.scenes.new("ArchiAgent | IFC Import")
    bpy.context.window.scene = scene
    bpy.ops.bim.load_project(filepath=ifc_path)
    walls = [o for o in scene.objects if o.name.startswith("IfcWall")]
    print(f"loaded {len(walls)} IfcWall objects from {ifc_path}")
    return len(walls)


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender --python load.py -- <model.ifc>")
    load(argv[0])
