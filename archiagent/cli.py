"""`archiagent --pdfFilePath plan.pdf --outputDir out/` -- the tool surface.

Three modes:
  --inspect        print the layer inventory and stop. No LLM, no output file.
  --classify-only  classify and print the roles. No output file.
  (default)        classify, build, author the IFC.

--inspect and --walls exist so the tool stays usable with no credentials and
no network. --inspect is also how you find out what a drawing contains
before spending a call on it.

Every input and output is a NAMED flag, not a positional. A previous
revision took the input as a bare positional plus a positional OUT_IFC, and
argparse could not always tell `PDF --dxfFilePath X` (an error: both given)
apart from `--dxfFilePath X OUT_IFC` (valid) -- both parse to one leftover
positional token. The tool guessed it was OUT_IFC and wrote the authored
IFC over the user's own input file, at exit 0. Named flags remove the
ambiguity outright: --outputDir names a DIRECTORY the tool writes into
under a filename it derives itself, so no flag value the user supplies can
ever be mistaken for a destination to overwrite.

VISION IS ON BY DEFAULT for DXF drawings: low-confidence layers get a
second look via rendered images sent to the configured LLM provider (the
vision / stage 2 escalation). Pass --no_vision to keep every call
text-only, or set ARCHIAGENT_VISION=0.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
from dataclasses import fields, replace
import os
import shlex
import sys
from pathlib import Path

from archiagent.classify.cache import CachingClassifier
from archiagent.classify.dxf_classifier import DxfLayerClassifier
from archiagent.classify.escalate import ESCALATION_CAP
from archiagent.classify.dxf_inventory import build_dxf_inventory
from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import (Classification, LayerClassifier,
                                        StubClassifier)
from archiagent.classify.llm_classifier import MAX_TOKENS, LLMLayerClassifier
from archiagent.classify.roles import Role
from archiagent.ifc.author import author_ifc, author_building
from archiagent.ifc.inspect import validate_export
from archiagent.classify.rules import RuleClassifier
from archiagent.regions import load_regions, select_region, propose_regions
from archiagent.semantic import PlanRegion, SymbolInstance
from archiagent.scale.verify import load_measurements
from archiagent.reporting import write_review, record_export_validation
from archiagent.benchmark import load_reference, evaluate_reference
from archiagent.ingest.dxf_vector import DxfUnitsError, load_dxf
from archiagent.ingest.pdf_vector import NoLayersError, load_pdf
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.config import build_client, config_from_env
from archiagent.model import Issue
from archiagent.pipeline import extract_from_dxf, extract_from_primitives
from archiagent.scale.resolve import ScaleGateError
from archiagent.validate import validate

EXIT_OK = 0
EXIT_PIPELINE = 1
EXIT_LLM = 2
EXIT_USAGE = 3

MANUAL_WALL_CONFIDENCE = 1.0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="archiagent",
        description=(
            "Turn a layered 2D floorplan (PDF or DXF) into a to-scale IFC4 "
            "model. Exactly one of --pdfFilePath or --dxfFilePath is "
            "required. By default, rendered images of DXF layers ARE SENT "
            "to the configured LLM provider for a second look at "
            "low-confidence layers (the vision stage). Pass --no_vision to "
            "keep every call text-only."))
    p.add_argument("--pdfFilePath", dest="pdfFilePath", metavar="PATH",
                   default=None,
                   help="input floorplan PDF (mutually exclusive with "
                        "--dxfFilePath; exactly one is required)")
    p.add_argument("--dxfFilePath", dest="dxfFilePath", metavar="PATH",
                   default=None,
                   help="input floorplan DXF (mutually exclusive with "
                        "--pdfFilePath; exactly one is required)")
    p.add_argument("--outputDir", dest="outputDir", metavar="DIR",
                   default=None,
                   help="directory to write the .ifc into (required unless "
                        "--inspect or --classify-only is given). The "
                        "filename is derived from the input's own name "
                        "(plan.pdf -> plan.ifc, drawing.dxf -> "
                        "drawing.ifc). Created if it does not exist; the "
                        "run refuses to overwrite an existing output file.")
    p.add_argument("--page", type=int, default=0,
                   help="PDF only: page index (default 0)")
    p.add_argument("--height", type=float, default=10.0,
                   metavar="FT", help="wall height in feet (default 10.0)")
    p.add_argument("--walls", nargs="+", metavar="NAME", default=None,
                   help="skip the LLM; treat these layers as walls")
    p.add_argument("--provider", default=None,
                   help="override ARCHIAGENT_LLM_PROVIDER")
    p.add_argument("--model", default=None,
                   help="override ARCHIAGENT_LLM_MODEL")
    p.add_argument("--no_vision", action="store_true",
                   help="DXF only: disable stage 2 (image) escalation. "
                        "Without this flag, rendered images of "
                        "low-confidence layers ARE SENT to the configured "
                        "LLM provider by default -- ARCHIAGENT_VISION=0 "
                        "has the same effect as this flag, but this flag "
                        "wins if both are given.")
    p.add_argument("--max-tokens", type=int, default=None,
                   metavar="N",
                   help="cap on the model's reply length (default %d, or "
                        "ARCHIAGENT_MAX_TOKENS). A drawing with many layers "
                        "needs a bigger budget: the reply carries a role, a "
                        "confidence and a reason for EVERY layer, and a reply "
                        "cut short is a hard error, not a partial answer."
                        % MAX_TOKENS)
    p.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                   help="how long to wait for the model (default: the SDK's "
                        "600s, or ARCHIAGENT_LLM_TIMEOUT). A vision call "
                        "carrying many rendered layers to a slow model can "
                        "exceed that, and a timeout returns nothing at all.")
    p.add_argument("--maxEscalation", type=int, default=ESCALATION_CAP,
                   metavar="N",
                   help=f"DXF only: how many uncertain layers the vision stage "
                        f"may examine (default {ESCALATION_CAP}). Each one costs "
                        f"a rendered image in the request; layers beyond the cap "
                        f"are reported but not examined.")
    p.add_argument("--units-per-foot", type=float, default=None,
                   metavar="FLOAT",
                   help="drawing source units per foot; DXF header override or PDF calibration "
                        "(12 for inches, 1 for feet, 304.8 for mm)")
    p.add_argument("--rules", action="store_true", help="offline layer hints; no provider call")
    p.add_argument("--region", nargs=4, type=float, metavar=("XMIN","YMIN","XMAX","YMAX"), help="one source-coordinate plan window")
    p.add_argument("--regions-file", help="reviewed JSON regions: kind='plan', explicit elevations, optional units_per_foot per region")
    p.add_argument("--list-regions", action="store_true", help="print spatial region proposals; no floor identity is inferred")
    p.add_argument("--storey-name", default="Unassigned plan")
    p.add_argument("--elevation", type=float, default=None, help="storey elevation in feet; otherwise explicitly assumed")
    p.add_argument("--measurements", help="reviewed source endpoint dimensions JSON")
    p.add_argument("--review-file", help="reviewed symbols/footprint/void JSON in model feet; optional regions map")
    p.add_argument("--reference-file", help="source-bound reference annotations for scoring one selected plan")
    p.add_argument("--workbench", action="store_true", help="write an offline HTML source annotation workbench per plan")
    p.add_argument("--ocr", action="store_true", help="explicitly run local macOS Vision OCR on vector outlines (requires cv extra)")
    p.add_argument("--require-accepted", action="store_true", help="write review report but refuse IFC when acceptance errors remain")
    p.add_argument("--freeze-only", action="store_true", help="save interpretation, review and overlays without authoring IFC")
    p.add_argument("--replay-manifest", metavar="PATH", help="rebuild from a frozen interpretation after checking the input checksum; no recognition, OCR or provider calls")
    p.add_argument("--blender-package", action="store_true", help="after IFC validation, write IFC-derived mesh data and a standalone Blender build script")
    p.add_argument("--print-interpretation-prompt", action="store_true", help="print the versioned interpretation system prompt and exit; no input needed")
    p.add_argument("--inspect", action="store_true",
                   help="print the layer inventory and exit")
    p.add_argument("--classify-only", action="store_true",
                   help="classify, print the roles, and exit")
    p.add_argument("--no-cache", action="store_true",
                   help="ignore the classification cache")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="report issues by severity")
    return p


def _print_inventory(stats) -> None:
    print(f"{'layer':<24} {'paths':>7} {'segs':>7} {'axis%':>6} "
          f"{'p50':>8} {'p90':>8}")
    for s in stats:
        print(f"{s.name:<24} {s.path_count:>7} {s.segment_count:>7} "
              f"{s.axis_aligned_fraction * 100:>5.0f}% "
              f"{s.length_p50:>8.1f} {s.length_p90:>8.1f}")


MAX_REASON_CHARS = 60


def _safe_reason(reason: str) -> str:
    """The reason is model-authored text going straight to a terminal.

    Control characters could rewrite the table (or worse, via ANSI escapes),
    and an over-long reason destroys the column layout, so strip and clip
    before printing.
    """
    clean = "".join(c for c in reason if c.isprintable())
    if len(clean) > MAX_REASON_CHARS:
        clean = clean[:MAX_REASON_CHARS - 1] + "…"
    return clean


def _print_decisions(decisions) -> None:
    print(f"{'layer':<24} {'role':<18} {'conf':>5}  {'source':<8} reason")
    for d in decisions:
        print(f"{d.layer:<24} {d.role.value:<18} {d.confidence:>5.2f}  "
              f"{d.source:<8} {_safe_reason(d.reason)}")


def _print_issues(issues) -> None:
    for severity in ("error", "warn", "info"):
        matching = [i for i in issues if i.severity == severity]
        if not matching:
            continue
        print(f"\n{severity} ({len(matching)}):", file=sys.stderr)
        for i in matching:
            print(f"  [{i.code}] {i.entity}: {i.msg}", file=sys.stderr)


def _classifier(args) -> LayerClassifier:
    """Manual map, or the LLM behind the cache.

    Raises LLMUnavailable, which main() maps to EXIT_LLM.
    """
    if args.rules and not args.walls:
        return RuleClassifier()
    if args.walls:
        return StubClassifier({name: (Role.WALL_STRUCTURAL,
                                      MANUAL_WALL_CONFIDENCE)
                               for name in args.walls})

    cfg = config_from_env(provider=args.provider, model=args.model,
                          timeout=args.timeout)
    inner = LLMLayerClassifier(build_client(cfg), max_tokens=_max_tokens(args))
    return CachingClassifier(inner, model=cfg.model, provider=cfg.provider,
                             base_url=cfg.base_url, enabled=not args.no_cache)


def _max_tokens(args) -> int:
    """--max-tokens wins; then ARCHIAGENT_MAX_TOKENS; then the default.

    A reply truncated by this cap is a hard error with no partial result,
    so the knob has to be reachable from the command line: the right value
    scales with the drawing's layer count, which we cannot know in advance.
    """
    if getattr(args, "max_tokens", None):
        return int(args.max_tokens)
    env = os.environ.get("ARCHIAGENT_MAX_TOKENS", "").strip()
    if env:
        try:
            n = int(env)
        except ValueError:
            return MAX_TOKENS
        if n > 0:
            return n
    return MAX_TOKENS


def _vision_enabled(no_vision_flag: bool) -> bool:
    """Vision is ON BY DEFAULT. --no_vision forces it off; so does
    ARCHIAGENT_VISION=0. The explicit flag wins over the environment
    variable.

    A module-level function, not folded into arg parsing, because tests
    call it directly rather than driving it through argv.
    """
    if no_vision_flag:
        return False
    return os.environ.get("ARCHIAGENT_VISION", "1") != "0"


def _dxf_classifier(args, dxf_path: str, on_issue=None) -> LayerClassifier:
    """Manual map, or the DXF two-stage classifier behind the cache.

    Mirrors `_classifier`, swapping LLMLayerClassifier (text-only, PDF
    prompt) for DxfLayerClassifier (DXF prompt, optional vision escalation).
    `on_issue` is threaded straight through to DxfLayerClassifier so
    escalation diagnostics (layer_escalation_skipped, layer_role_revised)
    reach the caller instead of being silently discarded.
    Raises LLMUnavailable, which main() maps to EXIT_LLM.
    """
    if args.rules and not args.walls:
        return RuleClassifier()
    if args.walls:
        return StubClassifier({name: (Role.WALL_STRUCTURAL,
                                      MANUAL_WALL_CONFIDENCE)
                               for name in args.walls})

    cfg = config_from_env(provider=args.provider, model=args.model,
                          timeout=args.timeout)
    inner = DxfLayerClassifier(build_client(cfg), dxf_path,
                               vision=_vision_enabled(args.no_vision),
                               max_tokens=_max_tokens(args),
                               cap=args.maxEscalation,
                               on_issue=on_issue)
    return CachingClassifier(inner, model=cfg.model, provider=cfg.provider,
                             base_url=cfg.base_url, enabled=not args.no_cache)


class _PrecomputedClassifier:
    """Adapts an already-computed Classification to the LayerClassifier
    protocol.

    extract_from_dxf takes a PrimitiveSet and a classifier, not a dxf_path,
    so it cannot call build_dxf_inventory itself -- only build_inventory(ps),
    which is blind to the DXF-only features (entity_mix, lineweight, ...)
    that DxfLayerClassifier's escalation trigger depends on. The real
    classification is therefore computed here in main(), against the SAME
    build_dxf_inventory inventory already used to validate --walls, and
    handed to extract_from_dxf wrapped so its own internal (unused) stats
    rebuild can never disagree with it.
    """

    def __init__(self, classification: Classification) -> None:
        self._classification = classification

    def classify(self, stats) -> Classification:
        return self._classification


def _unmatched_wall_names(names: list[str], stats) -> list[str]:
    known = {s.name for s in stats}
    return [n for n in names if n not in known]


def _validate_options(args) -> None:
    for name in ("height", "units_per_foot", "timeout"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"--{name.replace('_', '-')} must be finite and positive")
    if args.elevation is not None and not math.isfinite(args.elevation):
        raise ValueError("--elevation must be finite")
    if args.page < 0 or args.maxEscalation < 0:
        raise ValueError("--page and --maxEscalation must be nonnegative")
    if args.max_tokens is not None and args.max_tokens <= 0:
        raise ValueError("--max-tokens must be positive")
    if args.region and args.regions_file:
        raise ValueError("use either --region or --regions-file")
    if args.region:
        PlanRegion("selected-plan", tuple(args.region))
    if not args.storey_name.strip():
        raise ValueError("--storey-name must not be empty")
    if args.freeze_only and (args.replay_manifest or args.blender_package):
        raise ValueError("--freeze-only cannot be combined with replay or Blender export")
    if (args.freeze_only or args.replay_manifest or args.blender_package) and (args.inspect or args.classify_only or args.list_regions):
        raise ValueError("interpretation/export flags cannot be combined with inspection-only modes")


def _configuration(call, label):
    """Translate malformed user JSON shapes without hiding pipeline bugs."""
    try:
        return call()
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def _review_document(path, source_sha256, region_ids):
    review = json.loads(Path(path).read_text()) if path else {}
    if not isinstance(review, dict):
        raise ValueError("review file must contain an object")

    def point(value, label):
        if (not isinstance(value, (list, tuple)) or len(value) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                    not math.isfinite(v) for v in value)):
            raise ValueError(f"{label} requires two finite numeric coordinates")

    def ring(value, label):
        if not isinstance(value, list) or len(value) < 4:
            raise ValueError(f"{label} requires a closed ring of at least four points")
        for p in value:
            point(p, label)
        if value[0] != value[-1]:
            raise ValueError(f"{label} must be explicitly closed")

    def document(record, label):
        if not isinstance(record, dict):
            raise ValueError(f"{label} must contain an object")
        if "symbols_verified" in record and not isinstance(record["symbols_verified"], bool):
            raise ValueError(f"{label}.symbols_verified must be a boolean confirming symbol coverage and interpretation review")
        if "footprint_verified" in record and not isinstance(record["footprint_verified"], bool):
            raise ValueError(f"{label}.footprint_verified must be a boolean")
        if "source_sha256" in record:
            supplied = record["source_sha256"]
            if (not isinstance(supplied, str) or len(supplied) != 64 or
                    supplied.lower() != source_sha256.lower()):
                raise ValueError(f"{label} source_sha256 does not match the input drawing")
        for field in ("symbols", "templates", "footprints", "voids", "wall_profiles"):
            if field in record and not isinstance(record[field], list):
                raise ValueError(f"{label}.{field} must be a list")
        if any(not isinstance(t, dict) for t in record.get("templates", [])):
            raise ValueError("templates entries must be objects")
        if "wall_profiles" in record:
            from archiagent.geometry.profiles import reviewed_wall_profiles
            reviewed_wall_profiles(record["wall_profiles"])
        for symbol in record.get("symbols", []):
            if not isinstance(symbol, dict):
                raise ValueError(f"{label}.symbols entries must be objects")
            unknown_fields = symbol.keys() - {field.name for field in fields(SymbolInstance)}
            if unknown_fields:
                raise ValueError(f"unknown symbol fields: {', '.join(sorted(unknown_fields))}")
            required = {"id", "kind", "position", "width_ft", "depth_ft"}
            if required - symbol.keys():
                raise ValueError(f"symbol missing fields: {', '.join(sorted(required - symbol.keys()))}")
            point(symbol["position"], "symbol.position")
            for field in ("id", "kind", "subtype", "evidence"):
                if field in symbol and (not isinstance(symbol[field], str) or not symbol[field]):
                    raise ValueError(f"symbol.{field} must be a nonempty string")
            for field in ("width_ft", "depth_ft", "rotation_rad", "confidence", "height_ft"):
                if field not in symbol or (field == "height_ft" and symbol[field] is None):
                    continue
                value = symbol[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"symbol.{field} must be a finite number")
            if "boundary" in symbol and not isinstance(symbol["boundary"], list):
                raise ValueError("symbol.boundary must be a list of coordinates")
            if symbol.get("boundary"):
                ring(symbol["boundary"], "symbol.boundary")
            if "source_ids" in symbol and (not isinstance(symbol["source_ids"], list) or
                    any(not isinstance(v, str) for v in symbol["source_ids"])):
                raise ValueError("symbol.source_ids must be a list of strings")
            if "properties" in symbol and (not isinstance(symbol["properties"], list) or
                    any(not isinstance(pair, list) or len(pair) != 2 or
                        any(not isinstance(v, str) for v in pair) for pair in symbol["properties"])):
                raise ValueError("symbol.properties must be a list of string pairs")
        for footprint in record.get("footprints", []):
            if not isinstance(footprint, dict) or "boundary" not in footprint:
                raise ValueError("footprint requires an object with boundary")
            ring(footprint["boundary"], "footprint.boundary")
            if not isinstance(footprint.get("holes", []), list):
                raise ValueError("footprint.holes must be a list")
            for hole in footprint.get("holes", []):
                ring(hole, "footprint.hole")
        for void in record.get("voids", []):
            ring(void, "void")

    document(review, "review")
    if "regions" in review:
        if not isinstance(review["regions"], dict):
            raise ValueError("review.regions must be an object keyed by region ID")
        if not region_ids:
            raise ValueError("review.regions requires --region or --regions-file")
        unknown = set(review["regions"]) - set(region_ids)
        if unknown:
            raise ValueError(f"review names unknown regions: {', '.join(sorted(unknown))}")
        for key, record in review["regions"].items():
            document(record, f"review.regions.{key}")
    return review


def _whole_drawing_storey(model, name, elevation):
    previous_checks = set(validate(model))
    other_issues = tuple(i for i in model.issues if i not in previous_checks)
    model = replace(model, storey_name=name, elevation_ft=elevation, region_id="whole-drawing")
    return replace(model, issues=validate(model) + other_issues)


def _blender_package(out):
    from archiagent.blender.bridge import export_blender_package
    package = export_blender_package(out, Path(out).with_suffix(".blender"))
    print(f"Blender package {package['blender_script_path']}")
    print("The package contains a build script; run it in Blender to create the .blend file.")
    return package


def _replay(args, input_path, out_path):
    """Author only the frozen geometry: never re-read CAD or invoke a classifier."""
    from archiagent.interpretation import read_manifest, implementation_versions
    from dataclasses import asdict
    models, manifest = read_manifest(args.replay_manifest, input_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    current = implementation_versions()
    changes = {k: {"recorded": value, "current": current.get(k)}
               for k, value in manifest.get("versions", {}).items() if current.get(k) != value}
    report_path = out_path.with_suffix(".report.json")
    report = {"schema_version": 1, "mode": "frozen-replay", "interpretation_sha256": manifest["content_sha256"],
              "version_changes": changes, "source_accuracy": manifest.get("accuracy"),
              "ifc_validation": "not executed", "regions": [
                  {"status": "draft" if any(i.severity == "error" for i in m.issues) else "checks-passed",
                   "model": asdict(m)} for m in models]}
    with report_path.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    with out_path.with_suffix(".interpretation.json").open("x") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True, allow_nan=False)
    if changes:
        print("warning: implementation versions differ from the snapshot; geometry is frozen, current export checks will run", file=sys.stderr)
    if args.require_accepted and any(i.severity == "error" for m in models for i in m.issues):
        print("error: frozen interpretation does not pass current acceptance checks; IFC not authored", file=sys.stderr)
        return EXIT_PIPELINE
    out = author_ifc(models[0], out_path) if len(models) == 1 else author_building(models, out_path)
    result = validate_export(out, models)
    record_export_validation(report_path, result)
    if not result["passed"]:
        print("error: replayed IFC failed export validation; inspect the report", file=sys.stderr)
        return EXIT_PIPELINE
    if args.blender_package:
        _blender_package(out)
    print(f"wrote {out} from frozen interpretation; recognition and OCR were not rerun")
    return EXIT_PIPELINE if any(i.code == "scale_gate_failed" for m in models for i in m.issues) else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse raises SystemExit(0) for -h/--help. That is a SUCCESSFUL
        # help request, not bad usage -- reporting 3 breaks any
        # `archiagent --help` install check.
        return EXIT_OK if not e.code else EXIT_USAGE

    if args.print_interpretation_prompt:
        from archiagent.classify.interpretation_prompt import INTERPRETATION_SYSTEM_PROMPT
        print(INTERPRETATION_SYSTEM_PROMPT)
        return EXIT_OK

    try:
        _validate_options(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.replay_manifest:
        incompatible = {"--height", "--walls", "--rules", "--provider", "--model", "--units-per-foot",
                        "--region", "--regions-file", "--storey-name", "--elevation", "--measurements",
                        "--review-file", "--reference-file", "--ocr", "--workbench", "--page"}
        specified = {arg.split("=", 1)[0] for arg in argv}
        if conflicts := sorted(specified & incompatible):
            print(f"error: replay cannot reinterpret frozen decisions with {', '.join(conflicts)}", file=sys.stderr)
            return EXIT_USAGE

    # Exactly one of --pdfFilePath / --dxfFilePath. Both are ordinary named
    # flags, so there is no positional-matching ambiguity for argparse to
    # get wrong (see the module docstring for the bug this replaced).
    # Neither and both are both EXIT_USAGE, and the message names both
    # options so the user sees the choice either way.
    have_pdf = bool(args.pdfFilePath)
    have_dxf = bool(args.dxfFilePath)
    if have_pdf == have_dxf:
        print("error: pass exactly one of --pdfFilePath or --dxfFilePath",
              file=sys.stderr)
        return EXIT_USAGE
    is_dxf = have_dxf
    input_path = args.dxfFilePath if is_dxf else args.pdfFilePath

    authoring = not (args.inspect or args.classify_only or args.list_regions)
    out_path: Path | None = None
    if authoring:
        if not args.outputDir:
            print("error: --outputDir is required unless --inspect or "
                  "--classify-only is given", file=sys.stderr)
            return EXIT_USAGE
        # The filename is derived from the INPUT's own stem, never taken
        # from a flag the caller could point anywhere -- that is the
        # structural fix for the overwrite bug: the tool names its own
        # output, and can only write inside a directory the caller chose.
        out_path = Path(args.outputDir) / f"{Path(input_path).stem}.ifc"
        if any(p.exists() for p in (out_path, out_path.with_suffix(".report.json"), out_path.with_suffix(".overlay.svg"),
                                   out_path.with_suffix(".interpretation.json"), out_path.with_suffix(".blender"))):
            print(f"error: {out_path} already exists; refusing to "
                  "overwrite it", file=sys.stderr)
            print("hint: choose a different --outputDir, or remove the "
                  "existing file first.", file=sys.stderr)
            return EXIT_USAGE

    if args.replay_manifest:
        try:
            return _replay(args, input_path, out_path)
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"error: cannot replay interpretation: {exc}", file=sys.stderr)
            return EXIT_PIPELINE

    # The classifier is built BEFORE the drawing is read, so a bad provider
    # or a missing key fails fast with EXIT_LLM instead of after a slow
    # parse. classifier_issues collects DxfLayerClassifier's escalation
    # diagnostics (layer_escalation_skipped, layer_role_revised) so they
    # can be printed alongside the run's other issues instead of vanishing
    # -- BuildingModel.issues comes entirely from validate(model), which has
    # no side-channel for issues raised during classification.
    classifier_issues: list[Issue] = []
    classifier: LayerClassifier | None = None
    if not (args.inspect or args.list_regions):
        try:
            classifier = (_dxf_classifier(args, input_path,
                                          on_issue=classifier_issues.append)
                         if is_dxf else _classifier(args))
        except LLMUnavailable as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_LLM

    try:
        # ONE load per run, for every mode. The inventory is needed before
        # classification so --walls can be checked against the real layer
        # names, and the load is by far the most expensive step.
        if is_dxf:
            ps, units_per_foot = load_dxf(input_path,
                                          units_per_foot=args.units_per_foot)
            stats = build_dxf_inventory(input_path, ps)
        else:
            ps = load_pdf(input_path, page=args.page)
            stats = build_inventory(ps)

        if args.list_regions:
            from dataclasses import asdict
            if not is_dxf and args.units_per_foot is None:
                raise ValueError("PDF region proposals require --units-per-foot")
            proposals = propose_regions(ps, units_per_foot if is_dxf else args.units_per_foot)
            print(json.dumps([asdict(r) for r in proposals], indent=2))
            return EXIT_OK
        if args.inspect:
            _print_inventory(stats)
            return EXIT_OK

        # A --walls name is an ASSERTION about the drawing. If it matches no
        # layer, StubClassifier silently drops it -- and a mistyped name in a
        # list of correct ones yields a complete IFC, at exit 0, built from
        # half the wall network and scaled off the wrong candidate runs.
        # That is a silent wrong answer, so it stops the run.
        if args.walls:
            unmatched = _unmatched_wall_names(args.walls, stats)
            if unmatched:
                print("error: --walls named "
                      f"{len(unmatched)} layer(s) that are not in "
                      f"{input_path}: {', '.join(repr(n) for n in unmatched)}",
                      file=sys.stderr)
                # Real drawing filenames contain spaces, so quote the path:
                # the hint has to be runnable as printed.
                flag = "--dxfFilePath" if is_dxf else "--pdfFilePath"
                print(f"hint: run `python -m archiagent {flag} "
                      f"{shlex.quote(input_path)} --inspect` to see the "
                      "drawing's real layer names (they are case-sensitive).",
                      file=sys.stderr)
                return EXIT_USAGE

        if args.classify_only:
            _print_decisions(classifier.classify(stats))
            if args.verbose and classifier_issues:
                _print_issues(tuple(classifier_issues))
            return EXIT_OK

        regions = _configuration(lambda: load_regions(args.regions_file), "regions file") if args.regions_file else (
            (PlanRegion("selected-plan",tuple(args.region),"plan",args.storey_name,args.elevation,evidence="user-window"),)
            if args.region else (None,))
        for region in regions:
            if region is not None:
                if region.kind != "plan":
                    raise ValueError(f"region {region.id!r} has kind={region.kind!r}; model generation requires "
                                     "reviewed kind='plan'. Remove details/elevations and review spatial proposals first")
                if (len(region.origin) != 2 or not all(not isinstance(v, bool) and
                        isinstance(v, (int, float)) and math.isfinite(v) for v in region.origin)):
                    raise ValueError("region origin requires two finite numeric coordinates")
                if not isinstance(region.name, str) or not region.name.strip():
                    raise ValueError("region name must be a nonempty string")
        if args.reference_file and len(regions) != 1:
            raise ValueError("--reference-file scores one selected plan; run each region independently")
        review_all = _review_document(args.review_file, ps.source_sha256,
                                      [r.id for r in regions if r is not None])
        models=[];sources=[]
        for plan_index, region in enumerate(regions, 1):
            source = select_region(ps,region) if region else ps
            region_scale = region.units_per_foot if region is not None else None
            selected_scale = region_scale if region_scale is not None else (
                units_per_foot if is_dxf else args.units_per_foot)
            if args.ocr:
                if selected_scale is None:
                    raise ValueError("--ocr requires explicit source units for a vector PDF")
                from archiagent.ingest.ocr_evidence import enrich_source_with_ocr
                source = enrich_source_with_ocr(source, selected_scale,
                    out_path.with_name(f"{out_path.stem}.{plan_index}.ocr"),
                    Path(__file__).resolve().parents[1] / ".test-tmp" / "swift-cache")
            region_stats = build_inventory(source) if region else stats
            classification = classifier.classify(region_stats)
            selected_classifier = _PrecomputedClassifier(classification)
            measurements = _configuration(lambda: load_measurements(args.measurements, region),
                                          "measurements file") if args.measurements else ()
            review = review_all.get("regions",{}).get(region.id,{}) if region and "regions" in review_all else review_all
            kwargs = dict(wall_height_ft=args.height,region=region,measurements=measurements,review=review)
            if is_dxf:
                model = extract_from_dxf(source,selected_classifier,units_per_foot=selected_scale,**kwargs)
            else:
                model = extract_from_primitives(source,selected_classifier,units_per_foot=selected_scale,stats=region_stats,**kwargs)
            if region is None:
                model = _whole_drawing_storey(model, args.storey_name, args.elevation)
            models.append(model);sources.append(source)
        reference_evaluation = None
        if args.reference_file:
            reference = load_reference(args.reference_file, sources[0], regions[0])
            reference_evaluation = evaluate_reference(models[0], reference)
        try:
            reports = write_review(models,sources,out_path)
            print(f"review {reports[0]}")
            print(f"overlay {reports[1]}")
            if reference_evaluation is not None:
                from archiagent.reporting import record_reference_evaluation
                record_reference_evaluation(reports[0], reference_evaluation)
                print("reference evaluation saved; scoped scores do not imply whole-plan acceptance")
            if args.workbench:
                from archiagent.review_workbench import write_workbench
                for index, (plan_model, plan_source) in enumerate(zip(models, sources), 1):
                    workbench_path = out_path.with_name(f"{out_path.stem}.{index}.review.html")
                    write_workbench(plan_model, plan_source, workbench_path)
                    print(f"workbench {workbench_path}")
            from archiagent.interpretation import save_manifest
            interpretation = save_manifest(models, out_path.with_suffix(".interpretation.json"),
                inputs={"review": review_all, "height_ft": args.height,
                        "ocr_enabled": args.ocr, "page": args.page,
                        "layer_mode": "explicit" if args.walls else "rules" if args.rules else "provider"})
            print(f"interpretation {interpretation}")
            if args.freeze_only:
                rejected = args.require_accepted and any(i.severity == "error" for m in models for i in m.issues)
                print("frozen interpretation saved; IFC not authored; assumptions and unresolved issues are retained")
                return EXIT_PIPELINE if rejected else EXIT_OK
            if args.require_accepted and any(i.severity=="error" for m in models for i in m.issues):
                print("error: acceptance checks failed; draft evidence saved, IFC not authored",file=sys.stderr)
                return EXIT_PIPELINE
            out = author_ifc(models[0],out_path) if len(models)==1 else author_building(models,out_path)
            export_validation = validate_export(out, models)
            record_export_validation(reports[0], export_validation)
            if not export_validation["passed"]:
                print("error: exported IFC validation failed; inspect report before using this draft", file=sys.stderr)
                return EXIT_PIPELINE
            if args.blender_package:
                _blender_package(out)
        except (OSError, RuntimeError) as e:
            print(f"error: could not write {out_path}: {e}",file=sys.stderr)
            return EXIT_PIPELINE
    except (LLMUnavailable, LLMSchemaError) as e:
        # Both subclass RuntimeError, so this must precede the RuntimeError
        # catch below.
        print(f"error: {e}", file=sys.stderr)
        return EXIT_LLM
    except ScaleGateError as e:
        # ScaleGateError's own message ("no dimensions or no candidate runs
        # to match") never says "scale", and the user needs to know WHICH
        # gate stopped them -- not to suspect the classifier. Unreachable on
        # the DXF path, which skips resolve_scale entirely.
        print(f"error: scale gate (R1) failed: {e}", file=sys.stderr)
        print("hint: provide associated reviewed dimensions with --measurements "
              "and calibrated --units-per-foot; optional --ocr can recover outlined "
              "text on supported systems, but its associations still need checking.",
              file=sys.stderr)
        return EXIT_PIPELINE
    except NoLayersError as e:
        print(f"error: {input_path} has no CAD layers (PDF optional content "
              f"groups): {e}", file=sys.stderr)
        return EXIT_PIPELINE
    except DxfUnitsError as e:
        # A units problem is something the user can fix with a flag, not a
        # broken drawing -- EXIT_USAGE, not EXIT_PIPELINE. Must precede the
        # (OSError, RuntimeError) catch below: DxfUnitsError subclasses
        # RuntimeError.
        print(f"error: {e}", file=sys.stderr)
        print("hint: pass --units-per-foot to specify the drawing's units "
              "(12 for inches, 1 for feet, 304.8 for mm).", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_PIPELINE
    except (OSError, RuntimeError) as e:
        print(f"error: could not read {input_path}: {e}", file=sys.stderr)
        return EXIT_PIPELINE

    all_issues = tuple(i for m in models for i in m.issues) + tuple(classifier_issues)
    counts = collections.Counter(i.severity for i in all_issues)
    print(f"wrote {out}")
    for model in models:
        label = f" [{model.region_id or model.storey_name}]" if len(models) > 1 else ""
        print(f"  scale{label}   {model.scale.units_per_foot:.4f} units/ft, "
              f"verification: {'verified' if model.scale_verified else 'UNVERIFIED'}")
    print(f"  walls   {sum(len(m.walls) for m in models)}")
    if any(m.wall_profiles for m in models):
        print(f"  wall profiles {sum(len(m.wall_profiles) for m in models)} (covered wall runs share profile solids)")
    print(f"  spaces  {sum(len(m.spaces) for m in models)}")
    print(f"  symbols {sum(len(m.symbols) for m in models)}, openings {sum(len(m.openings) for m in models)}")
    print(f"  status  {'DRAFT' if counts['error'] else 'checks-passed'}")
    print(f"  issues  {counts['error']} error, {counts['warn']} warn"
          f"{'' if args.verbose else '  (-v to list)'}")

    if args.verbose:
        _print_issues(all_issues)

    # Draft exports remain successful unless an explicit scale gate fails.
    # --require-accepted already refuses authoring for any acceptance error.
    gate = [(m, i) for m in models for i in m.issues if i.code == "scale_gate_failed"]
    for model, issue in gate:
        print(f"error: scale gate (R1) failed [{model.region_id or model.storey_name}]: {issue.msg}",
              file=sys.stderr)
    return EXIT_PIPELINE if gate else EXIT_OK
