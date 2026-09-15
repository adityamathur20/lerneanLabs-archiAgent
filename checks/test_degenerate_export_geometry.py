"""Real drawings produce floating-point residue that export QC must not trip on."""
from dataclasses import replace
import unittest

from shapely.geometry import LineString, Polygon

from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.evidence import SourceEntity
from archiagent.geometry.junctions import resolve_junctions
from archiagent.geometry.profiles import detect_wall_profiles
from archiagent.geometry.spaces import _spaces
from archiagent.ifc.inspect import _expectations, _scope
from archiagent.primitives import Primitive, PrimitiveSet
from archiagent.recognition import _spans_overlap, recognize_symbols
from checks.test_geometry_semantic import wall
from checks.test_semantic_pipeline import build


class DegenerateExportGeometryChecks(unittest.TestCase):
    def test_float_residue_between_ends_is_contracted_not_kept_as_a_wall(self):
        # MR RAJEEV JI TWANI JI.dxf: a doorway host ends 1e-7ft from the
        # corner where the next wall starts. Noding without repair cut the
        # perpendicular wall there, and the 1e-7ft piece failed IFC solids.
        host = wall((0, 0), (3, 0), "opening-host")
        walls = [host, wall((3, -5), (3, 1e-7)), wall((3, 1e-7), (8, 1e-7))]
        graph = resolve_junctions(walls, snap_in=0, extend_in=0, min_dangle_ft=0)
        self.assertGreaterEqual(min(w.length_ft for w in graph.walls), 1e-4)
        self.assertIn(host, graph.walls)
        self.assertEqual([(j.point, len(j.wall_indices)) for j in graph.junctions], [((3, 0), 3)])
        self.assertEqual(graph.unresolved, ((0, 0), (3, -5), (8, 1e-7)))

    def test_door_filling_its_whole_host_keeps_only_the_lintel(self):
        model = build()
        door = model.openings[0]
        host = model.walls[door.host_wall_index]
        self.assertEqual((door.start, door.end), (host.start, host.end))
        # Projection noise on a real drawing: the void reaches the host ends
        # to within 1e-12ft, which must not leave a full-height sliver.
        noisy = replace(door, start=(door.start[0]+1e-12, door.start[1]),
                        end=(door.end[0]-1e-12, door.end[1]))
        expected, _, _ = _expectations((replace(model, openings=(noisy,)),))
        bounds = expected[("IfcWall", f"W{door.host_wall_index:03d}", _scope(model))]["bounds_m"]
        self.assertAlmostEqual(bounds[2], 7*.3048, places=9)

    def test_single_line_on_beam_layer_is_not_a_structural_solid(self):
        line = Primitive("line", ((0, 0), (6, 0)), "beams", None, None)
        ps = PrimitiveSet((line,), (), 6, 1, "legacy", "legacy")
        classification = StubClassifier({"beams": (Role.BEAM_OVERHEAD, 1)}).classify(build_inventory(ps))
        symbols = recognize_symbols(ps, 1, classification)
        self.assertEqual([s for s in symbols if s.kind in {"beam", "column"}], [])

    def test_window_spans_a_residue_apart_still_overlap(self):
        # SANJANA Giriraj Ji plan: one window is drawn twice. Its spans differ
        # by ~1e-10ft on a barely sloped wall, so their exact segment
        # intersection is a point, and both voids were cut into one host.
        a = ((22691.499477418, -27530.284021177), (22693.499477418, -27530.28402114691))
        b = ((22691.499477418114, -27530.284021177), a[1])
        self.assertEqual(LineString(a).intersection(LineString(b)).length, 0)
        self.assertTrue(_spans_overlap(*a, *b))
        self.assertFalse(_spans_overlap(*a, a[1], (a[1][0]+2, a[1][1])))
        self.assertFalse(_spans_overlap(*a, (a[0][0], a[0][1]+.5), (a[1][0], a[1][1]+.5)))

    def test_zero_width_spike_is_removed_from_a_saved_outline(self):
        # Aiims Road 3BHK Flats: a footprint union returned a ring that runs
        # out and straight back, which IFC authoring refuses as invalid.
        spiked = Polygon([(0, 0), (4, 0), (4, 4), (2, 4), (2, 5), (2, 4), (0, 4), (0, 0)])
        self.assertFalse(spiked.is_valid)
        (space,) = _spaces([spiked], 1e-9)
        self.assertTrue(Polygon(space.boundary, space.holes).is_valid)
        self.assertAlmostEqual(space.area_sqft, 16)

    def test_repeated_hatch_is_one_profile_and_partial_overlap_is_left_for_review(self):
        # Floor Plan.dxf: 48 wall hatches are drawn twice, and small jamb
        # hatches overlap a wall hatch; export refused both as overlaps.
        ell = ((0, 0), (6, 0), (6, 4), (5.5, 4), (5.5, .5), (0, .5), (0, 0))
        jamb = ((5.4, 3), (6.1, 3), (6.1, 3.3), (5.45, 3.35), (5.4, 3))
        entities = tuple(SourceEntity(sid, "HATCH", "walls", ring, closed=True)
                         for sid, ring in (("h1", ell), ("h2", ell), ("j1", jamb)))
        ps = PrimitiveSet((), (), 7, 5, "synthetic", "0", entities=entities)
        profiles, warnings = detect_wall_profiles(ps, {"walls"}, 1)
        self.assertEqual([p.source_ids for p in profiles], [("h1", "h2")])
        self.assertEqual([(w.code, w.source_id) for w in warnings], [("wall_profile_needs_review", "j1")])


if __name__ == "__main__":
    unittest.main()
