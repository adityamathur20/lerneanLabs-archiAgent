"""The CLI's contract: modes, exit codes, and working with no credentials.

The fixture-gated tests use the real drawings and no LLM at all.
"""

import pytest

from archiagent.cli import EXIT_LLM, EXIT_OK, EXIT_PIPELINE, EXIT_USAGE, main


def test_no_arguments_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_authoring_without_an_output_path_is_a_usage_error(capsys):
    assert main(["plan.pdf"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "OUT_IFC" in err
    # No --walls was given, so the argument-order hint does not apply and
    # must not appear.
    assert "--walls" not in err


def test_walls_swallowing_out_ifc_explains_the_argument_order(capsys):
    """--walls is nargs="+" and eats every argument after it, including an
    OUT_IFC placed after it on the command line. The diagnostic must name
    that, not just repeat "OUT_IFC is required" as if none were typed."""
    code = main(["plan.pdf", "--walls", "A", "B", "out.ifc"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "--walls" in err
    assert "out.ifc" in err


def test_no_argv_falls_back_to_sys_argv(monkeypatch):
    monkeypatch.setattr("sys.argv", ["archiagent"])
    assert main() == EXIT_USAGE


def test_a_missing_pdf_is_a_pipeline_error_naming_the_path(capsys):
    assert main(["nope.pdf", "out.ifc", "--walls", "WALLS"]) == EXIT_PIPELINE
    assert "nope.pdf" in capsys.readouterr().err


def test_an_unknown_provider_exits_with_the_llm_code(capsys, tmp_path):
    code = main(["nope.pdf", str(tmp_path / "o.ifc"), "--provider", "bedrock"])
    assert code == EXIT_LLM
    err = capsys.readouterr().err
    assert "anthropic" in err and "openai" in err


def test_inspect_needs_no_credentials(demolition_pdf, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert main([str(demolition_pdf), "--inspect"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "walll" in out
    assert "FURNITURE" in out


def test_inspect_does_not_write_an_ifc(demolition_pdf, tmp_path):
    out_ifc = tmp_path / "should-not-exist.ifc"
    assert main([str(demolition_pdf), str(out_ifc), "--inspect"]) == EXIT_OK
    assert not out_ifc.exists()


def test_walls_runs_the_whole_pipeline_with_no_llm(demolition_pdf, tmp_path,
                                                   monkeypatch):
    """Measured: this drawing yields 51 walls, 1 space, and 42 'error'
    issues -- all unresolved_junction, i.e. dangling wall ends, which are
    normal on a real drawing and do not stop the IFC being written. Exit
    status therefore tracks the R1 scale gate, not the issue list."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out_ifc = tmp_path / "demolition.ifc"

    code = main([str(demolition_pdf), str(out_ifc), "--walls", "walll"])

    assert code == EXIT_OK
    assert out_ifc.exists()
    import ifcopenshell
    f = ifcopenshell.open(out_ifc)
    assert f.schema == "IFC4"
    assert len(f.by_type("IfcWall")) >= 30


def test_naming_a_layer_that_is_not_in_the_drawing_is_a_usage_error(
        demolition_pdf, tmp_path, capsys):
    """A --walls name is an assertion about the drawing, so a false one stops
    the run. This used to reach the pipeline and fail with "no layers were
    classified as walls"; it is now caught against the inventory first, which
    can name the offending string and point at --inspect."""
    out_ifc = tmp_path / "o.ifc"
    code = main([str(demolition_pdf), str(out_ifc), "--walls", "NO-SUCH-LAYER"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "NO-SUCH-LAYER" in err
    assert "--inspect" in err
    assert not out_ifc.exists()


def test_one_mistyped_wall_name_among_correct_ones_stops_the_run(
        demolition_pdf, tmp_path, capsys):
    """THE critical case. 'walll' is real, 'walll-typo' is not. Before this
    check the run produced a complete IFC at exit 0 built from whatever the
    correct names matched, with the scale resolved from the wrong candidate
    runs -- a silent wrong answer, which this project forbids."""
    out_ifc = tmp_path / "o.ifc"
    code = main([str(demolition_pdf), str(out_ifc),
                 "--walls", "walll", "walll-typo"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "walll-typo" in err
    # The name that DID match must not be blamed.
    assert "'walll'" not in err
    assert not out_ifc.exists()


def test_a_bad_wall_name_is_caught_on_the_classify_only_path_too(
        demolition_pdf, capsys):
    assert main([str(demolition_pdf), "--classify-only",
                 "--walls", "NOPE"]) == EXIT_USAGE
    assert "NOPE" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    """argparse raises SystemExit(0) for --help. Reporting that as bad usage
    breaks every `archiagent --help` install check and CI smoke test."""
    assert main(["--help"]) == EXIT_OK
    assert "OUT_IFC" in capsys.readouterr().out


def test_an_unknown_flag_is_still_a_usage_error(capsys):
    assert main(["plan.pdf", "--no-such-flag"]) == EXIT_USAGE


def test_an_unwritable_output_path_is_a_clean_error_not_a_traceback(
        demolition_pdf, tmp_path, capsys):
    """author_ifc used to run OUTSIDE the try, so a failed write reached the
    user as a traceback instead of the `error: ...` every other failure
    produces."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        code = main([str(demolition_pdf), str(locked / "o.ifc"),
                     "--walls", "walll"])
    finally:
        locked.chmod(0o700)
    assert code == EXIT_PIPELINE
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "o.ifc" in err


def test_a_model_authored_reason_is_truncated_and_stripped(capsys):
    """The reason is model-authored text going straight to a terminal. A
    control character could rewrite the table; an over-long one destroys it."""
    from archiagent.classify.layers import LayerDecision
    from archiagent.classify.roles import Role
    from archiagent.cli import MAX_REASON_CHARS, _print_decisions

    nasty = "\x1b[2Jwiped\x07 " + "y" * 200
    _print_decisions((LayerDecision("L", Role.WALL_STRUCTURAL, 0.9, nasty,
                                    "llm"),))
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\x07" not in out
    row = out.splitlines()[1]
    reason = row.split("llm", 1)[1].strip()
    assert len(reason) <= MAX_REASON_CHARS
    assert out.count("\n") == 2  # header + exactly one row, no injected line


def test_classify_only_prints_confidence_and_source(demolition_pdf, capsys):
    assert main([str(demolition_pdf), "--classify-only",
                "--walls", "walll"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "wall_structural" in out
    assert "manual" in out
    assert "1.00" in out


def test_verbose_reports_issues_by_severity(demolition_pdf, tmp_path, capsys):
    main([str(demolition_pdf), str(tmp_path / "o.ifc"),
          "--walls", "walll", "-v"])
    err = capsys.readouterr().err
    assert "warn" in err


def test_a_drawing_with_no_readable_text_fails_at_the_scale_gate(
        ground_floor_pdf, tmp_path, capsys):
    """W1 does not pretend to fix what W3 addresses. The failure must name
    the scale gate, not the classifier.

    The layer is 'WALLS' -- verified against the drawing. ScaleGateError's
    own message is 'no dimensions or no candidate runs to match', which
    never says 'scale', so the CLI has to supply that framing itself.
    """
    code = main([str(ground_floor_pdf), str(tmp_path / "o.ifc"),
                "--walls", "WALLS"])
    assert code == EXIT_PIPELINE
    err = capsys.readouterr().err.lower()
    assert "scale gate" in err
    assert "classif" not in err
