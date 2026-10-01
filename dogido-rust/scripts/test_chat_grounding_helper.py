import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import pytest
import dialogue_helper as helper
from chat_grounding_helper import grounding_input, grounding_result
from dogido_server.dialogue import player_chat_planner as planner
from dogido_server.config import Settings
from dogido_server.models import (GameEvent, EventDescriptor, EventName, SourceKind, PriorityHint, Certainty, PlayerState, Position, WorldState, TimePhase, Weather, CombatState, MetaState)
from dogido_server.player_input import route_player_input
from dogido_server.state_machine import DogidoStateMachine
from dogido_server.state_machine.mixins import narration


def test_closed_frame_returns_native_grounding_and_never_calls_python_matcher(monkeypatch):
    p=planner._fallback_plan('ヤギいる？')
    hits=[{'entry_id':'goat','label_ja':'ヤギ','score':1,'facts':['not sent']}]
    observed=[]
    g=planner.ground_player_chat_entity(p,topic_hits=hits,observed_entities=observed)
    fixed=planner.fixed_grounded_player_chat_reply(p,g)
    frames=[]
    def exchange(frame):
        frames.append(frame)
        return {'grounding':asdict(g) | {'candidate_ids':list(g.candidate_ids),'candidate_labels':list(g.candidate_labels),
            'observed_ids':list(g.observed_ids),'observed_labels':list(g.observed_labels)}, 'fixed_reply':fixed}
    monkeypatch.setattr(helper,'exchange',exchange)
    def forbidden(*args,**kwargs): raise AssertionError('Python grounding was invoked')
    monkeypatch.setattr(planner,'ground_player_chat_entity',forbidden)
    monkeypatch.setattr(planner,'fixed_grounded_player_chat_reply',forbidden)
    llm=helper.BridgeLLM(Settings(_env_file=None),'fixture')
    assert llm.ground_player_chat(p,topic_hits=hits,observed_entities=observed)==(g,fixed)
    assert len(frames)==1 and frames[0]['op']=='chat_ground'
    assert set(frames[0]['input']['topic_hits'][0])=={'entry_id','label','score'}
    assert frames[0]['input']['source']=='current_observation'


def test_projection_roundtrip_matches_all_python_grounding_fixtures():
    rows=json.loads((Path(__file__).resolve().parents[1]/'fixtures/planner/grounding.json').read_text())['cases']
    for row in rows:
        output=row['expected']
        g,fixed=grounding_result(output)
        assert json.loads(json.dumps(asdict(g)))==output['grounding']
        assert fixed==output['fixed_reply']
    with pytest.raises(ValueError): grounding_result({'grounding':{},'fixed_reply':None})


def test_narration_native_hook_bypasses_old_grounding_but_python_default_remains(monkeypatch):
    event=GameEvent(schema_version='2026-05-24',adapter='test',observed_at=datetime.now(timezone.utc),sequence=1,
        event=EventDescriptor(name=EventName.STATUS_SNAPSHOT,source_kind=SourceKind.SYSTEM,priority_hint=PriorityHint.BACKGROUND,certainty=Certainty.HIGH),
        player=PlayerState(name='player',position=Position(x=0,y=64,z=0),dimension='minecraft:overworld',health=20,hunger=20),
        world=WorldState(time_phase=TimePhase.DAY,weather=Weather.CLEAR,biome='plains',local_light=15),combat=CombatState(),meta=MetaState(user_text='ヤギいる？'))
    settings=Settings(_env_file=None,llm_enabled=False,audio_enabled=False,memory_enabled=False)
    original_ground=planner.ground_player_chat_entity
    original_fixed=planner.fixed_grounded_player_chat_reply
    from dogido_server.dialogue import chat_policy
    original_filter = chat_policy.filter_usable_topic_hits
    original_stance = chat_policy.resolve_reply_stance
    original_policy = chat_policy.reply_policy_line
    native_hits = DogidoStateMachine(settings)._player_chat_topic_hits("ヤギ", [])
    class Native:
        native_topic_catalog = True
        def __init__(self): self.calls=[]
        def prepare_player_chat_topics(self,plan,**kw):
            self.calls.append((plan,kw))
            assert kw["topic_hits"] == []
            kw = {**kw, "topic_hits": native_hits}
            usable = original_filter(kw["topic_hits"])
            g=original_ground(plan,topic_hits=usable,observed_entities=kw["observed_entities"])
            stance=original_stance(**{k:v for k,v in kw.items() if k not in {"observed_entities", "name_context"}})
            return g,original_fixed(plan,g), {"usable_topic_hits":usable,"reply_stance":stance,
                "reply_policy":original_policy(stance),"topic_for_identify":[],"identify_skeleton":None,
                "raw_topic_hits":native_hits, "catalog_topic_hints":""}
    def run(llm):
        machine=DogidoStateMachine(settings,llm=llm)
        machine.player_input=route_player_input('ヤギいる？')
        return machine._render_player_chat_reply(event)
    default=run(None)
    def forbidden(*args,**kwargs): raise AssertionError('legacy grounding after native handoff')
    monkeypatch.setattr(planner,'ground_player_chat_entity',forbidden)
    monkeypatch.setattr(planner,'fixed_grounded_player_chat_reply',forbidden)
    monkeypatch.setattr(chat_policy,'build_identify_skeleton',forbidden)
    monkeypatch.setattr(DogidoStateMachine,'_player_chat_topic_hits',forbidden)
    monkeypatch.setattr(DogidoStateMachine,'_format_player_chat_topic_hints',forbidden)
    native=Native()
    assert run(native)==default=='今の観測では、ヤギは確認できてへんわ。'
    assert len(native.calls)==1
    assert native.calls[0][1]['observed_entities']==[]


def test_topic_projection_preserves_full_catalog_rows_and_native_indices(monkeypatch):
    from chat_grounding_helper import topics_input, topics_result
    p = planner._fallback_plan("ヤギいる？")
    hits = [{"entry_id":"goat", "label_ja":"ヤギ", "score":8.0,
             "matched_terms":["ヤギ"], "facts":["keep original metadata"]}]
    frame = topics_input(p, topic_hits=hits, observed_entities=[], has_visual_threats=False,
                         threat_summary="", user_text=p.entity_query, observed_ids=[])
    assert frame["topic_policy"]["topic_hits"][0]["matched_terms"] == ["ヤギ"]
    assert "facts" not in frame["topic_policy"]["topic_hits"][0]
    g = planner.ground_player_chat_entity(p, topic_hits=hits, observed_entities=[])
    report = json.loads(json.dumps({"grounding":asdict(g), "fixed_reply":planner.fixed_grounded_player_chat_reply(p,g),
        "topics":{"usable_indices":[0], "reply_stance":"none", "reply_policy":"方針",
                  "topic_for_identify_indices":[], "identify_skeleton":None}}))
    _, _, policy = topics_result(report, hits)
    assert policy["usable_topic_hits"][0] is hits[0]
    report["topics"]["usable_indices"] = [True]
    with pytest.raises(ValueError): topics_result(report, hits)
    report["topics"]["usable_indices"] = [1]
    with pytest.raises(ValueError): topics_result(report, hits)
