# Declared wall thickness measured on the corpus

Date: 2026-10-09. Plan task T8 of `plans/2026-10-09-declared-wall-thickness.md`.

**What was run.** Each converted drawing, offline (`--rules`-equivalent: `RuleClassifier`, no model, no adjudicator),
scale 12 units per foot (every drawing here is inches; Aiims Road confirmed by its 424 dimensions, Baglow and RAJEEV
by the asserted walls used in the Drawing-view checks). Four runs per drawing:

1. **baseline**: today's behaviour, thickness set inferred from the drawing;
2. **sanity**: the set inference itself finds, declared at inference's own 1 in tolerance. Must change nothing;
3. **declared 4½″ + 9″**, tolerance 0.5 in, not exhaustive;
4. **declared 4½″ + 9″, exhaustive**: any candidate matching neither is rejected.

4½″ and 9″ is the usual Indian brick-wall pair, applied to *every* drawing here, whether or not that is what its drafter
used: which is what makes this a stress test of the veto rather than a measurement of well-declared drawings.

**Not measured.** There is no ground truth for which runs are real walls, so "phantom walls removed" cannot be counted.
What is counted is what changed, and the thickness of each run the veto removed, so a reader can judge.

## Results

| Drawing | Inference finds | Sanity: inferred set changes nothing | Accepted runs: baseline → declared → exhaustive | Accepted length (ft): baseline → exhaustive | Walls built: baseline → exhaustive |
|---|---|---|---|---|---|
| 34'-9''x60'-0'' South Face | 4.5″, 9″ | yes | 1530 → 1473 → 1154 | 10,751 → 8,268 (-23%) | 1872 → 1140 |
| Aiims Road 3BHK Flats-vk | 4.5″, 9″ | yes | 770 → 770 → 664 | 5,856 → 4,596 (-22%) | 900 → 731 |
| MB Panwar JI Revision 2 | 6″, 9″ | yes | 114 → 111 → 70 | 780 → 500 (-36%) | 127 → 83 |
| MR RAJEEV JI TWANI JI | 4.5″, 9″ | yes | 182 → 180 → 157 | 1,563 → 1,351 (-14%) | 220 → 183 |
| M.r Premg Agarwal  Baglow 90x50.dx | 4.5″, 9″ | yes | 244 → 244 → 150 | 1,493 → 1,103 (-26%) | 305 → 169 |
| SANJANA Giriraj Ji plan for struct | 4.5″, 9″ | yes | 144 → 133 → 110 | 1,543 → 1,314 (-15%) | 196 → 141 |
| SANJANA SURESH JI | 4.5″ | yes | 72 → 72 → 63 | 654 → 598 (-9%) | 93 → 81 |
| abhishek ji | 4.5″, 9″ | yes | 126 → 125 → 102 | 1,148 → 984 (-14%) | 155 → 108 |

## What the veto removed (accepted at baseline, rejected when exhaustive), by thickness in inches

- **34'-9''x60'-0'' South Face**: 376 runs, 2,483 ft: 2.0″×110, 2.5″×1, 3.0″×9, 3.5″×3, 4.0″×5, 6.5″×1, 10.0″×125, 12.0″×85, 15.0″×26, 18.0″×2, 19.5″×1, 21.0″×5, 24.0″×3
  - accepted thickness mix at baseline (share of length): 4.5in:42%, 9in:35%, 2in:7%, 12in:7%, 10in:5%
- **Aiims Road 3BHK Flats-vk**: 106 runs, 1,260 ft: 2.0″×7, 3.0″×5, 4.0″×16, 6.0″×22, 8.5″×14, 10.0″×2, 12.0″×20, 15.0″×2, 16.5″×1, 18.0″×2, 21.0″×1, 24.0″×14
  - accepted thickness mix at baseline (share of length): 4.5in:62%, 9in:16%, 6in:8%, 24in:7%, 12in:2%
- **MB Panwar JI Revision 2**: 45 runs, 286 ft: 2.0″×5, 3.0″×5, 3.5″×2, 6.0″×31, 8.0″×1, 9.5″×1
  - accepted thickness mix at baseline (share of length): 9in:62%, 6in:21%, 3in:7%, 2in:6%, 3.5in:1%
- **MR RAJEEV JI TWANI JI**: 25 runs, 212 ft: 2.0″×2, 2.5″×4, 3.0″×8, 3.5″×7, 11.0″×1, 24.0″×3
  - accepted thickness mix at baseline (share of length): 4.5in:48%, 9in:38%, 3in:5%, 2.5in:4%, 3.5in:2%
