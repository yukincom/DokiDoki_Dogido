"""Python正本serviceと同じ知識/workshop境界を使うことを確認する。"""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from test_workshop_helper import frame
from workshop_helper import handle, snapshot_for
from dogido_server.state_machine import AudioAction
from dogido_server.player_input import route_player_input
from dogido_server.service import DogidoService


@pytest.mark.parametrize("text", [
    "枕詞って何？", "川柳の決まりを教えて", "ソネットの形式は？", "ダイヤモンドの剣のIDは？",
    "一は何年生で習うの？", "国語で幻の枕詞って何？", "この川柳の意味は？", "今の句とは？",
    "さくらのはって何？", "くろいおのへととは？", "この句の音数を数えて", "静かに", "/help",
    "上五を『さくらいろ』にして", "うん", "こんにちは",
])
def test_routing_matches_python_service_before_any_workshop_change(text):
    f = frame(text); f["op"] = "knowledge_route"
    unchanged = deepcopy(f)
    route = handle(f)
    workshop = snapshot_for(f)
    machine = SimpleNamespace(player_input=route_player_input(text), knowledge_query_handled=False,
        _render_knowledge_reply=lambda:"knowledge", _knowledge_speech_actions=lambda text:[AudioAction(layer="speech",interrupt=False,text=text)])
    session = SimpleNamespace(machine=machine, haiku_workshop=workshop)
    service = SimpleNamespace(_respond_to_workshop_input=lambda *args:[AudioAction(layer="speech",interrupt=False,text="workshop")])
    actions = DogidoService._haiku_workshop_actions(service,session,SimpleNamespace(observed_at=datetime.now(timezone.utc)))
    assert (route["query"] is not None) == any(a.text=="knowledge" for a in actions)
    assert f == unchanged


def test_pending_phrase_keeps_its_workshop_owner():
    f=frame("さくらいろって何？"); f["op"]="knowledge_route"
    lines=deepcopy(f["workshop"]["emission"]["lines"])
    lines[0]["reading_text"]="さくらいろ"; lines[0]["surface_text"]="桜色"
    f["workshop"]["pending"]={"lines":lines,"base":f["workshop"]["emission"]["lines"]}
    assert handle(f)["query"] is None


@pytest.mark.parametrize("pending", [False, True])
def test_known_dictionary_word_is_still_workshop_owned_when_in_the_verse(pending):
    f=frame("枕詞って何？"); f["op"]="knowledge_route"
    assert route_player_input(f["text"]).knowledge_query is not None
    assert handle(f)["query"] is not None
    lines=deepcopy(f["workshop"]["emission"]["lines"])
    lines[0]["surface_text"]="枕詞"; lines[0]["reading_text"]="まくらことば"
    if pending:
        f["workshop"]["pending"]={"lines":lines,"base":f["workshop"]["emission"]["lines"]}
    else:
        f["workshop"]["current_lines"]=lines
    assert handle(f)["query"] is None
