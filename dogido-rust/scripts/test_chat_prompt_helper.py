"""Native chat leaf owns acceptance and repair; Python only projects observations."""
import json
from pathlib import Path
import pytest
import dialogue_helper as helper
from dogido_server.config import Settings
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.llm.player_chat_prompts import build_player_chat_messages
from chat_prompt_helper import prompt_input

@pytest.mark.parametrize("selected,details", [
    ("そうなんやな。", {}),
    ("固定の返事や。", {}),
    ("今の観測では分からへんわ。", {"speech_whitelist_enforce": True,
        "allowed_speech_labels": ["ゾンビ"], "forbidden_advice": [],
        "speech_name_corrections":{"ゾンビ":"村人ゾンビ"}}),
])
def test_native_frame_delegates_entire_leaf_once_without_python_validation(monkeypatch, selected, details):
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions", llm_provider="local",
                        llm_model="fixture-model", llm_max_tokens=72, audio_enabled=False, memory_enabled=False)
    frames = []
    def forbidden(*_args, **_kwargs):
        raise AssertionError("player_chat prompt, validation and retry must run in Rust")
    def exchange(frame):
        frames.append(frame)
        assert frame["op"] == "chat_leaf"
        return {"text": selected}
    monkeypatch.setattr(helper, "build_messages", forbidden)
    monkeypatch.setattr(helper.DogidoLLM, "_validate_leaf_candidate", forbidden)
    monkeypatch.setattr(helper.BridgeLLM, "_generate_backend_text", forbidden)
    monkeypatch.setattr(helper, "exchange", exchange)
    request = LeafGenerationRequest(kind="player_chat", fallback_text="固定の返事や。", temperature=.65,
        details={"user_text": "今日はどうや？", "player_name": "Player_1", "reply_stance": "none", **details})
    assert helper.BridgeLLM(settings, "fixture-model").generate_leaf_text(request) == selected
    assert len(frames) == 1
    frame = frames[0]["input"]
    prompt = frame["prompt"]
    assert "messages" not in prompt and prompt["kind"] == "player_chat"
    assert prompt["model"] == "fixture-model" and prompt["max_tokens"] == 72
    assert prompt["temperature"] == .65 and prompt["enable_thinking"] is False
    assert "forbidden_advice" not in prompt["details"]
    for field in details:
        assert frame["validation"][field] == details[field]
    assert frame["fallback_text"] == "固定の返事や。"
    assert "player_chat_repair" not in prompt["details"]


def test_native_frame_rejects_malformed_host_text(monkeypatch):
    settings=Settings(_env_file=None)
    monkeypatch.setattr(helper,"exchange",lambda _:{"text":None})
    with pytest.raises(ValueError):
        helper.BridgeLLM(settings,"fixture-model").generate_leaf_text(
            LeafGenerationRequest(kind="player_chat",fallback_text="固定の返事や。",details={}))


def test_fixture_still_matches_full_python_import():
    fixture = json.loads((Path(__file__).resolve().parents[1] / "src/chat_prompt/fixtures.json").read_text())
    pool = fixture["pool"]
    for details, indexes in fixture["cases"]:
        request = LeafGenerationRequest(kind="player_chat", fallback_text="", details=pool[details])
        assert build_player_chat_messages(request) == [pool[index] for index in indexes]
    with pytest.raises(ValueError):
        prompt_input(LeafGenerationRequest(kind="ambient", fallback_text="", details={}), "fixture-model", 72)


def test_native_speech_labels_match_current_catalog():
    from dogido_server.dialogue.chat_policy import catalog_speech_labels
    path=Path(__file__).resolve().parents[1] / "src/chat_validation/labels.json"
    assert json.loads(path.read_text()) == list(catalog_speech_labels())
