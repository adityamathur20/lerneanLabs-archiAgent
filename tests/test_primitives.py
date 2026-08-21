from archiagent.primitives import Primitive, TextItem, PrimitiveSet


def test_line_primitive_yields_one_segment():
    p = Primitive(kind="line", coords=((0.0, 0.0), (3.0, 4.0)),
                  layer="WALLS", stroke_width=0.72, color=(0.0, 0.0, 0.0))
    assert p.segments() == [((0.0, 0.0), (3.0, 4.0))]


def test_rect_primitive_yields_four_closed_segments():
    p = Primitive(kind="rect", coords=((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)),
                  layer="WALLS", stroke_width=None, color=None)
    segs = p.segments()
    assert len(segs) == 4
    assert segs[0] == ((0.0, 0.0), (2.0, 0.0))
    assert segs[3] == ((0.0, 1.0), (0.0, 0.0))


def test_text_item_center():
    t = TextItem(text="14'-5\"", bbox=(10.0, 20.0, 30.0, 40.0), layer="DIM")
    assert t.center() == (20.0, 30.0)


def test_primitive_set_by_layer_is_case_insensitive():
    a = Primitive("line", ((0.0, 0.0), (1.0, 0.0)), "WALLS", None, None)
    b = Primitive("line", ((0.0, 1.0), (1.0, 1.0)), "furn", None, None)
    ps = PrimitiveSet(primitives=(a, b), texts=(), width=100.0, height=200.0,
                      source_path="x.pdf", source_sha256="deadbeef")
    assert ps.by_layer({"walls"}) == [a]
    assert ps.layer_names() == {"WALLS", "furn"}