- **M.r Premg Agarwal  Baglow 90x50.dx**: 94 runs, 391 ft: 2.0″×6, 2.5″×9, 3.0″×24, 4.0″×9, 6.0″×1, 6.5″×4, 8.0″×1, 8.5″×1, 10.0″×2, 12.0″×1, 14.0″×6, 14.5″×6, 15.0″×5, 16.0″×2, 17.0″×5, 17.5″×1, 18.0″×9, 20.0″×1, 24.0″×1
  - accepted thickness mix at baseline (share of length): 4.5in:61%, 9in:11%, 3in:10%, 4in:4%, 2in:2%
- **SANJANA Giriraj Ji plan for struct**: 34 runs, 228 ft: 4.0″×1, 7.5″×1, 10.0″×11, 12.0″×1, 13.5″×1, 15.5″×2, 23.0″×1, 24.0″×16
  - accepted thickness mix at baseline (share of length): 9in:57%, 4.5in:28%, 24in:9%, 10in:2%, 15.5in:1%
- **SANJANA SURESH JI**: 9 runs, 56 ft: 2.0″×2, 6.5″×1, 22.0″×6
  - accepted thickness mix at baseline (share of length): 4.5in:91%, 22in:6%, 2in:2%, 6.5in:1%
- **abhishek ji**: 24 runs, 163 ft: 2.0″×10, 3.0″×4, 10.0″×1, 12.0″×5, 18.0″×3, 20.0″×1
  - accepted thickness mix at baseline (share of length): 4.5in:48%, 9in:38%, 2in:6%, 3in:3%, 12in:3%

## Tolerance sweep (exhaustive veto, 4½″ + 9″, four drawings)

| Drawing | Removed at 0.5″ (default) | at 0.75″ | at 1.0″ |
|---|---|---|---|
| Aiims Road | 106 runs, 1,260 ft (22%) | 76 runs, 1,164 ft (20%) | 74 runs, 1,160 ft (20%) |
| Baglow 90x50 | 94 runs, 391 ft (26%) | 84 runs, 354 ft (24%) | 81 runs, 346 ft (23%) |
| RAJEEV | 25 runs, 212 ft (14%) | 25 runs, 212 ft (14%) | 23 runs, 208 ft (13%) |
| abhishek ji | 24 runs, 163 ft (14%) | 24 runs, 163 ft (14%) | 23 runs, 158 ft (14%) |

## Reading it

- **The sanity check holds on all eight drawings**: declaring exactly the set inference finds changes no verdict. A declared set is a faithful replacement, not a different algorithm.
- **A declared set without "only these" costs little**: accepted runs change by 0 to 11 of 144 (SANJANA Giriraj: 144 → 133), because only the thickness signal (weight 0.5 of 12) moves.
- **"Only these" is a strong lever, not a refinement.** It removes 9–36% of the accepted wall length. Most of what goes is not wall-like: 2–3″ lines, and 10–24″ runs (columns, beams, hatch edges). But a minority is plausibly real: **6″ runs** (Aiims 22, Baglow 1), **4″ and 8½″ runs** (Aiims 16 and 14, Baglow 9 and 1), 6″ in MB Panwar. MB Panwar is the clear case of a wrong declaration: inference finds 6″ and 9″ there, so declaring 4½″ and 9″ removes 31 genuine 6″ runs.
- **The tolerance matters at the edges, not the middle.** Widening from 0.5″ to 0.75″ rescues the 4″ and 8½″ runs (Aiims −30 runs removed, Baglow −10); beyond that nothing changes. 0.5″ is tight against how drafters actually draw "4½″" and "9″".
- **No ground truth here**, so "phantom walls removed" is unmeasured. Judging whether the removed 6″ and 12″ runs were walls needs the drawings looked at.
- The thickness-versus-scale cross-check never fired: every drawing's scale is right, and a wrong one would have been 12× or 25.4× off.

## Recommendation

Ship the declared set and the tolerance field. Treat "only these" as an opt-in whose cost the user should see: **the "N runs would be rejected" preview deferred from the spec is what makes it safe**, and should be built before "only these" is offered to customers who cannot judge a veto by eye. Consider a default tolerance of 0.75–1.0″ for the exhaustive case; the spec's 0.5″ is the owner's call.
