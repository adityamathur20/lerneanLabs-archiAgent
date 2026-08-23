# W1 Live Verification Report

**Date:** 2026-08-23
**Branch:** `feature/w1-llm-layer-classification`
**Environment:** `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` were both unset in
this environment, and no credentials were sought, invented, or substituted.

> ## ⚠️ Live-provider verification was NOT performed
>
> No API credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) were available in
> this environment. Every command in this report that does **not** need an
> LLM call was actually executed, and its real output is recorded below.
> Every command that **does** need a live model call — the two end-to-end
> classifications with the default provider, the ground-floor plan run
> through the real classifier, the cache hit/miss check, and the
> OpenAI-compatible provider check — was **not run**. Nothing in this
> document was estimated, simulated, or reconstructed to look like a live
> result. The success criteria in
> `docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md`
> §9 that depend on a live model call remain **unverified**. See
> "Outstanding: live verification" at the end of this report for the exact
> commands a human holding a key must run to close them.

---

## 1. `--inspect` on all three drawings (no credentials)

Command shape:

```bash
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
  "../input-floorplans/<drawing>.pdf" --inspect
```

### Demolition plan

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" --inspect
```

Exit code: **0**. 14 layers reported:

```
layer                      paths    segs  axis%      p50      p90
                             410     410     4%      1.7      7.4
0                              8       8   100%     11.8     11.8
EL                            12      12    33%      2.2      2.2
FUR                            74      74    73%      0.8     15.8
FURNITURE                   5433    5433    90%      1.1      1.2
HATCH-CONS                   289     289    67%     21.2    112.8
HATCH1                      6534    6534   100%      0.0      0.0
ID-LOOSE                      23      23   100%      8.4     18.4
ID-LOOSE2                     59      59   100%      2.4     11.9
elevationl line             5169    5169    22%      0.9     14.3
fixtures                       6       6   100%     22.9     24.1
walll                        187     187   100%     23.2    110.4
win                           873     873    44%      0.9      2.6
window                         31      31   100%     35.5     62.3
```

### Electrical plan

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/ELECTRICAL FINAL l PLAN.pdf" --inspect
```

Exit code: **0**. 49 layers reported (abbreviated; wall candidates called out):

```
layer                      paths    segs  axis%      p50      p90
                             259     259    30%      1.5      6.2
0                          60247   60247    63%      0.4      3.7
A-Anno-Scrn                    9       9   100%     37.1    391.9
A-Glaz-Curt                    4       4   100%     59.4     59.4
A-Wall                         2       2   100%      3.0      3.0
Annotations                  201     201   100%      2.5      7.1
BELL                          96      96    48%      1.1      2.3
BULB                         332     332    60%      0.5      1.2
BZ                            16      16   100%      3.5      5.6
CEILING ROSE                   8       8    75%      4.5      8.0
CONDUIT                        9       9   100%      1.4      1.8
DB                            34      34    65%     10.3     13.4
DB TO SHAFT CONDUIT            6       6   100%      2.3      2.8
DIM                            24      24    33%    594.6    653.6
EXF                            14      14    57%      2.9      8.5
FAN 30                         43      43     5%      0.5      2.7
FAN 48                         43      43     9%      0.5      2.9
FAN BOX                         3       3    67%      6.6      6.6
FORMAT                         46      46   100%    133.4    133.4
FURN                          765     765    10%      1.3      3.0
GL                            329     329    65%      0.4      0.8
Hatch-3                      1003    1003    12%      0.5      2.0
LCW                          2185    2185    11%      1.8      2.3
LIGHT CIRCUIT                   6       6   100%      2.3      2.8
LP                            323     323     6%      2.0      2.0
Mirror light                  104     104    69%     10.6     11.4
PICTUE LIGHT                   30      30    47%      3.4     13.3
PP                             38      38     5%      2.8      2.9
PP F                           19      19     5%      2.8      2.9
PP G                           20      20    25%      2.9      6.0
PP MIX.                        20      20    25%      2.9      6.0
PP SAC                        190     190    26%      1.7      3.7
PP WM                          19      19     5%      2.8      2.9
PP WP                          19      19     5%      2.7      2.8
Power conduit                   6       6   100%      2.3      2.8
SB                            203     203    26%      1.5      6.0
SJ Door                       629     629    60%      1.0      1.8
SPLIT AC                     1320    1320    28%      1.3      2.1
TEL                            33      33    30%      2.4      4.9
TEXT                            1       1   100%    133.4    133.4
TITLE BOX TEXT                  5       5   100%    133.4    133.4
TL W                          263     263    61%      0.2      1.0
TV                            206     206    26%      1.2      3.0
WALL                          896     896    96%      4.6     51.0
WALLS                        5138    5138    57%      1.6     11.2
cfl                          9091    9091    60%      0.6      1.3
dwgmodels.com                  44      44   100%     26.5     89.9
ele                            14      14   100%    133.4    133.4
usb charger                     8       8   100%      0.5      0.5
```

