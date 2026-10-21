"""What a region's class does to the walls around it and to the slab under it.

void  - no slab; thin walls along its edge are railings (thick ones stay walls)
pool  - no slab; every wall along its edge is a pool edge, going down below the floor
Every other class is recorded on the space and changes no geometry here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from shapely.geometry import Point, Polygon

from archiagent.geometry.room_attributes import railing_wall_indices


#: Share of a wall's own length that must lie along a pool's edge for it to be a pool edge.
POOL_EDGE_WALL_SHARE = 0.8


@dataclass(frozen=True)
class RegionEffects:
    walls: tuple
    voids: tuple          # rings with no slab
    railings: int = 0
    pool_edges: int = 0


def apply_regions(walls, spaces, classes: dict[int, str]) -> RegionEffects:
    walls = tuple(walls)
    voids = tuple(spaces[i].boundary for i, c in sorted(classes.items()) if c == "void")
    pools = tuple(spaces[i].boundary for i, c in sorted(classes.items()) if c == "pool")
    # A pool edge must lie mostly along the pool: a long boundary wall that only passes it stays a wall.
    edge = railing_wall_indices(walls, pools, max_thickness_ft=None, min_wall_share=POOL_EDGE_WALL_SHARE) if pools else set()
    rail = (railing_wall_indices(walls, voids) - edge) if voids else set()
    out = tuple(replace(w, wall_class="pool_edge") if i in edge else
                replace(w, wall_class="railing") if i in rail else w for i, w in enumerate(walls))
    return RegionEffects(out, voids + pools, len(rail), len(edge))


def carry_classes(old_spaces, classes: dict[int, str], new_spaces) -> tuple[str, ...]:
    """Each re-detected space takes the class of the original space it lies in."""
    old = [Polygon(s.boundary, s.holes) for s in old_spaces]
    out = []
    for s in new_spaces:
        p = Polygon(s.boundary, s.holes).representative_point()
        hit = next((i for i, poly in enumerate(old) if poly.contains(p)), None)
        out.append(classes.get(hit, "") if hit is not None else "")
    return tuple(out)
