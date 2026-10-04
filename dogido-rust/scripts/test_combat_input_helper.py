import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
from combat_input_helper import Worker
from dogido_server.combat_input_contract import CombatInputRequest


def frame(provider='chat'):
    return {"op": "classify", "text": "句に戻ろう", "verse": "句",
            "settings": {"provider": provider},
            "allowed_actions": ["resume_workshop", "workshop_input", "close_workshop", "unrelated", "uncertain"],
            "messages": [{"role": "system", "content": "Rustが確定した5分類の指示"},
                         {"role": "user", "content": "句に戻ろう"}]}


def test_configured_chat_does_not_probe_or_call_os(monkeypatch):
    from dogido_server.platform_ai import AppleFoundationModelsProvider, FoundryLocalProvider
    def forbidden(*a,**k): raise AssertionError("OS must not be touched")
    monkeypatch.setattr(AppleFoundationModelsProvider,"probe",forbidden)
    monkeypatch.setattr(FoundryLocalProvider,"probe",forbidden)
    w=Worker()
    try:
        r=w.handle(frame())
        assert r["needs_chat"] and r["provider"]=="chat_fallback" and "messages" not in r
        assert not w.router.settings.platform_ai_allow_model_download
    finally: w.close()


def test_available_os_uses_same_router_and_does_not_request_chat(monkeypatch):
    from dogido_server.platform_ai import AppleFoundationModelsProvider, PlatformAIProbe
    monkeypatch.setattr(AppleFoundationModelsProvider,"probe",lambda self:PlatformAIProbe(self.name,True,"test"))
    requests = []
    def generate(self, request):
        requests.append(request)
        assert isinstance(request, CombatInputRequest)
        return {"action":"resume_workshop","confidence":.95,"evidence":"句に戻ろう"}
    monkeypatch.setattr(AppleFoundationModelsProvider, "generate", generate)
    w=Worker()
    try:
        request_frame = frame("apple")
        r=w.handle(request_frame); original=w.router
        assert not r["needs_chat"] and r["provider"]=="apple_foundation_models"
        next_frame = {**request_frame, "allowed_actions": ["resume_workshop"]}
        w.handle(next_frame);assert w.router is original
        assert len(requests) == 2
        assert all(request.messages == request_frame["messages"] for request in requests)
        assert requests[0].details["allowed_actions"] == request_frame["allowed_actions"]
        assert requests[1].details["allowed_actions"] == next_frame["allowed_actions"]
    finally:w.close()


def test_missing_rust_messages_never_calls_a_provider(monkeypatch):
    from dogido_server.platform_ai import AppleFoundationModelsProvider, FoundryLocalProvider
    def forbidden(*args, **kwargs):
        raise AssertionError("SDK must not run for a malformed IPC frame")
    for provider in (AppleFoundationModelsProvider, FoundryLocalProvider):
        monkeypatch.setattr(provider, "probe", forbidden)
        monkeypatch.setattr(provider, "generate", forbidden)
    monkeypatch.setattr("combat_input_helper.PlatformStructuredAIRouter", forbidden)
    request = frame('apple')
    del request['messages']
    worker = Worker()
    try:
        with pytest.raises(KeyError, match='messages'):
            worker.handle(request)
    finally:
        worker.close()


@pytest.mark.parametrize("actions", [None, "uncertain", {}, [], [None], [1], [True],
                                      [[]], [""], [" "], [" uncertain"], ["uncertain\n"],
                                      ["uncertain", "uncertain"]])
def test_invalid_rust_actions_never_initialize_router(monkeypatch, actions):
    def forbidden(*args, **kwargs):
        raise AssertionError("malformed IPC must not initialize the router")
    monkeypatch.setattr("combat_input_helper.PlatformStructuredAIRouter", forbidden)
    request = frame('apple')
    request["allowed_actions"] = actions
    worker = Worker()
    try:
        with pytest.raises(ValueError, match="allowed_actions"):
            worker.handle(request)
        assert worker.router is None
    finally:
        worker.close()


def test_missing_rust_actions_never_initialize_router(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("missing contract must not initialize the router")
    monkeypatch.setattr("combat_input_helper.PlatformStructuredAIRouter", forbidden)
    request = frame('apple')
    del request["allowed_actions"]
    worker = Worker()
    try:
        with pytest.raises(ValueError, match="allowed_actions"):
            worker.handle(request)
        assert worker.router is None
    finally:
        worker.close()
