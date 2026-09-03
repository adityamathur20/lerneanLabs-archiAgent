import ezdxf
import pytest
from archiagent.cli import EXIT_OK, EXIT_PIPELINE, EXIT_USAGE, main


def _dxf(tmp_path, insunits=1, name="t.dxf"):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    for y in (0, 96):                       # two faces 8in apart -> one wall
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "WALLS"})
    p = tmp_path / name
    doc.saveas(p)
    return p


def test_neither_input_is_a_usage_error(capsys):
    assert main([]) == EXIT_USAGE


def test_both_inputs_is_a_usage_error(tmp_path, capsys):
    d = _dxf(tmp_path)
    rc = main(["--pdfFilePath", "some.pdf", "--dxfFilePath", str(d),
               "--outputDir", str(tmp_path / "out")])
    assert rc == EXIT_USAGE
    assert "one of" in capsys.readouterr().err.lower()


def test_dxf_inspect_lists_layers(tmp_path, capsys):
    d = _dxf(tmp_path)
    assert main(["--dxfFilePath", str(d), "--inspect"]) == EXIT_OK
    assert "WALLS" in capsys.readouterr().out


def test_dxf_walls_override_builds_an_ifc(tmp_path):
    d = _dxf(tmp_path)
    out_dir = tmp_path / "out"
    assert main(["--dxfFilePath", str(d), "--outputDir", str(out_dir),
                "--walls", "WALLS"]) == EXIT_OK
    assert (out_dir / "t.ifc").exists()


def test_units_per_foot_overrides_the_header(tmp_path, capsys):
    d = _dxf(tmp_path, insunits=2)          # header lies: says feet
    assert main(["--dxfFilePath", str(d), "--inspect",
                 "--units-per-foot", "12"]) == EXIT_OK


def test_vision_is_on_by_default(monkeypatch):
    """Vision is ON by default -- the user must be able to get stage 2
    escalation with no flags at all."""
    monkeypatch.delenv("ARCHIAGENT_VISION", raising=False)
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(no_vision_flag=False) is True


def test_no_vision_flag_disables_it(monkeypatch):
    monkeypatch.delenv("ARCHIAGENT_VISION", raising=False)
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(no_vision_flag=True) is False


