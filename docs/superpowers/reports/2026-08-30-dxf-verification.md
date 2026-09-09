# DXF Support — Verification Record

**Date:** 2026-08-30
**Branch:** `feature/dxf-layer-classification`
**Plan:** `docs/superpowers/plans/2026-08-30-dxf-layer-classification.md`
**Spec:** `docs/superpowers/specs/2026-08-30-dxf-layer-classification-design.md`

**Suite:** 311 tests pass (301 unit + 10 integration). The integration tests
skip unless `ARCHIAGENT_FIXTURES` names the drawings directory.

**No live LLM call was made anywhere in this work.** No credentials were
available. Every classification below came from `StubClassifier` or from an
explicit `--walls` argument. §5 lists what a human holding a key must run to
close the remaining criteria.

---

## 1. All four DXFs, end to end

Command shape:

```bash
python -m archiagent --dxfFilePath "<file>" --outputDir <dir> \
    --walls <layers...> [--units-per-foot N] --no_vision
```

| Drawing | `$INSUNITS` | units used | layers (empty) | entities | wall layers | walls | spaces | exit |
|---|---|---|---|---|---|---|---|---|
| `Floor Plan.dxf` | 2 = feet — **wrong** | **12 (inches), overridden** | 39 (7) | 51,786 | `0`, `WALLS` | **1000** | **27** | 0 |
| `Manoj JI Ladnu shyam nagar plumbing.dxf` | 1 = inches | 12, header correct | 36 (1) | 9,548 | `wall`, `boundary wall` | 227 | 1 | 0 |
| `PLAN.dxf` | 4 = mm | 304.8, header correct | 17 (1) | 5,862 | `WALL` | **1** | 0 | 0 |
| `VINAYAK APARTMENTS.dxf` | 1 = inches | 12, header correct | 9 (3) | 2,757 | `walls`, `NEW WALLS` | 740 | 18 | 0 |

Nothing crashed. Every drawing loaded, inventoried and authored an IFC.

---

## 2. Findings

### 2.1 `$INSUNITS` cannot be trusted

`Floor Plan.dxf` declares `$INSUNITS=2` (feet). Its columns measure
**12.04 x 24.07** — standard 12"x24" and 15"x24" column sizes, i.e. **inches**.
Trusting the header would scale the building by 12x.

The header is right on the other three. So it is usable as a default and wrong
often enough that `--units-per-foot` must exist. A test pins the discrepancy
(`test_declared_units_are_not_trustworthy`) so nobody later "simplifies" the
override away.

### 2.2 The layer named for a thing often is not that thing

On `Floor Plan.dxf`:

| wall layers used | walls | spaces |
|---|---|---|
| `WALLS` — the layer actually named for walls | 57 | **0** |
| `0` + `WALLS` — where the geometry really is | **1000** | **27** |

Layer `0` holds **54.9%** of the drawing's entities and carries the walls. Its
name says nothing, so no name-based method can find it. This is the measured
case the escalation rule exists for, and
`test_layer_zero_escalates_on_its_share_not_its_name` asserts the
`large_non_wall_layer` trigger fires on it.

**`PLAN.dxf` is the same trap, unresolved.** Its `WALL` layer holds 230 of
5,862 entities and yields **1 wall**; a layer named `CAY` holds **86%**. The
run succeeds and produces an almost-empty model. Nothing in the current
default path flags this as suspicious — it would take the vision stage, or a
sanity check that the walls found enclose rooms. Recorded as a known gap.

### 2.3 DXF removes the failure that blocks the PDF path

`Floor Plan.dxf` shares **100%** of its layer names with
`GROUND FLOOR PLAN_WORKING REVISED.pdf`. That PDF **fails outright** — exit 1
at the R1 scale gate, because its text is vector outlines with no readable
strings.

The DXF path skips scale resolution entirely (units are declared, §2.1), so
that failure cannot occur. `test_no_scale_gate_failure_on_the_dxf_path`
asserts no `scale_gate_failed` issue and a zero residual. **A drawing the
tool could not open at all now produces 1000 walls and 27 rooms.**

