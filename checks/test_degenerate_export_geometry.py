"""Real drawings produce floating-point residue that export QC must not trip on."""
from dataclasses import replace
import unittest

from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import StubClassifier
from archiagent.classify.roles import Role
from archiagent.geometry.junctions import resolve_junctions
from archiagent.ifc.inspect import _expectations, _scope
from archiagent.primitives import Primitive, PrimitiveSet
from archiagent.recognition import recognize_symbols
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


if __name__ == "__main__":
    unittest.main()
