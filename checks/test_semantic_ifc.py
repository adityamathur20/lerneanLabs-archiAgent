"""Acceptance regressions for source-linked BIM and conservative validation."""
from dataclasses import replace
import math

import pytest

from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.model import BuildingModel
from archiagent.scale.resolve import ScaleResult
from archiagent.semantic import Assumption, DimensionCheck, Opening, SymbolInstance
from archiagent.validate import validate


def fixture_model():
    ring = ((0., 0.), (12., 0.), (12., 10.), (0., 10.), (0., 0.))
    hole = ((4., 4.), (4., 6.), (6., 6.), (6., 4.), (4., 4.))
    walls = tuple(WallSeg(a, b, .5, "walls", "paired-line", "measured", (f"cad:{i}",))
                  for i, (a, b) in enumerate(zip(ring, ring[1:])))
    symbols = (SymbolInstance("d1", "door", (3.5, 0.), 3., .5,
                              source_ids=("insert:door",), evidence="block", confidence=1.),
               SymbolInstance("c1", "column", (2., 2.), 1., 1.,
                              source_ids=("insert:column",), evidence="block", confidence=1., height_ft=9.),
               SymbolInstance("b1", "beam", (6., 8.), 4., 1., evidence="block", confidence=1.,
                              height_ft=1., properties=(("base_height_ft", "8"),)))
    return BuildingModel(walls, (), (), (), ScaleResult(12., "clear", (0.,), 0., 1),
                         (), "fixture.dxf", "a" * 64, 10.,
                         symbols=symbols,
                         openings=(Opening("o1", "door", 0, (2., 0.), (5., 0.), 7.,
                                           symbol_id="d1", assumed_height=False),),
                         footprints=(Space(ring, 116., (hole,)),),
                         dimension_checks=(DimensionCheck("dim1", (0., 0.), (12., 0.), 12., 12., 0.,
                                                          "model-wall-endpoints", "verified"),),
                         assumptions=(Assumption("walls", "height_ft", "10", "fixture height"),),
                         region_id="plan-a", storey_name="Level 1", elevation_ft=3., scale_verified=True,
                         footprint_verified=True, symbols_verified=True)


def codes(model):
    return {i.code for i in validate(model)}


def test_unverified_scale_and_source_only_dimensions_do_not_pass():
    model = fixture_model()
    assert "scale_unverified" in codes(replace(model, scale_verified=False))
    assert "dimension_unverified" in codes(replace(model, dimension_checks=(
        replace(model.dimension_checks[0], basis="source-endpoints"),)))
    assert "model_dimensions_unverified" in codes(replace(model, dimension_checks=()))


def test_dimensions_and_footprint_do_not_verify_symbol_coverage():
    model = fixture_model()
    assert "symbol_interpretation_unverified" in codes(replace(model, symbols_verified=False))
    assert "symbol_interpretation_unverified" in codes(replace(
        model, symbols=(), openings=(), symbols_verified=False))


def test_final_dimensions_recompute_error_and_enforce_two_inches():
    model = fixture_model()
    check = replace(model.dimension_checks[0], actual_ft=12.25, error_in=0.)
    result = codes(replace(model, dimension_checks=(check,)))
    assert {"model_dimension_gate_failed", "dimension_residual_inconsistent"} <= result
    exact = replace(check, actual_ft=12. + 2. / 12., error_in=2.)
    assert "model_dimension_gate_failed" not in codes(replace(model, dimension_checks=(exact,)))


def test_openings_must_be_hosted_and_within_wall():
    model = fixture_model()
    assert "unhosted_opening_symbol" in codes(replace(model, openings=()))
    op = model.openings[0]
    assert "invalid_opening_host" in codes(replace(model, openings=(replace(op, host_wall_index=88),)))
    assert "opening_outside_host" in codes(replace(model, openings=(replace(op, end=(13., 0.)),)))
    assert "opening_outside_host" in codes(replace(model, openings=(replace(op, start=(-1., 0.)),)))
    assert "invalid_opening_host" in codes(replace(model, openings=(replace(op, host_wall_index=math.nan),)))
    assert "opening_outside_host" not in codes(replace(model, openings=(replace(op, start=op.end, end=op.start),)))
    assert "opening_outside_host" in codes(replace(model, openings=(replace(op, sill_ft=5.),)))
    assert "overlapping_openings" in codes(replace(model, openings=(op, replace(op, id="other"))))


