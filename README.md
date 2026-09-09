# lerneanLabs-archiAgent
This repo will hold system/solution:
1. will be able to take 2d Floorplan inputs in following formats image/pdf/DXF/DWG
2. input 2d Floorplan will be considered golden
3. System will generate 3D model of the input floorplan including and not limited to windows, gates, all rooms etc.
4. Ultimately the system will also be able to provide multiple alternative 3D models with full interior done on the basis of the design intent including and not limited to all lighting, furniture, textures etc.

## Usage

The input is a floorplan PDF or DXF, given by a named flag -- exactly one of
`--pdfFilePath` / `--dxfFilePath` is required. Output goes under
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
                        --inspect or --classify-only)
  --page N             PDF only: page index (default 0)
  --height FT          wall height in feet (default 10.0)
  --walls NAME...      skip the LLM; treat these layers as walls
  --provider NAME      anthropic (default) | openai
  --model NAME         model id (default claude-haiku-4-5)
  --no_vision          DXF only: disable stage 2 (image) escalation
  --units-per-foot N   DXF only: override the drawing's declared units
                        (12 for inches, 1 for feet, 304.8 for mm)
  --inspect            print the layer inventory and exit
  --classify-only      classify, print the roles, and exit
  --no-cache           ignore the classification cache
  -v                   report issues by severity
```

**Vision is on by default for DXF drawings.** Low-confidence layers get a
second look: by default, rendered images of the drawing are sent to the
configured LLM provider (the vision / stage 2 escalation). Pass
`--no_vision` to keep every call text-only, or set `ARCHIAGENT_VISION=0`;
an explicit `--no_vision` wins over the environment variable either way.

Exit codes: `0` success, `1` pipeline error (scale gate, no walls, unreadable
drawing), `2` LLM unavailable, `3` bad usage.

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

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ARCHIAGENT_LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ARCHIAGENT_LLM_MODEL` | `claude-haiku-4-5` | model id |
| `ARCHIAGENT_LLM_BASE_URL` | unset | Groq / Kimi / DeepSeek, via the `openai` provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | credentials |
| `ARCHIAGENT_CACHE_DIR` | `~/.cache/archiagent/layers` | classification cache |

Install the LLM extra with `pip install -e ".[llm]"`.
