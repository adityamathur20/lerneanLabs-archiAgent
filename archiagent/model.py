"""The assembled building model handed to the IFC author."""

from __future__ import annotations

from dataclasses import dataclass, field

from archiagent.geometry.junctions import Junction
from archiagent.geometry.spaces import Space
from archiagent.geometry.walls import WallSeg
from archiagent.primitives import Pt
from archiagent.scale.resolve import ScaleResult


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
    layer_roles: dict[str, str]
    source_path: str
    source_sha256: str
    wall_height_ft: float = 10.0
    issues: tuple[Issue, ...] = field(default=())

    def envelope(self) -> tuple[float, float, float, float]:
        xs = [c for w in self.walls for c in (w.start[0], w.end[0])]
        ys = [c for w in self.walls for c in (w.start[1], w.end[1])]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))
