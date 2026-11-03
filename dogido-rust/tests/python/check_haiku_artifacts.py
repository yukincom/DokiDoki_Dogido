#!/usr/bin/env python3
"""見込み: 発句・保存・採取の終了を別々に扱えば、取消した専有素材だけを整理できる。
合格: 元の要求ID、数値の内容、保存句数と再生成回数を照合。Rustは実HTTPで起動し、採取・音声・観測は一時領域の模擬。"""
import fcntl
import hashlib
import json
import struct
import threading

from check_dialogue import register, request, wait_for
from check_haiku_capture import captured

RAW = struct.pack("<8f", *range(8))
KINDS = ["haiku_irony", "haiku_scene", "haiku_draft"]


def record(folder, sid):
    rows = [json.loads(path.read_bytes()) for path in (folder / "memory/artifact-jobs").glob("*.json")]
    matching = [r for r in rows if r["session_id"] == sid]
    assert len(matching) <= 1, "同じ観測から二重に発句した"
    return matching[0] if matching else {}


def state(folder, sid, expected):
    def matching():
        value = record(folder, sid)
        return value if value.get("state") == expected else None
    return wait_for(matching, timeout=12)


def sentinels(root):
    # 所有札のない旧素材、他アプリの同一内容の素材を置き、自ジョブの処分後に直接ハッシュ照合する。
    result = {}
    for name, manifest in [("old_capture", {"response_id": "old_capture"}),
                           ("foreign_capture", {"response_id": "foreign_capture", "owner": "other"})]:
        path = root / name
        path.mkdir()
        for file, data in [(path / "hidden.f32", RAW), (path / "manifest.json", json.dumps(manifest).encode())]:
            file.write_bytes(data)
            result[file] = hashlib.sha256(data).digest()
    return result


def verify_sentinels(files):
    assert all(hashlib.sha256(file.read_bytes()).digest() == digest for file, digest in files.items())


def raw_intact(root, calls):
    for c in calls:
        path = root / c["id"]
        token = c["request"]["dogido_capture_request_id"]
        manifest = json.loads((path / "manifest.json").read_bytes())
        marker = json.loads((root / "_requests" / (token + ".json")).read_bytes())
        assert marker["response_id"] == c["id"] and manifest["capture_request_id"] == token
        assert marker["writer_closed"] is True and manifest["writer_closed"] is True
        assert (path / "hidden.f32").read_bytes() == RAW


def removed(folder, sid, root, calls):
    tokens = [c["request"]["dogido_capture_request_id"] for c in calls]
    assert len(set(tokens)) == len(tokens)
    def done():
        r = record(folder, sid)
        return (r.get("state") == "discard" and [j["token"] for j in r["requests"]] == tokens
                and all(j["removed"] for j in r["requests"]))
    wait_for(done, timeout=15)
    for token, call in zip(tokens, calls):
        assert not (root / call["id"]).exists() and not (root / (".discard-" + token)).exists()
        assert not (root / "_requests" / (token + ".json")).exists()


def same_capture(entry, calls):
    expected = [(c["kind"], c["request"]["dogido_capture_request_id"], c["id"]) for c in calls]
    actual = [(r["kind"], r["capture_request_id"], r["response_id"]) for r in entry["internal_capture"]["requests"]]
    assert actual == expected
    assert all(r["artifact"]["hidden_verified"] for r in entry["internal_capture"]["requests"])


# 見込み: HTTP応答前に取消しても、未終了のdraftはwriterに属す。
# 合格: activeでは3要求を保持。取消後に前2つが消えてもdraftは残り、遅いwriter解放後だけ消える。範囲: 実HTTPと模擬writerのOSロック。
def cancel_with_late_writer():
    with captured(tracked=True) as (env, root, calls):
        base, _, _, control, _, gate, drafting, _, send, _, _, stored, folder = env
        sentinel = sentinels(root)
        ready, release = threading.Event(), threading.Event()
        control["capture_release"] = {"haiku_draft": (ready, release)}
        gate.clear()
        sid = register(base, preview=False, body=True)
        send(sid)
        try:
            wait_for(drafting.is_set)
            assert [c["kind"] for c in calls] == KINDS
            active = state(folder, sid, "active")
            assert [r["token"] for r in active["requests"]] == [c["request"]["dogido_capture_request_id"] for c in calls]
            raw_intact(root, calls[:2])
            late = root / calls[2]["id"]
            assert (late / "hidden.f32").read_bytes() == struct.pack("<f", 0)
            send(sid, world={"game_paused": True})
            state(folder, sid, "discard")
            wait_for(lambda: not (root / calls[0]["id"]).exists() and not (root / calls[1]["id"]).exists())
            assert not stored(sid) and late.is_dir()
            gate.set()
            assert ready.wait(10), "遅いdraftの終端札が出ない"
            raw_intact(root, calls[2:])
            assert not record(folder, sid)["requests"][2]["removed"]
        finally:
            gate.set()
            release.set()
        removed(folder, sid, root, calls)
        assert not stored(sid)
        verify_sentinels(sentinel)


