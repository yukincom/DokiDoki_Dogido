"""Keep native validation goldens tied to the canonical helper, without a dictionary."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
import workshop_oracle as helper
from dogido_server import tts_reading

FIXTURES=json.loads((Path(__file__).parents[1]/'src/workshop_validation/fixtures.json').read_text())

@pytest.mark.parametrize('case',FIXTURES['cases'],ids=lambda c:c['name'])
def test_canonical_validation_oracle(case,monkeypatch):
    context=FIXTURES['contexts'][case['context']]
    frame=deepcopy(context['frame']);frame['payload']=deepcopy(case['payload'])
    details=deepcopy(context['details'])
    monkeypatch.setattr(helper,'details_for',lambda _:deepcopy(details))
    def forbidden():raise AssertionError('pure validation must not initialize a dictionary')
    monkeypatch.setattr(tts_reading,'_get_unidic_tagger',forbidden)
    if case['error']:
        with pytest.raises((TypeError,ValueError,KeyError)):helper.handle(frame)
    else:
        assert json.loads(json.dumps(helper.handle(frame),ensure_ascii=False))==case['expected']
    assert frame['payload']==case['payload']
    assert details==context['details']
