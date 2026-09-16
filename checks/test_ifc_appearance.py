"""Authored class styles survive actual IFC geometry and Blender mesh export."""
from dataclasses import replace

import ifcopenshell
import ifcopenshell.util.element
import pytest

from archiagent.blender.bridge import extract_ifc_meshes
from archiagent.ifc.appearance import PRESENTATION_PALETTE
from archiagent.ifc.author import author_building
from archiagent.ifc.inspect import validate_export
from archiagent.semantic import Opening, SymbolInstance
from checks.test_semantic_ifc import fixture_model


def test_authored_meshes_retain_reused_colors_transparency_and_identity(tmp_path):
    model = fixture_model()
    window = SymbolInstance("window", "window", (12., 4.5), 3., .5, evidence="fixture", confidence=1.)
    opening = Opening("window-opening", "window", 1, (12., 3.), (12., 6.), 4., 3.,
                      symbol_id="window", assumed_height=False)
    model = replace(model, symbols=(*model.symbols, window), openings=(*model.openings, opening))
    upper = replace(model, region_id="upper", elevation_ft=13., storey_name="Upper")
    path = author_building((model, upper), tmp_path / "styled.ifc")
    source = ifcopenshell.open(str(path))
    manifest = extract_ifc_meshes(path)
    # Walls are authored as IfcWallStandardCase and share the IfcWall preset.
    def preset_class(ifc_class):
        return "IfcWall" if ifc_class.startswith("IfcWall") else ifc_class

    assert {preset_class(e["class"]) for e in manifest["elements"]} == set(PRESENTATION_PALETTE)
    for element in manifest["elements"]:
        _, rgb, transparency = PRESENTATION_PALETTE[preset_class(element["class"])]
        assert element["materials"]
        for material in element["materials"]:
            assert material["color"] == pytest.approx(rgb)
            assert material["transparency"] == pytest.approx(transparency)
        assert element["material_ids"] == [0] * (len(element["faces"]) // 3)
        props = ifcopenshell.util.element.get_pset(source.by_guid(element["guid"]), "ArchiAgent_Provenance")
        assert "not a verified" in props["AppearanceBasis"]
    # Concrete is shared by columns/beams; styles are reused across storeys.
    assert len(source.by_type("IfcSurfaceStyle")) == len(set(PRESENTATION_PALETTE.values()))
    # Wall layer sets require a material; one is shared by every set and storey.
    assert [m.Name for m in source.by_type("IfcMaterial")] == ["Plaster"]
    report = validate_export(path, (model, upper))
    assert report["passed"], report["errors"]
    again = ifcopenshell.open(str(author_building((model, upper), tmp_path / "replay.ifc")))
    assert sorted(e.GlobalId for e in source.by_type("IfcRoot")) == sorted(e.GlobalId for e in again.by_type("IfcRoot"))
