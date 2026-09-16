"""Group model walls into the runs authored as single IFC walls.

A junction joins correctly in IFC only when the wall passing through it is one
entity: `connect_path` keeps a single connection per wall end, so a T stored as
two halves plus a stem loses joins and mitres the wrong pair. Runs restore the
through wall, leaving the stopping wall to butt into it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from archiagent.ifc.profile_layout import wall_layout
from archiagent.primitives import Pt

PARALLEL_TOL = 1e-7
THICKNESS_TOL_FT = 1e-9
POINT_TOL_FT = 1e-9


@dataclass(frozen=True)
class WallRun:
    id: str
    members: tuple[int, ...]
    start: Pt
    end: Pt
    thickness_ft: float
    source_layer: str
    source_ids: tuple[str, ...]
    priority: int

    @property
    def length_ft(self) -> float:
        return math.dist(self.start, self.end)


@dataclass(frozen=True)
class RunConnection:
    relating: int
    related: int
    relating_type: str
    related_type: str
    point: Pt


@dataclass(frozen=True)
class RunLayout:
    runs: tuple[WallRun, ...]
    connections: tuple[RunConnection, ...]
    untrimmed: tuple[Pt, ...]
    run_of_wall: dict[int, int] = field(default_factory=dict)


def _direction(wall):
    length = math.dist(wall.start, wall.end)
    return ((wall.end[0]-wall.start[0])/length, (wall.end[1]-wall.start[1])/length)


def _leaving(wall, point):
    """Unit direction of `wall` leaving `point`."""
    ux, uy = _direction(wall)
    return (ux, uy) if math.dist(point, wall.start) <= POINT_TOL_FT else (-ux, -uy)


def _partners(model, point, indices):
    """Pairs of wall indices that continue straight through `point`."""
    out = []
    for position, i in enumerate(indices):
        for j in indices[position+1:]:
            a, b = model.walls[i], model.walls[j]
            if a.source_layer != b.source_layer:
                continue
            if abs(a.thickness_ft - b.thickness_ft) > THICKNESS_TOL_FT:
                continue
            (ax, ay), (bx, by) = _leaving(a, point), _leaving(b, point)
            if abs(ax*by - ay*bx) < PARALLEL_TOL and ax*bx + ay*by < 0:
                out.append((i, j))
    return out


class _Groups:
    """Union-find over model wall indices."""

    def __init__(self, count):
        self.parent = list(range(count))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        a, b = self.find(i), self.find(j)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _chain_ends(model, members):
    """Outer endpoints of a chain of collinear walls."""
    counts = {}
    for i in members:
        for p in (model.walls[i].start, model.walls[i].end):
            counts[p] = counts.get(p, 0) + 1
    free = sorted(p for p, n in counts.items() if n == 1)
    if len(free) != 2:
        raise ValueError("wall chain must have two free ends")
    return free[0], free[1]


def _line_rank(model, groups, eligible):
    """Rank key per line: thicker first, then longer, then position."""
    lines = {}
    for i in eligible:
        lines.setdefault(groups.find(i), []).append(i)
    keys = {}
    for root, members in lines.items():
        thickness = model.walls[members[0]].thickness_ft
        length = sum(model.walls[i].length_ft for i in members)
        start, end = _chain_ends(model, members)
        keys[root] = (-thickness, -length, start, end)
    order = sorted(keys, key=lambda root: keys[root])
    return {root: position for position, root in enumerate(order)}


def _end_type(run, point):
    if math.dist(point, run.start) <= POINT_TOL_FT:
        return "ATSTART"
    if math.dist(point, run.end) <= POINT_TOL_FT:
        return "ATEND"
    return "ATPATH"


def run_footprints(layout, model):
    """Trimmed XY outline of every run, in feet, keyed by run index.

    A run is its rectangle, extended at a corner it owns to the far face of the
    wall stopping there, minus the rectangles of the walls it butts into.
    """
    from shapely.geometry import LineString

    def rectangle(index, extra_start=0.0, extra_end=0.0):
        wall_run = layout.runs[index]
        ux = (wall_run.end[0]-wall_run.start[0]) / wall_run.length_ft
        uy = (wall_run.end[1]-wall_run.start[1]) / wall_run.length_ft
        start = (wall_run.start[0]-ux*extra_start, wall_run.start[1]-uy*extra_start)
        end = (wall_run.end[0]+ux*extra_end, wall_run.end[1]+uy*extra_end)
        return LineString((start, end)).buffer(wall_run.thickness_ft/2, cap_style=2)

    extensions = {index: [0.0, 0.0] for index in range(len(layout.runs))}
    for connection in layout.connections:
        if connection.related_type == "ATPATH":
            continue  # a T or X leaves the wall it passes through untouched
        end = 0 if connection.related_type == "ATSTART" else 1
        extensions[connection.related][end] = max(
            extensions[connection.related][end], layout.runs[connection.relating].thickness_ft/2)
    shapes = {index: rectangle(index, *extensions[index]) for index in range(len(layout.runs))}
    for connection in layout.connections:
        shapes[connection.relating] = shapes[connection.relating].difference(
            shapes[connection.related])
    return shapes


def build_runs(model) -> RunLayout:
    _, _, profile_mapping = wall_layout(model)
    eligible = [i for i in range(len(model.walls)) if i not in profile_mapping]
    usable = set(eligible)

    lines = _Groups(len(model.walls))
    for junction in model.junctions:
        indices = [i for i in junction.wall_indices if i in usable]
        for i, j in _partners(model, junction.point, indices):
            lines.union(i, j)
    rank = _line_rank(model, lines, eligible)

    chains = _Groups(len(model.walls))
    decisions, untrimmed = [], []
    for junction in sorted(model.junctions, key=lambda j: j.point):
        indices = sorted(i for i in junction.wall_indices if i in usable)
        pairs = _partners(model, junction.point, indices)
        if len(indices) != len(junction.wall_indices):
            untrimmed.append(junction.point)          # touches a WP: profile wall
        elif len(indices) == 2 and pairs:
            chains.union(*pairs[0])
        elif len(indices) == 2:
            (ax, ay), (bx, by) = (_leaving(model.walls[i], junction.point) for i in indices)
            if abs(ax*by - ay*bx) < PARALLEL_TOL:
                untrimmed.append(junction.point)      # collinear step: IfcOpenShell ignores it
            else:
                decisions.append(("L", junction.point, indices))
        elif len(indices) == 3 and len(pairs) == 1:
            chains.union(*pairs[0])
            decisions.append(("T", junction.point, indices))
        elif len(indices) == 4 and len(pairs) == 2:
            blue = min(pairs, key=lambda pair: rank[lines.find(pair[0])])
            chains.union(*blue)
            decisions.append(("X", junction.point, indices))
        else:
            untrimmed.append(junction.point)

    members = {}
    for i in eligible:
        members.setdefault(chains.find(i), []).append(i)
    runs, run_of_wall = [], {}
    for root in sorted(members, key=lambda r: min(members[r])):
        group = sorted(members[root])
        start, end = _chain_ends(model, group)
        first = model.walls[group[0]]
        run_index = len(runs)
        # Higher priority owns an L corner. Line ranks are unique, so no two walls tie.
        priority = len(rank) - rank[lines.find(group[0])]
        runs.append(WallRun(f"W{min(group):03d}", tuple(group), start, end,
                            first.thickness_ft, first.source_layer,
                            tuple(sorted({sid for i in group for sid in model.walls[i].source_ids})),
                            priority))
        for i in group:
            run_of_wall[i] = run_index
    runs = tuple(runs)

    connections = []
    for kind, point, indices in decisions:
        involved = sorted({run_of_wall[i] for i in indices})
        if kind == "L":
            blue = max(involved, key=lambda index: runs[index].priority)
            green = next(index for index in involved if index != blue)
            connections.append(RunConnection(green, blue, _end_type(runs[green], point),
                                             _end_type(runs[blue], point), point))
        else:
            blue = next(index for index in involved
                        if _end_type(runs[index], point) == "ATPATH")
            for green in involved:
                if green != blue:
                    connections.append(RunConnection(green, blue, _end_type(runs[green], point),
                                                     "ATPATH", point))
    connections.sort(key=lambda c: (c.point, c.relating, c.related))
    return RunLayout(runs, tuple(connections), tuple(sorted(untrimmed)), run_of_wall)
