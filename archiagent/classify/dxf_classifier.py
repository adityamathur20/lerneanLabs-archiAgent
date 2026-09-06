"""The pipeline's two-stage DXF layer classifier.

Stage 1 is a cheap text-only pass over the whole inventory. A deterministic
rule (`select_for_escalation`) then names the layers stage 1's own numbers
say are not trustworthy -- low confidence, a suspiciously large non-wall
layer, an uninformative name, or competing wall layers. Stage 2 shows the
model PICTURES of just those layers and may revise their roles.

Stage 2 is an improvement, never a dependency. Anything that can go wrong
with it -- no renderer installed, a render failing, a transport error, a
malformed reply -- is caught here, reported as an Issue, and stage 1's
decisions are returned unchanged. This classifier always produces a
classification.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from archiagent.classify.escalate import (ESCALATION_CAP, escalation_candidates,
                                          select_for_escalation)
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import Classification
from archiagent.classify.llm_classifier import MAX_TOKENS, decisions_from_reply
from archiagent.classify.prompt import (DXF_SYSTEM_PROMPT,
                                        build_dxf_user_prompt,
                                        response_schema)
# Imported into this module's namespace deliberately: tests monkeypatch
# render_for_escalation here, at
# archiagent.classify.dxf_classifier.render_for_escalation, not on the
# thumbnails module.
from archiagent.classify.thumbnails import RenderUnavailable, render_for_escalation
from archiagent.llm.client import LLMClient
from archiagent.model import Issue

# MAX_TOKENS is defined once, in llm_classifier.py, and imported here rather
# than redeclared -- two independent constants of the same name meant
# bumping one silently left the DXF and PDF paths on different budgets.

# Where rendered layer thumbnails land when the caller does not specify a
# cache_dir, relative to the directory holding the drawing itself. Confidential
# client drawings, so this must never be under the repo, and it must not be a
# shared, cross-client bucket like the user's home cache either -- a render is
# a pixel-perfect picture of one client's building, not a small JSON role
# decision. .archiagent-cache/ is scoped to the engagement, deletable with it,
# and already covered by THIS repo's .gitignore -- but that .gitignore only
# protects the path inside this repo. Anchoring it to the process's current
# working directory (as an earlier revision did) meant running the tool from
# a client's own git repo, or from a home directory under version control,
# could land renders of a confidential building somewhere nothing ignores.
# Anchoring it to the input DXF's own directory instead means the renders sit
# beside the drawing they came from, regardless of where the tool is invoked
# from.
DEFAULT_CACHE_DIR_NAME = Path(".archiagent-cache") / "thumbnails"


VISION_BATCH_SIZE = 4


def _default_cache_dir(dxf_path: str | Path) -> Path:
    return Path(dxf_path).resolve().parent / DEFAULT_CACHE_DIR_NAME


class DxfLayerClassifier:
    """LayerClassifier for DXF drawings: text stage 1, optional vision stage 2.

    Satisfies the LayerClassifier protocol -- pipeline.py calls `classify`
    and never learns whether vision happened.

    `vision` defaults to True: the shipped policy is vision ON by default,
    and that policy belongs on the component itself, not only in the CLI
    that happens to construct it today. A script, notebook, or test that
    builds this class directly gets the same default the CLI ships --
    escalated layers get a second look via rendered images sent to the
    configured LLM provider -- unless it opts out explicitly.
    """

    def __init__(self, client: LLMClient, dxf_path: str | Path, *,
                vision: bool = True, cache_dir: str | Path | None = None,
                max_tokens: int = MAX_TOKENS,
                cap: int = ESCALATION_CAP,
                batch_size: int = VISION_BATCH_SIZE,
                on_issue: Callable[[object], None] | None = None) -> None:
        self._client = client
        self._dxf_path = dxf_path
        self._vision = vision
        self._max_tokens = max_tokens
        self._cap = cap
        self._batch_size = batch_size
        self._cache_dir = (Path(cache_dir) if cache_dir is not None
                           else _default_cache_dir(dxf_path))
        self._on_issue = on_issue

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        reply = self._client.classify_json(
            system=DXF_SYSTEM_PROMPT,
            user=build_dxf_user_prompt(stats),
            schema=response_schema(),
            max_tokens=self._max_tokens,
        )
        stage1 = decisions_from_reply(reply, stats)

        uncapped = escalation_candidates(stage1, stats)
        candidates = select_for_escalation(stage1, stats, self._cap)
        if not candidates:
            return stage1

        # The cap keeps the vision call cheap, but a layer it drops must
        # not fall through with zero signal in either mode -- it is exactly
        # as untrustworthy as the six that survive the cap.
        kept = {name for name, _ in candidates}
        dropped = [c for c in uncapped if c[0] not in kept]
        for layer, trigger in dropped:
            self._report(
                "warn", layer, "layer_escalation_skipped",
                f"escalation trigger={trigger!r}: dropped by the escalation "
                f"cap ({self._cap} candidates max)")

        if not self._vision:
            self._skip_all(candidates, "vision is off")
            return stage1

        return self._escalate(stage1, stats, candidates)

    def _escalate(self, stage1: Classification,
                 stats: tuple[LayerStats, ...],
                 candidates: tuple[tuple[str, str], ...]) -> Classification:
        names = [name for name, _ in candidates]

        try:
            ref_path, layer_paths = render_for_escalation(
                self._dxf_path, names, self._cache_dir)
        except RenderUnavailable as e:
            self._skip_all(candidates, f"renderer unavailable: {e}")
            return stage1
        except Exception as e:  # noqa: BLE001 - stage 2 is best-effort
            self._skip_all(candidates, f"render failed: {e}")
            return stage1

        # A layer holding no entities, or whose render raised, is skipped by
        # render_for_escalation and absent from layer_paths -- it never
        # reaches the model. That must be reported per layer, independent
        # of whether any of the other candidates rendered successfully:
        # missing one of three candidates is not "no renderable layers".
        rendered = [(name, trigger) for name, trigger in candidates
                   if name in layer_paths]
        missing = [(name, trigger) for name, trigger in candidates
                  if name not in layer_paths]
        for layer, trigger in missing:
            self._report(
                "warn", layer, "layer_escalation_skipped",
                f"escalation trigger={trigger!r}: layer render unavailable "
                "(no entities, or the render failed)")
        if not rendered:
            return stage1

        revised: dict[str, object] = {}
        batches = _batches(rendered, self._batch_size)

        # One call per batch instead of one call carrying everything.
        # Measured: 4 layers to a reasoning model took 7m18s, and a single
        # call carrying 20 would exceed the SDK's 600s ceiling -- returning
        # nothing at all, not a partial classification. Batches also mean one
        # failure costs four layers instead of every one of them.
        #
        # Run them together: the batches are independent, so wall-clock is
        # roughly one batch rather than the sum.
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            futures = {pool.submit(self._vision_batch, batch, ref_path,
                                   layer_paths, stats): batch
                       for batch in batches}
            for fut in as_completed(futures):
                batch = futures[fut]
                try:
                    revised.update(fut.result())
                except Exception as e:  # noqa: BLE001 - one batch, not the run
                    self._skip_all(tuple(batch), f"stage 2 failed: {e}")

        if not revised:
            return stage1

        out = []
        for d in stage1:
            new = revised.get(d.layer)
            if new is None:
                out.append(d)
                continue
            if new.role != d.role:
                self._report(
                    "info", d.layer, "layer_role_revised",
                    f"vision revised role {d.role.value!r} -> "
                    f"{new.role.value!r} (confidence {new.confidence:.2f})")
            out.append(new)
        return tuple(out)

    def _vision_batch(self, batch, ref_path, layer_paths,
                     stats: tuple[LayerStats, ...]) -> dict:
        """One vision call over one batch. Raises; the caller reports."""
        images: list[tuple[str, bytes]] = [
            ("reference", Path(ref_path).read_bytes())]
        for name, _ in batch:
            images.append((f"layer {name}", Path(layer_paths[name]).read_bytes()))

        names = {name for name, _ in batch}
        batch_stats = tuple(s for s in stats if s.name in names)
        reply = self._client.classify_json_vision(
            system=DXF_SYSTEM_PROMPT,
            user=build_dxf_user_prompt(batch_stats),
            schema=response_schema(),
            images=images,
            max_tokens=self._max_tokens,
        )
        return {d.layer: d for d in decisions_from_reply(reply, batch_stats)
                if d.source == "llm"}

    def _skip_all(self, candidates: tuple[tuple[str, str], ...],
                 reason: str) -> None:
        for layer, trigger in candidates:
            self._report(
                "warn", layer, "layer_escalation_skipped",
                f"escalation trigger={trigger!r}: {reason}")

    def _report(self, severity: str, entity: str, code: str, msg: str) -> None:
        if self._on_issue is None:
            return
        self._on_issue(Issue(severity, entity, code, msg))


def _batches(candidates, size: int) -> list[list]:
    """Split into batches, keeping wall-role rivals together.

    Every batch also carries the whole-drawing reference image, so a layer is
    still judged against its drawing. But `competing_wall_layers` candidates
    only mean anything SIDE BY SIDE -- the question is which of two confident
    wall layers is really the walls -- so splitting them across batches would
    destroy the comparison the trigger exists to make.
    """
    rivals = [c for c in candidates if c[1] == "competing_wall_layers"]
    others = [c for c in candidates if c[1] != "competing_wall_layers"]
    out = [rivals[i:i + size] for i in range(0, len(rivals), size)]
    out += [others[i:i + size] for i in range(0, len(others), size)]
    return [b for b in out if b]
