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
import sys

from archiagent.classify.cache import CachingClassifier
from archiagent.classify.inventory import build_inventory
from archiagent.classify.layers import LayerClassifier, StubClassifier
from archiagent.classify.llm_classifier import LLMLayerClassifier
from archiagent.classify.roles import Role
from archiagent.ifc.author import author_ifc
from archiagent.ingest.pdf_vector import NoLayersError, load_pdf
from archiagent.llm.client import LLMSchemaError, LLMUnavailable
from archiagent.llm.config import build_client, config_from_env
from archiagent.pipeline import extract
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
    p.add_argument("pdf", metavar="PDF", help="input floorplan PDF")
    p.add_argument("out_ifc", metavar="OUT_IFC", nargs="?",
                   help="output .ifc path (not needed with --inspect or "
                        "--classify-only)")
    p.add_argument("--page", type=int, default=0, help="page index (default 0)")
    p.add_argument("--height", type=float, default=10.0,
                   metavar="FT", help="wall height in feet (default 10.0)")
    p.add_argument("--walls", nargs="+", metavar="NAME", default=None,
                   help="skip the LLM; treat these layers as walls")
    p.add_argument("--provider", default=None,
                   help="override ARCHIAGENT_LLM_PROVIDER")
    p.add_argument("--model", default=None,
                   help="override ARCHIAGENT_LLM_MODEL")
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


def _print_decisions(decisions) -> None:
    print(f"{'layer':<24} {'role':<18} {'conf':>5}  {'source':<8} reason")
    for d in decisions:
        print(f"{d.layer:<24} {d.role.value:<18} {d.confidence:>5.2f}  "
              f"{d.source:<8} {d.reason}")


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
    return CachingClassifier(inner, model=cfg.model, enabled=not args.no_cache)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

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

    # The classifier is built BEFORE the PDF is read, so a bad provider or a
    # missing key fails fast with EXIT_LLM instead of after a slow parse.
    classifier: LayerClassifier | None = None
    if not args.inspect:
        try:
            classifier = _classifier(args)
        except LLMUnavailable as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_LLM

    try:
        if args.inspect:
            ps = load_pdf(args.pdf, page=args.page)
            _print_inventory(build_inventory(ps))
            return EXIT_OK

        if args.classify_only:
            ps = load_pdf(args.pdf, page=args.page)
            _print_decisions(classifier.classify(build_inventory(ps)))
            return EXIT_OK

        model = extract(args.pdf, classifier, page=args.page,
                        wall_height_ft=args.height)
    except (LLMUnavailable, LLMSchemaError) as e:
        # Both subclass RuntimeError, so this must precede the RuntimeError
        # catch below.
        print(f"error: {e}", file=sys.stderr)
        return EXIT_LLM
    except ScaleGateError as e:
        # ScaleGateError's own message ("no dimensions or no candidate runs
        # to match") never says "scale", and the user needs to know WHICH
        # gate stopped them -- not to suspect the classifier.
        print(f"error: scale gate (R1) failed: {e}", file=sys.stderr)
        print("hint: this drawing may have no live text -- dimensions "
              "converted to vector outlines cannot be read yet.",
              file=sys.stderr)
        return EXIT_PIPELINE
    except NoLayersError as e:
        print(f"error: {args.pdf} has no CAD layers (PDF optional content "
              f"groups): {e}", file=sys.stderr)
        return EXIT_PIPELINE
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_PIPELINE
    except (OSError, RuntimeError) as e:
        print(f"error: could not read {args.pdf}: {e}", file=sys.stderr)
        return EXIT_PIPELINE

    out = author_ifc(model, args.out_ifc)
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
