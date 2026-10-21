#!/usr/bin/env python3
"""乗れる動物の二段native API操作を実HTTP・模擬LLM/TTSで確認する。Minecraftや実モデルは使わない。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid

CAPABILITY = "integrated_server.horse.recall.v2"
HORSE_NAME = "HORSE_CANDIDATE_SENTINEL_b774c"
RENAMED = "HORSE_RENAMED_SENTINEL_b774c"
WORLD_ID = "10000000-0000-0000-0000-000000000001"
PLAYER_ID = "20000000-0000-0000-0000-000000000001"
HORSE_ID = "30000000-0000-0000-0000-000000000001"
RECALL = "さっき乗った馬をここに呼んで"
CHAT = "今日は空がきれいやね"
SPEECH = "せやな、空を見とると落ち着くわ。"
SUCCESS_STORY = "Minecraftで、依頼された乗れる動物の呼び戻しが成功した。"
FAILURE_STORY = "依頼された乗れる動物の呼び戻しは成功を確認できなかった。理由: 個体または命令の結果が一致せず、移動成功を確認できなかった。"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class MockPlan:
    """呼出順と候補公開範囲を検査し、決まったAPI応答だけを返す。"""
    def __init__(self, responses, *, capability=True, block_selection=False):
        self.responses = responses
        self.capability = capability
        self.block_selection = block_selection
        self.calls = []
        self.errors = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not block_selection:
            self.release.set()
        self.lock = threading.Lock()

    def __call__(self, incoming):
        with self.lock:
            index = len(self.calls)
            self.calls.append(incoming)
        try:
            assert index < len(self.responses), f"unexpected extra LLM call {index + 1}"
            stage, response = self.responses[index]
            prompt = json.dumps(incoming["messages"], ensure_ascii=False)
            if self.capability:
                names = [t["function"]["name"] for t in incoming.get("tools", [])]
                expected = {"selection": ["select_horse"], "selection_unavailable": [],
                            "ordinary": ["list_horses"], "fallback_chat": []}
                assert names == expected[stage], names
            else:
                assert not incoming.get("tools"), "missing capability exposed tools"
            assert WORLD_ID not in prompt and PLAYER_ID not in prompt and HORSE_ID not in prompt, "UUID leaked to model"
            if stage in ("selection", "selection_unavailable"):
                assert HORSE_NAME in prompt, "second call did not receive observed candidate name"
                assert "candidate_id" in prompt and "h1" in prompt, "second call has no bounded candidate ID"
                if stage == "selection":
                    assert "select_horse" in prompt, "second call does not declare select_horse"
                self.entered.set()
                if not self.release.wait(timeout=8):
                    raise AssertionError("mock selection gate was not released")
            else:
                assert HORSE_NAME not in prompt and RENAMED not in prompt, "candidate names reached ordinary chat"
                if self.capability and stage != "fallback_chat":
                    assert "list_horses" in prompt, "ordinary chat lacks list_horses contract"
                else:
                    assert "list_horses" not in prompt and "select_horse" not in prompt, "ordinary fallback or unsupported capability exposed horse action"
            if self.capability and stage != "fallback_chat":
                if response["action"] in ("list_horses", "select_horse"):
                    arguments = {k: v for k, v in response.items() if k != "action"}
                    return {"role": "assistant", "content": None, "tool_calls": [{
                        "id": f"mock-horse-{index}", "type": "function", "function": {
                            "name": response["action"], "arguments": json.dumps(arguments, ensure_ascii=False)}}]}
                return {"role": "assistant", "content": response["speech"], "tool_calls": []}
            return response
        except BaseException as error:
            with self.lock:
                self.errors.append(f"call {index + 1}: {error}")
            return {"action": "speak", "speech": "模擬検査で不整合があったで。"}

    def verify(self):
        assert not self.errors, self.errors
        assert len(self.calls) == len(self.responses), (len(self.calls), len(self.responses))


def list_response(text=RECALL):
    return {"action": "list_horses", "evidence": text, "confidence": .99}


def select_response(text=RECALL):
    return {"action": "select_horse", "candidate_id": "h1", "evidence": text, "confidence": .99}


def selection_plan(*, block=False, after_chat=False, text=RECALL):
    responses = [("ordinary", list_response(text)), ("selection", select_response(text))]
    if after_chat:
        responses.append(("ordinary", {"action": "speak", "speech": SPEECH}))
    return MockPlan(responses, block_selection=block)


@contextmanager
def fixture(helper, binary, plan, *, capability=True, fail_selection=False):
    """新規server、session、記憶先、localhost依存だけを所有して終了時に回収する。"""
    with tempfile.TemporaryDirectory(prefix="dogido-horse-http-") as temp, helper.dependencies() as (dep, control, seen):
        directory = Path(temp)
        def respond(incoming):
            response = plan(incoming)
            if fail_selection and any(t["function"]["name"] == "select_horse" for t in incoming.get("tools", [])):
                raise ConnectionError("intentional mock selection transport failure")
            return response
        control["structured_handler"] = respond
        try:
            with helper.running(
                binary, directory, dep, live=False,
                haiku_settings={"enabled": False, "memory_enabled": False,
                                "memory_dir": str(directory / "memory")},
            ) as (base, process, log):
                capabilities = ["dialogue_text.v1"]
                session = helper.request(base, "/api/v1/adapter-sessions", {
                    "adapter_name": "fabric-test", "adapter_version": "horse-http-test",
                    "game": "minecraft-java", "schema_version": "2026-05-24", "player_name": "試験",
                    "capabilities": capabilities, "voice_enabled": False,
                    "execution_capabilities": [CAPABILITY] if capability else [],
                })["session_id"]
                state = {"sequence": 0, "name": HORSE_NAME, "combat": False}
                def send(*, results=(), event_name="status_snapshot", horses=True, paused=False, damage=False):
                    state["sequence"] += 1
                    observed = utc_now()
                    event = {
                        "schema_version": "2026-05-24", "game": "minecraft-java", "adapter": "fixture",
                        "sequence": state["sequence"], "observed_at": observed,
                        "event": {"name": event_name, "source_kind": "system", "priority_hint": "background", "certainty": "high"},
                        "player": {"name": "試験", "dimension": "minecraft:overworld", "position": {"x": 0, "y": 64, "z": 0}},
                        "world": {"biome": "minecraft:plains", "time_phase": "day", "weather": "clear", "local_light": 15,
                                  "danger_darkness_score": 0, "sky_visible": True, "ceiling_height": 20, "game_paused": paused},
                        "combat": {"combat_active_hint": False},
                        "owned_horses": {"schema_version": 2, "status": "available", "world_id": WORLD_ID,
                                         "revision": state["sequence"], "observed_at": observed, "player_uuid": PLAYER_ID,
                                         "dimension": "minecraft:overworld", "recent_mount_id": HORSE_ID,
                                         "candidates": [{"entity_id": HORSE_ID, "custom_name": state["name"], "tamed": state.get("tamed", True),
                                                         "entity_type": state.get("kind", "horse"), "owner_uuid": state.get("owner", PLAYER_ID),
                                                         "adult": True, "equipped": state.get("equipped", True), "has_vehicle": False, "alive": True, "dimension": "minecraft:overworld",
                                                         "has_passengers": state.get("passengers", False), "leashed": False}]},
                        "command_results": list(results),
                    }
                    if not horses:
                        del event["owned_horses"]
                    if state["combat"]:
                        event["visual_threats"] = [
                            {"type": "zombie", "entity_id": f"z{i}", "distance": 3,
                             "direction": {"horizontal": "front"}} for i in range(2)]
                        event["combat"].update(combat_active_hint=True, hostiles_within_10=2)
                    if state.get("alert"):
                        event["visual_threats"] = [{"type": "creeper", "entity_id": "alert-creeper", "distance": 9,
                                                    "direction": {"horizontal": "back"}}]
                        event["combat"].update(combat_active_hint=True, hostiles_within_10=1)
                    if damage:
                        event["combat"]["recent_damage_ms"] = 0
                    response = helper.request(base, "/api/v1/game-events", event, sid=session)
                    assert response["accepted"], response
                    return response
                def rows():
                    return [row for row in helper.snapshot(base)["utterances"] if row["session_id"] == session]
                def history():
                    return next(row["history"] for row in helper.snapshot(base)["sessions"] if row["session_id"] == session)
                def stories(text):
                    return [entry for entry in history() if entry.get("reaction") == "story" and entry.get("text") == text]
                assert not send().get("commands"), "spontaneous operation before request"
                try:
                    yield base, session, send, rows, history, stories, state, seen
                except BaseException:
                    print(json.dumps({"mock_errors": plan.errors, "calls": len(plan.calls),
                                      "snapshot": helper.snapshot(base)}, ensure_ascii=False), flush=True)
                    raise
        finally:
            plan.release.set()


def get_command(helper, send):
    def pending():
        response = send()
        return response if response.get("commands") else None
    response = helper.wait_for(pending, timeout=5)
    assert len(response["commands"]) == 1, response
    command = response["commands"][0]
    assert command["type"] == "recall_horse", command
    uuid.UUID(command["command_id"])
    assert command["entity_id"] == HORSE_ID and command["world_id"] == WORLD_ID, command
    assert command["player_uuid"] == PLAYER_ID and command["dimension"] == "minecraft:overworld", command
    issued = datetime.fromisoformat(command["issued_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00"))
    assert (expires - issued).total_seconds() == 5, command
    return command


def result(command, *, entity_id=HORSE_ID):
    # Rust AdapterCommandResult.extra is serde(flatten): identifiers are top-level wire fields.
    return {"command_id": command["command_id"], "command_type": "recall_horse", "status": "succeeded",
            "executed_at": utc_now(), "detail_code": "recalled", "entity_id": entity_id, "world_id": WORLD_ID}


def quiet_commands(send, seconds=.35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        assert not send().get("commands"), "unexpected or resurrected command"
        time.sleep(.03)


def check_success(helper, binary, passed):
    plan = selection_plan()
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        turn = helper.submit_text(base, sid, RECALL)
        command = get_command(helper, send)
        assert not stories(SUCCESS_STORY), history()
        assert not [r for r in rows() if r.get("text") == "こっちへ来たで。"], rows()
        assert send()["commands"] == [command], "retransmission changed command identity"
        unknown = result(command)
        unknown["command_id"] = str(uuid.uuid4())
        response = send(results=[unknown])
        assert response["acknowledged_command_ids"] == [unknown["command_id"]], response
        assert response["commands"] == [command] and not stories(SUCCESS_STORY), response
        receipt = result(command)
        response = send(results=[receipt])
        assert response["acknowledged_command_ids"] == [command["command_id"]] and not response["commands"], response
        helper.wait_for(lambda: stories(SUCCESS_STORY))
        assert len(stories(SUCCESS_STORY)) == 1, history()
        repeated = send(results=[receipt, receipt])
        assert repeated["acknowledged_command_ids"] == [command["command_id"]], repeated
        assert len(stories(SUCCESS_STORY)) == 1 and not repeated["commands"], repeated
        quiet_commands(send)
        assert sum(r.get("horse_command_id") == command["command_id"] for r in rows()) == 1, rows()
        assert not any(item["path"].startswith(("/audio_query", "/synthesis")) for item in seen), "voice-off produced TTS"
        plan.verify()
    passed.append("two_stage_candidate_visibility_one_command_retransmission_and_verified_ack_once")


def check_wrong_identity(helper, binary, passed):
    plan = selection_plan()
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        helper.submit_text(base, sid, RECALL)
        command = get_command(helper, send)
        bad = result(command, entity_id="30000000-0000-0000-0000-000000000002")
        response = send(results=[bad])
        assert response["acknowledged_command_ids"] == [command["command_id"]], response
        assert not response["commands"] and not stories(SUCCESS_STORY), response
        helper.wait_for(lambda: stories(FAILURE_STORY))
        response = send(results=[result(command)])
        assert response["acknowledged_command_ids"] == [command["command_id"]], response
        assert not stories(SUCCESS_STORY), history()
        assert len(stories(FAILURE_STORY)) == 1, history()
        plan.verify()
    passed.append("wrong_entity_ack_never_establishes_success_or_revives_on_later_correct_receipt")


def check_delayed_receipt(helper, binary, passed):
    plan = selection_plan()
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        helper.submit_text(base, sid, RECALL)
        command = get_command(helper, send)
        receipt = result(command)
        expires = datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00"))
        # 実行時刻を期限内に固定し、実HTTPでは次の観測まで結果が遅れた状況を作る。
        while datetime.now(timezone.utc) <= expires:
            assert not stories(FAILURE_STORY), history()
            send()
            time.sleep(.1)
        quiet_commands(send, seconds=.2)
        assert not stories(FAILURE_STORY) and not stories(SUCCESS_STORY), history()
        response = send(results=[receipt])
        assert response["acknowledged_command_ids"] == [command["command_id"]], response
        helper.wait_for(lambda: stories(SUCCESS_STORY))
        assert len(stories(SUCCESS_STORY)) == 1 and not stories(FAILURE_STORY), history()
        send(results=[receipt])
        assert len(stories(SUCCESS_STORY)) == 1, history()
        plan.verify()
    passed.append("on_time_execution_with_delayed_receipt_succeeds_without_post_deadline_retransmission")


def check_chat(helper, binary, passed, *, capability):
    plan = MockPlan([("ordinary", {"action": "speak", "speech": SPEECH})], capability=capability)
    with fixture(helper, binary, plan, capability=capability) as (base, sid, send, rows, history, stories, state, seen):
        # no-capでも同じ馬依頼を通常会話へ通し、操作契約を公開していないことを確認する。
        turn = helper.submit_text(base, sid, CHAT if capability else RECALL)
        reply = helper.wait_for(lambda: helper.display_row(base, turn))
        assert reply["text"] == SPEECH, reply
        helper.acknowledge(base, sid, reply)
        quiet_commands(send)
        assert not stories(SUCCESS_STORY), history()
        plan.verify()
    passed.append("ordinary_chat_one_call_without_candidate_lookup" if capability else "missing_capability_keeps_one_ordinary_call_and_no_command")


def check_change_during_selection(helper, binary, passed, *, cancel):
    plan = selection_plan(block=True, after_chat=cancel)
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        try:
            old = helper.submit_text(base, sid, RECALL)
            helper.wait_for(plan.entered.is_set, timeout=5)
            assert not send().get("commands"), "issued before selection completed"
            if cancel:
                newer = helper.submit_text(base, sid, CHAT)
                plan.release.set()
                helper.wait_for(lambda: helper.row(base, old, {"cancelled"}))
                reply = helper.wait_for(lambda: helper.display_row(base, newer))
                assert reply["text"] == SPEECH, reply
            else:
                state["name"] = RENAMED
                assert not send().get("commands")
                plan.release.set()
                reply = helper.wait_for(lambda: helper.display_row(base, old))
                assert reply.get("horse_recall_status") == "rejected" or "状態が変わった" in reply.get("text", ""), reply
            quiet_commands(send, seconds=.5)
            assert not stories(SUCCESS_STORY), history()
            assert not any(r.get("horse_command_id") for r in rows()), rows()
            plan.verify()
        finally:
            plan.release.set()
    passed.append("cancelled_selection_never_issues_after_newer_input" if cancel else "renamed_latest_candidate_rejects_stale_selection_before_command")


def check_blocked_request(helper, binary, passed, *, missing):
    plan = MockPlan([("ordinary", list_response())])
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        send(horses=not missing, paused=not missing)
        turn = helper.submit_text(base, sid, RECALL)
        reply = helper.wait_for(lambda: helper.display_row(base, turn))
        assert "まだ呼んでへん" in reply["text"], reply
        row = helper.wait_for(lambda: next((r for r in rows() if r.get("turn_id") == turn and r.get("horse_recall_status")), None))
        assert row["horse_recall_status"] == "blocked", row
        assert row["horse_recall_reason"] == ("horse_snapshot_missing" if missing else "horse_game_paused"), row
        helper.acknowledge(base, sid, reply)
        quiet_commands(send)
        assert not any(r.get("horse_command_id") for r in rows()), rows()
        plan.verify()
    passed.append("missing_snapshot_returns_unexecuted_reason_without_selection" if missing else "paused_game_recognizes_request_and_returns_unexecuted_reason")


def check_partial_observation(helper, binary, passed):
    plan = selection_plan()
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        send(event_name="threat_approaching", horses=False)
        helper.submit_text(base, sid, RECALL)
        command = get_command(helper, lambda: send(event_name="threat_approaching", horses=False))
        response = send(event_name="threat_approaching", horses=False, results=[result(command)])
        assert not response["commands"], response
        helper.wait_for(lambda: stories(SUCCESS_STORY))
        assert len(stories(SUCCESS_STORY)) == 1, history()
        plan.verify()
    passed.append("fresh_horse_snapshot_survives_combat_event_for_selection_revalidation_and_receipt")


def check_combat_recall(helper, binary, passed, *, cancel=False):
    plan = selection_plan(block=True, after_chat=cancel)
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        try:
            state["combat"] = True
            send()
            helper.submit_text(base, sid, RECALL)
            helper.wait_for(plan.entered.is_set, timeout=5)
            # 警告・被弾の新しい観測でも入力世代はそのまま。通常会話用epochだけが変わる。
            send(damage=True)
            if cancel:
                helper.submit_text(base, sid, CHAT)
                plan.release.set()
                helper.wait_for(lambda: any(r.get("horse_recall_status") == "not_requested" for r in rows()))
                quiet_commands(send)
                assert not any(r.get("horse_command_id") for r in rows()), rows()
                assert not any(entry.get("text") == SPEECH for entry in history()), history()
            else:
                plan.release.set()
                command = get_command(helper, send)
                send(results=[result(command)])
                helper.wait_for(lambda: stories(SUCCESS_STORY))
                assert len(stories(SUCCESS_STORY)) == 1, history()
                assert any(r.get("combat_actions") for r in rows()), "warnings did not run alongside recall"
                assert sum(r.get("horse_command_id") == command["command_id"] for r in rows()) == 1, rows()
            plan.verify()
        finally:
            plan.release.set()
    passed.append("combat_new_input_cancels_selection_and_keeps_nonhorse_speech_suppressed" if cancel else
                  "combat_recall_survives_warning_and_damage_without_waiting_for_speech")


def check_selection_failure(helper, binary, passed):
    plan = selection_plan()
    with fixture(helper, binary, plan, fail_selection=True) as (base, sid, send, rows, history, stories, state, seen):
        state["combat"] = True
        send()
        helper.submit_text(base, sid, RECALL)
        row = helper.wait_for(lambda: next((r for r in rows()
            if r.get("horse_recall_reason") == "horse_selection_unavailable"), None))
        assert row["assist_status"] == "handled" and row["horse_recall_status"] == "rejected", row
        quiet_commands(send)
        assert not any(r.get("horse_command_id") for r in rows()), rows()
        plan.verify()
    passed.append("selection_transport_failure_keeps_authorized_request_out_of_ordinary_chat")


def check_alert_input_routing(helper, binary, passed, *, recall=False):
    unspoken = "馬判定だけの本文は配送せえへん。"
    plan = selection_plan() if recall else MockPlan([
        ("ordinary", {"action": "speak", "speech": unspoken}),
        ("fallback_chat", {"action": "speak", "speech": SPEECH}),
    ])
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        state["alert"] = True
        send()
        # panicではないクリーパー一頭。警告配送の終了後にも戦闘の所有権は続いている。
        warning = helper.wait_for(lambda: next((r for r in rows() if r.get("display_id") and r.get("combat_actions")), None))
        helper.acknowledge(base, sid, warning)
        helper.wait_for(lambda: helper.row(base, warning["turn_id"], {"audio_disabled"}))
        send()
        observed = next(s for s in helper.snapshot(base)["sessions"] if s["session_id"] == sid)
        assert observed["state"] == "alert" and observed["chat_allowed"], observed
        assert observed["foreground"]["combat_active"], observed
        helper.submit_text(base, sid, RECALL if recall else CHAT)
        if recall:
            command = get_command(helper, send)
            send(results=[result(command)])
            helper.wait_for(lambda: stories(SUCCESS_STORY))
        else:
            helper.wait_for(lambda: any(r.get("horse_recall_status") == "not_requested" for r in rows()))
            # Alertでは既存のchat_allowedを維持。馬判定の本文ではなく、通常会話を配送する。
            reply = helper.wait_for(lambda: next((r for r in rows()
                if r.get("display_id") and r.get("text") == SPEECH), None))
            helper.acknowledge(base, sid, reply)
            helper.wait_for(lambda: any(r.get("text") == SPEECH for r in history()))
            quiet_commands(send)
            assert not any(r.get("text") == unspoken for r in history()), history()
            assert not any(r.get("horse_command_id") for r in rows()), rows()
        plan.verify()
    passed.append("alert_creeper_accepts_horse_request" if recall else "alert_creeper_preserves_existing_ordinary_chat_permission")


def check_expanded_mounts(helper, binary, passed):
    for kind, text in [("pig", "さっきのブタを呼んで"), ("happy_ghast", "ハッピーガストをここに呼んで"),
                       ("nautilus", "オウムガイを呼んで"), ("camel_husk", "ラクダハスクを呼んで")]:
        plan = selection_plan(text=text)
        with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
            state.update(kind=kind, owner=PLAYER_ID if kind == "nautilus" else None,
                         tamed=kind not in ("pig", "happy_ghast"))
            send()
            helper.submit_text(base, sid, text)
            command = get_command(helper, send)
            user_materials = "\n".join(m["content"] for m in plan.calls[1]["messages"] if m["role"] == "user")
            assert f'"entity_type":"{kind}"' in user_materials, user_materials
            send(results=[result(command)])
            helper.wait_for(lambda: stories(SUCCESS_STORY))
            plan.verify()
    passed.append("ownerless_equipped_and_owned_aquatic_candidates_reach_selection_and_ack_with_species")


def check_unequipped_during_selection(helper, binary, passed):
    text = "さっきのブタを呼んで"
    plan = selection_plan(block=True, text=text)
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        state.update(kind="pig", owner=None, tamed=False)
        send()
        turn = helper.submit_text(base, sid, text)
        helper.wait_for(plan.entered.is_set, timeout=5)
        state["equipped"] = False
        send()
        plan.release.set()
        reply = helper.wait_for(lambda: helper.display_row(base, turn))
        assert reply.get("horse_recall_status") == "rejected" or "状態が変わった" in reply.get("text", ""), reply
        quiet_commands(send)
        assert not any(r.get("horse_command_id") for r in rows()), rows()
        plan.verify()
    passed.append("removing_saddle_from_ownerless_mount_during_selection_prevents_command")


def check_no_eligible_candidate(helper, binary, passed):
    speech = "その子は今、誰かが乗っとるから呼べへんで。"
    plan = MockPlan([("ordinary", list_response()),
                     ("selection_unavailable", {"action": "speak", "speech": speech})])
    with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
        state["passengers"] = True
        send()
        turn = helper.submit_text(base, sid, RECALL)
        reply = helper.wait_for(lambda: helper.display_row(base, turn))
        assert reply["text"] == speech, reply
        assert "その個体に乗員がいる" in json.dumps(plan.calls[1]["messages"], ensure_ascii=False)
        quiet_commands(send)
        plan.verify()
    passed.append("no_eligible_candidate_keeps_observed_reason_and_reply_without_selection_tool")


def check_failure_reason_reaches_followup_context(helper, binary, passed):
    for code, fact, speech in [
        ("horse_not_loaded", "遠距離・別次元・消失のどれが原因かは未確認", "声が届かへんかったみたいや。"),
        ("no_water_landing", "横3×3以上の水域と身体の空間", "近くに、横3×3の水域で安全に出られる場所が見つからへんかったわ。まだ呼んでへんで。"),
    ]:
        plan = selection_plan(after_chat=True)
        with fixture(helper, binary, plan) as (base, sid, send, rows, history, stories, state, seen):
            helper.submit_text(base, sid, RECALL)
            command = get_command(helper, send)
            receipt = result(command)
            receipt.update(status="rejected", detail_code=code)
            response = send(results=[receipt])
            assert not response["commands"] and response["acknowledged_command_ids"] == [command["command_id"]], response
            helper.wait_for(lambda: any(fact in entry.get("text", "") for entry in history()))
            reply = helper.wait_for(lambda: next((r for r in rows() if r.get("display_id") and r.get("text") == speech), None))
            helper.acknowledge(base, sid, reply)
            helper.wait_for(lambda: helper.row(base, reply["turn_id"], {"audio_disabled"}))
            turn = helper.submit_text(base, sid, CHAT)
            helper.wait_for(lambda: helper.display_row(base, turn))
            assert fact in json.dumps(plan.calls[-1]["messages"], ensure_ascii=False), "reason was lost before next model call"
            send(results=[receipt])
            assert sum(fact in entry.get("text", "") and entry.get("reaction") == "story" for entry in history()) == 1
            plan.verify()
    passed.append("minecraft_failure_reason_reaches_next_chat_once_with_character_friendly_absence_reply")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="DokiDoki-Dogido checkout containing scripts/check_dialogue.py")
    parser.add_argument("--binary", type=Path, required=True, help="already-built dogido-rust binary")
    args = parser.parse_args()
    scripts = args.repo.resolve() / "dogido-rust/scripts"
    assert (scripts / "check_dialogue.py").is_file(), scripts
    binary = args.binary.resolve()
    assert binary.is_file(), binary
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(scripts))
    helper = importlib.import_module("check_dialogue")
    passed = []
    check_success(helper, binary, passed)
    check_wrong_identity(helper, binary, passed)
    check_delayed_receipt(helper, binary, passed)
    check_chat(helper, binary, passed, capability=True)
    check_chat(helper, binary, passed, capability=False)
    check_change_during_selection(helper, binary, passed, cancel=False)
    check_change_during_selection(helper, binary, passed, cancel=True)
    check_blocked_request(helper, binary, passed, missing=True)
    check_blocked_request(helper, binary, passed, missing=False)
    check_partial_observation(helper, binary, passed)
    check_combat_recall(helper, binary, passed)
    check_combat_recall(helper, binary, passed, cancel=True)
    check_selection_failure(helper, binary, passed)
    check_alert_input_routing(helper, binary, passed)
    check_alert_input_routing(helper, binary, passed, recall=True)
    check_expanded_mounts(helper, binary, passed)
    check_unequipped_during_selection(helper, binary, passed)
    check_no_eligible_candidate(helper, binary, passed)
    check_failure_reason_reaches_followup_context(helper, binary, passed)
    print(json.dumps({"passed": passed, "count": len(passed), "scope": "mock HTTP; no real model or Minecraft"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
