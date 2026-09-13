"""Optional OpenCV proposals must retain precise, complete source association."""
from dataclasses import replace
import importlib.util
import math
import unittest

from archiagent.classify.raster_templates import match_raster_templates
from archiagent.primitives import Primitive, PrimitiveSet


def move(points,angle=0,offset=(0,0)):
    c,s=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    return tuple((offset[0]+x*c-y*s,offset[1]+x*s+y*c) for x,y in points)


def path(sid,points,closed=False):
    return Primitive('rect' if closed else 'line',tuple(points),'mixed',None,None,
                     source_id=sid,entity_type='LWPOLYLINE' if closed else 'LINE',closed=closed)


def drawing(parts):
    return PrimitiveSet(tuple(parts),(),100,100,'raster-source.dxf','raster-source')


def record(**kwargs):
    return {'id':'reviewed-chair','kind':'furniture','source_ids':['seed'],
            'matcher':'raster',**kwargs}


@unittest.skipUnless(importlib.util.find_spec('cv2'),'optional OpenCV extra unavailable')
class RasterTemplateChecks(unittest.TestCase):
    def test_rotated_resegmented_copy_retains_only_actual_source_paths(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        target=move(ring,90,(8,2))
        parts=[path('seed',ring,True)]
        parts += [path(f'copy-{i}',(a,b)) for i,(a,b) in enumerate(zip(target,target[1:]))]
        # Nearby wall passes outside the template instead of being bbox-masked.
        parts.append(path('nearby-wall',((5.5,1),(5.5,4))))
        symbols=match_raster_templates(drawing(parts),1,[record()])
        reliable={s.source_ids for s in symbols if s.source_ids}
        self.assertIn(('seed',),reliable)
        self.assertIn(('copy-0','copy-1','copy-2','copy-3'),reliable)
        self.assertFalse(any('nearby-wall' in s.source_ids for s in symbols))
        self.assertTrue(all(s.evidence in {'opencv-template','reviewed-template'} for s in symbols))
        self.assertTrue(all(float(dict(s.properties)['source_coverage'])>=.98 for s in symbols if s.source_ids))

    def test_source_origin_and_pixel_quantization_preserve_model_feet(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        origin=(-1024.017,512.009)
        seed=move(ring,offset=origin)
        target=move(ring,offset=(origin[0]+8.007,origin[1]+3.011))
        ps=drawing([path('seed',seed,True),path('copy',target,True)])
        symbols=match_raster_templates(ps,1,[record(rotations_deg=[0])])
        copy=next(s for s in symbols if s.source_ids==('copy',))
        self.assertAlmostEqual(copy.position[0],origin[0]+8.007+.5,places=6)
        self.assertAlmostEqual(copy.position[1],origin[1]+3.011+1,places=6)
        self.assertAlmostEqual(copy.width_ft,2,places=6)

    def test_long_near_template_wall_is_not_claimed_as_symbol_member(self):
        # The seed includes a short stem. At the other location its raster
        # footprint is part of a much longer wall: never erase that entity.
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        target=move(ring,offset=(5,0))
        parts=[path('seed',ring,True)]
        parts += [path(f'copy-{i}',(a,b)) for i,(a,b) in enumerate(zip(target,target[1:])) if i!=0]
        parts.append(path('long-wall',((3,0),(9,0))))
        symbols=match_raster_templates(drawing(parts),1,[record(rotations_deg=[0])])
        self.assertFalse(any('long-wall' in s.source_ids for s in symbols))
        self.assertTrue(all(symbol.source_ids for symbol in symbols))
        self.assertFalse(any(symbol.position[0]>4 for symbol in symbols))
        seed=next(symbol for symbol in symbols if symbol.source_ids==('seed',))
        self.assertIn('raster_rejected_candidate_count',dict(seed.properties))

    def test_reviewed_seed_survives_overlapping_unrelated_strokes(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        parts=[path('seed',ring,True)]
        parts += [path(f'clutter-{i}',((-.1,i/10),(1.1,i/10))) for i in range(1,20)]
        ps=drawing(parts)
        symbols=match_raster_templates(ps,1,[record(rotations_deg=[0])])
        seed=next(symbol for symbol in symbols if symbol.source_ids==('seed',))
        self.assertEqual(seed.evidence,'reviewed-template')
        self.assertEqual(dict(seed.properties)['template_seed_reviewed'],'true')
        self.assertFalse(any(sid.startswith('clutter-') for symbol in symbols for sid in symbol.source_ids))
        from archiagent.recognition import exclude_symbol_geometry
        remaining=exclude_symbol_geometry(ps,symbols)
        self.assertEqual({p.source_id for p in remaining.primitives},{f'clutter-{i}' for i in range(1,20)})

    def test_blank_regions_and_rotation_symmetry_do_not_create_instances(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        # Distant single lines create a large blank region but no complete copy.
        ps=drawing([path('seed',ring,True),path('distant',((20,20),(23,20)))])
        symbols=match_raster_templates(ps,1,[record()])
        self.assertEqual(len(symbols),1)
        self.assertEqual(symbols[0].source_ids,('seed',))

    def test_malformed_matcher_and_kind_raise_value_error(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([path('seed',ring,True)])
        for records in ([record(matcher=[])],[record(kind=[])],[None],{'id':'invalid'}):
            with self.assertRaises(ValueError):match_raster_templates(ps,1,records)

    def test_limits_missing_seeds_and_tiny_patterns_are_explicit(self):
        ring=((0,0),(1,0),(1,2),(0,2),(0,0))
        ps=drawing([path('seed',ring,True)])
        for invalid in (record(threshold=.5),record(pixels_per_foot=256),record(scale=2),
                        record(source_ids=['missing']),record(tolerance_in=1)):
            with self.assertRaises(ValueError):match_raster_templates(ps,1,[invalid])
        tiny=drawing([path('seed',((0,0),(.1,0),(.1,.1),(0,.1),(0,0)),True)])
        with self.assertRaises(ValueError):match_raster_templates(tiny,1,[record()])
        huge=replace(ps,primitives=ps.primitives+(path('distant',((10000,10000),(10001,10000))),))
        with self.assertRaises(ValueError):match_raster_templates(huge,1,[record()])


if __name__=='__main__':unittest.main()
