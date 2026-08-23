# W1 — Automatic Layer Classification + CLI

**Date:** 2026-08-23
**Status:** Design, pending review
**Roadmap:** `docs/superpowers/specs/2026-08-23-phase1-continuation-roadmap.md` (W1)
**Spec it extends:** `PLAN.md` §7 Stage 1

---

## 1. Goal

Turn `archiagent` from a library you write Python against into a tool you
point at a PDF.

Today, running the pipeline requires hand-authoring a dictionary of that
specific drawing's layer names:

```python
roles = {"walll": (Role.WALL_STRUCTURAL, 0.95), ...}
run_pipeline(pdf, StubClassifier(roles), "out.ifc")
```

W1 delivers `python -m archiagent plan.pdf out.ifc` working on an unseen
drawing, with the layer roles determined automatically.

This is also the pipeline's **first LLM call**. The mechanism W1
establishes is inherited by W3 (glyph identification) and W5 (symbol
naming), so it is designed as shared infrastructure rather than a
one-off.

### Guiding principle, unchanged

> Deterministic code owns geometry. The LLM owns vocabulary.

The classifier sees layer *statistics* and returns *roles*. It never sees
or emits a coordinate.

---

## 2. Architecture

```
LayerClassifier                     (protocol — already exists)
  ├─ StubClassifier                 (exists; retained for tests and --walls)
  └─ LLMLayerClassifier             NEW — builds the prompt, validates the reply
       └─ LLMClient                 NEW — port defined by our needs
            ├─ AnthropicClient      NEW — `anthropic` SDK. Default.
            └─ OpenAICompatClient   NEW — `openai` SDK; base_url serves
                                          OpenAI, Groq, Kimi, DeepSeek
```

**Why a port rather than a compatibility shim.** Each adapter uses its
own vendor's real SDK. We do not route Anthropic through an
OpenAI-compatible endpoint, and we do not pretend one provider's wire
format is another's. `LLMClient` is an application-level port describing
what *this project* needs — a schema-constrained classification — not a
lowest-common-denominator LLM abstraction.

Groq, Kimi (Moonshot) and DeepSeek all publish OpenAI-compatible
endpoints, so a single OpenAI adapter with a configurable `base_url`
covers them using their own documented interface.

### `LLMClient` port

```python
class LLMClient(Protocol):
    def classify_json(self, *, system: str, user: str,
                      schema: dict, max_tokens: int = 2048) -> dict:
        """Return a JSON object conforming to `schema`.

        Raises LLMUnavailable on transport, auth or quota failure.
        Raises LLMSchemaError if the provider returns something the
        schema rejects.
        """
```

One method. That is the whole surface W1, W3 and W5 need — every one of
them is "classify this into a fixed vocabulary and give me structured
JSON back". Resisting a broader interface now keeps the adapters small
and honest.

---

## 3. The classification call

### What the model sees

The existing `LayerStats` inventory, rendered as a compact table. Nothing
else. These statistics are already strongly discriminative — measured on
the demolition plan, walls are 100% axis-aligned with a median segment
length of 23.2 against ~1.0 for furniture and hatch:

```
layer            paths   segs  axis%  len_p10  len_p50  len_p90  widths      colors
'walll'            187    187   100%      4.3     23.2     71.0  [0.0]       [(0,0,0)]
'FURNITURE'       5433   5433    90%      0.4      1.1      3.2  [0.0]       [(0.29,0.51,0.58)]
'HATCH1'          6534   6534   100%      0.0      0.0      0.0  [0.0]       [...]
```

**Thumbnails are deliberately out of scope for W1.** The statistics alone
may be sufficient; adding an image per layer multiplies cost and latency
and introduces vision handling. If measured accuracy is inadequate,
thumbnails become a bounded follow-up — the port already accommodates it.

### What the model returns

Structured output constrained to a schema, so "the model invented a role
that doesn't exist" cannot occur:

```json
{
  "layers": [
    {"name": "walll", "role": "wall_structural", "confidence": 0.95,
     "reason": "100% axis-aligned, long median run"}
  ]
}
```

`role` is an enum of the exact `Role` values. `confidence` is a float in
[0, 1]. `reason` is one short clause — not used by the pipeline, but it is
what W2's review surface will display beside each interpretation, and it
makes a wrong classification diagnosable instead of merely wrong.

### Model and parameters

Default **`claude-haiku-4-5`**. This is 16–53 short judgements over a
bounded vocabulary; the task does not need a frontier model, and the
default is the user's explicit choice.

Two provider constraints that shape the call:

- **Haiku 4.5 rejects `output_config.effort`** — do not send it.
- **Haiku 4.5 does not take adaptive thinking.** Send no `thinking`
  parameter. Classification does not need it.
- `output_config: {format: {...}}` (structured outputs) is used; the
  deprecated top-level `output_format` is not.
- `max_tokens` 2048 — enough for ~53 layers with short reasons.

---

## 4. Configuration

