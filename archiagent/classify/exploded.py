"""Conservative swing-door proposals in exploded, unlayered CAD geometry.

A connected curve is not itself a door. These rules require a fitted quarter
circle and a radial leaf joining its center to an arc endpoint. They neither
identify generic rectangles as columns nor infer symbols from bounding boxes.
All thresholds are in feet after explicit source-scale conversion.
"""
from __future__ import annotations

from dataclasses import replace
import math
import json

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree


def _circle_arc(points):
    """Return (center, radius) only for well-supported monotone quarter arcs."""
    if len(points) < 5:
        return None
    mx = sum(p[0] for p in points) / len(points)
    my = sum(p[1] for p in points) / len(points)
    local = [(p[0]-mx, p[1]-my) for p in points]
    xx = sum(x*x for x, y in local)
    yy = sum(y*y for x, y in local)
    xy = sum(x*y for x, y in local)
    det = xx*yy-xy*xy
    if det <= 1e-8*(xx+yy)**2 or xx+yy <= 0:
        return None
    xr = sum(x*(x*x+y*y) for x, y in local)/2
    yr = sum(y*(x*x+y*y) for x, y in local)/2
    cx, cy = (xr*yy-yr*xy)/det+mx, (yr*xx-xr*xy)/det+my
    distances = [math.hypot(x-cx, y-cy) for x, y in points]
    radius = sum(distances)/len(distances)
    if not 1.5 <= radius <= 5:
        return None
    residuals = [abs(d-radius) for d in distances]
    if max(residuals) > .025 or math.sqrt(sum(r*r for r in residuals)/len(points)) > .012:
        return None
    angles = [math.atan2(y-cy, x-cx) for x, y in points]
    turns = [(b-a+math.pi) % (2*math.pi)-math.pi for a, b in zip(angles, angles[1:])]
    sweep = sum(turns)
    if not math.radians(65) <= abs(sweep) <= math.radians(115):
        return None
    if any(t*sweep <= 0 or abs(t) > math.radians(35) for t in turns):
        return None
    return (cx, cy), radius


def _smooth(a, b, c):
    u, v = (b[0]-a[0], b[1]-a[1]), (c[0]-b[0], c[1]-b[1])
    length = math.hypot(*u)*math.hypot(*v)
    return length > 0 and (u[0]*v[0]+u[1]*v[1])/length >= math.cos(math.radians(35))


def _chains(edges):
    """Trace each source edge once, splitting branches and sharp corners.

    Endpoint clustering is limited to 0.002 ft and serves recognition only;
    wall coordinates are never changed by this operation.
    """
    tolerance = .002
    buckets, nodes, node_for = {}, [], {}
    for point in sorted({p for a, b, ids in edges for p in (a, b)}):
        cell = (math.floor(point[0]/tolerance), math.floor(point[1]/tolerance))
        near = [i for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for i in buckets.get((cell[0]+dx, cell[1]+dy), ())
                if math.dist(nodes[i], point) <= tolerance]
        if near:
            node_for[point] = min(near, key=lambda i: (math.dist(nodes[i], point), i))
        else:
            index = len(nodes)
            nodes.append(point)
            buckets.setdefault(cell, []).append(index)
            node_for[point] = index
    # Duplicate coincident linework must not create artificial branches.
    unique = {}
    for a, b, ids in edges:
        key = tuple(sorted((node_for[a], node_for[b])))
        if key[0] != key[1]:
            unique.setdefault(key, set()).update(ids)
    records = [(a, b, ids) for (a, b), ids in sorted(unique.items())]
    adjacency = {}
    for i, (a, b, ids) in enumerate(records):
        adjacency.setdefault(a, []).append(i)
        adjacency.setdefault(b, []).append(i)
    seen = set()
    def trace(start, edge):
        points, ids = [nodes[start]], set()
        node = start
        while edge not in seen:
            seen.add(edge)
            a, b, source_ids = records[edge]
            ids.update(source_ids)
            other = b if a == node else a
            points.append(nodes[other])
            incident = adjacency[other]
            if len(incident) != 2:
                break
            following = incident[0] if incident[1] == edge else incident[1]
            na, nb, _ = records[following]
            last = nb if na == other else na
            if following in seen or not _smooth(nodes[node], nodes[other], nodes[last]):
                break
            node, edge = other, following
        return tuple(points), ids
    # Start on branch/end nodes and sharp corners before processing cycles.
    starts = []
    for node, incident in sorted(adjacency.items()):
        corner = len(incident) != 2
        if not corner:
            neighbors = [b if a == node else a for a, b, _ in (records[i] for i in incident)]
            corner = not _smooth(nodes[neighbors[0]], nodes[node], nodes[neighbors[1]])
        if corner:
            starts.extend((node, edge) for edge in incident)
    starts.extend((a, i) for i, (a, b, ids) in enumerate(records))
    for node, edge in starts:
        if edge not in seen:
            yield trace(node, edge)


