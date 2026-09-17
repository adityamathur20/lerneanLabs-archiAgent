"""IFC-first mesh export preserves openings, placements, and surface styles."""

import ast
import json
from pathlib import Path

import ifcopenshell
import ifcopenshell.api.style
import pytest

from archiagent.blender.bridge import export_blender_package, extract_ifc_meshes
from archiagent.ifc.author import author_ifc
from checks.test_semantic_ifc import fixture_model


@pytest.fixture
def source_ifc(tmp_path):
    path = tmp_path / "model.ifc"
    author_ifc(fixture_model(), path)
    return path


def test_world_geometry_and_per_face_ifc_style_survive_export(source_ifc):
    source = ifcopenshell.open(str(source_ifc))
    wall = source.by_type("IfcWall")[0]
    style = ifcopenshell.api.style.add_style(source, name="Source finish")
    ifcopenshell.api.style.add_surface_style(source, style=style,
        ifc_class="IfcSurfaceStyleShading", attributes={
            "SurfaceColour": {"Name": None, "Red": .12, "Green": .34, "Blue": .56},
            "Transparency": .25})
    for item in wall.Representation.Representations[0].Items:
        ifcopenshell.api.style.assign_item_style(source, item=item, style=style)
    source.write(str(source_ifc))
    manifest = extract_ifc_meshes(source_ifc)
    represented = [e for e in source.by_type("IfcElement")
                   if e.Representation and not e.is_a("IfcOpeningElement")]
    assert len(manifest["elements"]) == len(represented)
    assert not any(e["class"] == "IfcOpeningElement" for e in manifest["elements"])
    row = next(e for e in manifest["elements"] if e["guid"] == wall.GlobalId)
    assert min(row["vertices"][2::3]) == pytest.approx(3 * .3048)
    assert row["materials"][0]["color"] == pytest.approx([.12, .34, .56])
    assert row["materials"][0]["transparency"] == .25
    assert len(row["material_ids"]) == len(row["faces"]) // 3
    assert row["material_ids"] == [0] * len(row["material_ids"])
    assert extract_ifc_meshes(source_ifc) == manifest
    json.dumps(manifest, allow_nan=False)


def test_unrepresented_physical_leaf_is_not_silently_dropped(source_ifc):
    source = ifcopenshell.open(str(source_ifc))
    source.by_type("IfcWall")[0].Representation = None
    source.write(str(source_ifc))
    with pytest.raises(ValueError, match="missing representation"):
        extract_ifc_meshes(source_ifc)


def test_package_is_portable_and_refuses_overwrites(source_ifc, tmp_path):
    result = export_blender_package(source_ifc, tmp_path / "package")
    manifest_path = Path(result["mesh_manifest_path"])
    data = json.loads(manifest_path.read_text())
    assert data["source_ifc"] == "source.ifc"
    assert (manifest_path.parent / data["source_ifc"]).read_bytes() == source_ifc.read_bytes()
    assert result["represented_count"] == len(data["elements"])
    ast.parse(Path(result["blender_script_path"]).read_text())
    with pytest.raises(FileExistsError):
        export_blender_package(source_ifc, tmp_path / "package")
