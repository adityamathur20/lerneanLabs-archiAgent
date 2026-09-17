"""Replay identity and geometry coverage, including profile-wall regressions."""
from dataclasses import replace

import ifcopenshell
import pytest

from archiagent.geometry.profiles import WallProfile
from archiagent.ifc.author import author_ifc
from archiagent.ifc.inspect import validate_export
from archiagent.validate import validate
from checks.test_semantic_ifc import fixture_model


def _ids(path):
    f = ifcopenshell.open(str(path))
    return sorted((e.is_a(), e.Name or "", e.GlobalId) for e in f.by_type("IfcRoot"))


def test_replay_ids_are_stable_across_output_and_source_relocation(tmp_path):
    model = fixture_model()
    first = author_ifc(model, tmp_path / "first.ifc")
    second = author_ifc(model, tmp_path / "different-output.ifc")
    assert _ids(first) == _ids(second)
    moved = replace(model, source_path="moved/source/fixture.dxf")
    third = author_ifc(moved, tmp_path / "relocated.ifc")
    assert _ids(first) == _ids(third)


def test_distinct_regions_have_distinct_ids(tmp_path):
    model = fixture_model()
    a = _ids(author_ifc(model, tmp_path / "a.ifc"))
    b = _ids(author_ifc(replace(model, region_id="new-region"), tmp_path / "b.ifc"))
    assert {i[2] for i in a}.isdisjoint({i[2] for i in b})


@pytest.mark.parametrize("cls", ["IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcColumn", "IfcBeam", "IfcOpeningElement", "IfcSpace"])
def test_every_missing_representation_fails_coverage(tmp_path, cls):
    model = fixture_model()
    if cls == "IfcWindow":
        model = replace(model, openings=(replace(model.openings[0], kind="window"),),
                        symbols=(replace(model.symbols[0], kind="window"), *model.symbols[1:]))
    elif cls == "IfcSpace":
        model = replace(model, spaces=model.footprints)
    path = author_ifc(model, tmp_path / "missing.ifc")
    f = ifcopenshell.open(str(path))
    f.by_type(cls)[0].Representation = None
    f.write(str(path))
    report = validate_export(path, model)
    assert not report["passed"]
    assert "missing_representation" in {e["code"] for e in report["errors"]}


def test_unexpected_physical_class_cannot_escape_census(tmp_path):
    model = fixture_model()
    path = author_ifc(model, tmp_path / "unexpected.ifc")
    f = ifcopenshell.open(str(path))
    f.create_entity("IfcFurniture", GlobalId=ifcopenshell.guid.new(), Name="Unvalidated chair")
    f.write(str(path))
    report = validate_export(path, model)
    assert not report["passed"]
    assert {"missing_representation", "unsupported_physical_element"} <= {e["code"] for e in report["errors"]}


def test_profile_host_replaces_sweep_and_preserves_actual_door_void(tmp_path):
    model = fixture_model()
    # An L-profile carries the door wall and a connected short return. Its
    # indexed segment remains the semantic host, without a duplicate solid.
    profile = WallProfile("l-wall", ((0., -.25), (12., -.25), (12., .25),
                                     (1., .25), (1., 3.), (0., 3.), (0., -.25)))
    model = replace(model, walls=(model.walls[0],), wall_profiles=(profile,),
                    symbols=(model.symbols[0],))
    path = author_ifc(model, tmp_path / "profile.ifc")
    f = ifcopenshell.open(str(path))
    assert len(f.by_type("IfcWall")) == 1
    assert f.by_type("IfcDoor")[0].FillsVoids[0].RelatingOpeningElement.VoidsElements[0].RelatingBuildingElement.Name == "WP:l-wall"
    report = validate_export(path, model)
    assert report["passed"], report["errors"]


def test_profile_only_wall_holes_and_bounds(tmp_path):
    profile = WallProfile("ring", ((0., 0.), (12., 0.), (12., 10.), (0., 10.), (0., 0.)),
                          (((.5, .5), (.5, 9.5), (11.5, 9.5), (11.5, .5), (.5, .5)),))
    model = replace(fixture_model(), walls=(), openings=(), symbols=(), wall_profiles=(profile,))
    assert "empty_wall_model" not in {i.code for i in validate(model)}
    report = validate_export(author_ifc(model, tmp_path / "ring.ifc"), model)
    assert report["passed"], report["errors"]


def test_partial_profile_overlap_requires_review(tmp_path):
    profile = WallProfile("partial", ((0., -.25), (3., -.25), (3., .25), (0., .25), (0., -.25)))
    model = replace(fixture_model(), wall_profiles=(profile,))
    with pytest.raises(ValueError, match="partially overlaps"):
        author_ifc(model, tmp_path / "rejected.ifc")


def test_invalid_direct_profile_rejected_before_ifc(tmp_path):
    profile = WallProfile("invalid", ((0., 0.), (1., 0.), (1., 1.)))
    model = replace(fixture_model(), wall_profiles=(profile,))
    with pytest.raises(ValueError, match="closure"):
        author_ifc(model, tmp_path / "rejected.ifc")
