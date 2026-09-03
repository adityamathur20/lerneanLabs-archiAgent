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
import os
import shlex
import sys
from pathlib import Path

from archiagent.classify.cache import CachingClassifier
from archiagent.classify.dxf_classifier import DxfLayerClassifier
from archiagent.classify.dxf_inventory import build_dxf_inventory
from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import (Classification, LayerClassifier,
                                        StubClassifier)
from archiagent.classify.llm_classifier import MAX_TOKENS, LLMLayerClassifier
from archiagent.classify.roles import Role
from archiagent.ifc.author import author_ifc
from archiagent.ingest.dxf_vector import DxfUnitsError, load_dxf
from archiagent.ingest.pdf_vector import NoLayersError, load_pdf
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.config import build_client, config_from_env
from archiagent.model import Issue
from archiagent.pipeline import extract_from_dxf, extract_from_primitives
from archiagent.scale.resolve import ScaleGateError

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
    p.add_argument("--units-per-foot", type=float, default=None,
                   metavar="FLOAT",
                   help="DXF only: override the drawing's declared units "
                        "(12 for inches, 1 for feet, 304.8 for mm)")
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
    if args.walls:
        return StubClassifier({name: (Role.WALL_STRUCTURAL,
                                      MANUAL_WALL_CONFIDENCE)
                               for name in args.walls})

    cfg = config_from_env(provider=args.provider, model=args.model)
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
    if args.walls:
        return StubClassifier({name: (Role.WALL_STRUCTURAL,
                                      MANUAL_WALL_CONFIDENCE)
                               for name in args.walls})

    cfg = config_from_env(provider=args.provider, model=args.model)
    inner = DxfLayerClassifier(build_client(cfg), dxf_path,
                               vision=_vision_enabled(args.no_vision),
                               max_tokens=_max_tokens(args),
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

    authoring = not (args.inspect or args.classify_only)
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
        if out_path.exists():
            print(f"error: {out_path} already exists; refusing to "
                  "overwrite it", file=sys.stderr)
            print("hint: choose a different --outputDir, or remove the "
                  "existing file first.", file=sys.stderr)
            return EXIT_USAGE

    # The classifier is built BEFORE the drawing is read, so a bad provider
    # or a missing key fails fast with EXIT_LLM instead of after a slow
    # parse. classifier_issues collects DxfLayerClassifier's escalation
    # diagnostics (layer_escalation_skipped, layer_role_revised) so they
    # can be printed alongside the run's other issues instead of vanishing
    # -- BuildingModel.issues comes entirely from validate(model), which has
    # no side-channel for issues raised during classification.
    classifier_issues: list[Issue] = []
    classifier: LayerClassifier | None = None
    if not args.inspect:
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

        if is_dxf:
            # See _PrecomputedClassifier: the classification is computed
            # HERE, against the build_dxf_inventory stats already used to
            # validate --walls above, and handed to extract_from_dxf
            # pre-decided so its own internal (DXF-blind) stats rebuild
            # never gets a chance to disagree.
            classification = classifier.classify(stats)
            precomputed = _PrecomputedClassifier(classification)
            model = extract_from_dxf(ps, precomputed,
                                     units_per_foot=units_per_foot,
                                     wall_height_ft=args.height)
        else:
            model = extract_from_primitives(ps, classifier,
                                            wall_height_ft=args.height,
                                            stats=stats)
        # B1: authoring is inside the try so an unwritable output path
        # produces the same clean `error: ...` as every other failure. Its
        # own handler is nested because the outer one blames the INPUT
        # path, and a failed write is not a failed read.
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out = author_ifc(model, out_path)
        except (OSError, RuntimeError) as e:
            print(f"error: could not write {out_path}: {e}",
                  file=sys.stderr)
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
        print("hint: this drawing may have no live text -- dimensions "
              "converted to vector outlines cannot be read yet.",
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

    all_issues = model.issues + tuple(classifier_issues)
    counts = collections.Counter(i.severity for i in all_issues)
    print(f"wrote {out}")
    print(f"  scale   {model.scale.units_per_foot:.4f} units/ft, "
          f"max residual {model.scale.max_residual_in:.3f}in")
    print(f"  walls   {len(model.walls)}")
    print(f"  spaces  {len(model.spaces)}")
    print(f"  issues  {counts['error']} error, {counts['warn']} warn"
          f"{'' if args.verbose else '  (-v to list)'}")

    if args.verbose:
        _print_issues(all_issues)

    # Exit status tracks the R1 gate ALONE. A real drawing routinely carries
    # dozens of unresolved_junction errors -- the demolition plan has 42 --
    # and the IFC is written and usable regardless. Failing the exit code on
    # those would make a successful run indistinguishable from a broken one.
    gate = [i for i in model.issues if i.code == "scale_gate_failed"]
    for i in gate:
        print(f"error: scale gate (R1) failed: {i.msg}", file=sys.stderr)
    return EXIT_PIPELINE if gate else EXIT_OK
