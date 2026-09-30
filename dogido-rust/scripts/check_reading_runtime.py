#!/usr/bin/env python3
"""Typed catalogue saves and ordinary speech: real Rust HTTP, mock model/audio."""
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dogido_server.catalog_readings import configure_corrections_path, resolve_reading
from check_haiku_runtime import fixture, LINES
from check_workshop_runtime import ready, install, step, session
from check_dialogue import request, register, row, wait_for


def save(base, surface="草地", reading="くさち", expected_id=None):
    return request(base, "/api/v1/catalog/readings", {"surface":surface,"reading":reading,
        "entry_id":"biome:meadow","wrong_reading":"そうち","expected_id":expected_id}, method="PUT")


def main():
    passed=[]
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid=ready(base,send,rows)
        install(control,lambda text,prompt,n:step(text,"ask","どの言葉が気になったん？"))
        for source in ["voice","text"]:
            for text in ["何か音はするね","読み: 草地=くさち","草地の読みはくさち","そうちじゃなくてくさち"]:
                send(sid)
                turn=request(base,"/api/v1/player-input",{"session_id":sid,"text":text,"source":source})["turn_id"]
                r=wait_for(lambda:row(base,turn,{"completed"}))
                assert not r.get("memory_action"),r
                assert request(base,"/api/v1/catalog")["corrections"]==[]
        passed.append("ordinary_voice_and_text_never_register_readings_even_with_old_command_syntax")
        before=hud(sid); history=session(base,sid)["workshop_history"]
        saved=save(base)["correction"]
        assert saved["source"]=="biome:meadow" and saved["session_id"] is None
        assert hud(sid)==before and session(base,sid)["workshop_history"]==history
        path=folder/"memory/long_term/catalog_corrections.jsonl"
        configure_corrections_path(path)
        try: assert resolve_reading("草地")=="くさち"
        finally: configure_corrections_path(None)
        passed.append("catalog_form_saves_without_model_turn_speech_or_workshop_change_and_python_reads_it")
        request(base,"/api/v1/adapter-sessions/"+sid,method="DELETE")
        next_sid=register(base,preview=False)
        send(next_sid,world={"biome":"minecraft:meadow","time_phase":"morning","weather":"clear","sky_visible":True})
        entry=wait_for(lambda:stored(next_sid))[0]
        constraints=entry["materials_snapshot"]["haiku_constraints"]
        assert "くさち" in constraints["allowed_terms"] and "そうち" in constraints["forbidden_terms"],constraints
        passed.append("next_poem_uses_saved_reading_and_forbidden_misreading")
        request(base,"/api/v1/catalog/readings",{"surface":"草地","expected_id":saved["id"]},method="DELETE")
        assert request(base,"/api/v1/catalog")["corrections"]==[]
        configure_corrections_path(path)
        try: assert resolve_reading("草地") is None
        finally: configure_corrections_path(None)
        assert len(path.read_text().splitlines())==2
        passed.append("removal_retains_audit_record_and_both_runtimes_drop_overlay")
    with fixture(memory_enabled=False) as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        assert request(base,"/api/v1/catalog")["enabled"] is False
        try: save(base); raise AssertionError("disabled save succeeded")
        except HTTPError as e: assert e.code==409
        assert not (folder/"memory/long_term/catalog_corrections.jsonl").exists()
        passed.append("disabled_memory_does_not_write")
    with fixture() as (base, process, log, control, seen, gate, drafting, checks, send, hud, rows, stored, folder):
        sid=ready(base,send,rows)
        path=folder/"memory/long_term/catalog_corrections.jsonl";path.mkdir(parents=True)
        try: save(base); raise AssertionError("failed storage reported saved")
        except HTTPError as e: assert e.code==500
        assert hud(sid)["canonical_lines"]==LINES
        path.rmdir()
        assert save(base)["saved"]
        passed.append("storage_failure_preserves_workshop_and_form_can_retry")
    print(json.dumps({"passed":passed,"live_model":False,"owned_processes_stopped":True},ensure_ascii=False,indent=2))


if __name__=="__main__": main()
