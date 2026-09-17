"""Independent geometric expectations for complex, source-backed wall profiles."""
from dataclasses import replace
import math
import unittest

from shapely.geometry import Polygon

from archiagent.evidence import SourceEntity
from archiagent.geometry.profiles import (detect_wall_profiles, profile_polygon,
    reviewed_wall_profiles, detect_exploded_hatch_profiles)
from archiagent.primitives import Primitive, PrimitiveSet


L_SHAPE = ((0, 0), (8, 0), (8, .5), (.5, .5), (.5, 6), (0, 6), (0, 0))


def source(entities=(), primitives=()):
    return PrimitiveSet(tuple(primitives), (), 100, 100, "generated", "generated", tuple(entities))


def hatch(coords=L_SHAPE, *, sid="h", holes=()):
    return SourceEntity(sid, "HATCH", "walls", coords, closed=True, holes=holes)


class WallProfilesChecks(unittest.TestCase):
    def test_l_wall_retains_concave_corner_and_measured_area(self):
        profiles, warnings = detect_wall_profiles(source((hatch(),)), {"walls"}, 1)
        self.assertEqual(warnings, ())
        self.assertEqual(len(profiles), 1)
        polygon = profile_polygon(profiles[0])
        self.assertAlmostEqual(polygon.area, 6.75)
        self.assertEqual(polygon.symmetric_difference(Polygon(L_SHAPE)).area, 0)
        self.assertEqual(profiles[0].source_ids, ("h",))
        self.assertEqual(profiles[0].review_status, "accepted_by_rule")

    def test_rotated_scaled_evidence_preserves_shape_and_holes(self):
        outer = ((0,0), (10,0), (10,8), (0,8), (0,0))
        inner = ((.5,.5), (9.5,.5), (9.5,7.5), (.5,7.5), (.5,.5))
        angle = math.radians(23)
        def transform(ring):
            return tuple((12*(x*math.cos(angle)-y*math.sin(angle)),
                          12*(x*math.sin(angle)+y*math.cos(angle))) for x,y in ring)
        profiles, warnings = detect_wall_profiles(
            source((hatch(transform(outer), holes=(transform(inner),)),)), {"walls"}, 12)
        self.assertEqual(warnings, ())
        self.assertEqual(len(profiles[0].holes), 1)
        self.assertAlmostEqual(profile_polygon(profiles[0]).area, 17)

    def test_hatch_components_use_boundary_records_not_parent_or_hole_fills(self):
        parent = hatch()
        component = replace(parent, id="h/boundary/0", kind="HATCH_BOUNDARY", parent_id="h")
        fill = Primitive("fill", L_SHAPE, "walls", None, None, "h", "HATCH", True)
        profiles, warnings = detect_wall_profiles(source((parent, component), (fill,)), {"walls"}, 1)
        self.assertEqual(warnings, ())
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].source_ids, ("h", "h/boundary/0"))

    def test_thin_rectangles_stay_with_existing_sweep_detector(self):
        coords = ((0,0), (8,0), (8,.5), (0,.5), (0,0))
        self.assertEqual(detect_wall_profiles(source((hatch(coords),)), {"walls"}, 1), ((), ()))

    def test_broad_complex_fill_requires_review_and_invalid_ring_is_reported(self):
        broad = tuple((x*8,y*8) for x,y in L_SHAPE)
        bad = ((0,0), (1,1), (0,1), (1,0), (0,0))
        profiles, warnings = detect_wall_profiles(source((hatch(broad), hatch(bad, sid="bad"))), {"walls"}, 1)
        self.assertEqual(profiles, ())
        self.assertEqual({w.code for w in warnings}, {"invalid_wall_profile", "wall_profile_needs_review"})

    def test_canonical_ids_are_independent_of_entity_order_and_ring_direction(self):
        second = hatch(tuple((x+20,y) for x,y in L_SHAPE), sid="b")
        a, _ = detect_wall_profiles(source((hatch(), second)), {"walls"}, 1)
        b, _ = detect_wall_profiles(source((second, hatch(tuple(reversed(L_SHAPE))))), {"walls"}, 1)
        self.assertEqual(a,b)

    def test_review_requires_valid_finite_accepted_unique_profiles_in_feet(self):
        record = {"id":"accepted", "boundary":L_SHAPE, "source_ids":["h"]}
        found = reviewed_wall_profiles([record])
        self.assertEqual(found[0].review_status, "accepted_after_review")
        for invalid in ([record, record], [{**record,"units":"in"}],
                        [{**record,"review_status":"unresolved"}],
                        [{**record,"boundary":((0,0),(1,0),(1,math.nan))}]):
            with self.assertRaises(ValueError):
                reviewed_wall_profiles(invalid)

    def test_exploded_hatch_preserves_concavity_and_does_not_guess_unhatched_cells(self):
        border = Primitive("line", L_SHAPE, "outline", None, None, "border", closed=True)
        strokes = tuple(Primitive("line", ((i+.1,.1),(i+.3,.3)), "hatch", None, None, f"s{i}")
                        for i in range(4))
        pset = source(primitives=(border, *strokes))
        found = detect_exploded_hatch_profiles(pset, {"outline"}, {"hatch"}, 1)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(profile_polygon(found[0]).area, 6.75)
        self.assertEqual(set(found[0].source_ids), {"border", "s0", "s1", "s2", "s3"})
        self.assertEqual(found, detect_exploded_hatch_profiles(
            replace(pset, primitives=tuple(reversed(pset.primitives))), {"outline"}, {"hatch"}, 1))
        self.assertEqual(detect_exploded_hatch_profiles(source(primitives=(border,)), {"outline"}, {"hatch"}, 1), ())
        with self.assertRaises(ValueError):
            detect_exploded_hatch_profiles(pset, {"outline"}, {"hatch"}, 1, max_segments=2)
        with self.assertRaises(ValueError):
            detect_exploded_hatch_profiles(pset, {"outline"}, {"outline"}, 1)
        with self.assertRaisesRegex(ValueError, "intersection limit"):
            detect_exploded_hatch_profiles(pset, {"outline"}, {"hatch"}, 1, max_intersections=2)

    def test_reviewed_profiles_integrate_without_wall_layers(self):
        from archiagent.classify.layers import StubClassifier
        from archiagent.pipeline import extract_from_dxf
        model = extract_from_dxf(source(), StubClassifier({}), units_per_foot=1,
                                 review={"wall_profiles":[{"boundary":L_SHAPE}]})
        self.assertEqual(len(model.wall_profiles), 1)
        self.assertEqual(model.envelope(), (0,0,8,6))

    def test_explicit_symbol_decision_is_not_recontextualized_by_nearby_ocr(self):
        from dataclasses import asdict
        from checks.test_opening_context import source as notes_source
        from checks.test_opening_localization import door
        from archiagent.classify.layers import StubClassifier
        from archiagent.pipeline import extract_from_dxf
        symbol = door((0,0),(3,0))
        ps = notes_source([("SILL = 3 ft", (1.5,1), .95, "OCR_TEXT")])
        model = extract_from_dxf(ps, StubClassifier({}), units_per_foot=1,
            review={"wall_profiles":[{"boundary":L_SHAPE}], "symbols":[asdict(symbol)]})
        self.assertEqual(model.symbols, (symbol,))

    def test_claimed_hatch_parent_excludes_child_contours_without_spatial_mask(self):
        from archiagent.recognition import exclude_symbol_geometry
        from archiagent.semantic import SymbolInstance
        parent = hatch(sid="fixture-hatch")
        child = replace(parent, id="fixture-boundary", kind="HATCH_BOUNDARY", parent_id=parent.id)
        # Coincident unrelated drawing geometry must survive: ownership rather
        # than proximity decides exclusion.
        neighbor = hatch(sid="unrelated-wall")
        symbol = SymbolInstance("reviewed-fixture", "furniture", (0,0), 1, 1,
                                source_ids=(parent.id,))
        pset = source((parent,child,neighbor), (
            Primitive("fill",L_SHAPE,"walls",None,None,parent.id,closed=True),
            Primitive("line",L_SHAPE,"walls",None,None,child.id,closed=True)))
        remaining = exclude_symbol_geometry(pset, (symbol,))
        self.assertEqual(remaining.primitives, ())
        self.assertEqual(remaining.entities, (neighbor,))
        profiles, _ = detect_wall_profiles(remaining, {"walls"}, 1)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].source_ids, (neighbor.id,))

    def test_claimed_insert_walks_nested_children_but_claimed_child_keeps_siblings(self):
        from archiagent.recognition import exclude_symbol_geometry
        from archiagent.semantic import SymbolInstance
        root = SourceEntity("block", "INSERT", "walls")
        nested = SourceEntity("nested", "INSERT", "walls", parent_id="block")
        child = replace(hatch(sid="leaf"), parent_id="nested")
        sibling = replace(hatch(sid="sibling"), parent_id="block")
        pset = source((root,nested,child,sibling), tuple(
            Primitive("fill",L_SHAPE,"walls",None,None,e.id,closed=True) for e in (child,sibling)))
        symbol = SymbolInstance("column", "column", (0,0), 1, 1, source_ids=(root.id,))
        self.assertEqual(exclude_symbol_geometry(pset,(symbol,)).entities, ())
        self.assertEqual(exclude_symbol_geometry(pset,(symbol,)).primitives, ())
        remaining = exclude_symbol_geometry(pset,(replace(symbol, source_ids=(child.id,)),))
        self.assertEqual(remaining.entities, (root,nested,sibling))
        self.assertEqual(tuple(p.source_id for p in remaining.primitives), (sibling.id,))


if __name__ == "__main__":
    unittest.main()
