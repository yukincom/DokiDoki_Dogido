from __future__ import annotations

import json


def build_select_sword_intent_messages(details: dict[str, object]) -> list[dict[str, str]]:
    player_text = str(details.get("player_text") or "").strip()
    system = (
        "あなたはMinecraftの限定意図抽出器。返すのはJSONオブジェクト1件だけ。"
        "プレイヤーが今すぐ手持ちを剣へ変更してほしいと依頼・提案しているかだけを判定する。"
        "入力はSTT由来のことがあり、剣が県・件・券・腱・チェン・ケンになる可能性はある。"
        "ただし音近傍語やmobの存在だけで依頼を補作せず、同じ発話中の持ち替え・装備・"
        "変えて・変更など、現在の操作要求を根拠にする。"
        "剣の所持確認、雑談、感想、作成依頼、過去形は依頼ではない。"
        "intentはselect_weaponかother、weapon_kindはswordかunknown、is_requestは真偽。"
        "evidenceは依頼性を示すプレイヤー発話の連続部分を一字も補作せず抜き出す。"
        "確信が弱ければconfidenceを下げる。"
    )
    user = (
        "プレイヤー発話: "
        + json.dumps(player_text, ensure_ascii=False)
        + "\n形式: "
        + '{"intent":"other","weapon_kind":"unknown","is_request":false,'
        + '"evidence":"","confidence":0.0}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
