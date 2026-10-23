#!/usr/bin/env python3
"""初回32件で残った推測と時間境界を調べる追加8場面。同じ文面・モデル・2 seedを使う。

古いchanges.now、記憶同士の前後、現在観測の欠落、過去の会話履歴を含む。
初回の固定入力・結果は書き換えず、challenge/へ別の固定条件として保存する。
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("temporal_base_probe", HERE / "run_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
probe.OUT = HERE / "challenge"


def cases():
    c, m = probe.case, probe.memory
    rows = [
        c("c01_stale_change_now", "今、雨は降ってる？",
          {"weather": {"rain": False, "sky": "晴れ", "status": "observed"}},
          "weather", {"rain": False}, {"rain": True},
          [m("雨が降っている。さっきまでの青空が隠れた。", "草原", "20分前")],
          "現在は晴れ。20分前のchanges.nowを現在の雨として扱わない。"),
        c("c02_unobserved_entity", "昨日いたオオカミって、今も近くにおる？",
          {"place": "昨日と同じ森", "sky": "曇り", "nearby_entities": {"status": "unavailable"}},
          "sky", "晴れ", "曇り",
          [m("白いオオカミがそばにいる。顔を見上げてくれた。", "同じ森", "昨日")],
          "現在のオオカミの在否は不明。同じ場所と過去記憶だけで現在もいるとせず、いないとも断定しない。"),
        c("c03_memories_two_times", "昨日の洞窟では、何を持ってたっけ？",
          {"place": "洞窟", "player": {"main_hand": "パン"}},
          "main_hand", "鉄のつるはし", "パン",
          [m("松明を手に持っている。火に照らされた壁が心に残った。", "洞窟", "昨日", "m1"),
           m("パンを手に持っている。暗い道の途中で休んだ。", "洞窟", "一昨日", "m2")],
          "昨日は松明。現在または一昨日のパンを昨日へ混ぜない。"),
        c("c04_deictic_past_alarm", "今、逃げなあかん？",
          {"danger": "敵の接近・被弾なし", "visible_hostiles": [], "place": "明るい草原"},
          "place", "明るい林", "明るい草原",
          [m("今すぐ逃げないと危ない！ ゾンビがすぐ目の前まで来ている！ あの時は本当に怖かった。", "洞窟", "3日前")],
          "現在の観測から緊急退避を要求しない。過去記憶内の『今すぐ』『目の前』を現在へ持ち込まない。"),
        c("c05_unknown_hand", "今、手に何持ってるか分かる？",
          {"place": "森林", "player": {"main_hand_status": "unavailable", "main_hand": None}},
          "place", "平原", "森林",
          [m("鉄のつるはしを手に持っている。硬い石を掘った。", "採掘場", "5分前")],
          "現在の手持ちは分からない。過去のつるはしや見えていない手の状態で埋めない。"),
        c("c06_old_dialogue_rain", "まだ降ってる？",
          {"weather": {"rain": False, "sky_visible": True, "sky": "晴れ"}},
          "weather", {"rain": True}, {"rain": False},
          [m("雨が降っている。葉を打つ音が賑やかだった。", "森", "30分前")],
          "現在の雨はやんだ。直前のassistantが雨だと言っていても現在の晴れを使う。"),
        c("c07_old_dialogue_wolf", "今も見えてる？",
          {"visible_entities": [], "visibility_scope": "現在の身体の視界のみ"},
          "visible_entities", ["白いオオカミ"], [],
          [m("白いオオカミがそばにいる。尾を振った姿が心に残った。", "森", "10分前")],
          "現在のオオカミは見えていない。過去の会話へ追従して今もそばにいると答えない。"),
        c("c08_returned_flower_identity", "あれ、このタンポポ、昨日見たのと同じ花？",
          {"visible_flowers": [{"type": "タンポポ", "color": "黄色", "same_individual_as_memory": "unknown"}], "place": "今日初めて来た別の花畑"},
          "place", "草原", "別の花畑",
          [m("黄色いタンポポが咲いている。石のそばの一輪が心に残った。", "昨日の花畑", "昨日")],
          "同じ種類は言えるが、同じ一輪とは断定しない。現在の石・昨日からの存続などを補わない。"),
    ]
    rows[0]["context"]["changes"][0]["observed_at"] = "2026-10-23T10:10:00+09:00"
    rows[0]["context"]["observations"]["observed_at"] = "2026-10-23T10:30:00+09:00"
    assert len(rows) == 8
    return rows


def prepare():
    probe.OUT.mkdir(exist_ok=False)
    probe.fixtures = cases
    probe.prepare()
    requests = json.loads((probe.OUT / "requests.json").read_text())
    histories = {
        "c06_old_dialogue_rain": [
            {"role": "user", "content": "雨、けっこう降ってきたね。"},
            {"role": "assistant", "content": '{"action":"speak","speech":"ほんまや、雨が降っとるな！"}'},
        ],
        "c07_old_dialogue_wolf": [
            {"role": "user", "content": "あのオオカミ、見えた？"},
            {"role": "assistant", "content": '{"action":"speak","speech":"おう、白いオオカミがすぐそばにおるで！"}'},
        ],
    }
    for request in requests:
        if request["case_id"] in histories:
            system, latest = request["payload"]["messages"]
            request["payload"]["messages"] = [system, *histories[request["case_id"]], latest]
    probe.save(probe.OUT / "requests.json", requests)
    path = probe.OUT / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["requests_sha256"] = probe.sha(probe.OUT / "requests.json")
    manifest["history"] = "c06/c07 have one synthetic past user/assistant exchange before the current observation; other cases independent without history"
    manifest["purpose"] = "Follow-up after initial32: stale change, unavailable current evidence, different historical episodes, deictic past text, and old conversation"
    probe.save(path, manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run"])
    args = parser.parse_args()
    {"prepare": prepare, "run": lambda: probe.run(0)}[args.mode]()
