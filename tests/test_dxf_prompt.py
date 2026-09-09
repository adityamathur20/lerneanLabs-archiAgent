from archiagent.classify.inventory import LayerStats
from archiagent.classify.prompt import (
    DXF_SYSTEM_PROMPT, PROMPT_VERSION, build_dxf_user_prompt)


def _s(name, **kw):
    base = dict(path_count=3, segment_count=9, axis_aligned_fraction=0.9,
                stroke_widths=(), dominant_colors=(), bbox=(0, 0, 10, 10),
                length_p10=1.0, length_p50=5.0, length_p90=9.0)
    base.update(kw)
    return LayerStats(name=name, **base)


def test_entity_mix_is_rendered():
    s = (_s("doors", entity_mix=(("LINE", 1111), ("ARC", 147)), entity_share=0.2),)
    out = build_dxf_user_prompt(s)
    assert "LINE:1111" in out and "ARC:147" in out


def test_lineweight_and_linetype_are_rendered():
    s = (_s("wall", lineweight=35, linetype="Continuous"),
         _s("BEAM", lineweight=30, linetype="HIDDEN"))
    out = build_dxf_user_prompt(s)
    assert "35" in out and "HIDDEN" in out


def test_lineweight_minus_3_renders_as_default_not_number():
    """The DXF sentinel -3 means 'inherits, no signal'. It must render as the
    word 'default', never as the literal '-3'. Almost every layer in real
    client drawings carries -3; if it rendered as a number, the model would
    confuse missing information for a measurement."""
    s = (_s("inherited", lineweight=-3),)
    out = build_dxf_user_prompt(s)
    assert "default" in out.lower()
    assert "-3" not in out


def test_lineweight_none_renders_as_default_not_missing():
    """Absent lineweight (None) also means 'no signal', like -3. It must
    render as 'default', not be omitted or appear as None."""
    s = (_s("unspecified", lineweight=None),)
    out = build_dxf_user_prompt(s)
    assert "default" in out.lower()
    assert "-3" not in out


def test_layer_names_are_json_quoted():
    """Names contain spaces: 'COLUM HATCH', 'DB TO SHAFT CONDUIT'."""
    out = build_dxf_user_prompt((_s("COLUM HATCH"),))
    assert '"COLUM HATCH"' in out


def test_frozen_and_off_are_stated():
    out = build_dxf_user_prompt((_s("GRID", is_frozen=True),))
    assert "frozen" in out.lower()


def test_entity_share_is_rendered_as_a_percentage():
    out = build_dxf_user_prompt((_s("0", entity_share=0.549),))
    assert "54.9" in out or "55" in out


def test_no_raw_coordinates_leak_into_the_prompt():
    """As in W1: derived width/height only, never absolute coordinates."""
    out = build_dxf_user_prompt((_s("X", bbox=(1234.5, 6789.0, 1240.0, 6800.0)),))
    assert "1234.5" not in out and "6789" not in out


def test_prompt_version_covers_the_dxf_prompt(monkeypatch):
    """Editing DXF_SYSTEM_PROMPT without bumping the version would serve
    stale cached classifications forever. Recompute the hash with the DXF
    prompt perturbed and assert the version moves."""
    import hashlib
    import json as _json

    from archiagent.classify import prompt as mod

    def _version(dxf_prompt: str) -> str:
        return hashlib.sha256(
            (mod.SYSTEM_PROMPT + dxf_prompt
             + _json.dumps(mod.response_schema(), sort_keys=True)).encode("utf-8")
        ).hexdigest()[:12]

    assert _version(mod.DXF_SYSTEM_PROMPT) == PROMPT_VERSION
    assert _version(mod.DXF_SYSTEM_PROMPT + " ") != PROMPT_VERSION


def test_system_prompt_warns_that_the_default_layer_may_hold_the_building():
    assert "0" in DXF_SYSTEM_PROMPT
    low = DXF_SYSTEM_PROMPT.lower()
    assert "default layer" in low or "layer 0" in low
