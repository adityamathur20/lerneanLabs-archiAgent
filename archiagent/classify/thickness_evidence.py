"""Declared wall thickness as evidence about layers, computed before any role is chosen.

Pairing is already layer-local (geometry/walls.py), so running the detectors over
every layer at once gives each layer's own pairs. Nothing here decides a role.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from archiagent.classify.inventory import LayerStats, ThicknessEvidence
from archiagent.geometry.walls import WallSeg, detect_walls_filled_bodies, detect_walls_paired_lines

LONG_RUN_FT = 4.0


def evidence_from_walls(walls, declared_ft: tuple[float, ...],
                        tolerance_ft: float) -> dict[str, ThicknessEvidence]:
    by_layer: dict[str, list[WallSeg]] = defaultdict(list)
    for w in walls:
        by_layer[w.source_layer].append(w)
    declared_in = tuple(round(d * 12, 3) for d in declared_ft)
    out = {}
    for layer, items in by_layer.items():
        matched = [w for w in items
                   if any(abs(w.thickness_ft - d) <= tolerance_ft + 1e-9 for d in declared_ft)]
        matched_ft = sum(w.length_ft for w in matched)
        long_ft = sum(w.length_ft for w in matched if w.length_ft >= LONG_RUN_FT)
        out[layer] = ThicknessEvidence(sum(w.length_ft for w in items), matched_ft,
                                       long_ft / matched_ft if matched_ft else 0.0, declared_in)
    return out


def attach_thickness_evidence(stats: tuple[LayerStats, ...], ps, units_per_foot: float,
                              declared_ft: tuple[float, ...],
                              tolerance_ft: float) -> tuple[LayerStats, ...]:
    """The inventory with each layer's thickness evidence filled in."""
    if not declared_ft or not units_per_foot:
        return tuple(stats)
    low = max(0.5, min(2.0, (min(declared_ft) - tolerance_ft) * 12))
    high = min(48.0, max(24.0, (max(declared_ft) + tolerance_ft) * 12))
    layers = set(ps.layer_names())
    walls = (detect_walls_paired_lines(ps, layers, units_per_foot, min_t_in=low, max_t_in=high)
             + detect_walls_filled_bodies(ps, layers, units_per_foot, min_t_in=low, max_t_in=high))
    found = evidence_from_walls(walls, declared_ft, tolerance_ft)
    return tuple(replace(s, thickness=found.get(s.name)) for s in stats)
