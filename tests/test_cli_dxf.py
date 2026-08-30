import ezdxf
import pytest
from archiagent.cli import EXIT_OK, EXIT_PIPELINE, EXIT_USAGE, main


def _dxf(tmp_path, insunits=1):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for y in (0, 96):                       # two faces 8in apart -> one wall
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "WALLS"})
    p = tmp_path / "t.dxf"
    doc.saveas(p)
    return p


def test_neither_input_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_both_inputs_is_a_usage_error(tmp_path, capsys):
    d = _dxf(tmp_path)
    rc = main(["some.pdf", "--dxfFilePath", str(d), str(tmp_path / "o.ifc")])
    assert rc == EXIT_USAGE
    assert "one of" in capsys.readouterr().err.lower()


def test_dxf_inspect_lists_layers(tmp_path, capsys):
    d = _dxf(tmp_path)
    assert main(["--dxfFilePath", str(d), "--inspect"]) == EXIT_OK
    assert "WALLS" in capsys.readouterr().out


def test_dxf_walls_override_builds_an_ifc(tmp_path):
    d = _dxf(tmp_path)
    out = tmp_path / "o.ifc"
    assert main(["--dxfFilePath", str(d), str(out), "--walls", "WALLS"]) == EXIT_OK
    assert out.exists()


def test_units_per_foot_overrides_the_header(tmp_path, capsys):
    d = _dxf(tmp_path, insunits=2)          # header lies: says feet
    assert main(["--dxfFilePath", str(d), "--inspect",
                 "--units-per-foot", "12"]) == EXIT_OK


def test_vision_flag_is_off_by_default(tmp_path, monkeypatch):
    """ARCHIAGENT_VISION unset and no --vision means stage 1 only."""
    monkeypatch.delenv("ARCHIAGENT_VISION", raising=False)
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(vision_flag=False) is False


def test_vision_env_var_enables_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_VISION", "1")
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(vision_flag=False) is True


def test_explicit_flag_wins_over_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_VISION", "0")
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(vision_flag=True) is True


def test_a_bad_dxf_wall_name_is_caught(tmp_path, capsys):
    d = _dxf(tmp_path)
    rc = main(["--dxfFilePath", str(d), str(tmp_path / "o.ifc"),
               "--walls", "NOPE"])
    assert rc == EXIT_USAGE
    assert "NOPE" in capsys.readouterr().err
