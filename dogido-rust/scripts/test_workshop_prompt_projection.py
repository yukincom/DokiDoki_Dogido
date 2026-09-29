"""Native preparation requests only details; semantic/edit validation stays canonical."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
import workshop_helper as helper
from dogido_server import tts_reading

FIXTURES = json.loads((Path(__file__).parents[1] / 'src/workshop_prompt/fixtures.json').read_text())
for group in ('projection_cases', 'pure_cases'):
    for case in FIXTURES[group]:
        for message in case['expected']['messages']:
            message['content'] = FIXTURES['message_contents'][message.pop('content_ref')]
CASES = FIXTURES['projection_cases']

@pytest.fixture(autouse=True)
def no_dictionary(monkeypatch):
    def forbidden(): raise AssertionError('projection tests must not initialize a dictionary')
    monkeypatch.setattr(tts_reading, '_get_unidic_tagger', forbidden)

@pytest.mark.parametrize('case', CASES, ids=lambda c:c['name'])
def test_same_full_details_and_fixed_edit_without_prompt_assembly(case, monkeypatch):
    original = deepcopy(case['frame'])
    frame = deepcopy(original)
    frame['op'] = 'prepare_details'
    def forbidden(_details): raise AssertionError('Rust path must not assemble Python prompt')
    monkeypatch.setattr(helper, 'consultation_messages', forbidden)
    assert helper.handle(frame) == case['prepared']
    assert 'messages' not in helper.handle(frame)
    frame['op'] = original['op']
    assert frame == original

@pytest.mark.parametrize('case', CASES, ids=lambda c:c['name'])
def test_legacy_canonical_messages_remain_the_golden_oracle(case):
    assert helper.handle(deepcopy(case['frame'])) == case['expected']

@pytest.mark.parametrize('case', [c for c in CASES if c['name'].startswith('fixed-')], ids=lambda c:c['name'])
def test_projection_fixed_shortcut_still_uses_raw_edit_validation(case):
    frame = deepcopy(case['frame'])
    frame['op'] = 'prepare_details'
    prepared = helper.handle(frame)
    fixed = prepared['fixed_payload']
    assert fixed['evidence'] == frame['text']
    frame.update(op='validate', payload=fixed)
    before = helper.handle(deepcopy(frame))
    assert before['reason'] == 'accepted'
    assert before['step']['action'] == 'stage_player_edit'
    frame['op'] = 'prepare_details'
    helper.handle(frame)
    frame['op'] = 'validate'
    assert helper.handle(frame) == before

@pytest.mark.parametrize('raw,semantic', [
    ('シュウリョウにして', '終了にして'),
    ('終了しないよ', '終了して'),
])
def test_interpretation_never_supplies_state_change_authority(raw, semantic):
    frame = deepcopy(CASES[0]['frame'])
    frame.update(text=raw, interpreted_text=semantic, op='prepare_details')
    details = helper.handle(frame)['details']
    assert details['original_player_text'] == raw
    assert details['player_text'] == semantic
    frame.update(op='validate', payload={'action':'close_workshop','purpose':'finish_workshop',
        'confidence':0.99,'evidence':'終了','speech':'','checks':[]})
    assert helper.handle(frame)['step'] is None