### 2.4 Performance is not a concern

`load_dxf` + wall detection + junction resolution + space detection on the
plumbing drawing: **0.6s total** (0.4s of it reading the file).

### 2.5 High unresolved-junction counts

`Floor Plan.dxf` reports 976 `unresolved_junction` errors, VINAYAK 684. These
do not prevent a valid IFC and are the R2 requirement's existing territory,
not a DXF regression — the PDF path reports the same class of issue. Worth
attention when R2 is next worked on.

---

## 3. What the tests cover

`tests/test_dxf_end_to_end.py`, 10 tests, all skipping without
`ARCHIAGENT_FIXTURES`:

- layer `0` holds >50% of the drawing, and escalates on share rather than name
- the layer named `WALLS` yields zero rooms while `0`+`WALLS` yields 20+
- `$INSUNITS` claims feet while the columns measure inches
- an explicit `--units-per-foot` overrides the header
- no `scale_gate_failed` issue on the DXF path, residual exactly 0.0
- the authored IFC opens as IFC4 with 500+ `IfcWall`
- the plumbing and VINAYAK drawings each produce walls above a floor
- every DXF present loads and inventories without crashing

Counts are asserted with headroom, not pinned, so ordinary refactoring does
not fail the suite while a collapse still does.

---

## 4. Constraints honoured

- No `.pdf`, `.dwg`, `.dxf`, `.ifc` or rendered image is committed by this
  work. Authored IFC goes to `tmp_path`; thumbnails to `.archiagent-cache/`.
  Both are git-ignored.
- No test makes a live API call.
- `reference/demo_demolition_walls.ifc` is a **pre-existing** committed IFC
  (67 KB, 49 `IFCWALL`s, titled "ArchiAgent - Demolition Plan Ground Floor")
  that violates the rule above. It predates this branch, is **not** on
  `origin/main`, and was deliberately left untouched — its removal is the
  user's decision. **It becomes public on the first push.**

---

## 5. Outstanding: live verification

None of the following was run — no credentials existed. Each needs a human
with an API key.

> **Vision is ON by default.** Every command below **transmits rendered
> images of the drawing** to the configured LLM provider. Add `--no_vision`
> to keep a run text-only.

- [ ] **Stage 1 alone, no images** — does the model classify DXF layers from
      the feature table?

  ```bash
  python -m archiagent --dxfFilePath "input-floorplans/dxf/Floor Plan.dxf" \
      --classify-only --units-per-foot 12 --no_vision -v
  ```

  Closes: the model returns a role and confidence for every layer, and
  omissions are reported.

- [ ] **The decisive one — does the vision stage rescue layer `0`?**

  ```bash
  python -m archiagent --dxfFilePath "input-floorplans/dxf/Floor Plan.dxf" \
      --classify-only --units-per-foot 12 -v
  ```

  Stage 1 is expected to call layer `0` something like `ignore`. Escalation
  must fire on its 54.9% share, and the thumbnail — a full floor plan with a
  readable title block — should move it to a wall role. **Success is layer
  `0` ending as a wall role with a `layer_role_revised` issue reported.**
  This is the single most important open verification: the whole two-stage
  design rests on it.

- [ ] **Full run, no hand-written layer list**

  ```bash
  python -m archiagent --dxfFilePath "input-floorplans/dxf/Floor Plan.dxf" \
      --outputDir /tmp/dxfout --units-per-foot 12 -v
  ```

  Success is roughly 1000 walls and 27 spaces without `--walls`.

- [ ] **`PLAN.dxf` — does vision catch the `CAY` trap?** (§2.2)

  ```bash
  python -m archiagent --dxfFilePath "input-floorplans/dxf/PLAN.dxf" \
      --classify-only -v
  ```

  `--walls WALL` gives 1 wall. If vision promotes `CAY`, the escalation rule
  generalises beyond the one drawing it was designed against. If it does not,
  that is worth knowing before relying on it.

- [ ] **`--no_vision` degradation** — with vision off, confirm every layer
      that *would* have escalated is reported as `layer_escalation_skipped`.
      This is the promise that makes the flag honest.
