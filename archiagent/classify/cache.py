"""On-disk cache for layer classifications.

Re-running the same drawing should cost nothing and produce a byte-identical
model -- which is also what keeps a golden test on real output deterministic.

The key covers the inventory AND the model id AND the prompt version.
Keying on the inventory alone would serve a stale answer after any prompt
change, which is the kind of silent staleness this project keeps
eliminating elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import (Classification, LayerClassifier,
                                        LayerDecision)
from archiagent.classify.prompt import PROMPT_VERSION
from archiagent.classify.roles import Role

ENV_CACHE_DIR = "ARCHIAGENT_CACHE_DIR"


def cache_dir() -> Path:
    override = os.environ.get(ENV_CACHE_DIR)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "archiagent" / "layers"


def inventory_key(stats: tuple[LayerStats, ...], model: str) -> str:
    """A stable digest of everything that could change the answer."""
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "layers": [
            {
                "name": s.name,
                "paths": s.path_count,
                "segs": s.segment_count,
                "axis": round(s.axis_aligned_fraction, 4),
                "p10": round(s.length_p10, 3),
                "p50": round(s.length_p50, 3),
                "p90": round(s.length_p90, 3),
                "bbox": [round(v, 2) for v in s.bbox],
                "widths": [round(w, 2) for w in s.stroke_widths],
                "colors": [[round(c, 3) for c in rgb]
                           for rgb in s.dominant_colors],
            }
            for s in stats
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_cache(key: str) -> Classification | None:
    path = cache_dir() / f"{key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            LayerDecision(layer=d["layer"], role=Role(d["role"]),
                          confidence=float(d["confidence"]),
                          reason=d["reason"], source=d["source"])
            for d in raw
        )
    except (OSError, ValueError, KeyError, TypeError):
        # A missing, truncated or hand-edited entry is a miss, not a crash.
        return None


def write_cache(key: str, decisions: Classification) -> None:
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = [
        {"layer": d.layer, "role": d.role.value, "confidence": d.confidence,
         "reason": d.reason, "source": d.source}
        for d in decisions
    ]
    (directory / f"{key}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")


class CachingClassifier:
    """Wraps any LayerClassifier with the disk cache."""

    def __init__(self, inner: LayerClassifier, model: str,
                 enabled: bool = True) -> None:
        self._inner = inner
        self._model = model
        self._enabled = enabled

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        if not self._enabled:
            return self._inner.classify(stats)

        key = inventory_key(stats, self._model)
        hit = read_cache(key)
        if hit is not None:
            return hit

        decisions = self._inner.classify(stats)
        write_cache(key, decisions)
        return decisions
