"""Web同意と案内音声の完了ゲート。実音声・ブラウザー・ネットは使わない。"""

from copy import deepcopy

import pytest

from dogido_server.language_dialogue.__main__ import DEFAULT_VIRTUAL_WEB, run_cases
from dogido_server.language_dialogue.controller import LanguageDialogue
from dogido_server.language_dialogue.virtual_overview import VirtualOverviewResearch
from dogido_server.language_dialogue.web_handoff import (
    WEB_PERMISSION_PROMPT, WEB_DEPARTURE, WEB_DECLINED,
)
from test_language_web_research import LLM, Search, interpretation, local_reply


QUESTION = "金床の読み方を調べて"


class CountingWeb(VirtualOverviewResearch):
    def __init__(self):
        super().__init__(DEFAULT_VIRTUAL_WEB)
        self.calls = []

    def search(self, *args, **kwargs):
        self.calls.append(deepcopy((args, {k: v for k, v in kwargs.items() if k not in {"cancelled", "emit"}})))
        return super().search(*args, **kwargs)


def first_interpretation():
    value = interpretation(QUESTION)
    value.update(target="金床", facet="reading", search_terms=["金床"],
                 web_query="金床はなんて読むの？", lookup_requested=True)
    return value


def proposed(*responses, **kwargs):
    llm, web = LLM(first_interpretation(), *responses), CountingWeb()
    dialogue = LanguageDialogue(llm, Search(found=False), web=web, **kwargs)
    row = dialogue.turn(QUESTION, turn_id="t1")
    assert row["status"] == "web_consent_requested" and row["reply"] == WEB_PERMISSION_PROMPT
    assert "web" not in row and not web.calls and not web._pending
    assert dialogue.research is None
    return dialogue, web, llm


def approve(dialogue, text="ええで"):
    row = dialogue.turn(text, turn_id="consent")
    assert row["status"] == "web_waiting_playback" and row["reply"] == WEB_DEPARTURE
    assert row["speech"]["text"] == WEB_DEPARTURE and row["speech"]["completion_required"] is True
    return row["speech"]["utterance_id"]


def complete(dialogue, speech_id, **changes):
    return dialogue.on_speech_playback_result(speech_id, **{"status": "completed", "event_id": "played", **changes})


@pytest.mark.parametrize("reason", ["explicit_request", "no_local_facts", "uncertain_reply"])
def test_every_web_trigger_stops_before_network_and_waits_for_consent(reason):
    value = first_interpretation()
    value["lookup_requested"] = reason == "explicit_request"
    values = [value]
    if reason == "uncertain_reply":
        values.append(local_reply("partial", missing="読みの根拠", missing_kind="evidence"))
    web = CountingWeb()
    dialogue = LanguageDialogue(LLM(*values), Search(found=reason != "no_local_facts"), web=web)
    row = dialogue.turn(QUESTION, turn_id="t1")
    assert row["status"] == "web_consent_requested" and row["web_proposal"]["trigger_reason"] == reason
    assert not web.calls and not dialogue.research


def test_consent_alone_never_opens_and_matching_completion_uses_original_question_once():
    dialogue, web, llm = proposed()
    speech_id = approve(dialogue)
    assert not web.calls and not dialogue.research
    assert len(llm.requests) == 1
    row = complete(dialogue, speech_id)
    assert row["status"] == "awaiting_report" and len(web.calls) == 1
    assert web.calls[0][0] == ("金床", ["金床"], "reading")
    assert web.calls[0][1]["web_query"] == "金床はなんて読むの？"
    assert dialogue.research.question == QUESTION
    assert complete(dialogue, speech_id)["status"] == "stale_playback"
    assert len(web.calls) == 1


@pytest.mark.parametrize("text", ["うん", "はい", "ええで", "いいよ", "開いて！"])
def test_representative_yes_is_scoped_to_current_permission_request(text):
    dialogue, web, _ = proposed()
    approve(dialogue, text)
    assert not web.calls


@pytest.mark.parametrize("text", ["いや", "いいえ", "やめとく", "開かないで"])
def test_decline_never_starts_web_and_does_not_mark_topic_researched(text):
    dialogue, web, _ = proposed()
    row = dialogue.turn(text, turn_id="no")
    assert row["status"] == "web_declined" and row["reply"] == WEB_DECLINED
    assert dialogue._pending_web is None and dialogue.mode == "normal"
    assert not web.calls and not dialogue.return_context()


@pytest.mark.parametrize("response", [
    {"intent": "uncertain", "evidence": "安全なら", "confidence": .98},
    {"intent": "accept", "evidence": "安全なら", "confidence": .5},
    {"intent": "accept", "evidence": "開いていいよ", "confidence": .99},
    {"intent": "accept", "evidence": "安全なら", "confidence": "0.99"},
    {"intent": "accept", "evidence": "安全なら", "confidence": .99, "open_web": True},
    {},
])
def test_uncertain_weak_ungrounded_or_invalid_consent_never_grants_playback(response):
    dialogue, web, _ = proposed(response)
    row = dialogue.turn("安全なら", turn_id="ambiguous")
    assert row["status"] == "web_consent_requested" and "speech" not in row
    assert dialogue._pending_web.phase == "awaiting_consent" and not web.calls
    assert complete(dialogue, "invented")["status"] == "stale_playback"


