# Phase 0 on the real DWG corpus

Date: 2026-10-08
Plan: `docs/superpowers/plans/2026-10-07-mlightcad-cad-viewer.md`, Phase 0
Corpus: the 8 DWGs in `input-floorplans/PLANS` (R2000–R2018, 0.3–2.8 MB),
compared against the ODA File Converter installed on the same Mac.
Follows: `2026-10-07-phase0-gates.md` (public samples only).

## Verdict

| Gate | Result |
|---|---|
| Task 1 — LibreDWG replaces ODA | **No-go as specified.** All 8 convert (0.4–1.2 s), but LibreDWG's DXF **writer** damages every one. |
| Task 2 — handle join | **Pass.** 23,233 / 23,233 selectable entities join on 4 real drawings, for both converters. |
| Task 3a — production CSP | **Pass** for ODA output: real plans render, 0 violations, 0 third-party requests. The gate itself had a false pass and is fixed. |

## Task 1: what LibreDWG gets wrong, and where

LibreDWG's **reader** is close to faithful; its **DXF writer** is not. Proved by
reading the same DWGs through `libredwg-web`'s parse path
(`dwg_read_data` + `convert`), which gets each item below right.

| Defect (writer) | Drawings | Effect | Correctable? |
|---|---|---|---|
| Nearly every layer written **off** | 8 / 8 | A viewer draws nothing (archiAgent ignores visibility, so the pipeline did not notice) | Yes, from the parse path's layer state |
| Model extents left unset (`1e+20`) | 4 / 8 | Viewer frames to nothing or zooms far out | Yes, recompute from geometry |
| Door-block entities with a **blank layer** | 3 / 8 (1,258 entities) | Swings and leaves stranded on a nameless layer; same defect as LibreDWG 0.14 natively (2026-09-29) | Yes, blank → `0` matches ODA exactly |
| Annotation on invented `X @ N` layers | 3 / 8 (164 entities) | MTEXT/DIMENSION/LEADER on the wrong layer | Yes, strip the suffix; matches ODA exactly |
| **Block definitions written empty** | 1 / 8 (Giriraj: w.c., FIXT, FUR; 11 inserts) | Real loss of fixture symbols | Only by refilling from the parse path, whose geometry for these blocks is **identical** to ODA's |
| Fit-point SPLINEs encoded differently | 3 / 8 | Different flattening → ±1–6 symbols, walls or junctions | Not investigated |
| One R2004 public sample: writer throws | (public sample) | No DXF | Parse path reads it |

Correct: modelspace handles and geometry (identical per handle to 1e-10), arcs,
bulges, dimension counts, text counts.

After the first two text-level corrections (blank → `0`, strip `@ N`), the
model archiAgent builds matches ODA's (order-independent comparison of
walls, openings, spaces, junctions, symbols):

| Drawing | Match |
|---|---|
| Baglow 90x50 | identical |
| South Face | identical except 2 / 1,626 symbols |
| Aiims Road | identical except 4 / 1,996 wall verdicts |
| SANJANA SURESH | identical except 1 / 180 symbols |
| abhishek ji | identical except 1 / 162 symbols |
| MB Panwar | openings 14 vs 15, 4 / 94 symbols |
| MR RAJEEV | 6 / 220 walls, ~7 / 134 junctions |
| Giriraj | 34 / 196 walls differ (the emptied blocks) |

A control run (ODA's own DXF re-saved through ezdxf) matched ODA exactly, so
none of the above comes from the comparison tooling.

Scale was asserted identically on both sides (`--scale-from-wall 0 0 12 0 1ft`,
these drawings are in inches); most runs then stopped at "wall N partially
overlaps accepted wall profiles" on **both** sides. That refusal is
archiAgent's, and this worktree predates the wall-join fixes on `origin/main`.

### The mlightcad-writer route was tried and dropped

Parse path → `@mlightcad/libredwg-converter` → `@mlightcad/data-model`
`dxfOut` fixes the emptied blocks and blank layers, but the mlightcad writer has
its own defects: XDATA points stringified as `[object Object]` (1014),
IMAGEDEF pixel size written 11/12 instead of 11/21, duplicate handles,
MLINESTYLE objects colliding with layout handles, and every drawing's own APPIDs
dropped. Too many to be the pipeline's source of truth.

## Gate fixes made while testing

- `dwg_fidelity.py` now fails on writer defects (blank layers, `@ N` layers,
  layers mostly off, unset extents), on emptied blocks, and on any ingest-level
  difference. It printed "GATE PASSES" on this corpus before, because it only
  failed on pipeline differences.
- `csp-gate.mjs` passed on a **blank** render: its pixel count included the
  toolbar and command line. It now counts only the drawing area, waits for the
  renderer to go idle, and zooms to fit. On large drawings mlightcad's
  "Parsing entities…" overlay can still be up when idle reports true.

## Options for the DWG converter (decision needed)

1. **ODA on the VPS.** Most faithful. ODA ships a Linux build that runs headless
   under `xvfb-run`. Blocker is licence, not engineering: clearing SaaS use of
   the ODA File Converter.
2. **LibreDWG writer + corrections** (layer state, extents, blank layers,
   `@ N`, refill emptied blocks from the parse path). GPL-safe and free; about a
   day's work. Leaves the spline difference and the risk of writer defects not
   yet seen.
3. **Our own DXF writer from the LibreDWG parse path** (ezdxf in Python, fed by
   `libredwg-web`'s JSON). Cleanest long term, largest effort, every entity type
   becomes ours to maintain.
