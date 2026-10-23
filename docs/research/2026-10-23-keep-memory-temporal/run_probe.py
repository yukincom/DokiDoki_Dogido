#!/usr/bin/env python3
"""過去のキープメモリーと現在観測を分ける、独立した合成場面の実モデル試験。

prepareは現行の共通プロンプトと16場面・期待条件を固定する。runは固定した要求を
既存診断のsend_payloadで直列送信し、生の応答・所要時間を追記する。意味の採点は
別途全応答を読んで行う。ゲーム接続、実記憶の更新、サービス操作は行わない。
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from datetime import datetime, timezone
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SOURCE = ROOT / "dogido_server/llm/companion_prompts.json"
TRANSPORT = ROOT / "docs/research/2026-10-20-vision-resolution/run_experiment.py"
ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
SEEDS = [17, 31]
TEMPORAL = (
    "keep_memory は、今までの冒険で心に残った過去の記憶や。そこにある物・状態は、"
    "記録された時のもんで、今も目の前にあるとは限らへん。\n"
    "今の観測は observations、変化は changes の before から now として読んでな。"
    "今の状態は最新の observations を使って、過去の記憶を思い出すときは、"
    "過去のことやと分かるように話してな。\n"
    "現在の観測が不明なら、keep_memory で埋めず、分からんまま扱ってな。"
)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def memory(text, scene="以前の冒険", when="30分前", mid="m1"):
    return {"memory_id": mid, "experienced_at": when, "scene": scene, "text": text}


def case(cid, question, observations, kind, before, now, memories, expected, mode="reply"):
    return {
        "id": cid, "mode": mode, "question": question,
        "context": {"observed_at": "2026-10-23T10:30:00+09:00", "observations": observations,
                    "changes": [{"kind": kind, "observed_at": "2026-10-23T10:30:00+09:00",
                                 "before": before, "now": now}], "keep_memory": memories},
        "expected": expected,
    }


def fixtures():
    cases = [
        case("q01_rain_stopped", "今も雨が降ってる？",
             {"place": "空が見える草原", "weather": {"rain": False, "sky": "晴れ"}},
             "weather", {"rain": True}, {"rain": False},
             [memory("雨が降っている。葉を打つ雨音と松明の火が心に残る。", "ジャングル")],
             "現在は降っていない。過去の雨を現在の雨としない。"),
        case("q02_item_switched", "今、手に持ってるの何？",
             {"player": {"main_hand": "鉄のつるはし", "inventory": {"torch": 0}}},
             "main_hand", "松明", "鉄のつるはし",
             [memory("松明を握って暗い道を進んでいる。手元の火が頼もしい。", "洞窟")],
             "現在の手持ちは鉄のつるはし。松明を持っている・今も照らしているとは言わない。"),
        case("q03_wolf_out_of_view", "さっきのオオカミ、まだ見える？",
             {"visible_entities": [], "visibility_scope": "現在の身体の視界のみ"},
             "visible_entities", ["白いオオカミ"], [],
             [memory("白いオオカミがそばにいる。首をかしげた顔がかわいい。", "森", "5分前")],
             "現在は見えていない。今もそばにいると言わない。死んだ・逃げた・世界からいなくなったと補わない。"),
        case("q04_danger_now", "今も安心して見物できそう？",
             {"hostile": {"type": "ゾンビ", "visible": True, "approaching": True, "distance_blocks": 3}, "danger": "接近する敵あり"},
             "danger", "敵は観測されず落ち着いていた", "ゾンビが3ブロック先から接近",
             [memory("敵はいない。明るい花畑で安心して見物している。", "花畑")],
             "現在のゾンビ接近を踏まえて注意する。過去の安心を現在の安全としない。"),
        case("q05_work_finished", "オレ、まだ作業してるように見える？",
             {"player": {"activity": "草原を歩行中", "owner_working": False, "main_hand": "パン"}},
             "player_activity", "ブロックを置いて建築中", "歩行中、建築作業なし",
             [memory("プレイヤーが壁を積み上げている。家が少しずつ形になるのを見ていた。", "建築場所", "20分前")],
             "現在は歩いており作業中ではない。過去の建築を今の動作にしない。"),
        case("q06_place_changed", "今いるのはどんな場所？",
             {"place": {"biome": "砂漠", "sky_visible": True, "time": "昼", "surroundings": ["砂", "サボテン"]}},
             "place", "暗い洞窟", "昼の砂漠",
             [memory("暗い岩の底にいる。低い天井と松明の火が印象に残る。", "洞窟", "1時間前")],
             "現在は昼の砂漠。今を洞窟・低い天井・暗闇として描写しない。"),
        case("q07_current_unknown", "今も雨が降ってる？",
             {"place": "外が見えない地下", "weather": {"status": "unavailable", "rain": None}},
             "weather_visibility", {"status": "observed", "rain": True}, {"status": "unavailable", "rain": None},
             [memory("雨が降っている。濡れた葉が光っている。", "地上の森", "2分前")],
             "現在の雨は分からない。過去を使って降っているとも、観測不能を晴れとも断定しない。"),
        case("q08_rain_returned", "晴れたまま？ それとも今は雨？",
             {"weather": {"status": "observed", "rain": True, "sky_visible": True}},
             "weather", {"rain": False, "sky": "晴れ"}, {"rain": True},
             [memory("雨が降っている。葉を打つ音が心に残る。", "森", "1時間前"),
              memory("雨がやんで明るくなった。", "草原", "10分前", "m2")],
             "現在は雨。changes.beforeや直近の晴れの記憶を現在として扱わない。"),
        case("q09_same_observed_now", "今もオオカミは見える？",
             {"visible_entities": [{"name": "シロ", "type": "オオカミ", "distance_blocks": 2}], "sky": "晴れ"},
             "sky", "曇り", "晴れ",
             [memory("シロというオオカミがそばにいる。顔を見上げてくれた。", "同じ草原", "3分前")],
             "現在もシロが見えると答える。過去だから不明という機械的な回答をしない。"),
        case("q10_identity_unknown", "今いる白いオオカミって、前のシロかな？",
             {"visible_entities": [{"type": "白いオオカミ", "name": None, "identity_match_to_past": "unknown"}], "place": "別の森"},
             "visible_entities", [], [{"type": "白いオオカミ", "name": None}],
             [memory("白いオオカミのシロと会った。丸い目が心に残った。", "以前の森", "昨日")],
             "今の白いオオカミがシロと同一かは不明。白い・同種というだけで同一個体にしない。"),
        case("q11_ask_about_past", "昨日の洞窟では、オレは何を手に持ってたっけ？",
             {"player": {"main_hand": "鉄のつるはし"}, "place": "草原"},
             "main_hand", "パン", "鉄のつるはし",
             [memory("プレイヤーは松明を手に持って進んでいた。火が心強かった。", "洞窟", "昨日")],
             "昨日は松明だったと過去記憶から答える。現在のつるはし・changes.beforeのパンを昨日へ混ぜない。"),
        case("q12_inventory_not_hand", "今、松明は手に持ってる？",
             {"player": {"main_hand": "鉄のつるはし", "inventory": {"torch": 12}}},
             "inventory_torch_count", 0, 12,
             [memory("松明を手に持っている。その明かりが頼りになる。", "洞窟", "昨日")],
             "松明は持ち物にあるが手にはつるはし。過去の持ち方を現在の手持ちに復元しない。"),
        case("a01_dry_now_rain_memory", None,
             {"place": "昼の草原", "weather": {"rain": False, "sky": "晴れ"}, "visible_entities": []},
             "weather", "雨", "晴れ",
             [memory("雨が降っている。葉を打つ雨音が響いて、松明の火が頼もしい。", "ジャングル", "昨日")],
             "自由な発話でも今を雨のジャングル・松明を所持中としない。雨に言及するなら過去またはやんだ変化として区別。沈黙は時制の証拠なし。", "ambient"),
        case("a02_flower_no_longer_seen", None,
             {"place": "地下の石の通路", "visible_blocks": ["石", "壁に設置された松明"], "visible_flowers": []},
             "place", "地上の花畑", "地下の石の通路",
             [memory("黄色いタンポポが目の前に咲いている。鮮やかな色に見とれた。", "花畑", "10分前")],
             "現在の地下に花が咲くとは言わない。花を話すなら過去の場面として区別。沈黙は時制の証拠なし。", "ambient"),
        case("a03_player_out_of_view", None,
             {"player": {"visibility": "not_visible", "distance_blocks": 7, "face_visible": False}, "place": "曲がり角のある通路"},
             "player_visibility", "近くで顔が見えていた", "曲がり角の向こうで見えない",
             [memory("プレイヤーがそばで顔を見せてくれている。目が合って安心した。", "通路", "1分前")],
             "今も顔・目が見える、目が合うとは言わない。現在の不在を事故・死亡へ補わない。沈黙は時制の証拠なし。", "ambient"),
        case("a04_many_memories", None,
             {"place": "晴れた昼の平原", "visible_entities": ["牛"], "player": {"main_hand": "パン", "inventory": {"torch": 0}}, "weather": {"rain": False}},
             "visible_entities", [], ["牛"],
             [memory("ゾンビが近づいている。怖くて二人で逃げた。", "森", "3日前", "m1"),
              memory("雪が降っている。白い道がきれいだった。", "雪原", "2日前", "m2"),
              memory("松明を握っている。暗い岩の底で火を頼りにした。", "洞窟", "昨日", "m3"),
              memory("雨が降っている。湖の上に波紋が広がっていた。", "湖", "1時間前", "m4"),
              memory("村人が集まっている。にぎやかな声が好きだった。", "村", "30分前", "m5"),
              memory("赤い花が咲いている。石の壁によく映えていた。", "花畑", "20分前", "m6")],
             "現在は牛のいる晴れた平原。多数の過去記憶から現在の敵・雪・雨・松明・村人・赤い花を捏造しない。沈黙は時制の証拠なし。", "ambient"),
    ]
    assert len(cases) == 16 and len({c["id"] for c in cases}) == 16
    return cases


def prepare():
    assert not (OUT / "manifest.json").exists(), "既存の固定条件を上書きしない"
    prompts = json.loads(SOURCE.read_text())
    systems = {}
    for purpose in ["reply", "ambient"]:
        systems[purpose] = "\n".join([prompts["base"], prompts["modes"]["normal"],
            prompts["grounding"], prompts["dialogue"][purpose], TEMPORAL, prompts["choice_contract"]])
    cases = fixtures()
    parameters = {"model": "default_model", "temperature": 0.65, "max_tokens": 256,
                  "stream": False, "chat_template_kwargs": {"enable_thinking": False}}
    requests = []
    for seed in SEEDS:
        for c in cases:
            content = {"world_context": c["context"]}
            if c["question"] is not None:
                content["player_input"] = c["question"]
            else:
                content["situation"] = "今の周りを見て、気になったことがあればひとこと話す場面や。"
            payload = dict(parameters, seed=seed, messages=[
                {"role": "system", "content": systems[c["mode"]]},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)}])
            requests.append({"trial_id": f'{c["id"]}_seed{seed}', "case_id": c["id"], "seed": seed, "payload": payload})
    save(OUT / "cases.json", cases)
    save(OUT / "requests.json", requests)
    (OUT / "temporal-prompt.txt").write_text(TEMPORAL + "\n")
    save(OUT / "manifest.json", {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Synthetic text-only past/current distinction; no real-game memory, image, TTS or poem generation",
        "cases": len(cases), "trials": len(requests), "seeds": SEEDS,
        "endpoint": ENDPOINT, "parameters": parameters,
        "history": "Independent requests with no conversational carry-over",
        "prompt_source": str(SOURCE.relative_to(ROOT)), "prompt_source_sha256": sha(SOURCE),
        "transport_source": str(TRANSPORT.relative_to(ROOT)), "transport_sha256": sha(TRANSPORT),
        "cases_sha256": sha(OUT / "cases.json"), "requests_sha256": sha(OUT / "requests.json"),
        "grading": "Manual semantic review of every raw response; silence is uninformative, not a temporal pass. Expected conditions are not sent to the model.",
        "limitations": "No no-memory ablation; two seeds per case do not establish reliability; natural-speech output only; fixture memories already selected and do not test selection or forgetting.",
    })
    print(json.dumps({"prepared_cases": len(cases), "prepared_trials": len(requests)}, ensure_ascii=False))


def server_info():
    with urllib.request.urlopen("http://127.0.0.1:8080/props", timeout=10) as response:
        data = json.load(response)
    defaults = data.get("default_generation_settings", {})
    params = defaults.get("params", {})
    return {"model_file": Path(data.get("model_path", "")).name, "model_alias": data.get("model_alias"),
            "build_info": data.get("build_info"), "n_ctx": defaults.get("n_ctx"),
            "sampling_defaults": {k: params.get(k) for k in ["temperature", "top_k", "top_p", "min_p", "repeat_penalty", "presence_penalty", "frequency_penalty", "seed"]}}


def run(limit):
    manifest = json.loads((OUT / "manifest.json").read_text())
    assert sha(OUT / "requests.json") == manifest["requests_sha256"]
    assert sha(OUT / "cases.json") == manifest["cases_sha256"]
    assert sha(TRANSPORT) == manifest["transport_sha256"]
    spec = importlib.util.spec_from_file_location("existing_dogido_diagnostic", TRANSPORT)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    before = server_info()
    if (OUT / "server-before.json").exists():
        assert json.loads((OUT / "server-before.json").read_text()) == before, "途中でモデル・条件が変わった"
    else:
        save(OUT / "server-before.json", before)
    path = OUT / "results.jsonl"
    done = {json.loads(s)["trial_id"] for s in path.read_text().splitlines()} if path.exists() else set()
    count = 0
    for trial in json.loads((OUT / "requests.json").read_text()):
        if trial["trial_id"] in done:
            continue
        began = datetime.now(timezone.utc).isoformat()
        result, elapsed = transport.send_payload(ENDPOINT, trial["payload"])
        row = {"trial_id": trial["trial_id"], "case_id": trial["case_id"], "seed": trial["seed"],
               "started_at": began, "completed_at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": elapsed, "response": result}
        with path.open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        message = result["choices"][0]
        print(json.dumps({"trial_id": trial["trial_id"], "seconds": elapsed,
            "finish_reason": message.get("finish_reason"), "content": message["message"].get("content")}, ensure_ascii=False), flush=True)
        count += 1
        if limit and count >= limit:
            break
    after = server_info()
    save(OUT / "server-after.json", after)
    assert after == before, "試験中にモデル・条件が変わった"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run"])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    {"prepare": prepare, "run": lambda: run(args.limit)}[args.mode]()
