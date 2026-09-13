# lerneanLabs-archiAgent

The semantic reconstruction branch adds source evidence, plan regions,
instance recognition, interval-based walls, hosted openings, independent
footprints and export validation. See [SEMANTIC_PIPELINE.md](SEMANTIC_PIPELINE.md)
for the implemented flow, review formats, acceptance gates and limitations.
It also provides an offline source-annotation workbench, scoped reference
evaluation, and optional local OCR/template matching. These are review and
reconstruction tools; the supplied floor plan has not been certified to meet
the agreed dimensional or semantic acceptance targets.
Interpretation can now be frozen into a source-bound manifest and replayed
without recognition or OCR. Accepted wall-face polygons preserve complex
boundaries and holes; validated IFC can supply an editable Blender package.
These changes improve reproducibility and representation coverage, not a
measured universal recognition-accuracy score.
This repo will hold system/solution:
1. will be able to take 2d Floorplan inputs in following formats image/pdf/DXF/DWG
2. input 2d Floorplan will be considered golden
3. System will generate 3D model of the input floorplan including and not limited to windows, gates, all rooms etc.
4. Ultimately the system will also be able to provide multiple alternative 3D models with full interior done on the basis of the design intent including and not limited to all lighting, furniture, textures etc.

## Usage

The input is a floorplan PDF or DXF, given by a named flag -- exactly one of
`--pdfFilePath` / `--dxfFilePath` is required except when printing the reusable
interpretation prompt. Output goes under
`--outputDir`, a DIRECTORY: the filename is derived from the input's own
name (`plan.pdf` -> `plan.ifc`, `drawing.dxf` -> `drawing.ifc`), and the run
refuses to overwrite an existing output file rather than silently replacing
it.

```
python -m archiagent --pdfFilePath PDF --outputDir DIR [options]
python -m archiagent --dxfFilePath DXF --outputDir DIR [options]

  --pdfFilePath PATH   input floorplan PDF (mutually exclusive with --dxfFilePath)
  --dxfFilePath PATH   input floorplan DXF (mutually exclusive with --pdfFilePath)
  --outputDir DIR      directory to write the .ifc into (required unless
                        --inspect, --classify-only, --list-regions or
                        --print-interpretation-prompt)
  --page N             PDF only: page index (default 0)
  --height FT          wall height in feet (default 10.0)
  --walls NAME...      skip the LLM; treat these layers as walls
  --provider NAME      anthropic (default) | openai
  --model NAME         model id (default claude-haiku-4-5)
  --no_vision          DXF only: disable stage 2 (image) escalation
  --units-per-foot N   DXF header override or vector-PDF calibration
                        (12 for inches, 1 for feet, 304.8 for mm)
  --inspect            print the layer inventory and exit
  --classify-only      classify, print the roles, and exit
  --rules              offline name-based layer classification
  --region X0 Y0 X1 Y1 select one plan in source coordinates
  --regions-file PATH reviewed plan windows, scales and registration
  --list-regions       print unclassified spatial region proposals
  --measurements PATH associated reviewed dimension endpoints
  --review-file PATH  symbol templates/instances, footprint and void review
  --reference-file PATH source-bound annotations for scoring one selected plan
  --workbench          write a local HTML source-annotation workbench per plan
  --ocr                explicitly enable local macOS Vision OCR (cv extra)
  --require-accepted  refuse IFC authoring when acceptance checks fail
  --freeze-only       save interpretation, evidence and overlays without IFC
  --replay-manifest PATH rebuild from frozen geometry after source-hash checks;
                        skip recognition, OCR and provider calls
  --blender-package   after IFC validation, write mesh data and a Blender script
  --print-interpretation-prompt print the versioned prompt and exit; no input
  --no-cache           ignore the classification cache
  -v                   report issues by severity
```

**Vision is on by default for DXF drawings.** Low-confidence layers get a
second look: by default, rendered images of the drawing are sent to the
configured LLM provider (the vision / stage 2 escalation). Pass
`--no_vision` to keep every call text-only, or set `ARCHIAGENT_VISION=0`;
an explicit `--no_vision` wins over the environment variable either way.
LLM classification remains the default. `--walls` and `--rules` bypass provider
calls. `--ocr` is a separate local feature, off by default; enabling it alone
does not disable the configured LLM provider.

Exit codes: `0` requested stage succeeded (may still retain a marked draft;
freeze-only does not author IFC), `1` pipeline,
export-validation or requested acceptance failure, `2` LLM unavailable,
`3` bad usage. Use `--require-accepted` for acceptance-gated workflows.

### Without an API key

`--inspect` and `--walls` need no credentials and no network:

```bash
python -m archiagent --pdfFilePath plan.pdf --inspect
python -m archiagent --pdfFilePath plan.pdf --outputDir out/ \
    --walls WALLS PARTITION
```

### DXF example

```bash
python -m archiagent --dxfFilePath drawing.dxf --inspect
python -m archiagent --dxfFilePath drawing.dxf --outputDir out/ \
    --walls WALLS --no_vision
```

