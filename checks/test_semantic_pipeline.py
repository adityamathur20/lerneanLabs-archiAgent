"""Generated-source integration checks: no providers, files or downloads."""
import unittest

from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.evidence import SourceEntity
from archiagent.pipeline import extract_from_dxf
from archiagent.primitives import Primitive, PrimitiveSet
from archiagent.scale.verify import Measurement
from archiagent.semantic import PlanRegion


def _rectangle(x0, y0, x1, y1):
    return ((x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0))


def source_plan():
    """10ft square, 6in walls, 2ft doorway, mixed-layer column fill."""
    rectangles = [
        ("bottom-left", (0,-.25,4,.25)),
        ("bottom-right", (6,-.25,10,.25)),
        ("top", (0,9.75,10,10.25)),
        ("left", (-.25,0,.25,10)),
        ("right", (9.75,0,10.25,10)),
        ("column-hatch", (4,5,6,5.5)),
    ]
    primitives = []
    entities = []
    for sid, extent in rectangles:
        ring = _rectangle(*extent)
        primitives.append(Primitive("fill", ring, "walls", None, None,
                                    source_id=sid, entity_type="HATCH", closed=True))
        entities.append(SourceEntity(sid,"HATCH","walls",ring,closed=True,
                                     parent_id="column-block" if sid=="column-hatch" else ""))
    entities.append(SourceEntity("column-block","INSERT","walls",block_name="COLUMN_C1"))
    door = _rectangle(4,-.1,6,.1)
    primitives.append(Primitive("rect",door,"doors",None,None,
                                source_id="door-mark",entity_type="LWPOLYLINE",closed=True))
    entities.append(SourceEntity("door-mark","LWPOLYLINE","doors",door,closed=True))
    return PrimitiveSet(tuple(primitives),(),11,11,"generated-floorplan.dxf","generated-source",
                        entities=tuple(entities),declared_units_per_foot=1)


def classifier():
    return StubClassifier({"walls": (Role.WALL_PARTITION,1),"doors": (Role.DOOR,1)})


def measurements():
    return (Measurement("width",(0,0),(10,0),10,"centerline","reviewed-measurement"),
            Measurement("height",(0,0),(0,10),10,"centerline","reviewed-measurement"))


def review():
    return {"footprints": [{"boundary": _rectangle(-.25,-.25,10.25,10.25)}],
            "symbols_verified": True, "footprint_verified": True}


def build(**kwargs):
    values = dict(units_per_foot=1,measurements=measurements(),review=review(),
                  region=PlanRegion("floor-1",(-1,-1,11,11),"plan","Ground floor",0))
    values.update(kwargs)
    return extract_from_dxf(source_plan(),classifier(),**values)


class SemanticPipelineChecks(unittest.TestCase):
    def test_instances_exclude_primitives_and_entity_fill_paths(self):
        model = build()
        self.assertEqual({s.kind for s in model.symbols}, {"door","column"})
        self.assertEqual(len([s for s in model.symbols if s.kind=="column"]),1)
        self.assertFalse(any("column-hatch" in w.source_ids for w in model.walls))
        self.assertEqual(len(model.walls),6)
        self.assertEqual(model.unresolved,())
        self.assertEqual(model.source_path,"generated-floorplan.dxf")

    def test_hosted_door_retains_threshold_area_and_source_ids(self):
        model = build()
        self.assertEqual(len(model.openings),1)
        opening = model.openings[0]
        self.assertEqual(opening.kind,"door")
        self.assertAlmostEqual(opening.width_ft,2)
        self.assertIn("door-mark",opening.source_ids)
        host = model.walls[opening.host_wall_index]
        self.assertEqual(host.detector,"opening-host")
        self.assertEqual(set(host.source_ids),{"bottom-left","bottom-right","door-mark"})
        self.assertAlmostEqual(host.thickness_ft,.5)
        self.assertEqual(len(model.spaces),1)
        self.assertAlmostEqual(model.spaces[0].area_sqft,90.75)

    def test_reviewed_dimensions_and_exterior_pass_geometric_gates(self):
        model = build()
        self.assertTrue(model.scale_verified)
        self.assertTrue(model.footprint_verified)
        self.assertTrue(model.symbols_verified)
        self.assertEqual(len(model.dimension_checks),2)
        self.assertTrue(all(c.status=="verified" and c.error_in<1e-6 for c in model.dimension_checks))
        self.assertAlmostEqual(model.footprints[0].area_sqft,110.25)
        self.assertFalse([i for i in model.issues if i.severity=="error"])
        self.assertTrue(model.assumptions)  # Vertical dimensions remain disclosed.

    def test_inferred_footprint_and_missing_dimensions_do_not_pass(self):
        model = build(review={},measurements=())
        self.assertFalse(model.scale_verified)
        self.assertFalse(model.footprint_verified)
        codes = {i.code for i in model.issues}
        self.assertIn("scale_unverified",codes)
        self.assertIn("model_dimensions_unverified",codes)
        self.assertIn("footprint_unverified",codes)
        self.assertIn("symbol_interpretation_unverified",codes)

    def test_dimensions_and_footprint_alone_do_not_verify_symbols(self):
        model = build(review={"footprints": review()["footprints"], "footprint_verified": True})
        self.assertTrue(model.scale_verified)
        self.assertTrue(model.footprint_verified)
        self.assertFalse(model.symbols_verified)
        self.assertIn("symbol_interpretation_unverified", {i.code for i in model.issues})

    def test_supplied_contour_is_not_automatically_marked_reviewed(self):
        model = build(review={"footprints": review()["footprints"]})
        self.assertTrue(model.footprints)
        self.assertFalse(model.footprint_verified)
        self.assertIn("footprint_unverified", {i.code for i in model.issues})

    def test_wrong_units_fail_measured_model_gate(self):
        model = build(units_per_foot=2)
        self.assertFalse(model.scale_verified)
        self.assertTrue(any(c.status=="failed" for c in model.dimension_checks))
        self.assertTrue(any(i.code=="model_dimension_gate_failed" for i in model.issues))

    def test_legacy_symbol_ids_and_collinear_extents_are_consistent(self):
        from archiagent.recognition import recognize_symbols, exclude_symbol_geometry
        # Last source point is in the middle: source order must not shorten a
        # collinear window symbol's measured extent.
        line = Primitive("line",((0,0),(4,0),(2,0)),"windows",None,None)
        ps = PrimitiveSet((line,),(),4,1,"legacy","legacy")
        symbols = recognize_symbols(ps,1)
        self.assertEqual(len(symbols),1)
        self.assertAlmostEqual(symbols[0].width_ft,4)
        self.assertEqual(exclude_symbol_geometry(ps,symbols).primitives,())

    def test_reviewed_outer_ring_retains_separate_authoritative_void(self):
        reviewed = review()
        reviewed["voids"] = [_rectangle(7,7,8,8)]
        model = build(review=reviewed)
        self.assertEqual(len(model.footprints[0].holes),1)
        self.assertAlmostEqual(model.footprints[0].area_sqft,109.25)


if __name__ == "__main__":
    unittest.main()
