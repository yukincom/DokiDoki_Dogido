import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
from combat_input_helper import Worker, safe


@pytest.mark.parametrize("action,text", [("close_workshop","句は終わりにしない"),
    ("resume_workshop","『句の続きを話そう』と言われた"), ("close_workshop","句を終えたらどうなる？"),
    ("resume_workshop","句に戻らない"), ("resume_workshop","句を続けるな"), ("resume_workshop","うん")])
def test_negation_quote_and_condition_do_not_change_pause(action,text):
    assert not safe(action,text,text)


def test_configured_chat_does_not_probe_or_call_os(monkeypatch):
    from dogido_server.platform_ai import AppleFoundationModelsProvider, FoundryLocalProvider
    def forbidden(*a,**k): raise AssertionError("OS must not be touched")
    monkeypatch.setattr(AppleFoundationModelsProvider,"probe",forbidden)
    monkeypatch.setattr(FoundryLocalProvider,"probe",forbidden)
    w=Worker()
    try:
        r=w.handle({"op":"classify","text":"句の続き", "verse":"句", "settings":{"provider":"chat"}})
        assert r["needs_chat"] and r["provider"]=="chat_fallback" and r["messages"]
        assert not w.router.settings.platform_ai_allow_model_download
    finally: w.close()


def test_available_os_uses_same_router_and_does_not_request_chat(monkeypatch):
    from dogido_server.platform_ai import AppleFoundationModelsProvider, PlatformAIProbe
    monkeypatch.setattr(AppleFoundationModelsProvider,"probe",lambda self:PlatformAIProbe(self.name,True,"test"))
    monkeypatch.setattr(AppleFoundationModelsProvider,"generate",lambda self,request:{"action":"resume_workshop","confidence":.95,"evidence":"句に戻ろう"})
    w=Worker()
    try:
        frame={"op":"classify","text":"句に戻ろう","verse":"句","settings":{"provider":"apple"}}
        r=w.handle(frame); original=w.router
        assert not r["needs_chat"] and r["provider"]=="apple_foundation_models"
        w.handle(frame);assert w.router is original
    finally:w.close()
