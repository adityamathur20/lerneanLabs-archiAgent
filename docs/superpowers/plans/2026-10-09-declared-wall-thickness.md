# Declared wall thickness, typed on the scale screen

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user can state the wall thicknesses of their drawing (inches by default) beside the scale, optionally say "these are the only ones", and the pipeline uses that set instead of guessing it from the drawing.

**Status:** supersedes `2026-10-06-declared-wall-metrics.md`, narrowed on 2026-10-09 (owner decision: **thicknesses are typed on the scale screen; observed-thickness suggestions come later**). Spec: `docs/superpowers/specs/2026-10-06-declared-wall-metrics-design.md` §1, §2, §4 stand; its "GUI for thicknesses" proposals (observed classes with run counts, pre-ticked) and the "N runs would be rejected" preview are **deferred**, as is §6.

**Architecture:** Declared thicknesses travel as new defaulted fields on `candidacy.Context`, replacing `_thickness_modes`' inference when present; an optional exhaustive flag forces a `reject` band after scoring, leaving the tuned weights alone. They enter through three new CLI flags, two new `StartRequest` fields, and a "Wall thickness" section in the Drawing view's Scale panel.

**Tech Stack:** Python 3.12 / pytest / shapely (archiAgent); FastAPI / pydantic (service); the `cad/` panel (plain JS, `node --test`).

## What already exists, and what this plan does not redo

Verified 2026-10-09: `archiagent/geometry/candidacy.py` and `archiagent/pipeline.py` are **unchanged** since the old plan was written (commit `e2eecb9`), so its code for Tasks 1, 2, 3 and 5 is still exact.

| Old plan task | Now |
|---|---|
| 1 Declared thicknesses replace inferred modes | **T1 here: execute old Task 1 verbatim** |
| 2 The exhaustive veto, as a band override | **T2: execute old Task 2 verbatim** |
| 3 Thread the declared set from the pipeline | **T3: execute old Task 3 verbatim** |
| 4 Scale from one asserted length | **Done** by the scale work: `scale_from_reviewed`, `--scale-from-wall`, `_wall_length_measurements`. Skip. |
| 5 Thickness-versus-scale cross-check | **T4: execute old Task 5 verbatim**, one edit: its warning text says `units_per_foot=…`; it must say "the scale this run resolved (…)" because `--units-per-foot` no longer exists |
| 6 CLI flags | **Rewritten as T5** (the scale ladder it patched was replaced) |
| 7 Document the flags | T9 |

New here: T6 service, T7 panel, T8 measurement on the corpus.

## Global constraints

- Tests: `checks/` (archiAgent, `python -m pytest`), `service/tests` (with `REQUIRE_SERVICES=1` and the compose stack up), `tests/*.test.mjs` (`npm test`, Node 20 and 22).
- `Context` stores **feet only**; inches become feet **once**, at the CLI boundary (archiAgent) — and mm/cm become inches **once**, in the panel.
- Do not change `WEIGHTS`, `ACCEPT_FLOOR`, `REJECT_CEILING` or `combine`.
- Every new `Context` field is defaulted; existing callers are unaffected.
- Thickness is **optional**: Convert stays gated by scale alone.
- Branches, not `main`; archiAgent merges before archiViewer (the worker image builds from archiAgent `main`).

---

## T1–T4 — archiAgent core (old plan Tasks 1, 2, 3, 5)

Execute `2026-10-06-declared-wall-metrics.md` Tasks 1, 2, 3 and 5 as written, in that order, with the one T4 wording edit above. Each ends in its own commit.

## T5 — CLI flags (rewrites old Task 6)

**Files:** `archiagent/cli.py` (flags beside `--scale-from-wall`; replay-incompatible set at the `--replay-manifest` check; the `extract_from_dxf` call), `checks/test_declared_metrics_cli.py`

**Interfaces:** produces `cli._declared_thickness_ft(args) -> tuple[float, ...]`; consumes the keyword arguments T3 added to `extract_from_dxf`.

- [ ] Flags: `--wall-thickness IN [IN ...]` (`type=float`), `--wall-thickness-exhaustive`, `--wall-thickness-tolerance-in X` (default `0.5`).
- [ ] `_declared_thickness_ft`: each value finite and in `(0, 48]` inches, else `ValueError` (exit 3); duplicates collapsed; sorted; divided by 12 here and nowhere else.
- [ ] `--wall-thickness-exhaustive` without `--wall-thickness` is refused with a message naming the missing flag. Tolerance must be finite, `> 0`, `<= 6`.
- [ ] The three flags join `--replay-manifest`'s incompatible set (a replay must not re-decide geometry).
- [ ] Tests: inches→feet exactly once; each refusal; a CLI run on a synthetic drawing where a declared set that excludes the 6″ junk run changes the candidate verdicts, and the same run without the flag is byte-identical to today's output.

## T6 — Service

**Files:** `service/archiagent_service/api.py` (`StartRequest`), `pipeline.py` (`build_command`), `service/tests/test_start_request.py`, `test_pipeline.py`

- [ ] `StartRequest.wall_thickness_in: list[float] | None` (1–6 entries, each finite, `0 < v <= 48`) and `wall_thickness_exhaustive: bool | None`. A validator refuses exhaustive without a set. Both are accepted by `/start` and `/retry`.
- [ ] `build_command` emits `--wall-thickness 4 8` and `--wall-thickness-exhaustive`; nothing when absent. Values are formatted as plain decimals so they can never be read as a flag.
- [ ] Tests: each refusal; the emitted argv; a real parse of that argv by `archiagent.cli._parser` (the check used for `scale_from_wall`).

## T7 — The panel

**Files:** `cad/src/scale-model.js` (pure `evaluateThickness`), `cad/src/scale-panel.js`, `cad/index.html`, `tests/scale-model.test.mjs`, `scripts/cad-check.mjs`

- [ ] `evaluateThickness(rows, exhaustive)` → `{inches: number[], problems, options}`: unit `in` (default), `mm`, `cm`; converted to inches once and rounded to 3 decimals; an empty row is ignored, not an error; non-numeric, `<= 0` or `> 48 in` is a per-row problem; values within 0.01 in are one thickness.
- [ ] The panel gains a "Wall thickness (optional)" section: rows of *[number] [in ▾] ×*, an "Add a thickness" button, and the checkbox *"These are the only wall thicknesses in this drawing — reject anything else"* — disabled until one valid thickness exists, off by default. Convert merges its options into the scale options; a thickness problem blocks Convert and names the row.
- [ ] Dev mode prints the matching CLI flags.
- [ ] Tests: unit conversions (`4.5 in`, `114.3 mm`, `11.43 cm` are one thickness), every problem, the exhaustive guard. `cad-check`: add two thicknesses in different units, assert the options sent; the exhaustive box stays disabled with none; a bad row blocks Convert.

## T8 — Measure it on the corpus

- [ ] Using `--rules` (offline, no model) and an asserted scale, run the corpus drawings with and without a declared set (9″ and 4½″ for those drawn that way; say which in the report) and record: walls kept, walls rejected by the veto, change in the number of phantom walls.
- [ ] Sanity: a declared set equal to the one inference finds changes nothing.
- [ ] Report the numbers to the owner before deploying; **a veto that rejects real walls is the failure to look for.**

## T9 — Docs and release

- [ ] CLI help, `service/README.md`, the spec's status line.
- [ ] Merge order, backup, deploy and live smoke test as in `deploy/README.md` §11.

## Not in this plan

Observed-thickness suggestions with run counts; the "N runs would be rejected" preview; per-layer or per-storey thickness sets; the staged project/gates design.