def test_floor_is_independent_of_rooms_and_holes_are_validated():
    model = fixture_model()
    assert not [i for i in validate(model) if i.severity == "error"]
    assert "missing_footprint" in codes(replace(model, footprints=()))
    invalid = replace(model.footprints[0], holes=(((20., 20.), (21., 20.), (21., 21.), (20., 20.)),))
    assert "invalid_polygon" in codes(replace(model, footprints=(invalid,)))
    assert "empty_wall_model" in codes(replace(model, walls=(), openings=(), symbols=()))


def test_nonfinite_values_fail_before_native_geometry():
    model = fixture_model()
    bad = replace(model.walls[0], end=(math.nan, 0.))
    assert "nonfinite_geometry" in codes(replace(model, walls=(bad, *model.walls[1:])))


def test_ifc_voids_holes_types_provenance_and_elevation(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.geom
    import ifcopenshell.util.element
    import ifcopenshell.util.placement
    import ifcopenshell.util.shape
    from archiagent.ifc.author import FT, author_ifc

    model = fixture_model()
    window = SymbolInstance("win1", "window", (12., 4.5), 3., .5,
                            evidence="block", confidence=1.)
    window_opening = Opening("ow", "window", 1, (12., 3.), (12., 6.), 4., 3.,
                             symbol_id="win1", assumed_height=False)
    original_door = model.openings[0]
    reversed_door = replace(original_door, start=original_door.end, end=original_door.start)
    model = replace(model, symbols=(*model.symbols, window),
                    openings=(reversed_door, window_opening))
    path = author_ifc(model, tmp_path / "semantic.ifc")
    f = ifcopenshell.open(str(path))
    assert len(f.by_type("IfcSlab")) == 1
    assert len(f.by_type("IfcSpace")) == 0
    assert len(f.by_type("IfcColumn")) == len(f.by_type("IfcBeam")) == 1
    door = f.by_type("IfcDoor")[0]
    void = f.by_type("IfcOpeningElement")[0]
    assert door.FillsVoids[0].RelatingOpeningElement == void
    host = void.VoidsElements[0].RelatingBuildingElement
    assert host.Name == "W000"
    assert door.OverallWidth == pytest.approx(3 * FT)
    window_entity = f.by_type("IfcWindow")[0]
    assert window_entity.FillsVoids[0].RelatingOpeningElement.Name == "ow"
    assert window_entity.OverallHeight == pytest.approx(4 * FT)
    storey = f.by_type("IfcBuildingStorey")[0]
    assert storey.Name == "Level 1"
    assert storey.Elevation == pytest.approx(3 * FT)
    matrix = ifcopenshell.util.placement.get_local_placement(host.ObjectPlacement)
    assert matrix[2, 3] == pytest.approx(3 * FT)
    pset = ifcopenshell.util.element.get_psets(host)["ArchiAgent_Provenance"]
    assert pset["SourceSHA256"] == "a" * 64
    assert pset["SymbolsVerified"] is True
    assert "cad:0" in pset["SourceIds"]
    assert len(f.by_type("IfcArbitraryProfileDefWithVoids")) == 1
    settings = ifcopenshell.geom.settings()
    wall_shape = ifcopenshell.geom.create_shape(settings, host)
    slab_shape = ifcopenshell.geom.create_shape(settings, f.by_type("IfcSlab")[0])
    assert ifcopenshell.util.shape.get_volume(wall_shape.geometry) == pytest.approx((12 * .5 * 10 - 3 * .5 * 7) * FT ** 3)
    assert ifcopenshell.util.shape.get_volume(slab_shape.geometry) == pytest.approx(116 * .5 * FT ** 3)


def test_ifc_room_and_footprint_do_not_duplicate_floor(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from archiagent.ifc.author import author_ifc
    model = fixture_model()
    model = replace(model, spaces=model.footprints)
    f = ifcopenshell.open(str(author_ifc(model, tmp_path / "room.ifc")))
    assert len(f.by_type("IfcSlab")) == len(f.by_type("IfcSpace")) == 1
    assert len(f.by_type("IfcArbitraryProfileDefWithVoids")) == 2


def test_multiple_plans_require_explicit_registration(tmp_path):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    from archiagent.ifc.author import FT, author_building
    a = fixture_model()
    with pytest.raises(ValueError, match="explicit elevations"):
        author_building((a, replace(a, region_id="b", elevation_ft=None)), tmp_path / "bad.ifc")
    b = replace(a, region_id="b", elevation_ft=14., storey_name="Level 2")
    f = ifcopenshell.open(str(author_building((a, b), tmp_path / "building.ifc")))
    assert sorted(s.Elevation for s in f.by_type("IfcBuildingStorey")) == pytest.approx([3 * FT, 14 * FT])
