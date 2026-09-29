"""Canonical compatibility oracle and the remaining fixed-edit dictionary adapter."""
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


@pytest.mark.parametrize('case', CASES, ids=lambda c:c['name'])
def test_fragment_candidate_never_rebuilds_details_prompts_or_validation(case, monkeypatch):
    frame = deepcopy(case['frame'])
    frame['op'] = 'fragment_candidate'
    frame['allowed_actions'] = case['prepared']['details']['allowed_actions']
    original = deepcopy(frame)
    def forbidden(*args, **kwargs):
        raise AssertionError('native runtime must not call a Python projection or validator')
    for name in ('details_for', 'build_workshop_agent_details', 'workshop_context_details',
                 'consultation_messages', 'validate_structured_payload', 'finalize_workshop_agent_step',
                 'source_atoms_from_materials', 'line_source_ids_from_materials'):
        monkeypatch.setattr(helper, name, forbidden)
    assert helper.handle(frame) == {'fixed_payload': case['prepared'].get('fixed_payload')}
    assert frame == original


def test_runtime_has_no_obsolete_projection_exchange():
    runtime = (Path(__file__).parents[1] / 'src/dialogue/workshop_runtime.rs').read_text()
    assert '"prepare_details"' not in runtime
    assert '.exchange(json!({"op":"fixed_followup"' not in runtime
    assert '.exchange(json!({"op":"revision_input"' not in runtime
    assert 'workshop_projection::fixed_followup' in runtime
    assert 'workshop_projection::details_for' in runtime
    assert 'workshop_projection::revision_input' in runtime
