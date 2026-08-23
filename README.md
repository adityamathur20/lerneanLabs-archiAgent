# lerneanLabs-archiAgent
This repo will hold system/solution:
1. will be able to take 2d Floorplan inputs in following formats image/pdf/DXF/DWG
2. input 2d Floorplan will be considered golden
3. System will generate 3D model of the input floorplan including and not limited to windows, gates, all rooms etc.
4. Ultimately the system will also be able to provide multiple alternative 3D models with full interior done on the basis of the design intent including and not limited to all lighting, furniture, textures etc.

## Usage

```
python -m archiagent PDF OUT_IFC [options]

  --page N            page index (default 0)
  --height FT         wall height in feet (default 10.0)
  --walls NAME...     skip the LLM; treat these layers as walls
  --provider NAME     anthropic (default) | openai
  --model NAME        model id (default claude-haiku-4-5)
  --inspect           print the layer inventory and exit
  --classify-only     classify, print the roles, and exit
  --no-cache          ignore the classification cache
  -v                  report issues by severity
```

`OUT_IFC` must appear before `--walls` -- `--walls` takes one or more layer
names and consumes every argument after it, so an `OUT_IFC` placed after
`--walls` gets read in as a layer name instead.

Exit codes: `0` success, `1` pipeline error (scale gate, no walls, unreadable
PDF), `2` LLM unavailable, `3` bad usage.

### Without an API key

`--inspect` and `--walls` need no credentials and no network:

```bash
python -m archiagent plan.pdf --inspect
python -m archiagent plan.pdf out.ifc --walls WALLS PARTITION
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