### Ground floor plan

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/GROUND FLOOR PLAN_WORKING REVISED.pdf" --inspect
```

Exit code: **0**. 32 layers reported:

```
layer                      paths    segs  axis%      p50      p90
0                            930     930    12%      1.9      6.4
ARROW                        145     145    23%      1.0     16.3
BEAM                        1427    1427    98%      5.2      5.2
C-LINE                        856     856    47%      0.5      2.0
COLUM HATCH                   144     144    67%     10.2     18.3
ELE                             4       4   100%     83.7     83.7
ELECT DIM                      73      73     7%      1.5      2.2
ELECTRICAL TEXT              1357    1357    64%      0.0      0.1
ELEV                           71      71    51%      1.2    122.7
FUR                           290     290    28%      0.9      5.5
FURN                         1805    1805    42%      1.2     13.7
GRID CIRCLE                  1024    1024     0%      1.9      2.0
GRID LINES                   1016    1016   100%     12.7     12.8
GRID TEXT                    2894    2894    22%      0.2      1.2
HT                           3408    3408    33%      0.2      0.4
LVL TEXT                    13809   13809    46%      0.1      0.1
NORTH                         117     117    34%      0.9      4.6
OVERALL DIM                 15391   15391    28%      0.2      0.2
PROPERTY LINE                 615     615     2%      5.8      5.9
RAILING                          1       1   100%      9.4      9.4
RCC WALL                       12      12   100%     45.0     70.7
SILL TEXT                   10903   10903    68%      0.0      0.4
STAIR                          72      72   100%     34.7    108.5
Sheet                       52798   52798    41%      0.1      0.3
TEX                         21615   21615    45%      0.1      0.2
TEXT                        59583   59583    39%      0.1      0.6
WALL HATCH                   1410    1410     0%      4.3      8.6
WALLS                         107     107    96%     12.4    109.4
WOODEN HT                    1001    1001    18%      0.6      1.6
WORK DIM                     3893    3893    38%      0.5      3.5
door                          424     424    60%      2.0      3.2
window                        163     163   100%      1.0      3.0
```

All three `--inspect` runs succeeded with exit code 0 and no credentials
present, closing that half of success criterion 4 (`--inspect` works with
no credentials).

---

## 2. Full pipeline via `--walls` (no LLM)

### Demolition plan — `--walls walll`

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
    /tmp/archiagent-verify/demolition.ifc --walls walll -v
```

Exit code: **0**. Tail of output:

```
error (42):
  [unresolved_junction] node@16.52,18.24: wall endpoint connects to nothing; rooms downstream may leak
  ... (42 unresolved_junction errors total, one per unmatched wall endpoint)

warn (3):
  [dimension_outside_gate] scale: printed dimension off by 9.5in, beyond the 2.0in gate; it may measure non-wall geometry, or the scale may be wrong
  [dimension_outside_gate] scale: printed dimension off by 9.5in, beyond the 2.0in gate; it may measure non-wall geometry, or the scale may be wrong
  [dimension_outside_gate] scale: printed dimension off by 22.5in, beyond the 2.0in gate; it may measure non-wall geometry, or the scale may be wrong
wrote /tmp/archiagent-verify/demolition.ifc
  scale   11.6667 units/ft, max residual 1.568in
  walls   51
  spaces  1
  issues  42 error, 3 warn
```

Scale (11.6667 u/ft), wall count (51) and space count (1) match the
reference hand-map figures exactly. The 42 `unresolved_junction` errors and
3 `dimension_outside_gate` warnings are real observed output, not a figure
from the hand map, and are reported as-is.

### Electrical plan — layer choice

