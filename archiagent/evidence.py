"""Source observations, kept separately from inferred building objects.

Coordinates and radii remain in source drawing units. Metadata is immutable
and textual so observations can be safely cached and serialized.
"""
from __future__ import annotations

from dataclasses import dataclass

Pt = tuple[float, float]


@dataclass(frozen=True)
class SourceEntity:
    id: str
    kind: str
    layer: str
    coords: tuple[Pt, ...] = ()
    center: Pt | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    closed: bool = False
    holes: tuple[tuple[Pt, ...], ...] = ()
    block_name: str = ""
    parent_id: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NativeDimension:
    id: str
    start: Pt
    end: Pt
    measured_source_units: float
    text: str = ""
    layer: str = ""
    measurement_type: str = "linear"
    # Unit direction of the measured span. start/end are the actual witness
    # reference points, not the displaced annotation baseline endpoints.
    measurement_axis: Pt | None = None
    text_position: Pt | None = None
    text_source_ids: tuple[str, ...] = ()
    text_evidence: str = "native"


@dataclass(frozen=True)
class IngestWarning:
    code: str
    source_id: str
    message: str
