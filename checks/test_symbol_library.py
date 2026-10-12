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
        # common-l has 3 anchor spots: even one orientation (3 trials) exceeds a limit of 2.
        # rare-square has 1 spot: its 4 default angles do not fit, one does, so it is reduced.
        symbols, issues = match_library_templates(ps, 1, self.two_templates(), max_candidates=2)
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


# --- what kind of space a symbol belongs in -------------------------------------

class SpaceTypeChecks(unittest.TestCase):
    def test_a_template_may_say_which_spaces_it_belongs_in_and_how_strongly(self):
        from archiagent.classify.library_templates import _validated
        rec = _validated(template(kind="plumbing", space_types=["bathroom", "kitchen"], space_evidence="strong"))
        self.assertEqual(rec["space_types"], ("bathroom", "kitchen"))
        self.assertEqual(rec["space_evidence"], "strong")

    def test_without_them_nothing_changes(self):
        from archiagent.classify.library_templates import _validated
        rec = _validated(template())
        self.assertNotIn("space_types", rec)

    def test_an_unknown_space_type_or_evidence_is_refused(self):
        from archiagent.classify.library_templates import _validated
        with self.assertRaisesRegex(ValueError, "space type"):
            _validated(template(space_types=["bathrom"]))
        with self.assertRaisesRegex(ValueError, "space_evidence"):
            _validated(template(space_types=["bathroom"], space_evidence="certain"))
        with self.assertRaisesRegex(ValueError, "space type"):
            _validated(template(space_types="bathroom"))

    def test_a_matched_symbol_carries_them_as_properties(self):
        ps = drawing([primitive("one", L_SHAPE)])
        symbols, _ = match_library_templates(ps, 1, [template(space_types=["bedroom"], space_evidence="weak")])
        props = dict(symbols[0].properties)
        self.assertEqual(props["space_types"], "bedroom")
        self.assertEqual(props["space_evidence"], "weak")

    def test_annotation_is_an_accepted_kind(self):
        ps = drawing([primitive("one", L_SHAPE)])
        symbols, issues = match_library_templates(ps, 1, [template(kind="annotation", subtype="compass")])
        self.assertEqual([s.kind for s in symbols], ["annotation"])
        self.assertEqual(issues, ())

# --- an over-budget template is matched at fewer orientations before it is skipped ---

class FitOrientationChecks(unittest.TestCase):
    ALL = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0)       # the supported set

    def fit(self, anchors, angles=None, mirror=True, budget=5000):
        from archiagent.classify.library_templates import fit_orientations
        return fit_orientations(tuple(angles if angles is not None else self.ALL), mirror, anchors, budget)

    def test_what_fits_is_left_alone(self):
        angles, mirrors = self.fit(anchors=100)            # 100 x 7 x 2 = 1400
        self.assertEqual(angles, self.ALL)
        self.assertEqual(mirrors, (False, True))

    def test_the_most_orientations_that_fit_are_used(self):
        # 500 x 14 = 7000 > 5000. Five axis and diagonal angles with mirror is 10 trials a spot
        # (5000); all seven angles without mirror is 7 (3500). The wider coverage wins.
        angles, mirrors = self.fit(anchors=500)
        self.assertEqual(angles, (0.0, 45.0, 90.0, 180.0, 270.0))
        self.assertEqual(mirrors, (False, True))

    def test_then_the_five_axis_and_diagonal_angles(self):
        angles, mirrors = self.fit(anchors=900)             # 900 x 7 = 6300; 5 angles + mirror 9000; 5 x 900 = 4500
        self.assertEqual(angles, (0.0, 45.0, 90.0, 180.0, 270.0))
        self.assertEqual(mirrors, (False,))

    def test_then_one_angle(self):
        angles, mirrors = self.fit(anchors=4000, mirror=False)
        self.assertEqual(angles, (0.0,))

    def test_it_never_adds_an_angle_the_reviewer_did_not_allow(self):
        angles, _ = self.fit(anchors=3000, angles=(0.0, 90.0), mirror=False)   # 2 x 3000 > 5000
        self.assertEqual(angles, (0.0,))
        angles, _ = self.fit(anchors=3000, angles=(90.0, 180.0), mirror=False)
        self.assertTrue(set(angles) <= {90.0, 180.0})

    def test_too_common_even_for_one_angle_is_none(self):
        self.assertIsNone(self.fit(anchors=6000, mirror=False))

    def test_an_unsupported_angle_is_refused_at_load(self):
        from archiagent.classify.library_templates import _validated
        with self.assertRaisesRegex(ValueError, "rotation"):
            _validated(template(rotations_deg=[0, 22.5]))
        self.assertEqual(_validated(template(rotations_deg=[0, 45, 135]))["rotations_deg"], (0.0, 45.0, 135.0))

    def test_the_run_reduces_instead_of_skipping_and_says_so(self):
        ps = drawing([primitive("l1", L_SHAPE), primitive("l2", transform(L_SHAPE, 0, (30, 0), 1)),
                      primitive("l3", transform(L_SHAPE, 0, (60, 0), 1))])
        # 3 anchor spots x 4 default angles = 12 trials > 5, but 3 x 1 = 3 fits.
        symbols, issues = match_library_templates(ps, 1, [template()], max_candidates=5)
        self.assertEqual({s.source_ids for s in symbols}, {("l1",), ("l2",), ("l3",)})
        reduced = [i for i in issues if i.code == "library_template_reduced"]
        self.assertEqual(len(reduced), 1)
        self.assertEqual(reduced[0].severity, "info")
        self.assertEqual([i for i in issues if i.code == "library_template_skipped"], [])