Two layers in the electrical inventory are plausible wall candidates:

| Layer | paths | axis% | p50 seg | p90 seg |
|---|---|---|---|---|
| `A-Wall` | 2 | 100% | 3.0 | 3.0 |
| `WALL` | 896 | 96% | 4.6 | 51.0 |
| `WALLS` | 5138 | 57% | 1.6 | 11.2 |

`A-Wall` has only 2 paths — clearly not the wall network. `WALL` and
`WALLS` were both run through the full pipeline to decide between them:

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/ELECTRICAL FINAL l PLAN.pdf" \
    /tmp/archiagent-verify/electrical_WALL.ifc --walls WALL -v
```
→ exit 0, `scale 7.2806 units/ft, max residual 1.790in, walls 43, spaces 8, issues 86 error, 7 warn`

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/ELECTRICAL FINAL l PLAN.pdf" \
    /tmp/archiagent-verify/electrical_WALLS.ifc --walls WALLS -v
```
→ exit 0, `scale 6.8267 units/ft, max residual 0.984in, walls 185, spaces 2, issues 69 error, 2 warn`

**Chosen layer: `WALLS`.** With `WALLS` as the wall layer, the scale
resolver converges to 6.8267 units/ft — an exact match to the reference
hand-map scale — with a max residual of 0.984 in, comfortably inside the
2.0 in R1 gate. With `WALL` alone, the resolver converges to a different
scale (7.2806 u/ft) that does not match the reference and is not otherwise
corroborated. The scale match under `WALLS` is strong independent evidence
that `WALLS` is the layer that actually carries the wall network in this
drawing; `WALL` is likely a secondary/partial layer (e.g. curtain-wall or
glazing framing — note the neighbouring `A-Glaz-Curt` layer). The reported
run below uses `--walls WALLS`.

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/ELECTRICAL FINAL l PLAN.pdf" \
    /tmp/archiagent-verify/electrical_WALLS.ifc --walls WALLS -v
```

Exit code: **0**.

```
error (69):
  [unresolved_junction] node@...: wall endpoint connects to nothing; rooms downstream may leak
  ... (69 unresolved_junction errors total)

warn (2):
  [dimension_outside_gate] scale: printed dimension off by 41.5in, beyond the 2.0in gate; it may measure non-wall geometry, or the scale may be wrong
  [dimension_outside_gate] scale: printed dimension off by 42.5in, beyond the 2.0in gate; it may measure non-wall geometry, or the scale may be wrong
wrote /tmp/archiagent-verify/electrical_WALLS.ifc
  scale   6.8267 units/ft, max residual 0.984in
  walls   185
  spaces  2
  issues  69 error, 2 warn
```

Wall count (185) and space count (2) do **not** match the reference figures
(258 walls, 14 spaces). This is expected and not a regression: the hand map
was built by a person choosing which layer(s) counted as walls for that
drawing, and may have combined multiple layers (e.g. `WALL` + `WALLS`)
into the wall role, or interpreted the wall network differently. This run
used a single layer via `--walls`, with no LLM in the loop, purely to prove
the pipeline runs end to end without credentials. It is not a substitute for
criterion 2, which requires the actual classifier to decide the wall
role(s) — quite possibly across more than one layer — for this drawing.

### Ground floor plan — `--walls WALLS`

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/GROUND FLOOR PLAN_WORKING REVISED.pdf" \
    /tmp/archiagent-verify/ground.ifc --walls WALLS -v
```

Exit code: **1**. Full output:

```
error: scale gate (R1) failed: no dimensions or no candidate runs to match
hint: this drawing may have no live text -- dimensions converted to vector outlines cannot be read yet.
```

This matches the expected behavior in the brief: the failure names the
scale gate explicitly (`scale gate (R1) failed`), not the classifier, and
the hint correctly attributes the cause to outlined (non-live) text. Because
this failure happens downstream of and regardless of which classifier
(LLM or `--walls`) produced the wall role, this result also stands as
evidence for success criterion 3 (ground floor still fails at the scale
gate, blaming the gate and not the classifier) — though the brief's
canonical Step 5 run (through the live classifier, no `--walls`) has not
been executed and remains listed under "Outstanding" for completeness.

---

## 3. `--classify-only --walls walll` (demolition plan)

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
    --classify-only --walls walll
