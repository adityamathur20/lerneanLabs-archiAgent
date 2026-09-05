"""Stair treads and hatch are evenly-spaced parallel runs, not walls.

Measured on PLAN.dxf: 6 of 62 detected walls came from a layer named sSTAIR,
because a ~10in tread pitch sits inside the 2-24in wall-thickness window.
The discriminator is spacing regularity, not thickness.
"""
from archiagent.geometry.walls import (LADDER_MAX_SPACING_IN, LADDER_MIN_RUN,
                                       WallSeg, reject_ladder_runs)


def _h(y, x0=0.0, x1=10.0, layer="X"):
    return WallSeg((x0, y), (x1, y), 0.5, layer, "paired-line", "measured")


def _v(x, y0=0.0, y1=10.0, layer="X"):
    return WallSeg((x, y0), (x, y1), 0.5, layer, "paired-line", "measured")


def test_a_stair_tread_run_is_rejected():
    treads = [_h(i * 10.0 / 12.0) for i in range(8)]      # 10in pitch
    kept, rejected = reject_ladder_runs(treads)
    assert kept == ()
    assert len(rejected) == 8


def test_vertical_treads_are_rejected_too():
    treads = [_v(i * 10.0 / 12.0) for i in range(6)]
    kept, rejected = reject_ladder_runs(treads)
    assert kept == () and len(rejected) == 6


def test_real_parallel_room_walls_are_kept():
    """A row of rooms: parallel and evenly spaced, but FEET apart.
    The spacing cap is the only thing separating this from a staircase."""
    walls = [_h(i * 12.0) for i in range(6)]              # 12 ft apart
    kept, rejected = reject_ladder_runs(walls)
    assert len(kept) == 6 and rejected == ()


def test_a_short_run_is_kept():
    """Two or three close parallel walls are ordinary -- a cavity wall, a
    doorway reveal. Only a long uniform run means treads."""
    walls = [_h(i * 10.0 / 12.0) for i in range(LADDER_MIN_RUN - 1)]
    kept, rejected = reject_ladder_runs(walls)
    assert len(kept) == LADDER_MIN_RUN - 1 and rejected == ()


def test_irregular_spacing_is_kept():
    """Close together but NOT uniform -- not a staircase."""
    offsets = [0.0, 0.3, 1.0, 1.05, 1.4]
    walls = [_h(o) for o in offsets]
    kept, rejected = reject_ladder_runs(walls)
    assert len(kept) == 5 and rejected == ()


def test_spacing_just_over_the_cap_is_kept():
    gap = (LADDER_MAX_SPACING_IN + 1) / 12.0
    walls = [_h(i * gap) for i in range(6)]
    kept, rejected = reject_ladder_runs(walls)
    assert len(kept) == 6 and rejected == ()


def test_parallel_but_not_overlapping_is_kept():
    """Evenly spaced and close, but end to end rather than side by side --
    e.g. short wall stubs down a corridor, not treads of one stair."""
    walls = [WallSeg((i * 20.0, i * 10.0 / 12.0), (i * 20.0 + 5, i * 10.0 / 12.0),
                     0.5, "X", "paired-line", "measured") for i in range(6)]
    kept, rejected = reject_ladder_runs(walls)
    assert len(kept) == 6 and rejected == ()


def test_a_stair_beside_a_real_wall_keeps_the_wall():
    treads = [_h(i * 10.0 / 12.0) for i in range(8)]
    far_wall = _h(40.0)
    kept, rejected = reject_ladder_runs(treads + [far_wall])
    assert kept == (far_wall,)
    assert len(rejected) == 8


def test_empty_input():
    assert reject_ladder_runs([]) == ((), ())