# --- spots that cannot match are not counted against the budget -----------------------

SMALL_SQUARE = ((4, 0), (5, 0), (5, 1), (4, 1), (4, 0))


def two_part_template(first, second, **kw):
    record = template(**kw)
    record["geometry"] = normalise([{"category": "closed", "points": first, "holes": []},
                                    {"category": "closed", "points": second, "holes": []}])
    return record


class PrefilterChecks(unittest.TestCase):
    def test_spots_outside_the_size_range_do_not_use_the_budget(self):
        # 8 big copies and one in range. Counted, 9 spots x 4 angles exceed a limit of 4;
        # only the in-range one counts, so it is matched at all four angles with no reduction.
        parts = [primitive(f"big{i}", transform(L_SHAPE, 0, (40 * i, 0), 5)) for i in range(8)]
        ps = drawing(parts + [primitive("fit", transform(L_SHAPE, 0, (0, 60), 1))])
        symbols, issues = match_library_templates(ps, 1, [template(size_ft={"min": 2.0, "max": 4.0})],
                                                  max_candidates=4)
        self.assertEqual({s.source_ids for s in symbols}, {("fit",)})
        self.assertEqual([i for i in issues if i.code in ("library_template_reduced", "library_template_skipped")], [])
        out = [i for i in issues if i.code == "symbol_size_out_of_range"]
        self.assertEqual(len(out), 1)                       # one summary per template, not one per spot
        self.assertIn("8", out[0].msg)

    def test_a_same_kind_of_shape_with_a_different_form_is_not_a_spot(self):
        # Closed four-sided shapes: a unit square and long thin bars (a quite different form).
        bars = [primitive(f"bar{i}", ((30 * i, 0), (30 * i + 4, 0), (30 * i + 4, .5), (30 * i, .5), (30 * i, 0)))
                for i in range(10)]
        ps = drawing(bars + [primitive("sq", transform(SQUARE, 0, (0, 40), 1))])
        # 11 spots share the key. Counted, 11 x 1 angle > 4; by form only the square is a spot.
        symbols, issues = match_library_templates(ps, 1, [template(SQUARE)], max_candidates=4)
        self.assertEqual({s.source_ids for s in symbols}, {("sq",)})
        self.assertEqual([i for i in issues if i.code in ("library_template_reduced", "library_template_skipped")], [])

    def test_a_lone_shape_without_its_neighbours_is_not_a_spot(self):
        # The template is an L with a zigzag 4 ft to its right: two parts of the same key, the
        # L the longer so it is the anchor. Ten lone Ls, one real group.
        z_right = tuple((x + 4, y) for x, y in ZIGZAG)
        lone = [primitive(f"lone{i}", transform(L_SHAPE, 0, (0, 30 + 20 * i), 1)) for i in range(10)]
        group = [primitive("l", L_SHAPE), primitive("z", z_right)]
        ps = drawing(lone + group)
        record = two_part_template(L_SHAPE, z_right, size_ft={"min": 0.5, "max": 20.0})
        # With only the group's L and zigzag as spots, 2 x 4 angles = 8 fits. Counting all 12
        # same-key parts it would be 48, and the template would be reduced.
        symbols, issues = match_library_templates(ps, 1, [record], max_candidates=8)
        self.assertEqual({s.source_ids for s in symbols}, {("l", "z")})
        self.assertEqual([i for i in issues if i.code in ("library_template_reduced", "library_template_skipped")], [])

    def test_the_comparison_budget_is_per_template_not_per_run(self):
        from archiagent.classify import library_templates
        ps = drawing([primitive("a", L_SHAPE), primitive("b", SQUARE)])
        both = [template(id="a-l"), template(SQUARE, id="b-sq")]
        # Each template needs a handful of comparisons; a budget that one template fits in but
        # two together would not must not starve the second.
        with mock.patch.object(library_templates, "MAX_MATCH_OPERATIONS", 12):
            symbols, issues = match_library_templates(ps, 1, both)
        self.assertEqual({s.source_ids for s in symbols}, {("a",), ("b",)})
