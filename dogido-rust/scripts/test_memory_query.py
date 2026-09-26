"""Keep Japanese catalog/date interpretation aligned with existing routing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pytest
from memory_query import recall_query
from dogido_server.player_input.routing import route_player_input
from test_workshop_helper import frame
from workshop_helper import handle


@pytest.mark.parametrize("text", ["雪原の句を思い出して", "寒いところの句", "覚えてる句", "海の川柳", "7月の句"])
def test_catalog_query_matches_existing_route(text):
    actual = recall_query(text)
    old = route_player_input(text).haiku_recall_query
    assert actual and old
    assert actual["biome_id"] == old.biome_id
    assert actual["biome_ids"] == list(old.biome_ids)
    assert actual["place_label"] == old.place_label


@pytest.mark.parametrize("text", ["どういう意味？", "この句はどういう意味？", "上五をさくらいろにして", "こんにちは", "今の句"])
def test_regular_workshop_inputs_are_not_recall(text):
    assert recall_query(text) is None


def test_date_uses_wall_clock_not_world_time():
    result = recall_query("今月の句を思い出して", "2026-09-27T12:00:00+09:00")
    assert result["time_label"] == "今月"
    assert result["since"].startswith("2026-09-01T00:00:00")
    assert result["until"].startswith("2026-09-27")


def test_concrete_critique_is_validated_before_native_lesson_mapping():
    f = frame("くろいおのへとは言い方が不自然だね")
    f["payload"]["action"] = "respond"
    f["payload"]["purpose"] = "continue_discussion"
    f["payload"]["speech"] = "そこが不自然やったんやね。"
    f["payload"]["findings"] = [{"line_index":1,"fragment":"くろいおのへと","problem":"unnatural_japanese","note":"不自然","confidence":.95}]
    result = handle(f)
    assert result["step"] and result["step"]["analysis"]["findings"][0]["problem"] == "unnatural_japanese", result
