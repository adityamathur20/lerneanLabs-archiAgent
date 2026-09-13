"""Door voids follow source closed spans and observed wall/corner support."""
from dataclasses import replace
import json
import math
import unittest

from archiagent.geometry.walls import WallSeg
from archiagent.geometry.junctions import resolve_junctions
from archiagent.recognition import host_openings, remap_openings
from archiagent.semantic import SymbolInstance, Opening


def wall(a,b,sid,thickness=.4):
    return WallSeg(a,b,thickness,"walls","paired-line","measured",(sid,))


def door(a,b,sid="door"):
    width=math.dist(a,b)
    return SymbolInstance(sid,"door",((a[0]+b[0])/2,(a[1]+b[1])/2),width,width,
        subtype="single_swing",source_ids=(sid,),evidence="exploded-arc-and-leaf",
        boundary=(a,b,(b[0],b[1]+width),(a[0],a[1]+width),a),
        properties=(("opening_start_ft",json.dumps(a)),("opening_end_ft",json.dumps(b))))


class OpeningLocalizationChecks(unittest.TestCase):
    def test_swing_area_cannot_cut_perpendicular_continuous_wall(self):
        source=door((0,0),(3,0))
        walls=(wall((0,0),(0,5),"perpendicular"),)
        result,openings=host_openings(walls,(source,),10)
        self.assertEqual(result,walls)
        self.assertEqual(openings,())

    def test_parallel_flanks_keep_true_door_width_inside_jamb_gap(self):
        walls=(wall((0,0),(4,0),"left"),wall((7.4,0),(10,0),"right"))
        result,openings=host_openings(walls,(door((4.2,.1),(7.2,.1)),),10)
        self.assertEqual(len(openings),1)
        self.assertAlmostEqual(openings[0].width_ft,3)
        self.assertAlmostEqual(result[-1].length_ft,3.4)
        self.assertEqual(set(result[-1].source_ids),{"left","right","door"})
        self.assertEqual(openings[0].source_ids,("door",))

    def test_corner_host_uses_existing_perpendicular_body(self):
        walls=(wall((0,-2),(0,2),"corner"),wall((3.4,0),(8,0),"flank"))
        result,openings=host_openings(walls,(door((.2,0),(3.2,0)),),10)
        self.assertEqual(len(openings),1)
        self.assertAlmostEqual(openings[0].width_ft,3)
        self.assertAlmostEqual(result[-1].length_ft,3.4)
        self.assertEqual(result[0],walls[0])  # Perpendicular supporting wall is never cut.
        graph=resolve_junctions(result,snap_in=0,extend_in=0,min_dangle_ft=0)
        mapped=remap_openings(openings,graph.walls)
        self.assertEqual(len(mapped),1)
        self.assertIn("door",graph.walls[mapped[0].host_wall_index].source_ids)

    def test_missing_corner_and_t_intersection_remain_unhosted(self):
        source=door((.2,0),(3.2,0))
        flank=wall((3.4,0),(8,0),"flank")
        self.assertEqual(host_openings((flank,),(source,),10)[1],())
        walls=(wall((0,-2),(0,2),"corner"),flank,wall((1.5,0),(1.5,4),"obstruction"))
        self.assertEqual(host_openings(walls,(source,),10)[1],())

    def test_corner_localization_is_rotation_invariant(self):
        angle=math.radians(37)
        def rotate(p):
            return (p[0]*math.cos(angle)-p[1]*math.sin(angle)+20,
                    p[0]*math.sin(angle)+p[1]*math.cos(angle)+10)
        walls=(wall(rotate((0,-2)),rotate((0,2)),"corner"),
               wall(rotate((3.4,0)),rotate((8,0)),"flank"))
        result,openings=host_openings(walls,(door(rotate((.2,0)),rotate((3.2,0))),),10)
        self.assertEqual(len(openings),1)
        self.assertAlmostEqual(openings[0].width_ft,3)
        self.assertAlmostEqual(result[-1].length_ft,3.4)

    def test_remapping_does_not_pick_an_unrelated_shorter_wall(self):
        opening=Opening("o","door",0,(1,0),(4,0),7,source_ids=("door",))
        unrelated=wall((1,0),(4,0),"unrelated")
        linked=replace(wall((0,0),(5,0),"wall"),source_ids=("wall","door"))
        mapped=remap_openings((opening,),(unrelated,linked))
        self.assertEqual(mapped[0].host_wall_index,1)
        ambiguous=replace(linked,source_ids=("other-wall",))
        self.assertEqual(remap_openings((opening,),(unrelated,ambiguous)),())

    def test_legacy_exploded_boundary_uses_hinge_and_opposite_arc_tip(self):
        arc=tuple((3*math.cos(i*math.pi/20),3*math.sin(i*math.pi/20)) for i in range(11))
        source=replace(door((0,0),(3,0)),properties=(),boundary=arc+((0,0),(0,3)))
        walls=(wall((-4,0),(0,0),"left"),wall((3,0),(7,0),"right"))
        _,openings=host_openings(walls,(source,),10)
        self.assertEqual(len(openings),1)
        self.assertAlmostEqual(openings[0].width_ft,3)

    def test_malformed_axis_and_invalid_window_height_cannot_create_material(self):
        source=replace(door((0,0),(3,0)),properties=(("opening_start_ft","not-json"),))
        with self.assertRaises(ValueError):
            host_openings((wall((-4,0),(0,0),"left"),),(source,),10)
        window=replace(door((0,0),(3,0)),kind="window")
        walls=(wall((-4,0),(0,0),"left"),wall((3,0),(7,0),"right"))
        result,openings=host_openings(walls,(window,),3)
        self.assertEqual(result,walls)
        self.assertEqual(openings,())


if __name__=="__main__":
    unittest.main()
