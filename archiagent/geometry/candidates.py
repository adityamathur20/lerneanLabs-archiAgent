"""Every wall run the detectors proposed, and why it was kept or dropped.

A module of its own so BuildingModel can carry decisions without importing the
scoring code, which itself needs model.Issue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from archiagent.geometry.walls import WallSeg


@dataclass(frozen=True)
class CandidateDecision:
    id: str
    wall: WallSeg
    signals: tuple[tuple[str, float], ...]
    score: float
    verdict: Literal["accept", "reject"]
    verdict_source: Literal["deterministic", "adjudicated", "ambiguous-default", "ladder-run"]
    reason: str
