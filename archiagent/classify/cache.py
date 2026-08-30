"""On-disk cache for layer classifications.

Re-running the same drawing should cost nothing and produce a byte-identical
model -- which is also what keeps a golden test on real output deterministic.

The key covers the inventory AND the model id AND the provider AND the base
URL AND the prompt version. Keying on the inventory alone would serve a
stale answer after any prompt change, and omitting the endpoint would serve
one provider's answer for another's -- `--provider openai --model
llama-3.3-70b` reaches Groq or DeepSeek depending only on the base URL.
Both are the kind of silent staleness this project keeps eliminating.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import (SOURCES, WALL_ROLES, Classification,
                                        LayerClassifier, LayerDecision)
from archiagent.classify.prompt import PROMPT_VERSION
from archiagent.classify.roles import Role

ENV_CACHE_DIR = "ARCHIAGENT_CACHE_DIR"


def cache_dir() -> Path:
    override = os.environ.get(ENV_CACHE_DIR)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "archiagent" / "layers"


def inventory_key(stats: tuple[LayerStats, ...], model: str,
                  provider: str = "", base_url: str | None = None) -> str:
    """A stable digest of everything that could change the answer.

    INVARIANT: every numeric field below is rounded AT LEAST as finely as
    the prompt that renders it (axis% to 0 decimals here rounded to 4; p10/
    p50/p90 to 1 decimal there, 3 here; bbox to 1 there, 2 here; DXF
    share% to 1 decimal there, 4 here). So the key can only ever distinguish
    two inventories that the prompt renders identically -- a needless MISS,
    which is merely a wasted call. It can never collapse two inventories the
    prompt renders DIFFERENTLY into one key, which would be a false HIT
    serving the wrong drawing's answer. Coarsening a field here, or adding
    precision to `_row` / `_dxf_row`, breaks that direction;
    test_key_rounding_is_at_least_as_fine_as_the_prompt guards it for the
    PDF fields.

    The seven DXF-only LayerStats fields (entity_mix, entity_share,
    lineweight, linetype, is_off, is_frozen, extent_ratio) are hashed too.
    `build_dxf_user_prompt` / `_dxf_row` render six of them straight into
    the prompt the model sees (entity_mix, entity_share, lineweight,
    linetype, is_off, is_frozen, folded into a "flags" column) -- omitting
    them would let a DXF-only edit (unfreezing a layer, raising a
    lineweight off the -3 "no signal" sentinel) collide with the pre-edit
    key and serve a stale answer, silently, since a cache HIT returns
    before the inner classifier -- and therefore before on_issue -- ever
    runs. extent_ratio is not currently prompt-rendered but is included
    anyway: an unrendered field can only ever cause a needless MISS, never
    a false HIT, so hashing it trades nothing away and guards against it
    being wired into the prompt later without this key being updated to
    match. These fields are absent (left at their dataclass defaults) for
    the PDF front-end, so this changes nothing for PDF inventories.
    """
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "provider": provider,
        "base_url": base_url or "",
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
                "entity_mix": [[k, v] for k, v in s.entity_mix],
                "entity_share": round(s.entity_share, 4),
                "lineweight": s.lineweight,
                "linetype": s.linetype,
                "is_off": s.is_off,
                "is_frozen": s.is_frozen,
                "extent_ratio": round(s.extent_ratio, 4),
            }
            for s in stats
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _checked_source(value: object) -> str:
    """`source` drives validate(): only "default" warns.

    A hand-edited entry saying "LLM" or "model" would therefore permanently
    silence the layer_unclassified warning for that layer. An unrecognised
    value is a corrupt entry -- raise, and let read_cache turn it into a MISS.
    """
    if value not in SOURCES:
        raise ValueError(f"unknown decision source {value!r}; "
                         f"expected one of {sorted(SOURCES)}")
    return str(value)


def read_cache(key: str) -> Classification | None:
    path = cache_dir() / f"{key}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            LayerDecision(layer=d["layer"], role=Role(d["role"]),
                          confidence=float(d["confidence"]),
                          reason=d["reason"],
                          source=_checked_source(d["source"]))
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
    # Write-then-rename: a crash or a full disk mid-write must not leave a
    # half-written entry under the real key. os.replace is atomic within a
    # directory, so a reader sees the old entry or the new one, never both.
    tmp = directory / f"{key}.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, directory / f"{key}.json")


class CachingClassifier:
    """Wraps any LayerClassifier with the disk cache."""

    def __init__(self, inner: LayerClassifier, model: str,
                 provider: str = "", base_url: str | None = None,
                 enabled: bool = True) -> None:
        self._inner = inner
        self._model = model
        self._provider = provider
        self._base_url = base_url
        self._enabled = enabled

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        if not self._enabled:
            return self._inner.classify(stats)

        key = inventory_key(stats, self._model, provider=self._provider,
                            base_url=self._base_url)
        hit = read_cache(key)
        # A hit must cover exactly the layers we asked about. read_cache
        # rebuilds each decision's layer name from JSON rather than from the
        # current stats, so a hand-edited or key-colliding entry is the one
        # route by which a layer name absent from the drawing could reach
        # layers_for_roles. Treat any mismatch as a MISS.
        if hit is not None and {d.layer for d in hit} == {s.name for s in stats}:
            return hit

        decisions = self._inner.classify(stats)
        # A classification with no wall layer cannot drive the pipeline: it
        # raises "no layers were classified as walls" downstream. Persisting
        # it makes that failure STICKY -- the key does not change when the
        # user retries, so every later run reproduces it without ever calling
        # the model again. Not worth persisting.
        if any(d.role in WALL_ROLES for d in decisions):
            write_cache(key, decisions)
        return decisions
