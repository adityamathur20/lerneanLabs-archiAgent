"""Exploded symbol hypotheses require geometric evidence beyond a box."""
import math

import pytest

pytest.importorskip("shapely")
from archiagent.classify.exploded import recognize_exploded_doors
from archiagent.evidence import SourceEntity
from archiagent.primitives import Primitive, PrimitiveSet


def fixture(radius=3., rotation=0., leaf=True, polyline=False, arc_end=90., shift=(0., 0.)):
    def pt(angle, r=radius):
        angle=math.radians(angle+rotation)
        return shift[0]+r*math.cos(angle), shift[1]+r*math.sin(angle)
    points=tuple(pt(i*arc_end/8) for i in range(9))
    if polyline:
        primitives=[Primitive("line", points, "0", None, None, "curve", "LWPOLYLINE")]
    else:
        primitives=[Primitive("line", (a,b), "0", None, None, f"curve-{i}", "LINE")
                    for i,(a,b) in enumerate(zip(points,points[1:]))]
    if leaf:
        primitives.append(Primitive("line", (shift,pt(0)), "0", None, None, "leaf", "LINE"))
    return PrimitiveSet(tuple(primitives), (), 100, 100, "fixture", "fixture")


@pytest.mark.parametrize("rotation", [0, 23, 87, 190])
@pytest.mark.parametrize("polyline", [False, True])
def test_quarter_arc_and_leaf_recognize_rotated_exploded_door(rotation,polyline):
    ps=fixture(rotation=rotation,polyline=polyline,shift=(1000,-500))
    result=recognize_exploded_doors(ps,1)
    assert len(result)==1
    door=result[0]
    assert door.kind=="door" and door.subtype=="single_swing"
    assert door.width_ft==pytest.approx(3,abs=.002)
    assert "leaf" in door.source_ids
    assert any(s.startswith("curve") for s in door.source_ids)
    assert door.confidence<.7
    assert door.evidence=="exploded-arc-and-leaf"
    assert (1000,-500) in door.boundary


def test_arc_without_leaf_is_not_a_door():
    assert not recognize_exploded_doors(fixture(leaf=False),1)


def test_leaf_must_join_arc_endpoint():
    from dataclasses import replace
    ps=fixture(leaf=False)
    leaf=Primitive("line", ((0,0),(3/math.sqrt(2),3/math.sqrt(2))), "0", None,None,"leaf","LINE")
    assert not recognize_exploded_doors(replace(ps,primitives=ps.primitives+(leaf,)),1)


@pytest.mark.parametrize("radius,arc_end",[(.2,90),(8,90),(3,40),(3,180),(3,360)])
def test_wrong_size_or_sweep_does_not_become_a_door(radius,arc_end):
    assert not recognize_exploded_doors(fixture(radius=radius,arc_end=arc_end),1)


def test_ellipse_with_radial_leaf_is_not_a_circle_door():
    from dataclasses import replace
    ps=fixture(polyline=True)
    distorted=tuple(replace(p,coords=tuple((x,y*1.6) for x,y in p.coords)) for p in ps.primitives)
    assert not recognize_exploded_doors(replace(ps,primitives=distorted),1)


def test_claimed_leaf_prevents_duplicate_recognition():
    assert not recognize_exploded_doors(fixture(),1,{"leaf"})


def test_closed_rectangle_is_never_called_a_column_or_door():
    p=Primitive("rect",((0,0),(2,0),(2,2),(0,2)),"0",None,None,"box","LWPOLYLINE",True)
    ps=PrimitiveSet((p,),(),10,10,"fixture","fixture")
    assert not recognize_exploded_doors(ps,1)


def test_partial_region_symbol_stays_unresolved():
    from dataclasses import replace
    ps=fixture(polyline=True)
    entity=SourceEntity("curve","LWPOLYLINE","0",metadata=(("region_partial","true"),))
    assert not recognize_exploded_doors(replace(ps,entities=(entity,)),1)


def test_source_unit_conversion_and_input_order_do_not_change_result():
    from dataclasses import replace
    ps=fixture()
    inch_primitives=tuple(replace(p,coords=tuple((x*12,y*12) for x,y in p.coords)) for p in reversed(ps.primitives))
    a=recognize_exploded_doors(ps,1)
    b=recognize_exploded_doors(replace(ps,primitives=inch_primitives),12)
    assert [s.id for s in a]==[s.id for s in b]
    assert a[0].width_ft==pytest.approx(b[0].width_ft)


def test_wedge_with_two_radial_lines_is_ambiguous():
    from dataclasses import replace
    ps=fixture()
    second=Primitive("line",((0,0),(0,3)),"0",None,None,"second-leaf","LINE")
    assert not recognize_exploded_doors(replace(ps,primitives=ps.primitives+(second,)),1)
