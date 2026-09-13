"""Serialize editable meshes and appearance from the authoritative IFC.

No Blender import, subprocess, GUI, network access, or geometry reconstruction.
The resulting package can be opened with the bundled standalone bpy script.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil


def _rgb(color) -> list[float]:
    """IfcOpenShell versions expose colour channels as methods or properties."""
    values = []
    for channel in ("r", "g", "b"):
        value = getattr(color, channel)
        values.append(float(value() if callable(value) else value))
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError(f"Invalid IFC surface colour: {values}")
    return values


def _material(style) -> dict:
    transparency = float(style.transparency) if style.has_transparency() else 0.0
    if not math.isfinite(transparency) or not 0 <= transparency <= 1:
        raise ValueError(f"Invalid IFC transparency: {transparency}")
    return {"name": str(style.name), "color": _rgb(style.diffuse),
            "transparency": transparency}


def extract_ifc_meshes(ifc_path: str | Path) -> dict:
    """Read IFC physical elements in stable order, raising on missing geometry.

    Spatial objects, openings, and representation-free aggregate parents are
    recorded separately; represented leaves are never silently dropped.
    Geometry is in world coordinates and SI metres, regardless of file units.
    This is a geometry export check, not an architectural accuracy check.
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element

    path = Path(ifc_path).resolve()
    source = ifcopenshell.open(str(path))
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    elements, aggregates, errors = [], [], []
    for entity in sorted(source.by_type("IfcElement"),
                         key=lambda e: (e.GlobalId or "", e.id())):
        if entity.is_a("IfcOpeningElement"):
            continue
        if not entity.Representation:
            # get_decomposition also includes voids and fillings: those must
            # never excuse a wall whose own representation has been lost.
            children = [child for relation in getattr(entity, "IsDecomposedBy", ())
                        for child in relation.RelatedObjects]
            if any(child.is_a("IfcElement") and not child.is_a("IfcOpeningElement")
                   for child in children):
                aggregates.append({"id": entity.id(), "guid": entity.GlobalId,
                                   "class": entity.is_a(), "name": entity.Name})
            else:
                errors.append(f"#{entity.id()} {entity.is_a()}: missing representation")
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, entity)
            geometry = shape.geometry
            vertices = list(geometry.verts)
            faces = list(geometry.faces)
            material_ids = list(geometry.material_ids)
            if (not vertices or len(vertices) % 3 or not faces or len(faces) % 3
                    or not all(math.isfinite(v) for v in vertices)
                    or any(i < 0 or i >= len(vertices) // 3 for i in faces)):
                raise ValueError("empty or invalid triangle mesh")
            materials = [_material(style) for style in geometry.materials]
            if len(material_ids) != len(faces) // 3:
                raise ValueError("per-face material count mismatch")
            if any(i < -1 or i >= len(materials) for i in material_ids):
                raise ValueError("invalid per-face material index")
            container = ifcopenshell.util.element.get_container(entity)
            elements.append({
                "id": entity.id(), "guid": entity.GlobalId,
                "name": entity.Name or entity.is_a(), "class": entity.is_a(),
                "storey": container.Name if container else "Uncontained",
                "container_guid": container.GlobalId if container else None,
                "vertices": vertices, "faces": faces,
                "materials": materials, "material_ids": material_ids,
            })
        except Exception as exc:
            errors.append(f"#{entity.id()} {entity.is_a()}: {exc}")
    if errors:
        raise ValueError("IFC mesh export failed:\n" + "\n".join(errors))
    if not elements:
        raise ValueError("IFC contains no represented physical elements")
    return {
        "format": "archiagent.ifc-meshes.v1",
        "source_ifc": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "ifcopenshell_version": ifcopenshell.version,
        "coordinate_system": "world_metres",
        "elements": elements, "aggregate_parents": aggregates,
        "limitations": [
            "Triangle meshes are editable but are not parametric IFC profiles.",
            "Mesh edits do not automatically update the separate IFC file.",
            "Geometry export is not independent architectural accuracy validation.",
        ],
    }


def export_blender_package(ifc_path: str | Path, output_dir: str | Path) -> dict:
    """Write mesh_manifest.json, source.ifc, and standalone build_blender.py.

    Invoke Blender separately, e.g.:
      blender --background --python build_blender.py -- \
        --manifest mesh_manifest.json --output model.blend
    """
    directory = Path(output_dir).resolve()
    for name in ("source.ifc", "mesh_manifest.json", "build_blender.py"):
        if (directory / name).exists():
            raise FileExistsError(f"Refusing to replace existing package file: {directory / name}")
    manifest = extract_ifc_meshes(ifc_path)
    directory.mkdir(parents=True, exist_ok=True)
    copied_ifc = directory / "source.ifc"
    if Path(ifc_path).resolve() != copied_ifc:
        shutil.copyfile(ifc_path, copied_ifc)
    manifest["source_ifc"] = "source.ifc"
    manifest_path = directory / "mesh_manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, allow_nan=False),
                             encoding="utf-8")
    script = directory / "build_blender.py"
    original_script = Path(__file__).with_name("build_scene.py")
    if original_script.resolve() != script:
        shutil.copyfile(original_script, script)
    return {"mesh_manifest_path": str(manifest_path),
            "blender_script_path": str(script), "ifc_path": str(copied_ifc),
            "represented_count": len(manifest["elements"])}
