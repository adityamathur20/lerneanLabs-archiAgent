# Vertical treatment: pools, double-height voids and what text says about level

Date: 2026-10-07
Status: design, pending implementation plan
Part of: Sub-project B (room-label text as a classification gate)
Related: `2026-10-06-scale-resolution-design.md`, `2026-09-18-geometry-first-wall-candidacy-design.md`

## Problem

On `MR RAJEEV JI TWANI JI.dxf` the swimming pool is built as a **room-shaped box
at floor level**, enclosed by standard walls. A pool is not a room: its water
surface sits several feet *below* the finished floor, and it has no walls around
it in the sense that a bedroom does.

The same error class covers labels meaning *no slab here*: `DOUBLE HEIGHT`,
`DOUBLE HEIGHT FOYER`, `OPEN AREA`, `OPEN TO BELOW`. A double-height void is an
absence of floor, not a room with a floor.

Geometrically a pool, a void and a bedroom are the same thing: a closed loop of
line work. **Nothing but the text distinguishes them.** This is the clearest
case in the whole project for why text has to drive classification.

## Why this cannot be solved geometrically

- A pool outline is a closed loop, often rectangular, at room scale. So is a room.
- A double-height void is usually bounded by the *same* walls that enclose the
  storey below, so its boundary is real wall geometry — rejecting that geometry
  would delete walls that genuinely exist.
- Pools are frequently drawn with hatch (water) and nosing lines (steps), which
  look like floor finish and stair treads respectively.

Every available geometric signal says "room". Only the label says otherwise.

## Scope

In: deciding a **vertical treatment** per space, from its label, and honouring
it when authoring floors and walls.

Out: modelling pool depth profiles, ramps, step geometry, water, or railings. A
pool becomes a correctly placed depression with a flat bottom at a stated depth.
Out: inferring level changes without a label — an unlabelled sunken area stays
`NORMAL`.

## 1. The vocabulary gains an attribute

Sub-project B's room-label vocabulary maps text to a room kind. This spec adds a
second, independent attribute to each entry: **vertical treatment**.

| Treatment | Meaning | Floor slab | Enclosing walls |
|---|---|---|---|
| `NORMAL` | ordinary occupied room | at storey datum | full height |
| `SUNKEN` | floor is below the storey datum by a stated depth | at datum minus depth | retained, but as retaining/parapet geometry, not room walls |
| `VOID` | no floor at this storey | **none** | retained — they are the walls of the storey below |
| `OPEN` | outside the building envelope at this storey | none authored by this storey | none inferred |

Initial assignments, with corpus hit counts from the survey:

| Label | Treatment | Default depth | Hits / files |
|---|---|---|---|
| `POOL`, `SWIMMING POOL` | `SUNKEN` | 4.0 ft | 2 / 2 |
| `WATER TANK`, `OHT`, `UGT` | `SUNKEN` | 5.0 ft | 5 / 4 |
| `DOUBLE HEIGHT`, `DOUBLE HT` | `VOID` | — | 5 / 2 |
| `DOUBLE HEIGHT FOYER` | `VOID` | — | within the above |
| `OPEN TO BELOW`, `OPEN AREA` | `VOID` | — | 4 / 2 |
| `TERRACE`, `OPEN TERRACE` | `OPEN` | — | 18 / 5 |
| `GARDEN AREA`, `LAWN` | `OPEN` | — | 1 / 1 |
| `PARKING AREA`, `STILT` | `NORMAL` | — | 3 / 2 |
| everything else | `NORMAL` | — | — |

`SUNKEN` depths are **defaults, not measurements**. Each one authored from a
default emits an `Assumption`, the mechanism `_assemble` already uses for
`wall_height_ft`, so a reviewer sees that 4.0 ft was assumed rather than read.

## 2. Where the decision is made

After `detect_spaces` has produced room polygons and Sub-project B has
associated labels with them, each `Space` carries a treatment. The decision
therefore belongs with the label association, not with wall detection — wall
candidacy runs before spaces exist and must not be made to depend on them.

`Space` gains two fields, defaulted so existing callers are unaffected:

