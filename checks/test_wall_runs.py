"""Walls are grouped so a wall passing through a junction is one IFC entity."""
import unittest

from archiagent.ifc.wall_runs import build_runs
from checks.test_wall_joins_fixtures import model_with, spans


def connections(layout):
    return sorted((layout.runs[c.relating].start, layout.runs[c.related].start,
                   c.relating_type, c.related_type, c.point) for c in layout.connections)


class WallRunChecks(unittest.TestCase):
    def test_collinear_walls_chain_into_one_run(self):
        layout = build_runs(model_with([((0, 0), (4, 0), .5), ((4, 0), (9, 0), .5)]))
        self.assertEqual(spans(layout), [((0, 0), (9, 0), .5)])
        self.assertEqual(layout.connections, ())
        self.assertEqual(layout.untrimmed, ())

    def test_a_thickness_change_breaks_the_chain_and_is_untrimmed(self):
        layout = build_runs(model_with([((0, 0), (4, 0), .5), ((4, 0), (9, 0), .75)]))
        self.assertEqual(spans(layout), [((0, 0), (4, 0), .5), ((4, 0), (9, 0), .75)])
        self.assertEqual(layout.connections, ())
        self.assertEqual(layout.untrimmed, ((4, 0),))

    def test_t_chains_the_through_pair_and_the_stem_joins_along_it(self):
        layout = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (10, 0), .5),
                                        ((5, 0), (5, 4), .5)]))
        self.assertEqual(spans(layout), [((0, 0), (10, 0), .5), ((5, 0), (5, 4), .5)])
        self.assertEqual(connections(layout),
                         [((5, 0), (0, 0), "ATSTART", "ATPATH", (5, 0))])

    def test_x_chains_only_the_thicker_line_and_both_pieces_butt_into_it(self):
        layout = build_runs(model_with([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                                        ((5, -4), (5, 0), .5), ((5, 0), (5, 4), .5)]))
        self.assertEqual(spans(layout), [((0, 0), (10, 0), .75), ((5, -4), (5, 0), .5),
                                         ((5, 0), (5, 4), .5)])
        self.assertEqual(connections(layout),
                         [((5, -4), (0, 0), "ATEND", "ATPATH", (5, 0)),
                          ((5, 0), (0, 0), "ATSTART", "ATPATH", (5, 0))])
        through = next(r for r in layout.runs if r.thickness_ft == .75)
        self.assertTrue(all(through.priority > r.priority
                            for r in layout.runs if r is not through))

    def test_l_corner_is_owned_by_the_thicker_then_longer_then_first_wall(self):
        thicker = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (5, 4), .75)]))
        blue = next(r for r in thicker.runs if r.thickness_ft == .75)
        green = next(r for r in thicker.runs if r.thickness_ft == .5)
        self.assertGreater(blue.priority, green.priority)
        self.assertEqual(connections(thicker),
                         [((0, 0), (5, 0), "ATEND", "ATSTART", (5, 0))])

        longer = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (5, 9), .5)]))
        self.assertGreater(next(r for r in longer.runs if r.end == (5, 9)).priority,
                           next(r for r in longer.runs if r.end == (5, 0)).priority)

        tied = build_runs(model_with([((0, 0), (5, 0), .5), ((5, 0), (5, 5), .5)]))
        self.assertGreater(next(r for r in tied.runs if r.start == (0, 0)).priority,
                           next(r for r in tied.runs if r.start == (5, 0)).priority)

    def test_a_stem_parallel_to_its_through_wall_is_left_untrimmed(self):
        # VINAYAK APARTMENTS: a thin wall continues along the line of a thick
        # one. IfcOpenShell cannot join parallel walls, so proposing the joint
        # would delete the thin wall from the prediction while the file keeps it.
        # The real shape: a thick wall arrives at the node and continues, and a
        # thin wall leaves along exactly the same span. The thin one is neither
        # a partner (different thickness) nor a stem that can stop against it.
        layout = build_runs(model_with([((0, 0), (5, 0), .75), ((5, 0), (10, 0), .75),
                                        ((5, 0), (10, 0), .167)]))
        thin_index = next(index for index, r in enumerate(layout.runs)
                          if abs(r.thickness_ft - .167) < 1e-9)
        self.assertEqual([c for c in layout.connections
                          if thin_index in (c.relating, c.related)], [])
        self.assertIn((5, 0), layout.untrimmed)

    def test_grouping_does_not_depend_on_the_order_segments_are_written(self):
        segments = [((0, 0), (5, 0), .5), ((5, 0), (10, 0), .5), ((5, 0), (5, 4), .5)]
        first = build_runs(model_with(segments))
        shuffled = build_runs(model_with([segments[2], segments[1], segments[0]]))
        self.assertEqual(spans(first), spans(shuffled))
        self.assertEqual(connections(first), connections(shuffled))


if __name__ == "__main__":
    unittest.main()