```

Exit code: **0**. Full output:

```
layer                    role                conf  source   reason
                         ignore              0.00  manual
0                        ignore              0.00  manual
EL                       ignore              0.00  manual
FUR                      ignore              0.00  manual
FURNITURE                ignore              0.00  manual
HATCH-CONS               ignore              0.00  manual
HATCH1                   ignore              0.00  manual
ID-LOOSE                 ignore              0.00  manual
ID-LOOSE2                ignore              0.00  manual
elevationl line          ignore              0.00  manual
fixtures                 ignore              0.00  manual
walll                    wall_structural     1.00  manual
win                      ignore              0.00  manual
window                   ignore              0.00  manual
```

This confirms the role/confidence/source/reason table renders with all four
columns present. Because `--walls` bypasses the LLM, `source` is `manual`
for every layer and `reason` is empty — the table's structure is proven,
but the actual LLM-produced `reason` text (part of success criterion 6) is
not exercised by this run.

---

## 4. Credentials-missing error path

```
$ env -u ANTHROPIC_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" /tmp/x.ifc
```

Exit code: **2**. Full output:

```
error: ANTHROPIC_API_KEY is not set. Export it, or pass --walls LAYER... to name the wall layers yourself and skip the LLM entirely.
```

Confirmed: exit code 2, message names `ANTHROPIC_API_KEY` and `--walls`.

---

## 5. Unknown-provider path

```
$ env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" /tmp/x.ifc --provider bedrock
```

Exit code: **2**. Full output:

```
error: unknown provider 'bedrock'. Supported: anthropic, openai. Groq, Kimi and DeepSeek are reached with provider 'openai' plus ARCHIAGENT_LLM_BASE_URL.
```

Confirmed: exit code 2, message lists the supported providers (`anthropic`,
`openai`) and names how OpenAI-compatible vendors are reached.

---

## 6. Full test suite

```
$ ARCHIAGENT_FIXTURES=../input-floorplans .venv/bin/pytest -q
```

Exit code: **0**.

```
........................................................................ [ 35%]
........................................................................ [ 70%]
...........................................................              [100%]
203 passed, 5 warnings in 5.07s
```

203 tests passed, 0 failures. (The task instructions anticipated 203; the
brief's own summary table anticipated 194 — the actual, run count is 203
and is reported as observed.) The warnings are pre-existing SWIG
`DeprecationWarning`s from `ifcopenshell`'s native bindings, unrelated to
this task.

---

## Results table

| Drawing | Scale (u/ft) | Max residual | Walls | Spaces | Exit |
|---|---|---|---|---|---|
| Demolition (`--walls walll`) | 11.6667 | 1.568 in | 51 | 1 | 0 |
| Electrical (`--walls WALLS`) | 6.8267 | 0.984 in | 185 | 2 | 0 |
| Ground floor (`--walls WALLS`) | — | — | — | — | 1 (scale gate) |

All three rows above were produced by the `--walls` bypass (no LLM). The
brief's canonical end-to-end run — through the real classifier, no
`--walls` — is **not verified (no API key)** for all three drawings. In
particular: the classifier's own choice of wall layer(s), the resulting
wall/space counts it would produce, and every `role` / `confidence` /
`reason` the model would emit are **not verified (no API key)**.

---

## Outstanding: live verification

The following commands were **not run** in this environment because no API
credentials were available. A human holding an `ANTHROPIC_API_KEY` (and
optionally a second provider's key) must run them to close Task 8. Each is
listed with the exact command and the success criterion(s) from
`docs/superpowers/specs/2026-08-23-w1-llm-layer-classification-design.md`
§9 it closes.

- [ ] **Demolition plan, end to end, default provider**

  ```bash
  .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
    /tmp/demolition.ifc -v
  ```

  Closes criterion 1 (runs end to end with no hand-written role map, passes
  the R1 scale gate), criterion 5 (every layer gets a role and confidence,
  omissions reported), and the live-`reason`-text half of criterion 6.

- [ ] **Electrical plan, end to end, default provider**

  ```bash
  .venv/bin/python -m archiagent \
    "../input-floorplans/ELECTRICAL FINAL l PLAN.pdf" \
    /tmp/electrical.ifc -v
  ```

  Closes criterion 2 (electrical plan runs end to end and passes the gate;
  reference figures scale 6.8267 pt/ft, 258 walls, 14 spaces are for
  comparison only) and contributes to criteria 5 and 6.

- [ ] **Ground floor plan, end to end, default provider**

  ```bash
  .venv/bin/python -m archiagent \
    "../input-floorplans/GROUND FLOOR PLAN_WORKING REVISED.pdf" \
    /tmp/ground.ifc -v
  echo "exit: $?"
  ```

  Closes criterion 3 through the canonical path (real classifier, not the
  `--walls` bypass used in this report) — confirms the failure still names
  the scale gate, not the classifier, when the LLM (not a human) chose the
  wall role.

- [ ] **Cache hit/miss check**

  ```bash
  # First run populates the cache (same command as the demolition run above)
  .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
    /tmp/demolition.ifc -v | tee /tmp/demolition-run1.out
  # Second run: confirm no API call is made (compare wall-clock time / watch
  # for provider-request logging if any) and the printed summary lines are
  # byte-identical to the first run
  .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
    /tmp/demolition2.ifc -v | tee /tmp/demolition-run2.out
  diff /tmp/demolition-run1.out /tmp/demolition-run2.out
  # Third run: force a fresh call and confirm it actually calls out
  .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" \
    /tmp/demolition3.ifc -v --no-cache
  ```

  Not one of the six numbered success criteria, but validates the caching
  behavior specified in the design doc §5 ("Caching") and covered
  unit-test-only by `test_layer_cache.py` (Task 6) — this is the first time
  it would be exercised against a real API.

- [ ] **OpenAI-compatible provider check**

  ```bash
  ARCHIAGENT_LLM_PROVIDER=openai \
  ARCHIAGENT_LLM_BASE_URL=https://api.groq.com/openai/v1 \
  ARCHIAGENT_LLM_MODEL=moonshotai/kimi-k2-instruct \
  OPENAI_API_KEY=... \
    .venv/bin/python -m archiagent \
    "../input-floorplans/DEMOLITION PLAN FRUNITURE LAYOUT 2.pdf" --classify-only
  ```

  Not one of the six numbered success criteria, but is the only live check
  of the OpenAI-compatible adapter (Task 5) end to end, and specifically
  whether the third-party endpoint honors `strict` schema enforcement. If it
  does not, our own reply validation should catch it — that would be the
  design working as intended, not a bug, and should be recorded as such
  rather than treated as a failure.

If a second provider key is not available, that last item should be
recorded as "not verified — no key available" rather than skipped silently.

### The `WALL` vs `WALLS` question

The electrical drawing carries **two** candidate wall layers, and measured
with the `--walls` bypass they give materially different answers:

| layer | axis-aligned | p50 | p90 | result via `--walls` |
|---|---|---|---|---|
| `WALL` | 96% | 4.6 | 51.0 | exit 0, scale 7.2806, residual **1.790in**, 43 walls |
| `WALLS` | 57% | 1.6 | 11.2 | exit 0, scale 6.8267, residual 0.984in, 185 walls |

`SYSTEM_PROMPT`'s stated heuristic — *"Walls: near 100% axis-aligned, p50
long relative to other layers"* — describes `WALL`, and `WALL` alone yields
a scale roughly 6.6% off the figure `WALLS` produces while still passing the
R1 gate at 1.790in. This is the same shape as the 40% hatch error: a
wrong-but-passing scale. The gate cannot catch it, so the prompt must not
invite picking one layer.

One sentence was added to the walls bullet stating that a drawing often
splits its wall network across several layers and that every layer carrying
wall geometry should be classified as a wall. Nothing else was re-tuned: the
effect of a prompt change cannot be measured without a live call, and blind
prompt tuning is the failure mode this project has already been burned by.

**The first live run must answer this**: on
`../input-floorplans/ELECTRICAL FINAL l PLAN.pdf`, record which layer(s) the
classifier assigns a wall role, and the resulting scale and residual. If it
picks `WALL` alone, the prompt's heuristic is actively steering toward the
wrong-but-passing answer and needs measured revision — not a guess.

Note that `PROMPT_VERSION` is now derived from `SYSTEM_PROMPT` plus the
response schema, so this edit changed it (`21e97012a6dc` → `604821a21a69`)
and every cache entry written under the old prompt is already unreachable.
No manual cache clearing is needed before the live run.
