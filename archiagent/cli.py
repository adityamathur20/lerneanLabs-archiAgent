"""`python -m archiagent plan.pdf out.ifc` -- the tool surface.

Three modes:
  --inspect        print the layer inventory and stop. No LLM, no output file.
  --classify-only  classify and print the roles. No output file.
  (default)        classify, build, author the IFC.

--inspect and --walls exist so the tool stays usable with no credentials and
no network. --inspect is also how you find out what a drawing contains
before spending a call on it.
"""

from __future__ import annotations

import argparse
import collections
import os
import shlex
import sys

from archiagent.classify.cache import CachingClassifier
from archiagent.classify.dxf_classifier import DxfLayerClassifier
from archiagent.classify.dxf_inventory import build_dxf_inventory
from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import (Classification, LayerClassifier,
                                        StubClassifier)
from archiagent.classify.llm_classifier import LLMLayerClassifier
from archiagent.classify.roles import Role
from archiagent.ifc.author import author_ifc
from archiagent.ingest.dxf_vector import DxfUnitsError, load_dxf
from archiagent.ingest.pdf_vector import NoLayersError, load_pdf
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.config import build_client, config_from_env
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
        description="Turn a layered 2D floorplan PDF into a to-scale IFC4 model.")
    # nargs="?": exactly one of PDF or --dxfFilePath is required, but that
    # is a relationship between two arguments argparse cannot express by
    # itself (a mutually-exclusive group can't also let OUT_IFC stay
    # optional the way it needs to), so both are individually optional here
    # and main() validates the "exactly one" rule itself.
    p.add_argument("pdf", metavar="PDF", nargs="?", default=None,
                   help="input floorplan PDF (mutually exclusive with "
                        "--dxfFilePath)")
    p.add_argument("out_ifc", metavar="OUT_IFC", nargs="?",
                   help="output .ifc path (not needed with --inspect or "
                        "--classify-only)")
    p.add_argument("--dxfFilePath", dest="dxfFilePath", metavar="PATH",
                   default=None,
                   help="input floorplan DXF (mutually exclusive with PDF)")
    p.add_argument("--page", type=int, default=0, help="page index (default 0)")
    p.add_argument("--height", type=float, default=10.0,
                   metavar="FT", help="wall height in feet (default 10.0)")
    p.add_argument("--walls", nargs="+", metavar="NAME", default=None,
                   help="skip the LLM; treat these layers as walls")
    p.add_argument("--provider", default=None,
                   help="override ARCHIAGENT_LLM_PROVIDER")
    p.add_argument("--model", default=None,
                   help="override ARCHIAGENT_LLM_MODEL")
    p.add_argument("--vision", action="store_true",
                   help="DXF only: allow stage 2 (image) escalation; "
                        "otherwise ARCHIAGENT_VISION decides")
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
    inner = LLMLayerClassifier(build_client(cfg))
    return CachingClassifier(inner, model=cfg.model, provider=cfg.provider,
                             base_url=cfg.base_url, enabled=not args.no_cache)


def _vision_enabled(vision_flag: bool) -> bool:
    """The explicit --vision flag wins; otherwise ARCHIAGENT_VISION decides.

    A module-level function, not folded into arg parsing, because tests
    call it directly rather than driving it through argv.
    """
    if vision_flag:
        return True
    return os.environ.get("ARCHIAGENT_VISION", "") not in ("", "0")


