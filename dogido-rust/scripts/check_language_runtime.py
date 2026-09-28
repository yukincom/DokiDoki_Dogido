#!/usr/bin/env python3
"""限定国語対話の実HTTP・模擬モデル/音声確認。実サービスは起動しない。"""
import json
import time
from check_dialogue import register, request, row, snapshot, submit, wait_for
from check_haiku_runtime import fixture


def interpretation(current, **change):
    return {"dialogue_act":"information_request", "question":current["text"], "target":"きょう",
        "facet":"mora_count", "topic":"language", "relation":"new", "target_status":"explicit",
        "alternatives":[], "evidence":[{"turn_id":current["turn_id"],"quote":current["text"]}],
        "search_terms":[], "clarification":"", "lookup_requested":False,"web_query":"", **change}


def main():
    passed=[]
    with fixture(enabled=False) as f:
        base, _, log, control, seen, _, _, _, send, _, rows, _, folder=f
        captures=[]
        flags={"unknown_fact":False,"invalid_evidence":False,"missing_context":False}
        def model(incoming):
            tokens=incoming["max_tokens"]
            if tokens not in {850,700}: return None
            before, after=incoming["messages"][-1]["content"].split("\n以上は背景。今回応答する最新の発話はこちら：\n")
            details, current=json.loads(before), json.loads(after)
            captures.append((tokens, details, current))
            if tokens == 700:
                return {"status":"answer", "text":"枕詞は、決まった言葉の前につく言い回しやで。",
                    "fact_ids":["made-up-id" if flags["unknown_fact"] else details["facts"][0]["id"]],
                    "application":"資料にある説明。", "missing":"",
                    "missing_kind":"context" if flags["missing_context"] else "none",
                    "clarification":"どんな文で使われてたん？" if flags["missing_context"] else "",
                    **flags.get("reply_override", {})}
            text=current["text"]
            changes={}
            if "何年生" in text or "漢字の3" in text:
                changes={"target":"3","facet":"grade","search_terms":["3"],
                         "target_status":"contextual" if "漢字の3" in text else "explicit",
                         "relation":"continue" if "漢字の3" in text else "new"}
            elif "春" in text:
                changes={"target":"春"}
            elif "枕詞" in text:
                changes={"target":"枕詞","facet":"meaning","search_terms":["枕詞"]}
            elif "知らない" in text:
                changes={"target":"未収録の試験語彙","facet":"meaning","search_terms":["未収録の試験語彙"]}
            elif "冒険に戻ろう" in text:
                changes={"dialogue_act":"other","target":"","facet":"other","topic":"minecraft","relation":"end"}
            elif "ゆっくり" in text:
                changes={"target":"がっこう"}
            if flags["invalid_evidence"]:
                changes["evidence"]=[{"turn_id":"missing-turn","quote":"ない発話"}]
            return {**interpretation(current,**changes), **flags.get("interpretation_override", {})}
        control["structured_handler"]=model

        def fresh():
            sid=register(base,preview=False); send(sid); return sid
        def say(sid,text):
            send(sid)
            turn=submit(base,sid,text)
            return wait_for(lambda:row(base,turn,{"completed"}))
        def close(sid):request(base,"/api/v1/adapter-sessions/"+sid,method="DELETE")
        def focus(sid):return next(s["foreground"] for s in snapshot(base)["sessions"] if s["session_id"]==sid)
        def calls():return len([r for r in seen if r["path"]=="/v1/chat/completions"])

        sid=fresh(); count=calls()
        normal=say(sid,"こんにちは")
        assert calls()==count+2 and not captures, normal
        passed.append("ordinary_chat_has_no_language_classifier")
        count=calls(); mora=say(sid,"音数を教えて。きょう")
        assert mora["text"]=="「きょう」は2音やで。" and calls()==count+1, mora
        assert mora["category"]=="learning" and focus(sid)["blocks_new_haiku"]
        assert len(mora["llm_reports"])==1
        passed.append("explicit_kana_count_one_model_call_then_code")
        count=calls(); clarify=say(sid,"音数を教えて。春")
        assert "ひらがな" in clarify["text"] and calls()==count+1, clarify
        passed.append("unknown_reading_asks_without_inventing_pronunciation")
        close(sid)

        sid=fresh(); count=calls()
        first=say(sid,"3が出てきた。何年生で習うもの？")
        assert first["text"]=="それって、漢字を習う学年のこと？", first
        answer=say(sid,"漢字の3のことだよ")
        assert answer["text"]=="「三」は小学1年生で習う漢字やで。", answer
        assert calls()==count+2
        assert captures[-1][1]["focus"]["clarification"]==first["text"]
        assert any(r["role"]=="assistant" and r["text"]==first["text"] for r in captures[-1][1]["history"])
        assert answer["reference_ids"], answer
        passed.append("played_clarification_continues_to_verified_grade_and_references")
        close(sid)

        sid=fresh(); count=calls()
        answer=say(sid,"枕詞ってどういう意味なのか、例で教えて")
        assert calls()==count+2 and answer["language_status"]=="answer" and answer["reference_ids"],answer
        flags["unknown_fact"]=True
        bad=say(sid,"枕詞ってどういう意味なのか、例で教えて")
        assert bad["language_status"]=="unsupported" and not bad["reference_ids"],bad
        flags["unknown_fact"]=False; flags["missing_context"]=True
        clarification=say(sid,"枕詞ってどういう意味なのか、例で教えて")
        assert clarification["text"]=="どんな文で使われてたん？",clarification
        flags["missing_context"]=False
        passed.append("grounded_explanation_unknown_fact_rejection_and_context_question")
        count=calls(); missing=say(sid,"知らないことわざを教えて")
        assert calls()==count+1 and missing["language_status"]=="unsupported",missing
        flags["invalid_evidence"]=True
        invalid=say(sid,"音数を教えて。きょう")
        assert invalid["language_status"]=="clarify",invalid
        flags["invalid_evidence"]=False
        passed.append("missing_sources_and_false_evidence_do_not_generate_answers")
        count=calls(); returned=say(sid,"ドギド、マイクラの冒険に戻ろう")
        assert returned["language_status"]=="host_chat" and calls()==count+3,returned
        assert focus(sid)["route"]=="casual"
        passed.append("handoff_uses_existing_chat_once_with_current_observation")
        close(sid)

        sid=fresh(); (folder/"player_mode").write_text("fail")
        turn=submit(base,sid,"3が出てきた。何年生で習うもの？")
        wait_for(lambda:row(base,turn,{"failed"})); (folder/"player_mode").write_text("ok")
        say(sid,"漢字の3のことだよ")
        assert captures[-1][1]["focus"]["clarification"]==""
        assert not any(r["role"]=="assistant" for r in captures[-1][1]["history"])
        passed.append("failed_audio_never_commits_learning_clarification")
        close(sid)

        sid=fresh(); say(sid,"音数を教えて。きょう")
        (folder/"player_mode").write_text("slow")
        interrupted=submit(base,sid,"3が出てきた。何年生で習うもの？")
        wait_for(lambda:row(base,interrupted,{"started"}))
        (folder/"player_mode").write_text("ok")
        say(sid,"音数を教えて。きょう")
        assert row(base,interrupted,{"cancelled"})
        assert captures[-1][1]["focus"]["target"]=="きょう"
        assert captures[-1][1]["focus"]["clarification"]==""
        assert not any(r["turn_id"]==interrupted+":reply" for r in captures[-1][1]["history"])
        passed.append("new_input_cancels_audio_without_committing_unheard_question")
        close(sid)

        sid=fresh(); control["delay"]=.5; count=calls(); start=len(seen)
        turn=submit(base,sid,"枕詞ってどういう意味なのか、例で教えて")
        wait_for(lambda:calls()>count)
        send(sid,visual_threats=[{"type":"zombie","entity_id":"z","distance":3}])
        wait_for(lambda:row(base,turn,{"cancelled"})); time.sleep(.6)
        assert not any(r["body"]["max_tokens"]==700 for r in seen[start:] if r["path"]=="/v1/chat/completions")
        assert not any(r["role"]=="assistant" for s in snapshot(base)["sessions"] if s["session_id"]==sid for r in s["history"])
        control["delay"]=0; close(sid)
        passed.append("combat_cancels_interpretation_before_second_generation")

        sid=fresh()
        for extra in [{"status":"invented"}, {"references":[{"url":"https://invented.invalid"}]}, {"text":"犬"*421}]:
            flags["reply_override"]=extra; count=calls()
            answer=say(sid,"枕詞ってどういう意味なのか、例で教えて")
            assert answer["language_status"]=="unsupported" and not answer["reference_ids"],answer
            assert calls()==count+2, "invalid shape triggered another model call"
        flags.pop("reply_override")
        passed.append("native_reply_contract_rejects_bad_status_extra_sources_and_oversize_without_retry")
        flags["interpretation_override"]={"close_workshop":True}; count=calls()
        invalid=say(sid,"枕詞ってどういう意味なのか、例で教えて")
        assert invalid["language_status"]=="clarify" and calls()==count+1,invalid
        flags.pop("interpretation_override")
        passed.append("native_interpretation_contract_does_not_forward_unknown_actions_to_lookup")

        def truncated(incoming):
            if incoming["max_tokens"]!=flags.get("truncate"): return None
            return {"id":"mock-truncated","object":"chat.completion","created":0,"model":"mock-model",
                "choices":[{"index":0,"message":{"role":"assistant","content":json.dumps(model(incoming),ensure_ascii=False)},"finish_reason":"length"}],
                "usage":{"prompt_tokens":100,"completion_tokens":incoming["max_tokens"],"total_tokens":100+incoming["max_tokens"]}}
        control["completion_override"]=truncated
        for tokens,status,num_calls in [(850,"clarify",1),(700,"unsupported",2)]:
            flags["truncate"]=tokens; count=calls()
            answer=say(sid,"枕詞ってどういう意味なのか、例で教えて")
            assert answer["language_status"]==status and calls()==count+num_calls,answer
            assert answer["llm_reports"][-1]["generated"]["finish_reason"]=="length"
        control.pop("completion_override");flags.pop("truncate")
        passed.append("complete_looking_but_truncated_json_is_rejected_without_retry")
        close(sid)
    print(f"PASS {len(passed)} language checks; all owned processes stopped")
    for name in passed: print(name)


if __name__=="__main__":main()
