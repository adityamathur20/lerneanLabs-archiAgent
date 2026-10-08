"""Gate: does LibreDWG convert DWG well enough to replace the ODA File Converter?

Plan: docs/superpowers/plans/2026-10-07-mlightcad-cad-viewer.md, Task 1.

For every DWG in a directory this converts it with LibreDWG (tools/dwg2dxf)
and, when it is installed, with ODA. It then compares the two DXFs at three
depths:

1. ezdxf    — does it parse strictly, and what entities does modelspace hold?
2. ingest   — what archiAgent's own loader extracts (entities, primitives,
              texts, native dimensions), which is what the pipeline sees.
3. pipeline — the CLI end to end: exit code, region statuses, IFC elements.

    python scripts/gates/dwg_fidelity.py CORPUS_DIR OUT_DIR
    python scripts/gates/dwg_fidelity.py CORPUS_DIR OUT_DIR --skip-pipeline
    python scripts/gates/dwg_fidelity.py CORPUS_DIR OUT_DIR \\
        --pipeline-args "--rules --trust-extracted-scale"

Writes OUT_DIR/fidelity.json and OUT_DIR/fidelity.md. Exits 1 if any DWG
fails to convert with LibreDWG, or (when ODA ran) if the two converters
disagree on what the pipeline produced.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import ezdxf
from ezdxf import recover

ROOT = Path(__file__).resolve().parents[2]
CONVERT = ROOT / "tools" / "dwg2dxf" / "convert.mjs"

#: IFC classes worth counting: what a floor plan's model is made of.
IFC_CLASSES = ("IfcWall", "IfcDoor", "IfcWindow", "IfcOpeningElement", "IfcSpace", "IfcSlab")


def convert_libredwg(dwg: Path, out_dir: Path, timeout_s: int) -> tuple[Path | None, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf = out_dir / f"{dwg.stem}.dxf"
    started = time.monotonic()
    try:
        done = subprocess.run(["node", str(CONVERT), str(dwg), str(dxf)],
                              capture_output=True, text=True, timeout=timeout_s)
        ok, detail = done.returncode == 0, (done.stderr or done.stdout).strip()[-400:]
    except subprocess.TimeoutExpired:
        ok, detail = False, f"timed out after {timeout_s}s"
    info = {"ok": ok and dxf.is_file(), "ms": int((time.monotonic() - started) * 1000)}
    if not info["ok"]:
        info["error"] = detail
    return (dxf if info["ok"] else None), info


def convert_oda(dwg: Path, out_dir: Path, timeout_s: int) -> tuple[Path | None, dict]:
    from archiagent.ingest.dwg import DwgConversionError, convert_dwg
    started = time.monotonic()
    try:
        dxf = convert_dwg(dwg, out_dir, timeout_s=timeout_s)
        return dxf, {"ok": True, "ms": int((time.monotonic() - started) * 1000)}
    except DwgConversionError as error:
        return None, {"ok": False, "ms": int((time.monotonic() - started) * 1000),
                      "error": str(error)[-400:]}


def ezdxf_facts(dxf: Path) -> dict:
    try:
        doc, mode = ezdxf.readfile(str(dxf)), "strict"
        audit_errors = 0
    except Exception as error:  # noqa: BLE001 - any strict failure is the finding
        doc, auditor = recover.readfile(str(dxf))
        mode, audit_errors = f"recover ({type(error).__name__})", len(auditor.errors)
    msp = doc.modelspace()
    kinds = collections.Counter(e.dxftype() for e in msp)
    return {
        "mode": mode,
        "audit_errors": audit_errors,
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS"),
        "layers": len(doc.layers),
        "blocks": sum(1 for b in doc.blocks if not b.name.startswith("*")),
        "modelspace": sum(kinds.values()),
        "by_type": dict(sorted(kinds.items())),
    }


#: ezdxf's placeholder for "no extents": a DXF carrying it frames to nothing.
UNSET_EXTENT = 1e19


def writer_defects(dxf: Path) -> dict:
    """Defects LibreDWG's DXF writer was measured to introduce on real drawings
    (2026-10-08). Each one is invisible to a count comparison:

    - entities inside blocks written with an EMPTY layer name (door blocks);
    - "X @ N" layers: annotation reassigned to an invented duplicate layer;
    - layers written OFF although the DWG has them on (the viewer hides them);
    - $EXTMIN/$EXTMAX and the model layout's extents left unset.
    """
    doc = ezdxf.readfile(str(dxf))
    blank = sum(1 for blk in doc.blocks for e in blk if not e.dxf.get("layer", ""))
    suffixed = sorted(l.dxf.name for l in doc.layers if re.search(r" @ \d+$", l.dxf.name))
    layers = list(doc.layers)
    extmin = doc.modelspace().dxf.get("extmin")
    return {
        "blank_layer_entities": blank,
        "suffixed_layers": suffixed,
        "layers_off": sum(l.is_off() for l in layers),
        "layers": len(layers),
        "extents_unset": extmin is None or abs(extmin[0]) >= UNSET_EXTENT,
    }


def emptied_blocks(lib: Path, oda: Path) -> list[str]:
    """Block definitions ODA fills and LibreDWG writes empty: real data loss."""
    a, b = ezdxf.readfile(str(lib)), ezdxf.readfile(str(oda))
    return sorted(blk.name for blk in b.blocks
                  if len(blk) and blk.name in a.blocks and not len(a.blocks.get(blk.name)))


def ingest_facts(dxf: Path) -> dict:
    from archiagent.ingest.dxf_vector import load_dxf
    try:
        # A unit ratio is only needed to scale geometry; 1.0 keeps counts exact
        # for drawings whose header declares no units.
        ps, _ = load_dxf(dxf, units_per_foot=1.0)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[-400:]}
    return {
        "ok": True,
        "source_entities": len(ps.entities),
        "primitives": len(ps.primitives),
        "texts": len(ps.texts),
        "native_dimensions": len(ps.dimensions),
        "warnings": len(ps.warnings),
        "layers": len(ps.layer_names()),
        "handles": sorted({e.id for e in ps.entities if not e.parent_id})[:5],
    }


def pipeline_facts(dxf: Path, out_dir: Path, extra: list[str], timeout_s: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    command = [sys.executable, "-m", "archiagent", "--dxfFilePath", str(dxf),
               "--outputDir", str(out_dir), *extra]
    try:
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s)
        code, err = done.returncode, done.stderr
    except subprocess.TimeoutExpired:
        code, err = None, f"timed out after {timeout_s}s"
    facts: dict = {"exit": code, "ms": int((time.monotonic() - started) * 1000)}
    if code != 0:
        # The CLI prints a token summary last, so the last line is not the
        # reason. Prefer its `error:` line.
        lines = [line for line in err.splitlines() if line.strip()]
        reasons = [line for line in lines if line.startswith("error")] or \
                  [line for line in lines if not line.startswith("tokens:")]
        facts["error"] = reasons[-1][-300:] if reasons else ""
    statuses = collections.Counter()
    for report in out_dir.glob("*.report.json"):
        for region in json.loads(report.read_text()).get("regions", []):
            statuses[region.get("status")] += 1
    facts["regions"] = dict(statuses)
    ifcs = sorted(out_dir.glob("*.ifc"))
    if ifcs:
        import ifcopenshell
        model = ifcopenshell.open(str(ifcs[0]))
        facts["ifc"] = {cls: len(model.by_type(cls)) for cls in IFC_CLASSES}
    return facts


def compare(a: dict, b: dict) -> list[str]:
    """Differences between the LibreDWG and ODA results, as short sentences."""
    notes = []
    for key in ("modelspace", "layers", "blocks"):
        if a["ezdxf"].get(key) != b["ezdxf"].get(key):
            notes.append(f"ezdxf {key}: libredwg {a['ezdxf'].get(key)} vs oda {b['ezdxf'].get(key)}")
    for key in ("source_entities", "primitives", "texts", "native_dimensions"):
        if a["ingest"].get(key) != b["ingest"].get(key):
            notes.append(f"ingest {key}: libredwg {a['ingest'].get(key)} vs oda {b['ingest'].get(key)}")
    pa, pb = a.get("pipeline"), b.get("pipeline")
    if pa and pb:
        if pa["exit"] != pb["exit"]:
            notes.append(f"pipeline exit: libredwg {pa['exit']} vs oda {pb['exit']}")
        if pa.get("regions") != pb.get("regions"):
            notes.append(f"regions: libredwg {pa.get('regions')} vs oda {pb.get('regions')}")
        if pa.get("ifc") != pb.get("ifc"):
            notes.append(f"ifc: libredwg {pa.get('ifc')} vs oda {pb.get('ifc')}")
    return notes


def markdown(rows: list[dict], oda: bool, ran_pipeline: bool) -> str:
    lines = ["# DWG conversion fidelity", "",
             f"ODA compared: {'yes' if oda else 'no (not installed)'}. "
             f"Pipeline run: {'yes' if ran_pipeline else 'no'}.", "",
             "| DWG | LibreDWG | ms | ezdxf | modelspace | ingest entities | dims | pipeline | IFC walls | vs ODA |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lib = row["libredwg"]
        if not lib["convert"]["ok"]:
            lines.append(f"| {row['dwg']} | **FAILED**: {lib['convert'].get('error', '')[:80]} "
                         "| | | | | | | | |")
            continue
        pipe = lib.get("pipeline") or {}
        walls = (pipe.get("ifc") or {}).get("IfcWall", "")
        outcome = pipe.get("exit", "") if pipe else ""
        if pipe.get("error"):
            outcome = f"{outcome}: {pipe['error'][:60]}"
        versus = ("n/a" if "oda" not in row else
                  "match" if not row["differences"] else f"{len(row['differences'])} diffs")
        ingest = lib["ingest"]
        lines.append(
            f"| {row['dwg']} | ok | {lib['convert']['ms']} | {lib['ezdxf']['mode']} "
            f"| {lib['ezdxf']['modelspace']} | {ingest.get('source_entities', ingest.get('error', ''))} "
            f"| {ingest.get('native_dimensions', '')} | {outcome} "
            f"| {walls} | {versus} |")
    lines += ["", "## LibreDWG writer defects", "",
              "| DWG | blank-layer block entities | `@ N` layers | layers off | extents unset |",
              "|---|---|---|---|---|"]
    for row in rows:
        w = row["libredwg"].get("writer_defects")
        if w:
            lines.append(f"| {row['dwg']} | {w['blank_layer_entities']} | {len(w['suffixed_layers'])} "
                         f"| {w['layers_off']} / {w['layers']} | {'yes' if w['extents_unset'] else 'no'} |")
    diffs = [r for r in rows if r.get("differences")]
    if diffs:
        lines += ["", "## Differences from ODA", ""]
        for row in diffs:
            lines.append(f"- **{row['dwg']}**")
            lines += [f"  - {note}" for note in row["differences"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("corpus", type=Path, help="directory searched recursively for *.dwg")
    parser.add_argument("out", type=Path)
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument("--pipeline-args", default="--rules --trust-extracted-scale",
                        help="extra archiagent CLI arguments (default: %(default)s)")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per step")
    args = parser.parse_args(argv)

    from archiagent.ingest.dwg import converter_available
    oda = converter_available()
    extra = shlex.split(args.pipeline_args)
    dwgs = sorted(p for p in args.corpus.rglob("*") if p.suffix.lower() == ".dwg")
    if not dwgs:
        print(f"no .dwg files under {args.corpus}", file=sys.stderr)
        return 2

    rows = []
    for dwg in dwgs:
        name = str(dwg.relative_to(args.corpus))
        print(f"· {name}", file=sys.stderr)
        row: dict = {"dwg": name}
        backends = [("libredwg", convert_libredwg)] + ([("oda", convert_oda)] if oda else [])
        for backend, convert in backends:
            work = args.out / dwg.stem / backend
            dxf, info = convert(dwg, work / "dxf", args.timeout)
            result: dict = {"convert": info}
            if dxf:
                result["ezdxf"] = ezdxf_facts(dxf)
                if backend == "libredwg":
                    result["writer_defects"] = writer_defects(dxf)
                result["ingest"] = ingest_facts(dxf)
                if not args.skip_pipeline:
                    result["pipeline"] = pipeline_facts(dxf, work / "out", extra, args.timeout)
            row[backend] = result
        if "oda" in row and row["oda"]["convert"]["ok"] and row["libredwg"]["convert"]["ok"]:
            row["differences"] = compare(row["libredwg"], row["oda"])
            lib_dxf = args.out / dwg.stem / "libredwg" / "dxf" / f"{dwg.stem}.dxf"
            oda_dxf = args.out / dwg.stem / "oda" / "dxf" / f"{dwg.stem}.dxf"
            if emptied := emptied_blocks(lib_dxf, oda_dxf):
                row["differences"].append(f"blocks written empty: {', '.join(emptied)}")
        rows.append(row)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "fidelity.json").write_text(json.dumps(rows, indent=2))
    (args.out / "fidelity.md").write_text(markdown(rows, oda, not args.skip_pipeline))
    print(f"wrote {args.out / 'fidelity.md'}", file=sys.stderr)

    failed = [r["dwg"] for r in rows if not r["libredwg"]["convert"]["ok"]]
    # Any ingest-level difference fails too: what archiAgent loads must match,
    # not merely what the pipeline happens to conclude from it.
    pipeline_diffs = [r["dwg"] for r in rows
                      if any(d.startswith(("pipeline", "regions", "ifc", "ingest", "blocks"))
                             for d in r.get("differences", []))]
    defective = [r["dwg"] for r in rows
                 if (w := r["libredwg"].get("writer_defects"))
                 and (w["blank_layer_entities"] or w["suffixed_layers"] or w["extents_unset"]
                      or w["layers_off"] > w["layers"] // 2)]
    if failed or pipeline_diffs or defective:
        print(f"GATE FAILS: conversion failed {failed}, differs from ODA {pipeline_diffs}, "
              f"writer defects {defective}", file=sys.stderr)
        return 1
    print("GATE PASSES" + ("" if oda else " for conversion only: ODA absent, nothing compared"),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
