"""Replay bypasses recognition while preserving geometry, evidence and acceptance gates."""
from dataclasses import replace
import json
import ifcopenshell
import pytest
from archiagent import cli
from archiagent.classify.layers import LayerDecision
from archiagent.classify.roles import Role
from archiagent.geometry.profiles import WallProfile
from archiagent.ifc.author import author_ifc
from archiagent.interpretation import save_manifest,read_manifest,file_sha256,canonical_json,digest,make_manifest
from archiagent.validate import validate
from checks.test_semantic_ifc import fixture_model


def source_model(tmp_path):
    source=tmp_path/'example drawing.dxf'
    source.write_text('checksum binding; replay must not parse this')
    model=replace(fixture_model(),source_path=str(source),source_sha256=file_sha256(source),
                  layer_decisions=(LayerDecision('Walls',Role.WALL_STRUCTURAL,.9,'native','manual'),))
    return source,replace(model,issues=validate(model))


def write_changed(path,data):
    data['content_sha256']=digest({k:v for k,v in data.items() if k!='content_sha256'})
    path.write_text(canonical_json(data))


def test_roundtrip_nested_types_and_profile(tmp_path):
    source,model=source_model(tmp_path)
    profile=WallProfile('profile',((0.,0.),(1.,0.),(1.,1.),(0.,1.),(0.,0.)))
    model=replace(model,wall_profiles=(profile,));model=replace(model,issues=validate(model))
    path=save_manifest([model],tmp_path/'frozen.json');models,data=read_manifest(path,source)
    assert models==(model,)
    assert models[0].layer_decisions[0].role is Role.WALL_STRUCTURAL
    assert data['coordinate_system']=='model-feet'
    assert 'geos' in data['versions'] and 'numpy' in data['versions']
    assert make_manifest([model])==make_manifest([model])
    with pytest.raises(FileExistsError):save_manifest([model],path)


def test_replay_never_loads_source_or_calls_classifiers(tmp_path,monkeypatch):
    source,model=source_model(tmp_path);frozen=save_manifest([model],tmp_path/'frozen.json')
    def forbidden(*args,**kwargs):raise AssertionError('replay called recognition')
    for name in ('load_dxf','load_pdf','_dxf_classifier','_classifier'):monkeypatch.setattr(cli,name,forbidden)
    out=tmp_path/'replay'
    assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(out),'--replay-manifest',str(frozen),
                     '--require-accepted','--blender-package'])==0
    direct=author_ifc(model,tmp_path/'direct.ifc');replay=out/(source.stem+'.ifc')
    def ids(path):return sorted((e.is_a(),e.Name or '',e.GlobalId) for e in ifcopenshell.open(str(path)).by_type('IfcRoot'))
    assert ids(direct)==ids(replay)
    assert json.loads(replay.with_suffix('.report.json').read_text())['ifc_validation']['passed']
    assert (replay.with_suffix('.blender')/'build_blender.py').exists()
    assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(out),'--replay-manifest',str(frozen)])==cli.EXIT_USAGE


def test_checksum_and_digest_fail_before_authoring(tmp_path):
    source,model=source_model(tmp_path);path=save_manifest([model],tmp_path/'frozen.json')
    source.write_text('changed')
    with pytest.raises(ValueError,match='source checksum'):read_manifest(path,source)
    source.write_text('checksum binding; replay must not parse this')
    data=json.loads(path.read_text());data['models'][0]['wall_height_ft']=50;path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='digest mismatch'):read_manifest(path,source)


@pytest.mark.parametrize('mutate',[
    lambda d:d['models'][0].update(wall_height_ft=True),
    lambda d:d['models'][0].update(invented_field=3),
    lambda d:d['models'][0]['walls'][0].update(start=[0]),
    lambda d:d['models'][0]['layer_decisions'][0].update(role='invented'),
    lambda d:d.update(versions=[]),lambda d:d.update(models=[]),
    lambda d:d['models'][0].update(source_sha256='b'*64),
])
def test_typed_contract_rejects_malformed_even_with_new_digest(tmp_path,mutate):
    source,model=source_model(tmp_path);path=save_manifest([model],tmp_path/'frozen.json')
    data=json.loads(path.read_text());mutate(data);write_changed(path,data)
    with pytest.raises(ValueError):read_manifest(path,source)


