"""Active prompt delivery and cache invalidation, without provider calls.

These checks verify integration, not the semantic accuracy of an LLM's answer.
"""
from dataclasses import replace

import pytest

from archiagent.classify import cache, prompt
from archiagent.classify.dxf_classifier import DxfLayerClassifier
from archiagent.classify.inventory import LayerStats
from archiagent.classify.layers import LayerDecision
from archiagent.classify.llm_classifier import LLMLayerClassifier
from archiagent.classify.roles import Role


def inventory():
    return (LayerStats('A WALL HATCH', 5, 20, .3, (), (), (0, 0, 100, 80),
                       1, 4, 60, entity_mix=(('HATCH', 5),), entity_share=.4),)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def answer(self, mode, kwargs):
        self.calls.append((mode, kwargs))
        return {'layers': [{'name': 'A WALL HATCH', 'role': 'wall_partition',
                            'confidence': .8, 'reason': 'Wall-named hatch; structure unverified'}]}

    def classify_json(self, **kwargs):
        return self.answer('text', kwargs)

    def classify_json_vision(self, **kwargs):
        return self.answer('vision', kwargs)


@pytest.mark.parametrize('kind', ['pdf', 'dxf'])
def test_active_inventory_classifier_passes_enhanced_contract(kind, tmp_path):
    client = RecordingClient()
    classifier = (LLMLayerClassifier(client) if kind == 'pdf' else
                  DxfLayerClassifier(client, tmp_path/'unused.dxf', vision=False,
                                     cache_dir=tmp_path/'renders'))
    decisions = classifier.classify(inventory())
    mode, call = client.calls[0]
    assert len(client.calls) == 1 and mode == 'text'
    assert call['system'] == (prompt.SYSTEM_PROMPT if kind == 'pdf' else prompt.DXF_SYSTEM_PROMPT)
    assert 'A WALL HATCH' in call['user']
    assert call['schema'] == prompt.response_schema()
    assert decisions[0].role == Role.WALL_PARTITION
    assert decisions[0].source == 'llm'
    assert decisions[0].layer == 'A WALL HATCH'


def test_visual_batch_uses_dxf_contract_and_only_requested_layers(tmp_path):
    client = RecordingClient()
    classifier = DxfLayerClassifier(client, tmp_path/'unused.dxf', cache_dir=tmp_path/'renders')
    reference, isolated = tmp_path/'reference.png', tmp_path/'isolated.png'
    # Bytes are passed to a fake transport, never decoded or sent externally.
    reference.write_bytes(b'reference fixture')
    isolated.write_bytes(b'isolated fixture')
    stats = inventory() + (replace(inventory()[0], name='Other layer'),)
    result = classifier._vision_batch((('A WALL HATCH', 'low_confidence'),), reference,
                                     {'A WALL HATCH': isolated}, stats)
    mode, call = client.calls[0]
    assert mode == 'vision' and call['system'] == prompt.DXF_SYSTEM_PROMPT
    assert 'Other layer' not in call['user']
    assert [label for label, _ in call['images']] == ['reference', 'layer A WALL HATCH']
    assert set(result) == {'A WALL HATCH'}


def test_previous_prompt_cache_is_not_reused_and_new_answers_are_cached(tmp_path, monkeypatch):
    monkeypatch.setenv(cache.ENV_CACHE_DIR, str(tmp_path/'cache'))
    stats = inventory()
    # Identity of the actual classifier prompts before this enhancement.
    previous_version = 'b560648bb28b'
    assert prompt.PROMPT_VERSION != previous_version
    with monkeypatch.context() as previous:
        previous.setattr(cache, 'PROMPT_VERSION', previous_version)
        key = cache.inventory_key(stats, 'fixture-model')
        cache.write_cache(key, (LayerDecision('A WALL HATCH', Role.ANNOTATION, .99,
                                              'stale hatch-only classification', 'llm'),))
    client = RecordingClient()
    classifier = cache.CachingClassifier(LLMLayerClassifier(client), model='fixture-model')
    result = classifier.classify(stats)
    assert len(client.calls) == 1
    assert result[0].role == Role.WALL_PARTITION
    assert classifier.classify(stats) == result
    assert len(client.calls) == 1