### Review a selected plan

```bash
python -m archiagent --dxfFilePath drawing.dxf --outputDir out/review-1 \
    --walls WALLS --units-per-foot 12 --regions-file one-plan.json --workbench
```

Open the generated `.review.html` locally. Select source entities to label
symbols, or draw footprint and void outlines. **Export annotations** produces
a file for `--reference-file`; **Export model review** produces templates and
draft area geometry for `--review-file`. Exports preserve source identity,
coordinate registration and reviewer attribution. They never automatically
mark symbol coverage or the footprint accepted.

`--reference-file` evaluates one plan at a time. Precision/recall requires
explicitly complete, reviewed areas for named classes; partial annotations
produce diagnostic matches. Scores do not establish whole-plan acceptance.

### Optional OCR and raster templates

The `cv` extra supplies OpenCV for optional raster matching and vector-source
rendering: `python -m pip install -e ".[cv]"` (may download packages).
`--ocr` uses the installed **macOS Vision** recognizer through Swift and retains
unreviewed text, confidence, tile evidence and coordinate mappings. It requires
macOS and the local Swift toolchain. It does not download an OCR model or call a
cloud OCR service. Use `--walls` or `--rules` as well when provider calls should
be bypassed.

Reviewed templates use vector matching by default; setting `"matcher": "raster"`
in a template record enables bounded OpenCV matching. These features operate on
registered vector-source renders and do not constitute a general raster-image
or raster-PDF reconstruction pipeline. No DeepFloor or FloorplanToBlender3d
code or weights have been adopted. See [SEMANTIC_PIPELINE.md](SEMANTIC_PIPELINE.md)
for schemas, registration rules and remaining limitations.

### Freeze decisions and reproduce an IFC-first Blender model

Normal reconstruction saves `<input>.interpretation.json` alongside the report.
To stop after interpretation and review artifacts:

```bash
python -m archiagent --dxfFilePath drawing.dxf --outputDir out/interpretation \
    --rules --review-file review.json --freeze-only --workbench
```

Supply reviewed scale/region flags as needed for that drawing. Freezing preserves
draft assumptions and unresolved issues; it does not approve them. To reproduce
the frozen geometry in a new output directory:

```bash
python -m archiagent --dxfFilePath drawing.dxf --outputDir out/replay \
    --replay-manifest out/interpretation/drawing.interpretation.json \
    --blender-package
```

Replay still requires the matching source file for checksum verification. It
does not parse the drawing or contact a model provider. It rejects options that
would reinterpret frozen decisions, such as new scales, regions, reviews, or
heights. Current validation runs again and reports implementation-version
changes. Add `--require-accepted` to prevent IFC authoring from unresolved drafts.
The manifest digest detects accidental changes; it is not reviewer approval.

`--blender-package` creates `drawing.blender/` containing `source.ifc`,
`mesh_manifest.json`, and `build_blender.py`. It does **not** run Blender or
create a `.blend` itself. Use an installed Blender executable:

```bash
blender --background --factory-startup --python-exit-code 1 \
    --python out/replay/drawing.blender/build_blender.py -- \
    --manifest out/replay/drawing.blender/mesh_manifest.json \
    --output out/replay/drawing.blend
```

Add `--link-bonsai` after `--` to link individual meshes to an installed Bonsai
project. The script preserves existing scenes, derives geometry and face
materials from IFC, and frames an optional presentation camera. It refuses to
replace an existing blend unless explicitly passed `--overwrite`; use a fresh
session when linking Bonsai. Meshes are editable triangles, not automatically
synchronized IFC profiles. Use BIM editing/export tools for IFC changes; full
edit/export round-trip behavior is not certified.

Print the generic interpretation contract for review or an external agent:

```bash
python -m archiagent --print-interpretation-prompt
```

The CLI exposes this prompt without making a model call. Existing layer
classifiers use narrower prompts that retain hatch evidence, acknowledge mixed
layers and ambiguous symbols, and describe model confidence as uncalibrated.
Frozen replay controls construction decisions; rerunning an AI interpretation
does not guarantee identical decisions or independently verified accuracy.

The IFC author also assigns a reusable illustrative palette to walls, slabs,
columns, beams, doors and windows, including glazing transparency. These are
surface appearance defaults, not verified finish specifications or material
assemblies. The mesh bridge preserves the actual IFC style assignments.
See [ENHANCEMENT_RESULTS.md](ENHANCEMENT_RESULTS.md) for regression evidence,
the scoped replay benchmark and remaining limitations.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ARCHIAGENT_LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ARCHIAGENT_LLM_MODEL` | `claude-haiku-4-5` | model id |
| `ARCHIAGENT_LLM_BASE_URL` | unset | Groq / Kimi / DeepSeek, via the `openai` provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | credentials |
| `ARCHIAGENT_CACHE_DIR` | `~/.cache/archiagent/layers` | classification cache |

Install the LLM extra with `pip install -e ".[llm]"`.