Environment variables, with provider-native key names honoured so an
existing `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` just works:

| Variable | Default | Purpose |
|---|---|---|
| `ARCHIAGENT_LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` |
| `ARCHIAGENT_LLM_MODEL` | `claude-haiku-4-5` | model id |
| `ARCHIAGENT_LLM_BASE_URL` | unset | for Groq / Kimi / DeepSeek via the openai adapter |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | credentials, provider-native |

CLI flags override the environment. No config file in W1 — one is easy to
add later and premature now.

---

## 5. Failure handling

Nothing is silently guessed. Each failure names its cause and what to do.

| Condition | Behaviour |
|---|---|
| No API key and no provider credential | `LLMUnavailable` naming the variable to set, and suggesting `--walls` to bypass |
| Provider unreachable / quota / auth | `LLMUnavailable` with the provider's message preserved |
| Response violates the schema | `LLMSchemaError` — a bug worth surfacing, not silently retried |
| No layer classified as a wall | Existing `ValueError` from `pipeline.extract`, unchanged |
| Layer present in the drawing but absent from the reply | Defaults to `Role.IGNORE` at confidence 0.0, and is reported |

**Low confidence does not block.** A layer classified at 0.3 is used and
surfaced as an issue, consistent with `PLAN.md`'s rule that nothing is
silently corrected. Gating on confidence is W2's job, once there is
somewhere for a human to adjudicate.

### Caching

Classification is cached on disk, keyed by a hash of the layer inventory
(names plus rounded statistics). Re-running the same drawing costs
nothing and produces an identical model — which also keeps the golden
tests deterministic. Cache location: `~/.cache/archiagent/layers/`.
`--no-cache` bypasses it.

---

## 6. CLI

```
python -m archiagent PDF OUT_IFC [options]

  --page N            page index (default 0)
  --height FT         wall height, default 10.0
  --walls NAME...     skip the LLM; use these layers as walls
  --provider NAME     override ARCHIAGENT_LLM_PROVIDER
  --model NAME        override ARCHIAGENT_LLM_MODEL
  --inspect           print the layer inventory and exit; author nothing
  --classify-only     classify, print the roles and confidences, exit
  --no-cache          ignore the classification cache
  -v                  report issues by severity
```

`--inspect` and `--walls` matter beyond convenience: they make the tool
usable when the LLM is unavailable, and `--inspect` is how a user finds
out what a drawing contains before spending a call on it. Both are
already proven — they were the throwaway scripts used to run the pipeline
by hand.

Exit codes: `0` success; `1` pipeline error (scale gate, no walls); `2`
LLM unavailable; `3` bad usage.

---

## 7. Testing

**Unit — no network.** A `FakeLLMClient` returning canned JSON exercises
`LLMLayerClassifier` end to end: happy path, unknown layer defaulting to
`IGNORE`, schema violation, unavailability. The adapters are thin enough
that their own tests assert request shape, not behaviour.

**Prompt/schema golden test.** Assert the generated schema enumerates
exactly the `Role` values. A role added to the vocabulary without
reaching the schema is otherwise a silent gap.

**CLI.** `--inspect` and `--walls` are tested against the real drawings
(fixture-gated, skipping when absent) and require no LLM at all.

**Live provider calls are not in the test suite.** A single manual
verification against each of the three drawings is recorded in the
implementation report instead.

---

## 8. Explicitly out of scope

- Reviewing or correcting the classification — **W2**
- Glyph or symbol identification — **W3 / W5**, reusing `LLMClient`
- Per-layer thumbnails — bounded follow-up if accuracy demands it
- A config file — environment and flags suffice for W1
- Retry and backoff beyond what each vendor SDK already does
- Heuristic pre-classification — deliberately excluded; a heuristic that
  quietly misclassifies is a new silent-failure mode, and the whole
  design exists to avoid those

---

## 9. Success criteria

1. `python -m archiagent "DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" out.ifc`
   runs end to end with no hand-written role map and passes the R1 scale
   gate.

   **The classification need not reproduce the hand-written map.** That
   map was one person's judgement, and at least one of its calls was
   demonstrably wrong — classifying the hatch layers as walls produced a
   40% scale error (`PLAN.md`, Finding C). The bar is that any difference
   from the hand map is **explainable from the `reason` field and does
   not degrade the measured result**. Record the actual numbers; do not
   tune the prompt to reproduce a specific wall count.

   Reference figures from the hand map, for comparison only: scale
   11.6667 pt/ft, 51 walls, 28 junctions, 1 space.

2. The electrical plan runs end to end and passes the gate. Reference
   figures: scale 6.8267 pt/ft, 258 walls, 14 spaces.
3. The ground-floor plan still fails at the scale gate — W1 does not
   pretend to fix what W3 addresses. The failure must name the scale
   gate, not the classifier.
4. `--inspect` and `--walls` work with no credentials configured.
5. Every layer in each drawing receives a role and a confidence, and any
   layer the model omits is reported rather than silently ignored.
