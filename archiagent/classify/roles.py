"""The fixed role vocabulary a drawing's private layer names map onto.

Layer names are a per-drawing vocabulary (WALLS / WALL / Wall / walll /
A-Wall all appear across our three samples). Mapping them onto this fixed
set is the LLM's job — see PLAN.md §7 Stage 1.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    WALL_STRUCTURAL = "wall_structural"
    WALL_PARTITION = "wall_partition"
    COLUMN = "column"
    BEAM_OVERHEAD = "beam_overhead"
    DOOR = "door"
    WINDOW = "window"
    STAIR = "stair"
    RAILING = "railing"
    DIMENSION = "dimension"
    TEXT_LABEL = "text_label"
    GRID = "grid"
    ANNOTATION = "annotation"
    TITLE_BLOCK = "title_block"
    FURNITURE = "furniture"
    ELECTRICAL = "electrical"
    PLUMBING = "plumbing"
    VEHICLE = "vehicle"
    LANDSCAPE = "landscape"
    IGNORE = "ignore"
