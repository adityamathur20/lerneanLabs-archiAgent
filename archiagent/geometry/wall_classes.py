"""What a wall is, and what that means for its height and IFC type.

Classes come from the element stage (classify/element_stage.py) or from what the
drawing says (a thin wall on a void's edge is a railing). An empty class is an
ordinary wall of the storey's height.

Heights are rulings recorded in the element-stage plan; the owner may change them.
"""
from __future__ import annotations

WALL_CLASSES = ("exterior", "interior", "partition", "parapet", "railing", "site_wall",
                "pool_edge", "retaining", "not_a_wall")

PARAPET_HEIGHT_FT = 3.5
RAILING_HEIGHT_FT = 3.5
SITE_WALL_HEIGHT_FT = 6.0
#: A pool edge is a wall going down from the floor to the pool's floor.
POOL_DEPTH_FT = 5.0

_FIXED = {"parapet": PARAPET_HEIGHT_FT, "railing": RAILING_HEIGHT_FT,
          "site_wall": SITE_WALL_HEIGHT_FT, "pool_edge": POOL_DEPTH_FT}


def height_for(wall_class: str, storey_height_ft: float) -> float:
    return _FIXED.get(wall_class, storey_height_ft)


def base_offset_for(wall_class: str) -> float:
    """Where the wall starts relative to its floor: below it for a pool edge, on it otherwise."""
    return -POOL_DEPTH_FT if wall_class == "pool_edge" else 0.0


def ifc_type_for(wall_class: str) -> tuple[str, str | None]:
    if wall_class == "railing":
        return "IfcRailing", "GUARDRAIL"
    if wall_class == "parapet":
        return "IfcWallStandardCase", "PARAPET"
    return "IfcWallStandardCase", None