def test_vision_env_var_zero_disables_it(monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_VISION", "0")
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(no_vision_flag=False) is False


def test_explicit_no_vision_flag_wins_over_the_env_var(monkeypatch):
    monkeypatch.setenv("ARCHIAGENT_VISION", "1")
    from archiagent.cli import _vision_enabled
    assert _vision_enabled(no_vision_flag=True) is False


def test_a_bad_dxf_wall_name_is_caught(tmp_path, capsys):
    d = _dxf(tmp_path)
    rc = main(["--dxfFilePath", str(d), "--outputDir", str(tmp_path / "out"),
               "--walls", "NOPE"])
    assert rc == EXIT_USAGE
    assert "NOPE" in capsys.readouterr().err


def test_help_states_vision_is_on_by_default(capsys):
    """The user must never be surprised that DXF layer renders are sent to
    the LLM provider by default -- --help has to say so plainly."""
    assert main(["--help"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())  # normalize wrapping
    assert "--no_vision" in out
    assert "sent to the configured llm provider" in out.lower()


# --- Critical finding: the CLI used to overwrite input files -------------
#
# `archiagent myplan.pdf --dxfFilePath drawing.dxf --walls WALLS` used to
# reach main() with pdf="myplan.pdf" (argparse's greedy nargs="?" matching
# put the stray positional there) and out_ifc=None. main()'s own
# reinterpretation step then read "no OUT_IFC yet, but --dxfFilePath IS
# set, and pdf holds one token" as "that token must actually be OUT_IFC",
# reassigned it, and authored the IFC straight over myplan.pdf -- at exit
# 0, no warning. Positionals are gone now: there is no slot left for
# argparse, or main(), to misassign a stray token into.

def test_the_reported_overwrite_bug_is_now_a_usage_error(tmp_path, capsys):
    """Regression for the Critical finding, reproduced with a real DXF so
    the drawing side of the command is otherwise completely valid."""
    d = _dxf(tmp_path)
    victim = tmp_path / "myplan.pdf"
    original = b"33 bytes of totally real pdf text"
    victim.write_bytes(original)

    code = main([str(victim), "--dxfFilePath", str(d), "--walls", "WALLS"])

    assert code == EXIT_USAGE
    assert victim.read_bytes() == original


def test_outputdir_refuses_to_overwrite_an_existing_output_file(tmp_path):
    """Same family of mistake as the overwrite bug: a previous run's
    output in the same --outputDir must not be silently clobbered."""
    d = _dxf(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    existing = out_dir / "t.ifc"
    original = b"a previous run's real output"
    existing.write_bytes(original)

    code = main(["--dxfFilePath", str(d), "--outputDir", str(out_dir),
                 "--walls", "WALLS"])

    assert code == EXIT_USAGE
    assert existing.read_bytes() == original


def test_outputdir_is_created_if_missing(tmp_path):
    d = _dxf(tmp_path)
    out_dir = tmp_path / "does" / "not" / "exist" / "yet"
    assert not out_dir.exists()

    code = main(["--dxfFilePath", str(d), "--outputDir", str(out_dir),
                 "--walls", "WALLS"])

    assert code == EXIT_OK
    assert (out_dir / "t.ifc").exists()


# --- classifier-reported issues must reach CLI output ---------------------

class _FakeClient:
    """Stands in for a real LLMClient -- no credentials, no network."""

    def __init__(self, reply):
        self._reply = reply

    def classify_json(self, **kw):
        return self._reply

    def classify_json_vision(self, **kw):  # pragma: no cover
        raise AssertionError("vision must not be called when --no_vision "
                             "is set")


def test_classifier_reported_issues_reach_cli_output(tmp_path, monkeypatch,
                                                      capsys):
    """DxfLayerClassifier reports layer_escalation_skipped /
    layer_role_revised via its on_issue callback. cli.py used to build it
    with no on_issue= at all, so these were computed and silently
    discarded -- validate(model) has no side-channel for them. A
    low-confidence stage-1 answer with vision off must trigger escalation,
    get skipped for "vision is off", and that Issue must show up under -v.
    """
    d = _dxf(tmp_path)
    reply = {"layers": [{"name": "WALLS", "role": "wall_structural",
                         "confidence": 0.5, "reason": "unsure"}]}
    monkeypatch.setattr("archiagent.cli.build_client",
                        lambda cfg: _FakeClient(reply))

    out_dir = tmp_path / "out"
    code = main(["--dxfFilePath", str(d), "--outputDir", str(out_dir),
                 "--no_vision", "--no-cache", "-v"])

    assert code == EXIT_OK
    err = capsys.readouterr().err
    assert "layer_escalation_skipped" in err
    assert "WALLS" in err


# --- vision-by-default, end to end through the real CLI -------------------
#
# Escalation mechanics are covered at the DxfLayerClassifier unit level
# (test_dxf_classifier.py) and the wire format at the adapter level -- but
# no test drove the DEFAULT vision-on path through the real CLI: main() ->
# _dxf_classifier -> vision=True -> _escalate -> classify_json_vision. That
# full chain is this branch's headline behaviour (vision by default) and
# its riskiest (it transmits images), so it deserves one test that actually
# exercises it end to end rather than only composing separately-tested
# pieces.

class _VisionRecordingClient:
    """Stands in for a real LLMClient. Records whether classify_json_vision
    -- the call that would transmit rendered images to a live provider --
    was actually reached. No credentials, no network."""

    def __init__(self, stage1_reply, stage2_reply):
        self._stage1_reply = stage1_reply
        self._stage2_reply = stage2_reply
        self.vision_called = False
        self.vision_kwargs = None

    def classify_json(self, **kw):
        return self._stage1_reply

    def classify_json_vision(self, **kw):
        self.vision_called = True
        self.vision_kwargs = kw
        return self._stage2_reply


def test_vision_path_executes_end_to_end_through_the_cli(tmp_path, monkeypatch):
    """Drives main() with vision left ON (no --no_vision, no
    ARCHIAGENT_VISION override) over a small generated DXF, and asserts the
    vision stage actually ran. render_for_escalation is monkeypatched (as
    test_dxf_classifier.py does) so the test does not depend on the
    optional matplotlib/Pillow extra being installed -- the point here is
    proving the CLI's wiring reaches classify_json_vision, not re-testing
    the renderer."""
    d = _dxf(tmp_path)                      # one low-signal WALLS layer

    stage1_reply = {"layers": [{"name": "WALLS", "role": "wall_structural",
                                "confidence": 0.5, "reason": "unsure"}]}
    stage2_reply = {"layers": [{"name": "WALLS", "role": "wall_structural",
                                "confidence": 0.95,
                                "reason": "confirmed by image"}]}
    fake = _VisionRecordingClient(stage1_reply, stage2_reply)
    monkeypatch.setattr("archiagent.cli.build_client", lambda cfg: fake)

    ref_png = tmp_path / "ref.png"
    layer_png = tmp_path / "walls.png"
    ref_png.write_bytes(b"\x89PNG fake reference render")
    layer_png.write_bytes(b"\x89PNG fake WALLS render")
    monkeypatch.setattr(
        "archiagent.classify.dxf_classifier.render_for_escalation",
        lambda dxf_path, layers, cache_dir: (ref_png, {"WALLS": layer_png}))

    monkeypatch.delenv("ARCHIAGENT_VISION", raising=False)
    out_dir = tmp_path / "out"
    code = main(["--dxfFilePath", str(d), "--outputDir", str(out_dir),
                "--no-cache"])                       # note: no --no_vision

    assert code == EXIT_OK
    assert fake.vision_called is True
    assert [label for label, _ in fake.vision_kwargs["images"]] == \
        ["reference", "layer WALLS"]
    assert (out_dir / "t.ifc").exists()


# --- --max-tokens -----------------------------------------------------
#
# A reply truncated by the token cap is a hard error with no partial
# result, and the right budget scales with the drawing's layer count --
# which the tool cannot know before it reads the file. Measured: a
# 39-layer DXF overruns the 2048 default, because the reply carries a
# role, a confidence and a reason for every single layer.

def test_max_tokens_defaults_to_the_module_constant():
    from archiagent.classify.llm_classifier import MAX_TOKENS
    from archiagent.cli import _max_tokens, _parser
    assert _max_tokens(_parser().parse_args(
        ["--dxfFilePath", "x.dxf", "--inspect"])) == MAX_TOKENS


def test_max_tokens_flag_overrides_the_default():
    from archiagent.cli import _max_tokens, _parser
    assert _max_tokens(_parser().parse_args(
        ["--dxfFilePath", "x.dxf", "--inspect", "--max-tokens", "16384"])) == 16384


def test_max_tokens_env_var_is_honoured(monkeypatch):
    from archiagent.cli import _max_tokens, _parser
    monkeypatch.setenv("ARCHIAGENT_MAX_TOKENS", "8000")
    assert _max_tokens(_parser().parse_args(
        ["--dxfFilePath", "x.dxf", "--inspect"])) == 8000


def test_max_tokens_flag_wins_over_the_env_var(monkeypatch):
    from archiagent.cli import _max_tokens, _parser
    monkeypatch.setenv("ARCHIAGENT_MAX_TOKENS", "8000")
    assert _max_tokens(_parser().parse_args(
        ["--dxfFilePath", "x.dxf", "--inspect", "--max-tokens", "16384"])) == 16384


def test_a_junk_env_var_falls_back_rather_than_crashing(monkeypatch):
    from archiagent.classify.llm_classifier import MAX_TOKENS
    from archiagent.cli import _max_tokens, _parser
    monkeypatch.setenv("ARCHIAGENT_MAX_TOKENS", "not-a-number")
    assert _max_tokens(_parser().parse_args(
        ["--dxfFilePath", "x.dxf", "--inspect"])) == MAX_TOKENS


def test_the_budget_actually_reaches_the_client(tmp_path, monkeypatch):
    """The flag is worthless if it stops at the CLI boundary."""
    import ezdxf
    from archiagent.cli import main

    doc = ezdxf.new(); doc.header["$INSUNITS"] = 1
    doc.layers.add("WALLS", color=7)
    msp = doc.modelspace()
    for y in (0, 96):
        msp.add_line((0, y), (240, y), dxfattribs={"layer": "WALLS"})
    d = tmp_path / "t.dxf"; doc.saveas(d)

    seen = {}

    class Recorder:
        def classify_json(self, **kw):
            seen["max_tokens"] = kw["max_tokens"]
            return {"layers": [{"name": "WALLS", "role": "wall_structural",
                                "confidence": 0.9, "reason": "x"}]}

        def classify_json_vision(self, **kw):
            return {"layers": []}

    monkeypatch.setattr("archiagent.cli.build_client", lambda cfg: Recorder())
    main(["--dxfFilePath", str(d), "--classify-only",
          "--max-tokens", "16384", "--no-cache", "--no_vision"])
    assert seen["max_tokens"] == 16384
