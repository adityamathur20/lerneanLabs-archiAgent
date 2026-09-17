"""Source-linked architectural instances. All model geometry uses feet.

Recognition is distinct from acceptance: inferred/assumed values remain visible
through authoring and validation rather than becoming measurements on export.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

Pt = tuple[float, float]


@dataclass(frozen=True)
class SymbolInstance:
    id: str
    kind: str
    position: Pt
    width_ft: float
    depth_ft: float
    rotation_rad: float = 0.0
    subtype: str = "unknown"
    source_ids: tuple[str, ...] = ()
    evidence: str = "inferred"
    confidence: float = 0.0
    boundary: tuple[Pt, ...] = ()
    height_ft: float | None = None
    properties: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Opening:
    id: str
    kind: str  # door | window | opening
    host_wall_index: int
    start: Pt
    end: Pt
    height_ft: float
    sill_ft: float = 0.0
    symbol_id: str = ""
    source_ids: tuple[str, ...] = ()
    subtype: str = "unknown"
    evidence: str = "inferred"
    assumed_height: bool = True

    @property
    def width_ft(self) -> float:
        return math.dist(self.start, self.end)


@dataclass(frozen=True)
class DimensionCheck:
    id: str
    start: Pt
    end: Pt
    expected_ft: float
    actual_ft: float | None
    error_in: float | None
    basis: str = "source-endpoints"
    status: str = "unverified"
    source_ids: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class Assumption:
    entity: str
    property: str
    value: str
    reason: str


@dataclass(frozen=True)
class PlanRegion:
    id: str
    bounds: tuple[float, float, float, float]  # source coordinates
    kind: str = "unclassified"
    name: str = "Unassigned plan"
    elevation_ft: float | None = None
    origin: Pt = (0.0, 0.0)  # source coordinate origin for registration
    evidence: str = "inferred"
    units_per_foot: float | None = None  # optional region-specific calibration

    def __post_init__(self) -> None:
        if (len(self.bounds) != 4 or not all(math.isfinite(v) for v in self.bounds)
                or self.bounds[0] >= self.bounds[2] or self.bounds[1] >= self.bounds[3]):
            raise ValueError("region bounds must be finite xmin ymin xmax ymax with positive area")
        if self.elevation_ft is not None and not math.isfinite(self.elevation_ft):
            raise ValueError("storey elevation must be finite")
        if self.units_per_foot is not None and (
                isinstance(self.units_per_foot, bool) or
                not isinstance(self.units_per_foot, (int, float)) or
                not math.isfinite(self.units_per_foot) or self.units_per_foot <= 0):
            raise ValueError("region units_per_foot must be finite and positive")
