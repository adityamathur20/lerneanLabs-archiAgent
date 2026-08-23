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


def test_naming_a_layer_that_is_not_in_the_drawing_is_a_pipeline_error(
        demolition_pdf, tmp_path, capsys):
    code = main([str(demolition_pdf), str(tmp_path / "o.ifc"),
                 "--walls", "NO-SUCH-LAYER"])
    assert code == EXIT_PIPELINE
    assert "no layers were classified as walls" in capsys.readouterr().err


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
