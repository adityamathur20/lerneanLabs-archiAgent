"""Library templates match at any uniform scale, optionally mirrored.

Unlike drawing-seeded templates, a library shape carries no real size: the scale
is derived from the anchor and bounded by the template's declared size range.
"""
import math
import unittest
from unittest import mock

from archiagent.classify.library_templates import match_library_templates, normalise
from archiagent.geometry.candidacy import yields_to_walls
from archiagent.primitives import Primitive, PrimitiveSet


def transform(points, angle=0, offset=(0, 0), scale=1, mirror=False):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    out = []
    for x, y in points:
        x = -x if mirror else x
        out.append((offset[0]+scale*(x*c-y*s), offset[1]+scale*(x*s+y*c)))
    return tuple(out)


def primitive(sid, points, closed=True, layer="fixtures"):
    return Primitive("rect" if closed else "line", tuple(points), layer, None, None,
                     source_id=sid, entity_type="LWPOLYLINE" if closed else "LINE",
                     closed=closed)


def drawing(parts):
    return PrimitiveSet(tuple(parts), (), 100, 100, "lib-source.dxf", "local-source")


# Asymmetric, so a mirrored copy is distinguishable from a rotated one.
# Larger bounding side is 3, so an instance drawn at raw scale k measures 3k feet.
L_SHAPE = ((0, 0), (2, 0), (2, 1), (1, 1), (1, 3), (0, 3), (0, 0))
# Same signature as L_SHAPE (closed, six segments, no holes), different shape.
ZIGZAG = ((0, 0), (2, 0), (2, 1), (1, 2), (1, 3), (0, 3), (0, 0))


def template(points=L_SHAPE, **kwargs):
    record = {"id": "tmpl-1", "kind": "plumbing", "subtype": "wc",
              "geometry": normalise([{"category": "closed", "points": points, "holes": []}]),
              "size_ft": {"min": 0.5, "max": 20.0}, "status": "reviewed"}
    record.update(kwargs)
    return record


class SymbolLibraryChecks(unittest.TestCase):
    def test_matches_every_uniform_scale_within_the_size_range(self):
        ps = drawing([primitive("one", L_SHAPE),
                      primitive("big", transform(L_SHAPE, 0, (40, 0), 2.5)),
                      primitive("small", transform(L_SHAPE, 90, (10, 10), .4))])
        symbols, issues = match_library_templates(ps, 1, [template()])
        self.assertEqual({s.source_ids for s in symbols},
                         {("one",), ("big",), ("small",)})
        self.assertEqual(issues, ())
        sizes = sorted(round(float(dict(s.properties)["library_scale_ft"]), 6) for s in symbols)
        self.assertEqual(sizes, [1.2, 3.0, 7.5])

    def test_instances_outside_the_size_range_are_reported_not_matched(self):
        ps = drawing([primitive("in-range", L_SHAPE),
                      primitive("too-big", transform(L_SHAPE, 0, (40, 0), 2.5))])
        symbols, issues = match_library_templates(
            ps, 1, [template(size_ft={"min": 2.5, "max": 4.0})])
        self.assertEqual({s.source_ids for s in symbols}, {("in-range",)})
        self.assertEqual([i.code for i in issues], ["symbol_size_out_of_range"])

    def test_mirrored_copies_match_only_when_the_template_allows_it(self):
        ps = drawing([primitive("plain", L_SHAPE),
                      primitive("mirrored", transform(L_SHAPE, 0, (30, 0), 1, mirror=True))])
        guarded, _ = match_library_templates(ps, 1, [template()])
        self.assertEqual({s.source_ids for s in guarded}, {("plain",)})
        allowed, _ = match_library_templates(ps, 1, [template(mirror_allowed=True)])
        self.assertEqual({s.source_ids for s in allowed}, {("plain",), ("mirrored",)})
        self.assertEqual({dict(s.properties)["library_mirrored"] for s in allowed},
                         {"False", "True"})

    def test_a_different_shape_of_the_same_signature_does_not_match(self):
        ps = drawing([primitive("target", L_SHAPE), primitive("decoy", transform(ZIGZAG, 0, (30, 0)))])
        symbols, _ = match_library_templates(ps, 1, [template()])
        self.assertEqual({s.source_ids for s in symbols}, {("target",)})

    def test_library_evidence_keeps_matches_out_of_wall_detection(self):
        symbols, _ = match_library_templates(drawing([primitive("one", L_SHAPE)]), 1, [template()])
        self.assertTrue(symbols)
        for s in symbols:
            self.assertEqual(s.evidence, "symbol-library")
            # exclude_symbol_geometry only removes symbols that do not yield;
            # a yielding fixture would flow back into wall detection.
            self.assertFalse(yields_to_walls(s))

    def test_a_match_covering_only_part_of_an_entity_is_discarded(self):
        # Both primitives belong to one source entity, so claiming the ring
        # alone would erase the remainder of that entity.
        ps = drawing([primitive("shared", L_SHAPE),
                      primitive("shared", ((9, 9), (12, 12)), closed=False)])
        symbols, _ = match_library_templates(ps, 1, [template()])
        self.assertEqual(symbols, ())


if __name__ == "__main__":
    unittest.main()


# --- an over-budget template is skipped, not fatal -------------------------------

SQUARE = ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))      # a closed four-segment shape: its own anchor group


class BudgetChecks(unittest.TestCase):
    def two_templates(self):
        return [template(id="common-l"), template(SQUARE, id="rare-square")]

    def test_a_template_over_the_trial_budget_is_skipped_with_a_warning_and_the_rest_still_match(self):
        ps = drawing([primitive("l1", L_SHAPE), primitive("l2", transform(L_SHAPE, 0, (30, 0), 1)),
                      primitive("l3", transform(L_SHAPE, 0, (60, 0), 1)), primitive("sq", SQUARE)])
        # common-l has 3 anchor spots x 4 angles = 12 trials; rare-square has 1 x 4 = 4.
        symbols, issues = match_library_templates(ps, 1, self.two_templates(), max_candidates=5)
        self.assertEqual({s.source_ids for s in symbols}, {("sq",)})
        skipped = [i for i in issues if i.code == "library_template_skipped"]
        self.assertEqual([i.entity for i in skipped], ["common-l"])
        self.assertEqual(skipped[0].severity, "warn")
        self.assertIn("common-l", skipped[0].msg)

    def test_the_default_budget_leaves_both_matching(self):
        ps = drawing([primitive("l1", L_SHAPE), primitive("sq", SQUARE)])
        symbols, issues = match_library_templates(ps, 1, self.two_templates())
        self.assertEqual({s.source_ids for s in symbols}, {("l1",), ("sq",)})
        self.assertEqual([i for i in issues if i.code == "library_template_skipped"], [])

    def test_an_exhausted_comparison_budget_skips_instead_of_failing_the_run(self):
        from archiagent.classify import library_templates
        ps = drawing([primitive("l1", L_SHAPE), primitive("l2", transform(L_SHAPE, 0, (30, 0), 1))])
        with mock.patch.object(library_templates, "MAX_MATCH_OPERATIONS", 0):
            symbols, issues = match_library_templates(ps, 1, [template()])
        self.assertEqual(symbols, ())
        self.assertEqual([i.code for i in issues], ["library_template_skipped"])
