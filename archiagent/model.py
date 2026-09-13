"""The assembled building model handed to the IFC author."""

from __future__ import annotations

from dataclasses import dataclass, field

from archiagent.classify.layers import LayerDecision
from archiagent.geometry.junctions import Junction, EndpointAdjustment
from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.geometry.profiles import WallProfile
from archiagent.primitives import Pt
from archiagent.scale.resolve import ScaleResult
from archiagent.semantic import Assumption, DimensionCheck, Opening, SymbolInstance


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warn" | "info"
    entity: str
    code: str
    msg: str


@dataclass(frozen=True)
class BuildingModel:
    walls: tuple[WallSeg, ...]
    junctions: tuple[Junction, ...]
    unresolved: tuple[Pt, ...]
    spaces: tuple[Space, ...]
    scale: ScaleResult
    layer_decisions: tuple[LayerDecision, ...]
    source_path: str
    source_sha256: str
    wall_height_ft: float = 10.0
    issues: tuple[Issue, ...] = field(default=())

    symbols: tuple[SymbolInstance, ...] = ()
    openings: tuple[Opening, ...] = ()
    footprints: tuple[Space, ...] = ()
    dimension_checks: tuple[DimensionCheck, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    region_id: str = ""
    storey_name: str = "Unassigned plan"
    elevation_ft: float | None = None
    scale_verified: bool = False
    footprint_verified: bool = False
    endpoint_adjustments: tuple[EndpointAdjustment, ...] = ()
    repair_snap_in: float = 0.0
    repair_extend_in: float = 0.0
    symbols_verified: bool = False
    source_region_bounds: tuple[float, ...] = ()
    source_origin: Pt = (0.0, 0.0)
    wall_profiles: tuple[WallProfile, ...] = ()

    def envelope(self) -> tuple[float, float, float, float]:
        xs = [c for w in self.walls for c in (w.start[0], w.end[0])]
        ys = [c for w in self.walls for c in (w.start[1], w.end[1])]
        xs.extend(p[0] for profile in self.wall_profiles for p in profile.boundary)
        ys.extend(p[1] for profile in self.wall_profiles for p in profile.boundary)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))