# 見込み: 初稿と再生成の上限に達した発句は失敗応答で終了する。
# 合格: 句0件、見どころ/選択/初稿/再生成2回の5要求をすべて処分し、無関係な素材は不変。範囲: 模擬不正案と本体の生成検査・保存・整理。
def invalid_generation_discards_all_attempts():
    with captured(tracked=True, max_regeneration_rounds=2) as (env, root, calls):
        base, _, _, _, _, _, _, checks, send, _, rows, stored, folder = env
        sentinel = sentinels(root)
        checks["fail"] = True
        sid = register(base, preview=False, body=True)
        send(sid)
        wait_for(lambda: any(r["turn_id"].endswith(":failure") and r["playback_status"] == "completed" for r in rows(sid)))
        assert [c["kind"] for c in calls] == KINDS + ["haiku_line_regeneration"] * 2
        assert (checks["drafts"], checks["regenerations"]) == (1, 2)
        removed(folder, sid, root, calls)
        assert not stored(sid)
        verify_sentinels(sentinel)


# 見込み: 再生成で成立した句の読み上げ中に停止しても、全要求は同じ完成句の資料になる。
# 合格: 実際にstartedからcancelledへ変わり、実正本1行・要求順・全素材の内容が元のまま。範囲: 実HTTPと模擬再生プロセス。
def completed_poem_survives_playback_cancel():
    with captured(tracked=True, initial_lines=["あ", "い", "う"]) as (env, root, calls):
        base, _, _, _, _, gate, drafting, checks, send, _, rows, stored, folder = env
        gate.clear()
        sid = register(base, preview=False, body=True)
        send(sid)
        wait_for(drafting.is_set)
        (folder / "player_mode").write_text("slow")
        gate.set()
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "started" for r in rows(sid)))
        saved = state(folder, sid, "saved")
        entry = wait_for(lambda: stored(sid))[0]
        assert [c["kind"] for c in calls] == KINDS + ["haiku_line_regeneration"]
        assert checks["regenerations"] == 1 and saved["entry_id"] == entry["id"]
        same_capture(entry, calls); raw_intact(root, calls)
        assert request(base, "/api/v1/rust-dialogue/interrupt", {"session_id": sid})["accepted"]
        wait_for(lambda: any(r["turn_id"].endswith(":poem") and r["playback_status"] == "cancelled" for r in rows(sid)))
        assert state(folder, sid, "saved")["entry_id"] == entry["id"]
        assert stored(sid) == [entry]
        raw_intact(root, calls)


# 見込み: 実ファイルへの保存が失敗したときは同じ完成原記録を保留し、復旧は保存だけを再試行する。
# 合格: 失敗中の3素材が残り、復旧後の句1行と応答IDが同じ、モデルへの再要求0回。範囲: 実OS書込み障害・模擬生成。
def save_error_retries_only_the_completed_record():
    with captured(tracked=True) as (env, root, calls):
        base, _, log, _, seen, _, _, _, send, _, _, stored, folder = env
        sid = register(base, preview=False, body=True)
        path = folder / "memory/sessions" / sid / "long_term/haiku_entries.jsonl"
        path.mkdir(parents=True)
        send(sid)
        wait_for(lambda: "haiku_save_failed" in log.read_text())
        failed = state(folder, sid, "save_failed")
        expected = failed["entry_id"]
        assert expected and failed["emission"] and path.is_dir()
        assert [c["kind"] for c in calls] == KINDS
        raw_intact(root, calls)
        before = [c["body"] for c in seen if c["path"] == "/v1/chat/completions"]
        path.rmdir()
        saved = state(folder, sid, "saved")
        entries = stored(sid)
        assert len(entries) == 1 and entries[0]["id"] == expected == saved["entry_id"]
        assert saved["emission"] is None
        same_capture(entries[0], calls); raw_intact(root, calls)
        assert before == [c["body"] for c in seen if c["path"] == "/v1/chat/completions"]


# 見込み: 完成記録ができてから実正本への追記が遅れても、生成失敗扱いにはならない。
# 合格: ファイルロック中は保存予定の同じ句ID・3素材が残り正本は0行。解放後に同じ1句と要求順が確定する。範囲: OS flock・実Rust保存、模擬生成。
def pending_save_keeps_inputs_until_append():
    with captured(tracked=True) as (env, root, calls):
        base, _, _, _, _, _, _, _, send, _, _, stored, folder = env
        sid = register(base, preview=False, body=True)
        path = folder / "memory/sessions" / sid / "long_term/haiku_entries.jsonl"
        path.parent.mkdir(parents=True)
        with path.open("ab") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            try:
                send(sid)
                pending = state(folder, sid, "save_pending")
                assert pending["emission"] and pending["entry_id"] and path.read_bytes() == b""
                assert [c["kind"] for c in calls] == KINDS
                raw_intact(root, calls)
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)
        saved = state(folder, sid, "saved")
        entries = stored(sid)
        assert len(entries) == 1 and entries[0]["id"] == pending["entry_id"] == saved["entry_id"]
        same_capture(entries[0], calls); raw_intact(root, calls)


def main():
    checks = [cancel_with_late_writer, invalid_generation_discards_all_attempts, completed_poem_survives_playback_cancel,
              save_error_retries_only_the_completed_record, pending_save_keeps_inputs_until_append]
    for check in checks:
        check()
    print(json.dumps({"passed": len(checks), "checks": [check.__name__ for check in checks]}))


if __name__ == "__main__":
    main()
