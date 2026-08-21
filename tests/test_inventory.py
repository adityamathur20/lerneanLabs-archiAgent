from archiagent.classify.inventory import build_inventory
from archiagent.primitives import Primitive, PrimitiveSet


def _ps(prims):
    return PrimitiveSet(primitives=tuple(prims), texts=(), width=100.0,
                        height=100.0, source_path="x", source_sha256="y")


def test_inventory_groups_by_layer_and_counts():
    prims = [
        Primitive("line", ((0.0, 0.0), (10.0, 0.0)), "WALLS", 0.72, (0.0, 0.0, 0.0)),
        Primitive("line", ((0.0, 1.0), (10.0, 1.0)), "WALLS", 0.72, (0.0, 0.0, 0.0)),
        Primitive("line", ((0.0, 0.0), (3.0, 4.0)), "FURN", 0.29, (1.0, 0.0, 0.0)),
    ]
    inv = {s.name: s for s in build_inventory(_ps(prims))}
    assert inv["WALLS"].path_count == 2
    assert inv["WALLS"].segment_count == 2
    assert inv["FURN"].path_count == 1


def test_axis_aligned_fraction_discriminates_walls_from_diagonals():
    walls = [Primitive("line", ((0.0, float(i)), (10.0, float(i))),
                       "WALLS", 0.72, None) for i in range(4)]
    hatch = [Primitive("line", ((0.0, float(i)), (5.0, float(i) + 5.0)),
                       "HATCH", 0.14, None) for i in range(4)]
    inv = {s.name: s for s in build_inventory(_ps(walls + hatch))}
    assert inv["WALLS"].axis_aligned_fraction == 1.0
    assert inv["HATCH"].axis_aligned_fraction == 0.0


def test_length_percentiles_and_bbox():
    prims = [Primitive("line", ((0.0, 0.0), (float(n), 0.0)), "L", None, None)
             for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
    stats = build_inventory(_ps(prims))[0]
    assert stats.length_p50 == 6.0
    assert stats.bbox == (0.0, 0.0, 10.0, 0.0)


def test_dominant_colors_are_ranked():
    prims = ([Primitive("line", ((0.0, 0.0), (1.0, 0.0)), "L", None, (1.0, 0.0, 0.0))] * 3
             + [Primitive("line", ((0.0, 0.0), (1.0, 0.0)), "L", None, (0.0, 0.0, 1.0))])
    stats = build_inventory(_ps(prims))[0]
    assert stats.dominant_colors[0] == (1.0, 0.0, 0.0)