def recognize_exploded_doors(ps, units_per_foot, claimed_ids=()):
    """Return source-linked swing hypotheses; wall hosting remains separate."""
    if not math.isfinite(units_per_foot) or units_per_foot <= 0:
        raise ValueError("exploded recognition requires finite positive units_per_foot")
    from archiagent.recognition import _instance
    claimed = set(claimed_ids)
    partial = {e.id for e in ps.entities if dict(e.metadata).get("region_partial") == "true"}
    edges, leaves = [], []
    for i, primitive in enumerate(ps.primitives):
        sid = primitive.source_id or f"primitive-{i}"
        if sid in claimed or sid in partial or primitive.kind == "fill":
            continue
        if primitive.entity_type and primitive.entity_type not in ("LINE", "LWPOLYLINE", "POLYLINE", "PDF_PATH"):
            continue
        for a, b in primitive.segments():
            a = tuple(v/units_per_foot for v in a)
            b = tuple(v/units_per_foot for v in b)
            length = math.dist(a, b)
            if not math.isfinite(length):
                continue
            if 1.4 <= length <= 5.1:
                leaves.append((a, b, sid))
            if .005 <= length <= 2.75:
                edges.append((a, b, {sid}))
    if not leaves or not edges:
        return ()
    leaf_lines = [LineString((a, b)) for a, b, sid in leaves]
    leaf_tree = STRtree(leaf_lines)
    proposals = []
    for points, ids in _chains(edges):
        fit = _circle_arc(points)
        if fit is None:
            continue
        center, radius = fit
        candidates = []
        for i in leaf_tree.query(Point(center).buffer(.08)):
            a, b, sid = leaves[int(i)]
            if math.dist(center, a) > math.dist(center, b):
                a, b = b, a
            if math.dist(center, a) > .08 or abs(math.dist(a, b)-radius) > .08:
                continue
            tip_error = min(math.dist(b, points[0]), math.dist(b, points[-1]))
            if tip_error <= .08:
                candidates.append((math.dist(center, a)+tip_error, sid, a, b))
        if not candidates:
            continue
        candidates.sort()
        _, leaf_id, leaf_a, leaf_b = candidates[0]
        ids = ids | {leaf_id}
        # Distinct leaves at both arc endpoints make a wedge, not a proven
        # single door leaf; retain such symbols for richer interpretation.
        if any(min(math.dist(tip, leaf_b), math.dist(tip, leaf_a)) > .1
               for _, _, hinge, tip in candidates[1:]):
            continue
        symbol = _instance("door", ids, points+(leaf_a, leaf_b), (), "exploded-arc-and-leaf")
        if symbol:
            closed_tip = max((points[0], points[-1]), key=lambda p: math.dist(p, leaf_b))
            rotation = math.atan2(closed_tip[1]-center[1], closed_tip[0]-center[0]) % math.pi
            proposals.append(replace(symbol, subtype="single_swing", confidence=.55,
                width_ft=radius, depth_ft=radius, rotation_rad=rotation,
                boundary=points+(leaf_a, leaf_b),
                properties=(("fitted_radius_ft", str(radius)), ("recognition", "circle-fit-and-radial-leaf"),
                            ("opening_start_ft", json.dumps(center)),
                            ("opening_end_ft", json.dumps(closed_tip)),
                            ("opening_axis_evidence", "hinge-and-opposite-arc-tip"))))
    # Deterministic ownership prevents overlapping candidates from consuming
    # the same symbol evidence twice. Prior recognized symbols take priority.
    accepted, used = [], set(claimed)
    for symbol in sorted(proposals, key=lambda s: s.id):
        if not used.intersection(symbol.source_ids):
            accepted.append(symbol)
            used.update(symbol.source_ids)
    return tuple(accepted)
