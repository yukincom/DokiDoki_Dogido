"""原文と正規化面を分けて移し、残るparserの入力所有権を維持する。"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from generate_player_text_fixtures import prepared
from input_helper import prepared_context
from dogido_server.player_input import route_player_input
from dogido_server.player_input import routing


def frame(text, **extra):
    p=prepared(text)
    context=json.loads(json.dumps(asdict(route_player_input(text)),default=lambda v:v.isoformat()))
    return {"text":text,"prepared_input":p,"prepared_context":context,
            "prepared_knowledge_query":context["knowledge_query"],
            "language_requested":p["explicit_language"],**extra}


@pytest.mark.parametrize("text",[
    "", "\n \t", "こんにちは", "和行為為団って何？", "関圧番はマイクラで何？",
    "持ち物を教えて", "今の音は何？", "ゾンビどこ？", "敵は何体？", "静かにして", "剣に持ち替えて",
    "県に持ち替えて", "句を思い出して", "川柳保存: 今日の空\n見上げて歩く\nいい天気",
    "直し: 桜の葉\n黒い斧へと\n朝の色", "草地の読みはくさち", "/say 枕詞って何？",
])
def test_prepared_projection_preserves_all_parser_fields_without_normalizing_twice(monkeypatch,text):
    # 「今日」の期間終端を同じ時刻に固定し、二回の実行時刻の差を比較しない。
    parse_range=routing.parse_haiku_time_range
    monkeypatch.setattr(routing,"parse_haiku_time_range",lambda value: parse_range(value,now=datetime(2026,9,28,tzinfo=timezone.utc)))
    expected=asdict(route_player_input(text))
    data=frame(text)
    def fail(*args,**kwargs):raise AssertionError("Rust-prepared input must not normalize again")
    monkeypatch.setattr(routing,"normalize_player_text",fail)
    monkeypatch.setattr(routing,"apply_asr_fixes",fail)
    monkeypatch.setattr(routing,"extract_explicit_knowledge_query",fail)
    for name in ("route_player_input", "route_prepared_player_input", "is_explicit_select_sword_request",
                 "asks_about_sound", "asks_dragon_direction", "asks_hostile_direction", "asks_haiku_recall",
                 "asks_hostile_count", "asks_inventory", "asks_save_last_haiku", "extract_player_haiku",
                 "extract_reading_correction", "extract_revised_haiku", "is_explicit_reading_correction",
                 "parse_haiku_time_range", "should_block_ambient", "wants_quiet"):
        monkeypatch.setattr(routing,name,fail)
    assert asdict(prepared_context(data))==expected


@pytest.mark.parametrize("prepared_input",[{},None,{"raw_text":"別の入力","normalized_text":"語"},{"raw_text":"今の入力","normalized_text":None}])
def test_projection_cannot_be_attached_to_another_input(prepared_input):
    with pytest.raises(ValueError,match="prepared input"):
        prepared_context({"text":"今の入力","prepared_input":prepared_input})


@pytest.mark.parametrize("change", [{"evidence":"別の入力"}, {"domain":"world_operation"}, {"intent":"select_sword"}, {"subject":[]}, {"unexpected":True}])
def test_prepared_query_is_bound_to_the_current_input_and_closed_types(change):
    data=frame("枕詞って何？")
    data["prepared_knowledge_query"].update(change)
    with pytest.raises(ValueError,match="prepared knowledge query"):
        prepared_context(data)


def test_all_canonical_context_fields_convert_without_parser_calls(monkeypatch):
    def fail(*args,**kwargs):
        raise AssertionError("native context must not be classified again")
    monkeypatch.setattr(routing, "route_prepared_player_input", fail)
    monkeypatch.setattr(routing, "route_player_input", fail)
    from dogido_server import entry_catalog
    monkeypatch.setattr(entry_catalog, "resolve_biome_place_from_text", fail)
    cases=json.loads((Path(__file__).resolve().parents[1]/"fixtures/input-context.json").read_text())
    for case in cases:
        if case["interpreted"]:
            continue  # this helper's production boundary has only the current raw input
        expected=case["expected"]
        data={"text":case["raw"],"prepared_input":{"raw_text":case["raw"],"normalized_text":case["normalized"]},
              "prepared_context":expected,"prepared_knowledge_query":expected["knowledge_query"]}
        actual=prepared_context(data)
        assert json.loads(json.dumps(asdict(actual),default=lambda v:v.isoformat()))==expected,case["raw"]
        if actual.haiku_recall_query:
            assert type(actual.haiku_recall_query.biome_ids) is tuple
            assert type(actual.haiku_recall_query.group_ids) is tuple
            assert actual.haiku_recall_query.since is None or isinstance(actual.haiku_recall_query.since,datetime)


@pytest.mark.parametrize("change", [{"wants_quiet":1}, {"requests_sword":True}, {"normalized_text":"別の入力"},
                                   {"unexpected":False}, {"reading_correction":{"surface":"草地","reading":"くさち","wrong_reading":"そうち","explicit":False}}])
def test_context_rejects_wrong_types_or_unbound_surfaces(change):
    data=frame("草地はくさち")
    data["prepared_context"].update(change)
    with pytest.raises(ValueError,match="prepared context"):
        prepared_context(data)
