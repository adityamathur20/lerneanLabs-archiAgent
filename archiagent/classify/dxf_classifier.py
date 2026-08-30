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
                on_issue: Callable[[object], None] | None = None) -> None:
        self._client = client
        self._dxf_path = dxf_path
        self._vision = vision
        self._cache_dir = (Path(cache_dir) if cache_dir is not None
                           else _default_cache_dir(dxf_path))
        self._on_issue = on_issue

    def classify(self, stats: tuple[LayerStats, ...]) -> Classification:
        reply = self._client.classify_json(
            system=DXF_SYSTEM_PROMPT,
            user=build_dxf_user_prompt(stats),
            schema=response_schema(),
            max_tokens=MAX_TOKENS,
        )
        stage1 = decisions_from_reply(reply, stats)

        uncapped = escalation_candidates(stage1, stats)
        candidates = select_for_escalation(stage1, stats)
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
                f"cap ({ESCALATION_CAP} candidates max)")

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

        try:
            images: list[tuple[str, bytes]] = [
                ("reference", Path(ref_path).read_bytes())]
            for name, _ in rendered:
                images.append(
                    (f"layer {name}", Path(layer_paths[name]).read_bytes()))

            rendered_names = {name for name, _ in rendered}
            escalated_stats = tuple(s for s in stats
                                    if s.name in rendered_names)

            reply2 = self._client.classify_json_vision(
                system=DXF_SYSTEM_PROMPT,
                user=build_dxf_user_prompt(escalated_stats),
                schema=response_schema(),
                images=images,
                max_tokens=MAX_TOKENS,
            )
            stage2 = decisions_from_reply(reply2, escalated_stats)
        except Exception as e:  # noqa: BLE001 - any failure keeps stage 1
            # Only the layers actually sent to stage 2 (`rendered`) get this
            # Issue -- the ones missing a render already got a more precise
            # reason above, and repeating this generic one for them would
            # just be noise.
            self._skip_all(rendered, f"stage 2 failed: {e}")
            return stage1

        # Only layers stage 2 actually answered (source == "llm") splice
        # over stage 1. A layer that was escalated but not answered keeps
        # its stage 1 decision rather than being overwritten with
        # decisions_from_reply's own "unanswered" default -- stage 2 must
        # only ever improve on stage 1, never quietly downgrade it.
        revised = {d.layer: d for d in stage2 if d.source == "llm"}

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