```python
treatment: str = "NORMAL"          # NORMAL | SUNKEN | VOID | OPEN
level_offset_ft: float = 0.0       # negative for SUNKEN; 0 otherwise
```

## 3. What changes when authoring

In `ifc/author.py`:

- **`VOID` and `OPEN`**: author no floor slab for that space. The walls bounding
  it are authored unchanged — they are real walls, and for a double-height space
  they are the walls of the storey below, which is why they must not be deleted.
- **`SUNKEN`**: author the slab at `elevation + level_offset_ft` rather than at
  the storey datum. The bounding walls become retaining walls: authored from the
  sunken level up to the storey datum, not to full `wall_height_ft`.
- **`NORMAL`**: unchanged in every respect.

The IFC result for a pool is a slab 4 ft below the floor with a 4 ft retaining
edge, instead of a 10 ft-walled room. The result for a double-height foyer is a
hole in the floor plate with its surrounding walls intact.

## 4. The hard-constraint interaction

Sub-project B's decision is that room-label text is a **hard constraint**. That
rule applies here with one carve-out that must be stated, because getting it
wrong reintroduces a worse bug than the one being fixed.

A `VOID` label must **not** cause the walls bounding it to be rejected as walls.
The hard constraint governs **what is authored inside a space**, not whether its
boundary is a wall. Rejecting the boundary of a double-height space would delete
real structure that holds up the floor above — a far more damaging error than
the spurious slab it was trying to prevent.

So: text vetoes the *slab*, never the *wall*.

## 5. Error handling

| Condition | Result |
|---|---|
| `SUNKEN` label with no depth in the vocabulary | default depth applied, `Assumption` recorded |
| `SUNKEN` depth greater than the storey height | configuration error naming both |
| a space carrying two labels with different treatments | `warn` Issue, most conservative treatment wins (`NORMAL` over `SUNKEN` over `VOID`), reviewer decides |
| `VOID` space whose boundary is not closed | treated as `NORMAL`, `info` Issue — an unbounded void cannot be subtracted reliably |
| label found but no space encloses it | existing unassociated-label handling, unchanged |

Conservative-wins on conflict is deliberate: authoring a floor that should not
exist is visible and easily corrected; omitting one that should is a hole
someone may not notice.

## 6. Testing

- `POOL` label inside a closed space: slab at −4.0 ft, bounding walls 4.0 ft
  tall, `Assumption` recorded for the default depth
- `DOUBLE HEIGHT` label: no slab authored, **all** bounding walls still authored
  at full height — the regression pin for §4
- `OPEN AREA`: no slab, no inferred walls
- an unlabelled closed space is `NORMAL` with a slab at datum
- two conflicting labels in one space: `NORMAL` wins, `warn` issued
- `SUNKEN` depth exceeding storey height exits with both values named
- `MR RAJEEV JI TWANI JI.dxf`: the pool space authors no floor-level room box,
  and the wall count elsewhere in the model is unchanged from the current output
- case and spacing variants (`Double Height`, `DOUBLE  HEIGHT`, `double ht`)
  all resolve, matching the normalisation Sub-project B applies to labels

## 7. Risks

- **Default depths are invented numbers.** 4 ft for a pool is plausible and
  wrong in any specific building. They are defaults so that something sensible
  is authored rather than a floor-level box, and every use is recorded as an
  `Assumption`. A later spec can read depth from a section or a label like
  `POOL (DEPTH 3'-6")`.
- **Label-to-space association is the weak link, not this spec.** If B puts the
  `POOL` text in the wrong polygon, this spec confidently sinks the wrong room.
  That argues for showing treatments in the review output, where a sunken living
  room is immediately obvious.
- ~~**Terrace as `OPEN` may be too strong.**~~ **Resolved 2026-10-07:** terrace
  stays `OPEN`. The staged workflow settles it — roof and terrace insertion is a
  later, separate step, so a terrace's slab arrives from the roof stage rather
  than from the room stage. `OPEN` is therefore correct *at this stage*, not an
  omission. If roof insertion is ever folded back into one pass, revisit this.
