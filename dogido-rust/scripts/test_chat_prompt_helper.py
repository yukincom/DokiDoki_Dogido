"""Native prompt projection preserves existing acceptance and one repair."""
import json
from pathlib import Path
import pytest
import dialogue_helper as helper
from dogido_server.config import Settings
from dogido_server.llm.types import LeafGenerationRequest
from dogido_server.llm.player_chat_prompts import build_player_chat_messages
from chat_prompt_helper import prompt_input

@pytest.mark.parametrize("outputs,expected,reason,details", [
    (["そうなんやな。"], "そうなんやな。", None, {}),
    (["This is an explanation.", "そうなんやな。"], "そうなんやな。", "non_japanese_explanation", {}),
    (["This is an explanation.", "Another explanation."], "固定の返事や。", "non_japanese_explanation", {}),
    ([RuntimeError("initial failure")], "固定の返事や。", None, {}),
    (["This is an explanation.", RuntimeError("repair failure")], "固定の返事や。", "non_japanese_explanation", {}),
    (["ウィッチがおるで。", "今の観測では分からへんわ。"], "今の観測では分からへんわ。", "unobserved_entity_name", {"speech_whitelist_enforce": True, "allowed_speech_labels": ["ゾンビ"], "forbidden_advice": []}),
])
def test_native_frame_keeps_existing_acceptance_and_bounded_repair(monkeypatch, outputs, expected, reason, details):
    settings = Settings(_env_file=None, llm_enabled=True, llm_backend="chat_completions", llm_provider="local",
                        llm_model="fixture-model", llm_max_tokens=72, audio_enabled=False, memory_enabled=False)
    frames, remaining = [], list(outputs)
    def no_python_prompt(_request):
        raise AssertionError("player_chat prompt must be assembled in Rust")
    def exchange(frame):
        frames.append(frame)
        assert frame["op"] == "chat_prompt"
        value = remaining.pop(0)
        if isinstance(value, Exception):
            raise value
        return {"generated": {"text": value, "finish_reason": "stop", "prompt_tokens": 3, "completion_tokens": 4}}
    monkeypatch.setattr(helper, "build_messages", no_python_prompt)
    monkeypatch.setattr(helper, "exchange", exchange)
    request = LeafGenerationRequest(kind="player_chat", fallback_text="固定の返事や。", temperature=.65,
        details={"user_text": "今日はどうや？", "player_name": "Player_1", "reply_stance": "none", **details})
    assert helper.BridgeLLM(settings, "fixture-model").generate_leaf_text(request) == expected
    assert len(frames) == len(outputs) and not remaining
    for frame in frames:
        payload = frame["input"]
        assert "messages" not in payload and payload["kind"] == "player_chat"
        assert payload["model"] == "fixture-model" and payload["max_tokens"] == 72
        assert payload["temperature"] == .65 and payload["enable_thinking"] is False
        assert "forbidden_advice" not in payload["details"]
    if reason:
        assert "player_chat_repair" not in frames[0]["input"]["details"]
        assert frames[1]["input"]["details"]["player_chat_repair"] == {"candidate": outputs[0], "reason": reason}


def test_fixture_still_matches_full_python_import():
    fixture = json.loads((Path(__file__).resolve().parents[1] / "src/chat_prompt/fixtures.json").read_text())
    pool = fixture["pool"]
    for details, indexes in fixture["cases"]:
        request = LeafGenerationRequest(kind="player_chat", fallback_text="", details=pool[details])
        assert build_player_chat_messages(request) == [pool[index] for index in indexes]
    with pytest.raises(ValueError):
        prompt_input(LeafGenerationRequest(kind="ambient", fallback_text="", details={}), "fixture-model", 72)