def test_grounded_natural_consent_uses_limited_schema_and_still_waits_for_playback():
    text = "それなら一緒にウェブを見てみたいな"
    dialogue, web, llm = proposed({"intent": "accept", "evidence": text, "confidence": .98})
    speech_id = approve(dialogue, text)
    request = llm.requests[-1]
    assert request.kind == "language_web_consent" and request.route == "chat"
    assert request.details["current"]["text"] == text
    assert request.details["permission_prompt"] == WEB_PERMISSION_PROMPT
    assert not web.calls and complete(dialogue, speech_id)["status"] == "awaiting_report"


def test_wrong_or_fabricated_playback_notification_cannot_open():
    dialogue, web, _ = proposed()
    assert complete(dialogue, "without-consent")["status"] == "stale_playback"
    speech_id = approve(dialogue)
    assert complete(dialogue, speech_id + "wrong")["status"] == "stale_playback"
    assert not web.calls
    for status in ("tts_generated", "prepared", "started", "elapsed"):
        with pytest.raises(ValueError):
            complete(dialogue, speech_id, status=status)
    assert not web.calls


@pytest.mark.parametrize("action", ["cancel", "interrupt", "decline", "failed", "cancelled", "expiry"])
def test_revoked_or_failed_departure_cannot_open_later(action):
    now = [0]
    dialogue, web, _ = proposed(clock=lambda: now[0], ttl_seconds=10)
    speech_id = approve(dialogue)
    if action in {"cancel", "interrupt"}:
        getattr(dialogue, action)()
        dialogue.release()
    elif action == "decline":
        assert dialogue.turn("やめて", turn_id="withdraw")["status"] == "web_declined"
    elif action in {"failed", "cancelled"}:
        assert complete(dialogue, speech_id, status=action)["status"] == "web_handoff_cancelled"
    else:
        now[0] = 10
    assert complete(dialogue, speech_id)["status"] in {"stale_playback", "context_expired"}
    assert not web.calls


def test_completion_during_busy_is_unconsumed_until_host_redelivers_safely():
    dialogue, web, _ = proposed()
    speech_id = approve(dialogue)
    dialogue._busy = True
    assert complete(dialogue, speech_id)["status"] == "deferred" and not web.calls
    dialogue._busy = False
    assert complete(dialogue, speech_id)["status"] == "awaiting_report"
    assert len(web.calls) == 1


def test_question_change_revokes_old_speech_and_does_not_reuse_agreement():
    text = "漢字の三は何年生で習うの？"
    changed = interpretation(text)
    changed.update(target="三", facet="grade", evidence=[{"turn_id": "change", "quote": text}])
    dialogue, web, llm = proposed({"intent": "new_question", "evidence": text, "confidence": .99}, changed)
    speech_id = approve(dialogue)
    row = dialogue.turn(text, turn_id="change")
    assert row["status"] == "web_consent_requested"  # 固定Searchに資料がないため新しい提案。
    assert complete(dialogue, speech_id)["status"] == "stale_playback" and not web.calls
    assert dialogue._pending_web.interpretation.target == "三"


def test_repeated_agreement_does_not_repeat_departure_or_replace_speech_id():
    dialogue, web, _ = proposed()
    speech_id = approve(dialogue)
    row = dialogue.turn("うん", turn_id="again")
    assert row["status"] == "web_waiting_playback" and row["reply"] == "" and "speech" not in row
    assert dialogue._pending_web.utterance_id == speech_id and not web.calls


def test_uncertainty_after_agreement_revokes_old_completion_token():
    text = "でもちょっと待って"
    dialogue, web, _ = proposed({"intent": "uncertain", "evidence": text, "confidence": .99})
    speech_id = approve(dialogue)
    assert dialogue.turn(text, turn_id="wait")["status"] == "web_consent_requested"
    assert complete(dialogue, speech_id)["status"] == "stale_playback" and not web.calls


def test_playback_failure_during_consent_interpretation_cannot_restore_grant():
    dialogue, web, llm = proposed()
    speech_id = approve(dialogue)
    text = "そのまま一緒に見にいこう"

    def failure_during_generation(request):
        assert complete(dialogue, speech_id, status="failed")["status"] == "web_handoff_cancelled"
        return {"intent": "accept", "evidence": text, "confidence": 1.0}

    llm.values.append(failure_during_generation)
    row = dialogue.turn(text, turn_id="reconfirm")
    assert row["status"] == "web_handoff_cancelled" and row["reply"] == ""
    assert dialogue._pending_web is None and not web.calls


def test_cli_never_infers_playback_completion_from_printing_departure():
    llm, web, rows = LLM(first_interpretation()), CountingWeb(), []
    dialogue = LanguageDialogue(llm, Search(found=False), web=web)
    case = {"id": "consent", "turns": [
        {"text": QUESTION}, {"text": "ええで"}, {"control": "speech_completed"},
    ]}

    def emit(row):
        if row["status"] in {"web_consent_requested", "web_waiting_playback"}:
            assert not web.calls
        rows.append(row)

    run_cases([case], llm, emit, dialogue_factory=lambda: dialogue)
    assert [r["status"] for r in rows] == ["web_consent_requested", "web_waiting_playback", "awaiting_report"]
    assert rows[-1]["simulation"] is True


def test_representative_yes_without_a_web_proposal_does_not_grant_access():
    value = interpretation("うん")
    value.update(topic="unclear", target="", target_status="ambiguous")
    web = CountingWeb()
    dialogue = LanguageDialogue(LLM(value), Search(found=False), web=web)
    row = dialogue.turn("うん", turn_id="t1")
    assert row["status"] == "clarify" and not web.calls and dialogue._pending_web is None