def _dxf_classifier(args, dxf_path: str) -> LayerClassifier:
    """Manual map, or the DXF two-stage classifier behind the cache.

    Mirrors `_classifier`, swapping LLMLayerClassifier (text-only, PDF
    prompt) for DxfLayerClassifier (DXF prompt, optional vision escalation).
    Raises LLMUnavailable, which main() maps to EXIT_LLM.
    """
    if args.walls:
        return StubClassifier({name: (Role.WALL_STRUCTURAL,
                                      MANUAL_WALL_CONFIDENCE)
                               for name in args.walls})

    cfg = config_from_env(provider=args.provider, model=args.model)
    inner = DxfLayerClassifier(build_client(cfg), dxf_path,
                               vision=_vision_enabled(args.vision))
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

    # argparse fills two nargs="?" positionals greedily left-to-right: a
    # single leftover positional token lands in `pdf` first, even when
    # --dxfFilePath was given and `pdf` was never meant to receive
    # anything -- that token was OUT_IFC, typed positionally alongside
    # --dxfFilePath. Recognize exactly that shape and shift it over before
    # the exclusivity check below can misread it as "both PDF and
    # --dxfFilePath given".
    if args.dxfFilePath and args.pdf and not args.out_ifc:
        args.out_ifc = args.pdf
        args.pdf = None

    # Exactly one of {positional PDF, --dxfFilePath}. A mutually-exclusive
    # group can't express this: OUT_IFC's own optionality (nargs="?") needs
    # PDF to stay nargs="?" too, so the "exactly one" rule is checked here
    # instead. Neither and both are both EXIT_USAGE, and the message names
    # both options so the user sees the choice either way.
    have_pdf = bool(args.pdf)
    have_dxf = bool(args.dxfFilePath)
    if have_pdf == have_dxf:
        print("error: pass exactly one of PDF or --dxfFilePath",
              file=sys.stderr)
        return EXIT_USAGE
    is_dxf = have_dxf
    input_path = args.dxfFilePath if is_dxf else args.pdf

    authoring = not (args.inspect or args.classify_only)
    if authoring and not args.out_ifc:
        print("error: OUT_IFC is required unless --inspect or "
              "--classify-only is given", file=sys.stderr)
        if args.walls:
            # --walls is nargs="+" and OUT_IFC is nargs="?", so if OUT_IFC
            # comes AFTER --walls on the command line, argparse resolves
            # OUT_IFC to nothing and --walls eats everything that follows
            # it -- including what was meant as the output path. Name that
            # explicitly rather than leaving the user staring at a command
            # line where they plainly did supply OUT_IFC.
            print("note: --walls consumes every argument after it, so "
                  "OUT_IFC must come first.", file=sys.stderr)
            last = args.walls[-1]
            if last.endswith(".ifc"):
                remaining = " ".join(args.walls[:-1])
                print(f"      {last!r} was read as a layer name -- did "
                      f"you mean:", file=sys.stderr)
                suggestion = f"python -m archiagent PDF {last}"
                if remaining:
                    suggestion += f" --walls {remaining}"
                print(f"      {suggestion}", file=sys.stderr)
        return EXIT_USAGE

    # The classifier is built BEFORE the drawing is read, so a bad provider
    # or a missing key fails fast with EXIT_LLM instead of after a slow
    # parse.
    classifier: LayerClassifier | None = None
    if not args.inspect:
        try:
            classifier = (_dxf_classifier(args, input_path) if is_dxf
                         else _classifier(args))
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
                inspect_cmd = (f"--dxfFilePath {shlex.quote(input_path)}"
                               if is_dxf else shlex.quote(input_path))
                print(f"hint: run `python -m archiagent {inspect_cmd} "
                      "--inspect` to see the drawing's real layer names "
                      "(they are case-sensitive).", file=sys.stderr)
                return EXIT_USAGE

        if args.classify_only:
            _print_decisions(classifier.classify(stats))
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
            out = author_ifc(model, args.out_ifc)
        except (OSError, RuntimeError) as e:
            print(f"error: could not write {args.out_ifc}: {e}",
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

    counts = collections.Counter(i.severity for i in model.issues)
    print(f"wrote {out}")
    print(f"  scale   {model.scale.units_per_foot:.4f} units/ft, "
          f"max residual {model.scale.max_residual_in:.3f}in")
    print(f"  walls   {len(model.walls)}")
    print(f"  spaces  {len(model.spaces)}")
    print(f"  issues  {counts['error']} error, {counts['warn']} warn"
          f"{'' if args.verbose else '  (-v to list)'}")

    if args.verbose:
        _print_issues(model.issues)

    # Exit status tracks the R1 gate ALONE. A real drawing routinely carries
    # dozens of unresolved_junction errors -- the demolition plan has 42 --
    # and the IFC is written and usable regardless. Failing the exit code on
    # those would make a successful run indistinguishable from a broken one.
    gate = [i for i in model.issues if i.code == "scale_gate_failed"]
    for i in gate:
        print(f"error: scale gate (R1) failed: {i.msg}", file=sys.stderr)
    return EXIT_PIPELINE if gate else EXIT_OK
