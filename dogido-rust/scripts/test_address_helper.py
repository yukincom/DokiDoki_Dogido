"""宛先確認が既存の入力所有権を奪わないことをPython正本と比較する。"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import dialogue_helper
from dogido_server.player_input import route_player_input
from dogido_server.service import DogidoService
from test_input_helper import frame


@pytest.mark.parametrize("text", [
    "", "うん", "はいと言ったら？", "ドギド", "ドギド、家を建てたい", "君に言ったよ",
    "枕詞って何？", "マイクラのゾンビって何？", "持ち物を教えて", "松明ある？",
    "静かにして", "敵は何体？", "ゾンビはどこ？", "剣に持ち替えて", "句を思い出して",
    "今の句保存", "直し: はるのいろ / さくらのはみる / あさひかる", "草地の読みはくさち", "/help",
])
def test_general_input_ownership_matches_existing_service(text):
    session = SimpleNamespace(haiku_workshop=None)
    expected = DogidoService._is_general_conversation_input(None, session, route_player_input(text))
    with patch.object(dialogue_helper, "emit") as emit:
        dialogue_helper.run_address_route(frame(text))
    emit.assert_called_once_with({"op":"result", "general_conversation":expected})
