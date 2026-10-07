# Spike: do DXF handles join the browser picker to the pipeline?

Date: 2026-10-07
Gates: `2026-10-06-scale-resolution-design.md` §5 (the picker)
Status: **answered — join on handle**

## Question

The picker lets the user select a wall in the browser and state its length. For
that assertion to mean anything to the pipeline, both sides must agree on which
entity was selected. `load_dxf` uses `e.dxf.get("handle")` as its source id for
top-level entities ([dxf_vector.py:231](../../../archiagent/ingest/dxf_vector.py#L231))
and the `dxf` npm parser exposes DXF group code 5. Do they actually match?

## Measurement

| Drawing | top-level | nested | top-level LINE/LWPOLYLINE | ids that are handles |
|---|---|---|---|---|
| MR RAJEEV JI TWANI JI | 3,641 | 13,826 | 3,096 | **3,641 (100%)** |
| Aiims Road 3BHK Flats | 4,763 | 54,698 | 2,680 | **4,763 (100%)** |
| Floor Plan.dxf | 51,786 | 16,100 | 43,433 | **51,786 (100%)** |

Sample ids from MR RAJEEV: `7D`, `81`, `82`, `83`, `84`, `85` — bare hex DXF
handles, not synthesised paths.

## Verdict

**Join on handle.** Every top-level entity's `SourceEntity.id` is its raw DXF
handle, on all three drawings, with no exceptions. The picker records the handle
of the selected entity; the pipeline looks it up directly. No coordinate
matching and no fallback is needed for the selectable case.

## What this does not cover

Geometry expanded out of an `INSERT` carries a synthesised id of the form
`7D/0/2:LWPOLYLINE`, and those are the majority by count (13.8k, 54.7k and 16.1k
nested against the top-level figures above). A nested entity therefore **cannot**
be joined by a bare handle.

This does not matter for scale calibration, because:

- every drawing has thousands of top-level lines to choose from (2,680 at worst),
- and the picker only needs the user to select **one** entity.

So the picker should **restrict selection to top-level entities** rather than
implement a nested fallback. Selecting block geometry is refused with a reason
telling the user to pick a plain line instead. If a later feature needs to
address nested geometry, the synthesised id format is deterministic and can be
reconstructed, but nothing requires that today.

## Consequence for the picker

The measurement the picker exports is `{handle, stated_length, unit}` rather
than a coordinate pair. That is strictly better provenance: it survives
re-ingestion of the same file, where coordinates copied out of one session would
have to be re-matched by proximity.
