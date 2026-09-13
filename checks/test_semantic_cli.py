"""CLI integration checks without reading a drawing or contacting providers."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from archiagent import cli
from archiagent.model import BuildingModel, Issue
from archiagent.scale.resolve import ScaleResult


def minimal_model():
    return BuildingModel((), (), (), (), ScaleResult(12., "source-units-unverified", (), 0., 0),
                         (), "fixture.dxf", "a" * 64)


@pytest.fixture
def stub_pipeline(monkeypatch):
    source = SimpleNamespace(source_sha256="a" * 64)
    classifier = SimpleNamespace(classify=lambda stats: ())
    monkeypatch.setattr(cli, "_dxf_classifier", lambda *a, **kw: classifier)
    monkeypatch.setattr(cli, "load_dxf", lambda *a, **kw: (source, 12.))
    monkeypatch.setattr(cli, "build_dxf_inventory", lambda *a: ())
    monkeypatch.setattr(cli, "build_inventory", lambda *a: ())
    monkeypatch.setattr(cli, "select_region", lambda *a: source)
    monkeypatch.setattr(cli, "extract_from_dxf", lambda *a, **kw: minimal_model())
    monkeypatch.setattr(cli, "write_review", lambda models, sources, path:
                        (Path(path).with_suffix(".report.json"), Path(path).with_suffix(".overlay.svg")))
    monkeypatch.setattr(cli, "validate_export", lambda *a: {"passed": True})
    monkeypatch.setattr(cli, "record_export_validation", lambda *a: None)
    captured = []
    monkeypatch.setattr(cli, "author_ifc", lambda model, path: (captured.append(model) or path))
    monkeypatch.setattr(cli, "author_building", lambda models, path: (captured.extend(models) or path))
    return captured


def args(tmp_path, *extras):
    return ["--dxfFilePath", "fixture.dxf", "--outputDir", str(tmp_path / "out"), "--rules", *extras]


def test_whole_drawing_storey_flags_are_applied(stub_pipeline, tmp_path):
    assert cli.main(args(tmp_path, "--storey-name", "Upper floor", "--elevation", "11")) == cli.EXIT_OK
    model = stub_pipeline[0]
    assert model.storey_name == "Upper floor"
    assert model.elevation_ft == 11.
    assert model.region_id == "whole-drawing"
    assert "assumed_elevation" not in {i.code for i in model.issues}


def test_scale_gate_and_summary_cover_all_regions(stub_pipeline, monkeypatch, tmp_path, capsys):
    regions = tmp_path / "regions.json"
    regions.write_text(json.dumps([
        {"id": "first", "kind": "plan", "bounds": [0, 0, 10, 10], "elevation_ft": 0},
        {"id": "second", "kind": "plan", "bounds": [20, 0, 30, 10], "elevation_ft": 11}]))
    models = iter((replace(minimal_model(), region_id="first", issues=(Issue("error", "scale", "scale_gate_failed", "bad scale"),)),
                   replace(minimal_model(), region_id="second", scale=ScaleResult(1., "source-units-unverified", (), 0., 0))))
    monkeypatch.setattr(cli, "extract_from_dxf", lambda *a, **kw: next(models))
    assert cli.main(args(tmp_path, "--regions-file", str(regions))) == cli.EXIT_PIPELINE
    output = capsys.readouterr()
    assert "scale [first]" in output.out and "scale [second]" in output.out
    assert "[first]: bad scale" in output.err
    assert len(stub_pipeline) == 2  # legacy draft export semantics remain


@pytest.mark.parametrize("record", [
    {"source_sha256": "b" * 64},
    {"regions": []},
    {"symbols": [{"id": "incomplete"}]},
    {"footprints": [{"boundary": None}]},
    {"symbols_verified": "true"},
])
def test_bad_review_is_clean_error(stub_pipeline, tmp_path, capsys, record):
    path = tmp_path / "review.json"
    path.write_text(json.dumps(record))
    assert cli.main(args(tmp_path, "--review-file", str(path))) == cli.EXIT_PIPELINE
    assert "error:" in capsys.readouterr().err
    assert not stub_pipeline


def test_malformed_regions_and_measurements_are_clean_errors(stub_pipeline, tmp_path):
    regions = tmp_path / "regions.json"
    regions.write_text('[{"id":"bad"}]')
    assert cli.main(args(tmp_path, "--regions-file", str(regions))) == cli.EXIT_PIPELINE
    measurements = tmp_path / "dimensions.json"
    measurements.write_text('[{"expected_ft":12}]')
    assert cli.main(args(tmp_path, "--measurements", str(measurements))) == cli.EXIT_PIPELINE
    assert not stub_pipeline


def test_invalid_flags_and_strict_acceptance_do_not_author(stub_pipeline, tmp_path):
    assert cli.main(args(tmp_path, "--elevation", "nan")) == cli.EXIT_USAGE
    assert cli.main(args(tmp_path, "--height", "-2")) == cli.EXIT_USAGE
    assert cli.main(args(tmp_path, "--require-accepted")) == cli.EXIT_PIPELINE
    assert not stub_pipeline


@pytest.mark.parametrize("input_kind", ["dxf", "pdf"])
def test_region_scale_overrides_global_or_header(stub_pipeline, monkeypatch, tmp_path, input_kind):
    source = SimpleNamespace(source_sha256="a" * 64)
    monkeypatch.setattr(cli, "load_dxf", lambda *a, **kw: (source, kw.get("units_per_foot") or 12.))
    monkeypatch.setattr(cli, "load_pdf", lambda *a, **kw: source)
    monkeypatch.setattr(cli, "_classifier", lambda *a: SimpleNamespace(classify=lambda stats: ()))
    observed = []

    def extract(*a, **kw):
        observed.append(kw["units_per_foot"])
        return replace(minimal_model(), region_id=kw["region"].id,
                       scale=ScaleResult(kw["units_per_foot"], "region-calibration", (), 0., 0))

    monkeypatch.setattr(cli, "extract_from_dxf", extract)
    monkeypatch.setattr(cli, "extract_from_primitives", extract)
    regions = tmp_path / "scales.json"
    regions.write_text(json.dumps([
        {"id": "detail-scale", "kind": "plan", "bounds": [0, 0, 10, 10], "elevation_ft": 0,
         "units_per_foot": 6},
        {"id": "global-scale", "kind": "plan", "bounds": [20, 0, 30, 10], "elevation_ft": 10}]))
    argv = args(tmp_path, "--regions-file", str(regions), "--units-per-foot", "24")
    argv[0] = "--dxfFilePath" if input_kind == "dxf" else "--pdfFilePath"
    assert cli.main(argv) == cli.EXIT_OK
    assert observed == [6, 24]


def test_region_scale_loading_and_validation(tmp_path):
    from archiagent.regions import load_regions
    from archiagent.semantic import PlanRegion
    path = tmp_path / "regions.json"
    record = {"id": "floor", "kind": "plan", "bounds": [0, 0, 10, 10], "units_per_foot": 304.8}
    path.write_text(json.dumps([record]))
    assert load_regions(path)[0].units_per_foot == 304.8
    assert PlanRegion("old-positional", (0, 0, 1, 1), "plan", "Floor", 0, (0, 0), "reviewed").units_per_foot is None
    for invalid in (0, -1, float("nan"), float("inf"), True, "12"):
        path.write_text(json.dumps([{**record, "units_per_foot": invalid}]))
        with pytest.raises(ValueError, match="units_per_foot"):
            load_regions(path)


@pytest.mark.parametrize("kind", ["detail", "elevation", "unclassified", None])
def test_only_reviewed_plan_regions_are_authored(stub_pipeline, tmp_path, capsys, kind):
    record = {"id": "not-a-plan", "bounds": [0, 0, 10, 10], "elevation_ft": 0}
    if kind is not None:
        record["kind"] = kind
    path = tmp_path / "regions.json"
    path.write_text(json.dumps([record]))
    assert cli.main(args(tmp_path, "--regions-file", str(path))) == cli.EXIT_PIPELINE
    assert "kind='plan'" in capsys.readouterr().err
    assert not stub_pipeline
