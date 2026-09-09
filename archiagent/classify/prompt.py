"""What the classifier says to the model, and what shape it accepts back.

Pure functions only -- no client, no network, no disk. Keeping the prompt
here means it can be reviewed and diffed on its own, and it is the template
W3 (glyph identification) and W5 (symbol naming) will copy.

The layer table is deliberately compact: the whole inventory has to fit in
one cheap call, and the statistics are already strongly discriminative.
Measured on the demolition plan, walls are 100% axis-aligned with a median
segment length of 23.2 units against ~1.1 for furniture and 0.0 for hatch.
Bounding-box width and height are included because they are the main signal
separating a title block or sheet border from plan geometry.
"""

from __future__ import annotations

import hashlib
import json

from archiagent.classify.inventory import LayerStats
from archiagent.classify.roles import Role

SYSTEM_PROMPT = """\
You classify CAD layers from a single architectural floorplan.

You are given one row per layer, summarising the vector geometry on that
layer. You never see the drawing itself. Assign every layer exactly one role
from the fixed vocabulary, with a confidence and a one-clause reason.

Columns (lengths are in the drawing's own units, NOT feet -- the scale is
unknown at this stage, so only RELATIVE magnitudes are meaningful):
  paths   separate vector paths on the layer
  segs    total line segments across those paths
  axis%   percent of segments that are exactly horizontal or vertical
  p10 p50 p90   10th/50th/90th percentile segment length
  w h     bounding-box width and height
  widths  distinct stroke widths, most common first (0 = hairline or filled)
  colors  dominant RGB colours in 0-1, most common first

What the numbers usually mean:
- Walls: near 100% axis-aligned, p50 long relative to other layers, bounding
  box covering most of the plan. A drawing often splits its wall network
  across several layers, so classify EVERY layer carrying wall geometry as a
  wall rather than choosing a single best candidate.
- Hatch (wall poche, fill): very many segments that are either near-zero
  length (degenerate points) or short diagonal strokes at one consistent
  angle. HATCH IS NOT A WALL. Wall detection pairs opposing wall FACES, and
  feeding it hatch strokes corrupts the drawing's recovered scale. Classify
  hatch as annotation.
- Dimensions: many short segments (ticks and arrowheads) mixed with long
  thin runs, concentrated around the edges of the plan.
- Furniture, fixtures, vehicles, landscape: low axis%, short p50, often a
  distinct colour.
- Title block and sheet border: few paths, bounding box larger than or
  offset from the plan geometry.
- Grid: very few paths, long axis-aligned runs crossing the whole sheet.

Rules:
- Use only roles from the enum. There is no "other" -- use `ignore` when
  nothing fits, including empty, construction and scratch layers.
- Return one entry for EVERY layer you are given, with the name copied
  verbatim, including its exact case and any spaces.
- `confidence` is your calibrated probability that the role is correct. Do
  not inflate it. 0.5 means genuinely unsure, and being unsure is useful --
  a human reviews low-confidence layers.
- `reason` is one short clause citing the numbers that decided it.
"""


