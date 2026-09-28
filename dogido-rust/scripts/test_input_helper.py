"""原文と正規化面を分けて移し、残るparserの入力所有権を維持する。"""
from dataclasses import asdict
from datetime import datetime, timezone
import pytest
from generate_player_text_fixtures import prepared
from input_helper import prepared_context
from dogido_server.player_input import route_player_input
from dogido_server.player_input import routing


def frame(text, **extra):
    p=prepared(text)
    return {"text":text,"prepared_input":p,"language_requested":p["explicit_language"],**extra}


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
    assert asdict(prepared_context(data))==expected


@pytest.mark.parametrize("prepared_input",[{},None,{"raw_text":"別の入力","normalized_text":"語"},{"raw_text":"今の入力","normalized_text":None}])
def test_projection_cannot_be_attached_to_another_input(prepared_input):
    with pytest.raises(ValueError,match="prepared input"):
        prepared_context({"text":"今の入力","prepared_input":prepared_input})
