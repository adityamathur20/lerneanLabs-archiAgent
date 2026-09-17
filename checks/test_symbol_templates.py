"""Drawing-local reviewed templates match geometry and retain source members."""
from dataclasses import replace
import math
import unittest

from archiagent.classify.templates import match_templates
from archiagent.primitives import Primitive, PrimitiveSet


def transform(points,angle=0,offset=(0,0),scale=1):
    c,s=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    return tuple((offset[0]+scale*(x*c-y*s),offset[1]+scale*(x*s+y*c)) for x,y in points)


def primitive(sid,points,closed=False,layer="mixed"):
    return Primitive("rect" if closed else "line",tuple(points),layer,None,None,
                     source_id=sid,entity_type="LWPOLYLINE" if closed else "LINE",closed=closed)


def drawing(parts):
    return PrimitiveSet(tuple(parts),(),100,100,"template-source.dxf","local-source")


def record(ids,**kwargs):
    return {"id":"reviewed-pattern","kind":"column","source_ids":ids,**kwargs}


class SymbolTemplateChecks(unittest.TestCase):
    def test_rectangles_rotate_without_scaling_or_bbox_exclusion(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        parts=[primitive("seed",ring,True),primitive("rotated",transform(ring,90,(8,2)),True),
               primitive("scaled",transform(ring,0,(20,0),1.25),True),
               primitive("nearby-wall",((.5,-1),(.5,3)))]
        symbols=match_templates(drawing(parts),1,[record(["seed"])])
        self.assertEqual({s.source_ids for s in symbols},{("seed",),("rotated",)})
        self.assertTrue(all(s.evidence=="reviewed-template" for s in symbols))
        self.assertTrue(all(abs(s.width_ft-2)<1e-8 for s in symbols))
        self.assertFalse(any("nearby-wall" in s.source_ids for s in symbols))
        self.assertEqual(symbols,match_templates(drawing(list(reversed(parts))),1,[record(["seed"])]))

    def test_sparse_multisegment_pattern_retains_exact_members(self):
        a=((0,0),(3,0));b=((0,0),(0,2));c=((3,0),(3,.75))
        parts=[primitive("seed-a",a),primitive("seed-b",b),primitive("seed-c",c),
               primitive("copy-a",transform(a,90,(10,10))),primitive("copy-b",transform(b,90,(10,10))),
               primitive("copy-c",transform(c,90,(10,10))),primitive("unrelated",((8,9),(12,12)))]
        template=record(["seed-a","seed-b","seed-c"],kind="door",subtype="sliding")
        symbols=match_templates(drawing(parts),1,[template])
        self.assertEqual({s.source_ids for s in symbols},
                         {("seed-a","seed-b","seed-c"),("copy-a","copy-b","copy-c")})
        self.assertTrue(all(s.subtype=="sliding" for s in symbols))

    def test_custom_rotation_and_layer_scope_are_explicit(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([primitive("seed",ring,True),primitive("angle",transform(ring,30,(8,2)),True),
                    primitive("other-layer",transform(ring,0,(20,0)),True,layer="detail")])
        default=match_templates(ps,1,[record(["seed"])])
        self.assertEqual({s.source_ids for s in default},{("seed",)})
        explicit=match_templates(ps,1,[record(["seed"],rotations_deg=[0,30],target_layers=["mixed","detail"])])
        self.assertEqual({s.source_ids for s in explicit},{("seed",),("angle",),("other-layer",)})

    def test_tolerance_is_metric_and_bounded(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        changed=((5,0),(6.002,0),(6.002,2),(5,2),(5,0))
        changed_too_far=((10,0),(11.03,0),(11.03,2),(10,2),(10,0))
        ps=drawing([primitive("seed",ring,True),primitive("close",changed,True),primitive("far",changed_too_far,True)])
        symbols=match_templates(ps,1,[record(["seed"],tolerance_in=.1)])
        self.assertEqual({s.source_ids for s in symbols},{("seed",),("close",)})
        in_inches=replace(ps,primitives=tuple(replace(p,coords=transform(p.coords,scale=12)) for p in ps.primitives))
        inches=match_templates(in_inches,12,[record(["seed"],tolerance_in=.1)])
        self.assertEqual({s.source_ids for s in inches},{s.source_ids for s in symbols})

    def test_budget_invalid_seeds_and_conflicting_roles_are_reported(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([primitive("seed",ring,True)])
        for template in (record(["missing"]),record(["seed"],tolerance_in=1),
                         record(["seed"],scale=2),record(["seed"],max_candidates=1)):
            with self.assertRaises(ValueError):
                match_templates(ps,1,[template])
        with self.assertRaises(ValueError):
            match_templates(ps,1,[record(["seed"]),record(["seed"],id="different",kind="furniture")])

    def test_duplicate_seed_symmetries_and_templates_do_not_duplicate_instances(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([primitive("seed",ring,True)])
        symbols=match_templates(ps,1,[record(["seed","seed"]),record(["seed"],id="same-role")])
        self.assertEqual(len(symbols),1)
        self.assertEqual(symbols[0].source_ids,("seed",))

    def test_malformed_review_fields_raise_actionable_errors(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([primitive("seed",ring,True)])
        for records in ([None], [record(["seed"],kind=[])],
                        [record(1)], [record(["seed"],target_layers=1)],
                        [record(["seed"],tolerance_in=None)],
                        [record(["seed"],rotations_deg=[None])]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                match_templates(ps,1,records)

    def test_matching_cannot_claim_only_part_of_a_source_entity(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([primitive("seed",ring,True),primitive("compound",transform(ring,offset=(5,0)),True),
                    primitive("compound",((10,10),(11,11)))])
        symbols=match_templates(ps,1,[record(["seed"])])
        self.assertEqual({s.source_ids for s in symbols},{("seed",)})


if __name__=="__main__":
    unittest.main()
