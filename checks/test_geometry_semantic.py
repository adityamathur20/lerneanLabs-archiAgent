"""Regression checks for source-supported metric reconstruction."""
import itertools
import math
import unittest

from archiagent.primitives import Primitive, PrimitiveSet
from archiagent.geometry.walls import (WallSeg, detect_walls_paired_lines,
                                       detect_walls_filled_bodies, combine_wall_hypotheses)
from archiagent.geometry.junctions import cluster_endpoints, resolve_junctions, split_through_walls
from archiagent.geometry.spaces import (detect_spaces, detect_footprints, space_boundary_graph,
                                        room_graph_with_openings)


def wall(a, b, detector="paired-line"):
    return WallSeg(a, b, .5, "walls", detector, "measured")


def primitives(lines):
    return PrimitiveSet(tuple(Primitive("line", (a, b), layer, None, None)
                              for a, b, layer in lines), (), 100, 100, "synthetic", "0")


class SemanticGeometryChecks(unittest.TestCase):
    def test_partial_faces_and_rotations(self):
        base = [((0, 0), (10, 0), "walls"), ((0, .5), (4, .5), "walls"),
                ((6, .5), (10, .5), "walls")]
        for degrees in (0, 17, 45, 90, 137):
            angle = math.radians(degrees)
            def rotate(p):
                return (p[0]*math.cos(angle)-p[1]*math.sin(angle),
                        p[0]*math.sin(angle)+p[1]*math.cos(angle))
            lines = [(rotate(a), rotate(b), layer) for a, b, layer in base]
            found = detect_walls_paired_lines(primitives(lines), {"walls"}, 1)
            self.assertEqual(len(found), 2)
            self.assertAlmostEqual(sum(w.length_ft for w in found), 8, places=7)
            self.assertTrue(all(abs(w.thickness_ft-.5) < 1e-7 for w in found))
            self.assertEqual(found, detect_walls_paired_lines(primitives(list(reversed(lines))), {"walls"}, 1))

    def test_short_adjacent_fragments_and_cross_layer(self):
        lines = [((0, 0), (4, 0), "walls")]
        lines += [((i/2, .5), ((i+1)/2, .5), "walls") for i in range(8)]
        found = detect_walls_paired_lines(primitives(lines), {"walls"}, 1)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].length_ft, 4)
        cross = [((0, 0), (4, 0), "walls"), ((0, .5), (4, .5), "other")]
        self.assertEqual(detect_walls_paired_lines(primitives(cross), {"walls", "other"}, 1), ())

    def test_nearest_junction_is_permutation_invariant(self):
        walls = [wall((0, 0), (9.7, 0)), wall((9.8, -2), (9.8, 2)),
                 wall((10.1, -2), (10.1, 2))]
        graphs = [resolve_junctions(p, snap_in=0, extend_in=6, min_dangle_ft=0)
                  for p in itertools.permutations(walls)]
        self.assertTrue(all(g == graphs[0] for g in graphs))
        horizontal = [w for w in graphs[0].walls if w.start[1] == w.end[1]]
        self.assertEqual(max(w.end[0] for w in horizontal), 9.8)

    def test_x_is_noded_without_endpoint_and_duplicate_splits(self):
        graph = resolve_junctions([wall((-5, 0), (5, 0)), wall((0, -5), (0, 5))])
        self.assertEqual(len(graph.walls), 4)
        self.assertEqual([(j.point, j.kind) for j in graph.junctions], [((0, 0), "X")])
        split = split_through_walls([wall((0, 0), (10, 0))], [(5, 0), (5, 0)])
        self.assertEqual(len(split), 2)
        self.assertTrue(all(w.length_ft > 0 for w in split))

    def test_bounded_clusters_and_no_collinear_gap_fill(self):
        walls = [wall((i*.07, 0), (i*.07, 5+i)) for i in range(4)]
        result, _ = cluster_endpoints(walls, snap_in=1)
        self.assertTrue(all(math.dist(a.start,b.start) <= 1/12 for a,b in zip(walls,result)))
        gap = [wall((0, 0), (4, 0)), wall((4.04, 0), (8, 0))]
        result = resolve_junctions(gap)
        self.assertEqual(len(result.unresolved), 4)

    def test_room_requires_explicit_opening(self):
        walls = [wall((0, 0), (4, 0)), wall((6, 0), (10, 0)),
                 wall((10, 0), (10, 10)), wall((10, 10), (0, 10)), wall((0, 10), (0, 0))]
        self.assertEqual(detect_spaces(space_boundary_graph(walls)), ())
        graph = space_boundary_graph(walls, virtual_edges=(wall((4, 0), (6, 0), "virtual-opening"),))
        self.assertEqual(len(detect_spaces(graph)), 1)
        self.assertAlmostEqual(detect_spaces(graph)[0].area_sqft, 100)
        self.assertEqual(len(walls), 5)

    def test_footprint_voids_and_clear_area(self):
        walls = [wall((0, 0), (10, 0)), wall((10, 0), (10, 10)),
                 wall((10, 10), (0, 10)), wall((0, 10), (0, 0))]
        graph = resolve_junctions(walls)
        void = ((4, 4), (6, 4), (6, 6), (4, 6), (4, 4))
        footprint = detect_footprints(graph, voids=(void,))
        self.assertEqual(len(footprint), 1)
        self.assertEqual(len(footprint[0].holes), 1)
        self.assertGreater(footprint[0].area_sqft, 96)
        self.assertAlmostEqual(detect_spaces(graph, clear_boundary=True)[0].area_sqft, 9.5**2)
        self.assertEqual(detect_footprints(resolve_junctions(walls[:2])), ())

    def test_filled_body_and_compatible_hypothesis_union(self):
        from dataclasses import replace
        from archiagent.evidence import SourceEntity
        coords = ((0, 0), (10, 0), (10, .5), (0, .5), (0, 0))
        p = Primitive("fill", coords, "walls", None, None, source_id="h1", closed=True)
        ps = replace(primitives([]), primitives=(p,))
        found = detect_walls_filled_bodies(ps, {"walls"}, 1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].source_ids, ("h1",))
        self.assertAlmostEqual(found[0].length_ft, 10)
        extra = WallSeg((5, .25), (12, .25), .5, "walls", "paired-line", "measured", ("l1",))
        merged = combine_wall_hypotheses(found, (extra,))
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0].length_ft, 12)
        self.assertEqual(merged[0].source_ids, ("h1", "l1"))
        hole = ((4, .1), (5, .1), (5, .4), (4, .4), (4, .1))
        ps = replace(ps, entities=(SourceEntity("h1", "HATCH", "walls", coords,
                                               closed=True, holes=(hole,)),))
        self.assertEqual(detect_walls_filled_bodies(ps, {"walls"}, 1), ())
        gap = replace(extra, start=(12.01, .25), end=(15, .25))
        self.assertEqual(len(combine_wall_hypotheses(merged, (gap,))), 2)

    def test_room_threshold_is_virtual_but_window_sill_stays_physical(self):
        from dataclasses import replace
        from archiagent.semantic import Opening
        walls = [replace(wall((0, 0), (10, 0)), source_ids=("wall-source",)),
                 wall((10, 0), (10, 10)), wall((10, 10), (0, 10)), wall((0, 10), (0, 0))]
        opening = Opening("o1", "door", 0, (4, 0), (6, 0), 7, 0,
                          "symbol1", ("door-source",), "single_swing")
        graph = room_graph_with_openings(walls, (opening,))
        virtual = [w for w in graph.walls if w.detector == "virtual-opening"]
        self.assertEqual(len(virtual), 1)
        self.assertEqual(virtual[0].thickness_ft, 0)
        self.assertEqual(virtual[0].source_ids, ("door-source", "wall-source"))
        # A 2ft door returns its 0.25ft interior half-threshold to the room.
        self.assertAlmostEqual(detect_spaces(graph, clear_boundary=True)[0].area_sqft,
                               9.5**2 + 2*.25)
        window = replace(opening, kind="window", sill_ft=3, height_ft=4)
        window_graph = room_graph_with_openings(walls, (window,))
        self.assertAlmostEqual(detect_spaces(window_graph, clear_boundary=True)[0].area_sqft, 9.5**2)
        self.assertEqual(walls[0].thickness_ft, .5)
        self.assertEqual(walls[0].start, (0, 0))

    def test_invalid_parameters(self):
        for value in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                resolve_junctions([], snap_in=value)
            with self.assertRaises(ValueError):
                detect_walls_paired_lines(primitives([]), set(), value)
            with self.assertRaises(ValueError):
                detect_spaces(resolve_junctions([]), min_area_sqft=value)
        with self.assertRaises(ValueError):
            detect_walls_paired_lines(primitives([]), set(), 1, min_t_in=10, max_t_in=2)


if __name__ == "__main__":
    unittest.main()
