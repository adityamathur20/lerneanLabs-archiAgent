"""Group model walls into the runs authored as single IFC walls.

A junction joins correctly in IFC only when the wall passing through it is one
entity: `connect_path` keeps a single connection per wall end, so a T stored as
two halves plus a stem loses joins and mitres the wrong pair. Runs restore the
through wall, leaving the stopping wall to butt into it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math

from archiagent.ifc.profile_layout import wall_layout
from archiagent.primitives import Pt

PARALLEL_TOL = 1e-7
THICKNESS_TOL_FT = 1e-9
POINT_TOL_FT = 1e-9
# A joint's owner reaches (thickness/2)/sin(angle) past the junction, so walls
# meeting at a hair's angle mitre kilometres away. Beyond this many multiples
# of the wall's own thickness the joint is not expressible and is left alone.
MAX_REACH_THICKNESSES = 20.0


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


def _joinable(model, runs, point, green, blue):
    """Can IfcOpenShell express this joint, or would it run away to infinity?"""
    (gx, gy) = _leaving(model.walls[runs[green].members[0]], point)
    (bx, by) = _leaving(model.walls[runs[blue].members[0]], point)
    sine = abs(gx*by - gy*bx)
    if sine < PARALLEL_TOL:
        return False  # parallel walls: IfcOpenShell ignores them outright
    thickness = max(runs[green].thickness_ft, runs[blue].thickness_ft)
    return (thickness/2) / sine <= thickness * MAX_REACH_THICKNESSES


def _end_type(run, point):
    if math.dist(point, run.start) <= POINT_TOL_FT:
        return "ATSTART"
    if math.dist(point, run.end) <= POINT_TOL_FT:
        return "ATEND"
    return "ATPATH"


def _outranks(edges, winner, loser):
    """Does `winner` already have to outrank `loser` through earlier joints?"""
    stack, seen = [winner], set()
    while stack:
        node = stack.pop()
        if node == loser:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(above for below, above in edges if below == node)
    return False


def _priorities(count, edges):
    """Smallest layer priority per run that satisfies every joint's ownership.

    IfcOpenShell reads one priority per wall, and IFC allows 0-100 only, so a
    drawing-wide ranking does not fit: these are levels, and a wall only needs
    to outrank the walls that actually stop against it.
    """
    above = {}
    incoming = [0]*count
    for below, winner in edges:
        above.setdefault(below, []).append(winner)
        incoming[winner] += 1
    level = [0]*count
    queue = [index for index in range(count) if incoming[index] == 0]
    while queue:
        node = queue.pop()
        for winner in above.get(node, ()):
            level[winner] = max(level[winner], level[node]+1)
            incoming[winner] -= 1
            if incoming[winner] == 0:
                queue.append(winner)
    return [min(value, 100) for value in level]


def run_footprints(layout, model):
    """Trimmed XY outline of every run, in feet, keyed by run index.

    The wall owning a corner reaches the far face of the wall stopping there.
    That is only thickness/2 along its own axis at a right angle -- at any
    other angle it reaches further and its end is cut on a slant -- so the
    outline is built from the neighbour's faces, never a fixed distance.
    """
    from shapely.geometry import LineString, Polygon

    # A half-plane is built as a polygon, so it must be larger than the whole
    # drawing: sized from wall thickness alone it would stop short of a long
    # wall's far end and clip its middle out instead of its end.
    points = [value for wall_run in layout.runs
              for point in (wall_run.start, wall_run.end) for value in point]
    reach = (max(points, default=1.0) - min(points, default=0.0)) * 4 + max(
        (r.thickness_ft for r in layout.runs), default=1.0) * 10 + 1.0

    def axis(index):
        wall_run = layout.runs[index]
        return ((wall_run.end[0]-wall_run.start[0]) / wall_run.length_ft,
                (wall_run.end[1]-wall_run.start[1]) / wall_run.length_ft)

    def far_end(index, point):
        wall_run = layout.runs[index]
        return (wall_run.end if math.dist(point, wall_run.start) <= POINT_TOL_FT
                else wall_run.start)

    def side_of(point, origin, normal):
        return 1.0 if ((point[0]-origin[0])*normal[0] + (point[1]-origin[1])*normal[1]) >= 0 else -1.0

    def half_plane(origin, direction):
        """Everything on the `direction` side of the line through `origin`."""
        nx, ny = -direction[1]*reach, direction[0]*reach
        a = (origin[0]+nx, origin[1]+ny)
        b = (origin[0]-nx, origin[1]-ny)
        return Polygon([a, b, (b[0]+direction[0]*reach, b[1]+direction[1]*reach),
                        (a[0]+direction[0]*reach, a[1]+direction[1]*reach)])

    extensions = {index: [0.0, 0.0] for index in range(len(layout.runs))}
    keeps = {index: [] for index in range(len(layout.runs))}
    for connection in layout.connections:
        green, blue, point = connection.relating, connection.related, connection.point
        blue_run, green_run = layout.runs[blue], layout.runs[green]
        ub = axis(blue)
        nb = (-ub[1], ub[0])
        # The stopping wall is cut by the owner's near face, not by its body:
        # past the owner's end that face still bounds it.
        side = side_of(far_end(green, point), point, nb)
        keeps[green].append(half_plane(
            (point[0]+nb[0]*side*blue_run.thickness_ft/2,
             point[1]+nb[1]*side*blue_run.thickness_ft/2), (nb[0]*side, nb[1]*side)))
        if connection.related_type == "ATPATH":
            continue  # a T or X leaves the wall it passes through untouched
        # The owner reaches the far face of the wall stopping against it, which
        # at an angle is further than thickness/2 and cuts its end on a slant.
        ug = axis(green)
        ng = (-ug[1], ug[0])
        across = side_of(far_end(blue, point), point, ng)
        keeps[blue].append(half_plane(
            (point[0]-ng[0]*across*green_run.thickness_ft/2,
             point[1]-ng[1]*across*green_run.thickness_ft/2), (ng[0]*across, ng[1]*across)))
        extensions[blue][0 if math.dist(point, blue_run.start) <= POINT_TOL_FT else 1] = reach

    shapes = {}
    for index, wall_run in enumerate(layout.runs):
        ux, uy = axis(index)
        start = (wall_run.start[0]-ux*extensions[index][0], wall_run.start[1]-uy*extensions[index][0])
        end = (wall_run.end[0]+ux*extensions[index][1], wall_run.end[1]+uy*extensions[index][1])
        shape = LineString((start, end)).buffer(wall_run.thickness_ft/2, cap_style=2)
        for keep in keeps[index]:
            shape = shape.intersection(keep)
        shapes[index] = shape
    return shapes


_CACHE: list = []  # (model, layout) for the few models a run touches


def build_runs(model) -> RunLayout:
    """Group a model's walls into runs, reusing the last result for a model.

    Authoring, the expectations and every host-name lookup ask for the same
    layout, and rebuilding it per caller is quadratic in a drawing's walls.
    BuildingModel is frozen, so a layout stays valid for as long as the model
    object does; the entry holds the model itself, never a recycled id.
    """
    for cached_model, layout in _CACHE:
        if cached_model is model:
            return layout
    layout = _build_runs(model)
    _CACHE.append((model, layout))
    del _CACHE[:-4]
    return layout


def _build_runs(model) -> RunLayout:
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
        runs.append(WallRun(f"W{min(group):03d}", tuple(group), start, end,
                            first.thickness_ft, first.source_layer,
                            tuple(sorted({sid for i in group for sid in model.walls[i].source_ids})),
                            0))
        for i in group:
            run_of_wall[i] = run_index
    runs = tuple(runs)

    proposed = []
    for kind, point, indices in decisions:
        involved = sorted({run_of_wall[i] for i in indices})
        if kind == "L":
            blue = min(involved, key=lambda index: rank[lines.find(runs[index].members[0])])
            green = next(index for index in involved if index != blue)
            if not _joinable(model, runs, point, green, blue):
                untrimmed.append(point)
                continue
            proposed.append(RunConnection(green, blue, _end_type(runs[green], point),
                                          _end_type(runs[blue], point), point))
        else:
            blue = next(index for index in involved
                        if _end_type(runs[index], point) == "ATPATH")
            for green in involved:
                if green == blue:
                    continue
                if not _joinable(model, runs, point, green, blue):
                    untrimmed.append(point)
                    continue
                proposed.append(RunConnection(green, blue, _end_type(runs[green], point),
                                              "ATPATH", point))
    # A wall passing through a junction must outrank the wall stopping against
    # it, whatever the ranking says, or IfcOpenShell notches or splits it. Those
    # joints are recorded first; an L that would contradict them is dropped.
    proposed.sort(key=lambda c: (c.related_type != "ATPATH", c.point, c.relating, c.related))
    connections, edges = [], []
    for connection in proposed:
        if _outranks(edges, connection.relating, connection.related):
            untrimmed.append(connection.point)
            continue
        edges.append((connection.relating, connection.related))
        connections.append(connection)
    runs = tuple(replace(wall_run, priority=priority) for wall_run, priority
                 in zip(runs, _priorities(len(runs), edges)))
    connections.sort(key=lambda c: (c.point, c.relating, c.related))
    return RunLayout(runs, tuple(connections), tuple(sorted(untrimmed)), run_of_wall)
