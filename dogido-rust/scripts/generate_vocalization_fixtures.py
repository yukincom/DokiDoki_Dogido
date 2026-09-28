#!/usr/bin/env python3
"""Python正本の叫声規則・同時点状況の投影を比較用fixtureにする。モデル・音声なし。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.player_input.voice_vocalization import is_pure_voice_vocalization
from dogido_server.models import GameEvent
from dogido_server.service import DogidoService


def main():
    texts = ["", "うおおお", "うわあああ！", "ぎゃあああ", "ああああ", "ああ、そうか",
             "『うおおお』って言った", "うおおお、剣に持ち替えて", "石炭だ", "うわ！敵だ",
             "ウオオオ", "ﾜｱｱｱ", "ｱｱｱｱ", "うお\u001eおお", "（あぁぁぁ）",
             "(うわ ああ)", "「うわああ」", "う、わ、あ、あ！", "わわわ", "わあわ",
             "ああああ？", "うわああと言って", "うおおおお〜〜", "\u200bうわああ", "うおおお\u200b"]
    for head, tail in [("う", "お"), ("う", "わ"), ("わ", "あ"), ("ぎ", "ゃ"), ("ひ", "ャ"),
                       ("き", "ぁ"), ("あ", "ア"), ("ぅ", "う"), ("ァ", "ー")]:
        for count in range(1, 6):
            texts += [head + tail * count, "…（" + head + tail * count + "）！"]
    base = {"schema_version":"2026-05-24", "adapter":"fixture", "sequence":1,
            "observed_at":"2026-09-28T00:00:00Z", "event":{"name":"status_snapshot", "source_kind":"system",
            "priority_hint":"background", "certainty":"high"}, "player":{"name":"試験"}}
    observations = []
    visual = {"visual_threats":[{"type":"zombie", "distance":4}]}
    for extra in [{}, visual,
                  {"auditory_threats":[{"type":"zombie", "label":"ゾンビの声", "kind":"mob_idle", "distance_band":"close", "direction":{"horizontal":"front"}}]},
                  {**visual, "event":{**base["event"], "name":"player_died"}},
                  {**visual, "event":{**base["event"], "name":"creeper_detonated"}},
                  {"combat":{"hostile_outcomes":[{"entity_id":"c1", "type":"creeper", "outcome":"creeper_detonation", "evidence":"explosion_packet"}]}},
                  {"combat":{"hostile_outcomes":[{"entity_id":"c1", "type":"zombie", "outcome":"explosion_death", "evidence":"server_death_event"}]}}]:
        event = {**base, **extra}
        observations.append({"event":event, "note":DogidoService._observed_situation_note(GameEvent.model_validate(event))})
    payload = {"texts":[{"text":t, "pure":is_pure_voice_vocalization(t)} for t in dict.fromkeys(texts)],
               "observations":observations}
    path = ROOT / "dogido-rust/fixtures/vocalization.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(payload['texts'])} text cases / {len(observations)} observation cases")


if __name__ == "__main__":
    main()
