"""Library templates match at any uniform scale, optionally mirrored.

Unlike drawing-seeded templates, a library shape carries no real size: the scale
is derived from the anchor and bounded by the template's declared size range.
"""
import math
import unittest

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