def test_current_validation_not_deleted_by_frozen_issues(tmp_path):
    source,model=source_model(tmp_path);model=replace(model,symbols_verified=False,issues=())
    path=save_manifest([model],tmp_path/'draft.json');models,data=read_manifest(path,source)
    assert 'symbol_interpretation_unverified' in {i.code for i in models[0].issues}
    assert all(d['status']=='unreviewed_hypothesis' for d in data['decisions'] if '/symbol-' in d['id'])
    assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(tmp_path/'strict'),
                     '--replay-manifest',str(path),'--require-accepted'])==cli.EXIT_PIPELINE
    assert not (tmp_path/'strict'/f'{source.stem}.ifc').exists()


def test_replay_refuses_geometry_overrides_and_print_prompt_is_offline(tmp_path,capsys):
    source,model=source_model(tmp_path);path=save_manifest([model],tmp_path/'frozen.json')
    for flags in (['--height','10'],['--rules'],['--ocr'],['--units-per-foot=12']):
        assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(tmp_path/'out'),
                         '--replay-manifest',str(path),*flags])==cli.EXIT_USAGE
    assert cli.main(['--print-interpretation-prompt'])==0
    assert 'frozen' in capsys.readouterr().out.lower()


def test_changed_versions_reported_without_reinterpreting(tmp_path,capsys):
    source,model=source_model(tmp_path);path=save_manifest([model],tmp_path/'frozen.json')
    data=json.loads(path.read_text());data['versions']['implementation_sha256']='old';write_changed(path,data)
    assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(tmp_path/'out'),'--replay-manifest',str(path)])==0
    assert 'versions differ' in capsys.readouterr().err


def test_duplicate_json_keys_and_nonfinite_values_rejected(tmp_path):
    source,model=source_model(tmp_path);path=save_manifest([model],tmp_path/'frozen.json')
    path.write_text('{"kind":"a","kind":"b"}')
    with pytest.raises(ValueError,match='duplicate'):read_manifest(path,source)
    path.write_text('{"bad":NaN}')
    with pytest.raises(ValueError,match='nonfinite'):read_manifest(path,source)


@pytest.mark.parametrize('unit_code,upf,angle', [(2,1.,0.), (1,12.,23.), (4,304.8,-17.)])
def test_real_dxf_freeze_replay_preserves_rotated_wall_hole(tmp_path,unit_code,upf,angle):
    """Same physical geometry across three CAD unit systems; no client coordinates."""
    import math
    import ezdxf
    from shapely.geometry import Polygon
    from archiagent.ifc.inspect import validate_export
    outer=((0,0),(10,0),(10,8),(0,8))
    inner=((.5,.5),(9.5,.5),(9.5,7.5),(.5,7.5))
    radians=math.radians(angle)
    def transformed(ring):
        return [(upf*(x*math.cos(radians)-y*math.sin(radians)),
                 upf*(x*math.sin(radians)+y*math.cos(radians))) for x,y in ring]
    doc=ezdxf.new();doc.units=unit_code;doc.layers.new('A-WALL')
    hatch=doc.modelspace().add_hatch(dxfattribs={'layer':'A-WALL'})
    hatch.paths.add_polyline_path(transformed(outer),is_closed=True,flags=1)
    hatch.paths.add_polyline_path(transformed(inner),is_closed=True,flags=0)
    source=tmp_path/'generated plan.dxf';doc.saveas(source)
    out=tmp_path/'extract'
    assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(out),'--walls','A-WALL',
                     '--height','9','--elevation','0','--freeze-only'])==0
    assert not (out/(source.stem+'.ifc')).exists()
    frozen=out/(source.stem+'.interpretation.json')
    models,_=read_manifest(frozen,source);model=models[0]
    assert model.scale.units_per_foot==pytest.approx(upf)
    assert len(model.wall_profiles)==1
    profile=model.wall_profiles[0]
    assert len(profile.holes)==1
    assert Polygon(profile.boundary,profile.holes).area==pytest.approx(17.)
    replay_dir=tmp_path/'replay'
    assert cli.main(['--dxfFilePath',str(source),'--outputDir',str(replay_dir),
                     '--replay-manifest',str(frozen)])==0
    output=replay_dir/(source.stem+'.ifc')
    assert validate_export(output,models)['passed']
    f=ifcopenshell.open(str(output))
    assert len(f.by_type('IfcWall'))==1
    assert len(f.by_type('IfcArbitraryProfileDefWithVoids'))==1