def response_schema() -> dict:
    """The JSON schema the reply is constrained to.

    The role enum is generated from Role, so the vocabulary cannot drift
    out of sync with the code. No numeric constraints: structured outputs
    do not support minimum/maximum, and claiming them here would imply a
    guarantee the API does not make.
    """
    return {
        "type": "object",
        "properties": {
            "layers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string",
                                 "enum": [r.value for r in Role]},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["name", "role", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["layers"],
        "additionalProperties": False,
    }


DXF_SYSTEM_PROMPT = """\
You are classifying the layers of a DXF architectural floorplan so a
deterministic pipeline can build a 3D model of the WALLS.

You are given one row per layer. Use every column:

- name: the drafter's own label. Usually the strongest signal, but CAD
  offices do not follow the AIA/NCS standard -- expect names like
  "NEW WALLS", "walll", "COLUM HATCH", "win", "FURN".
- entities: counts by DXF entity type. ARC-heavy layers are usually doors
  (door swings are arcs). MTEXT/TEXT-heavy layers are labels. DIMENSION
  means a dimension layer. HATCH means fill, not wall faces.
- lineweight: walls and columns are drafted heavy (30-40); doors, windows
  and furniture light (5-9). A value of -3 means "default", i.e. no signal.
- linetype: HIDDEN usually means an element above the cut plane, such as a
  beam.
- share: the fraction of the drawing's entities on this layer.
- frozen/off: the drafter turned this layer off. Weak evidence it is not
  part of the plan.

Two warnings drawn from real drawings:

1. The default layer "0" is sometimes where the entire building is drawn,
   holding more than half the entities. Its name tells you nothing. Judge it
   on its share and its entity mix, not on its name.
2. A drawing often splits walls across several layers. Classify EVERY
   wall-carrying layer as a wall, not just the best one.

Return one entry per layer. Do not invent layers.
"""


PROMPT_VERSION = hashlib.sha256(
    (SYSTEM_PROMPT + DXF_SYSTEM_PROMPT
     + json.dumps(response_schema(), sort_keys=True)).encode("utf-8")
).hexdigest()[:12]
"""Identity of the prompt, DERIVED from its content rather than declared.

The classification cache key includes this so a stale answer is never
served for a new prompt. As a hand-maintained constant that guarantee
rested on a developer remembering to bump it -- and the failure mode is
exactly the one it exists to prevent: edit the prompt to correct a
misclassification, forget the bump, and every warm cache keeps serving the
answer the edit was meant to fix. Deriving it makes forgetting impossible.
"""


def _row(s: LayerStats) -> str:
    x0, y0, x1, y1 = s.bbox
    widths = " ".join(f"{w:g}" for w in s.stroke_widths) or "-"
    colors = " ".join(f"({r:.2f},{g:.2f},{b:.2f})"
                      for r, g, b in s.dominant_colors) or "-"
    return " | ".join((
        json.dumps(s.name),
        str(s.path_count),
        str(s.segment_count),
        f"{s.axis_aligned_fraction * 100:.0f}",
        f"{s.length_p10:.1f}",
        f"{s.length_p50:.1f}",
        f"{s.length_p90:.1f}",
        f"{x1 - x0:.1f}",
        f"{y1 - y0:.1f}",
        widths,
        colors,
    ))


def build_user_prompt(stats: tuple[LayerStats, ...]) -> str:
    """Render the inventory as one row per layer.

    Layer names are JSON-quoted: real drawings carry names like
    "WALL HATCH" and "SPLIT AC", and an unquoted space-separated table
    would be ambiguous.
    """
    header = "name | paths | segs | axis% | p10 | p50 | p90 | w | h | widths | colors"
    rows = "\n".join(_row(s) for s in stats)
    return (f"This drawing has {len(stats)} layers.\n\n"
            f"{header}\n{rows}\n\n"
            "Classify every one of them.")


def _dxf_row(s: LayerStats) -> str:
    x0, y0, x1, y1 = s.bbox
    mix = " ".join(f"{k}:{v}" for k, v in s.entity_mix) or "-"
    lw = "default" if s.lineweight in (None, -3) else str(s.lineweight)
    flags = ",".join(f for f, on in (("frozen", s.is_frozen), ("off", s.is_off)) if on) or "-"
    return (f'{json.dumps(s.name)} | entities={mix} | share={s.entity_share * 100:.1f}% '
            f'| lineweight={lw} | linetype={s.linetype or "-"} | flags={flags} '
            f'| size={x1 - x0:.0f}x{y1 - y0:.0f} | axis={s.axis_aligned_fraction:.2f} '
            f'| len_p50={s.length_p50:.1f}')


def build_dxf_user_prompt(stats: tuple[LayerStats, ...]) -> str:
    """Render the DXF inventory as one row per layer.

    Layer names are JSON-quoted so names with spaces (e.g. "COLUM HATCH",
    "DB TO SHAFT CONDUIT") are unambiguous. DXF-specific fields include
    entity mix, lineweight, linetype, and layer state flags.
    """
    rows = "\n".join(_dxf_row(s) for s in stats)
    return (f"INVENTORY ({len(stats)} layers)\n{rows}\n\n"
            "Classify every layer listed above.")
