"""川柳ワークショップ用プロンプト（詩人モード leaf）。

判定はコード（workshop_open_intent）。ここは言い回し生成のみ。
player_chat のサバイバル材料（look / topic / 所持）は載せない。
"""

from __future__ import annotations

from .prompt_common import detail_str, leaf_dialog
from .types import LeafGenerationRequest


def build_haiku_workshop_reply_messages(request: LeafGenerationRequest) -> list[dict[str, str]]:
    details = dict(request.details or {})
    verse = detail_str(details, "verse") or "（句なし）"
    materials = detail_str(details, "materials_speech")
    player_text = detail_str(details, "player_text") or "（聞き取れなかった）"
    intent_kind = detail_str(details, "intent_kind") or "soft_default"

    materials_line = f"狙いの一言: {materials}\n" if materials else "狙いの一言: （特になし）\n"
    user_prompt = (
        "いまは川柳ワークショップ中。プレイヤーと句の話をしている。\n"
        "あなたはドギド。関西弁。一人称はオレ。\n"
        "\n"
        "【やる】\n"
        "- プレイヤーの指摘・質問・言い換え提案に、句の言葉として短く乗る\n"
        "- 字余り・響き・読み・狙いの話をしてよい\n"
        "- 1文だけ。だいたい12〜42字\n"
        "\n"
        "【やらない】\n"
        "- アイテムの用途・採掘・クラフト・火起こし・戦闘・攻略\n"
        "- 周囲のブロックや look を話題に広げる\n"
        "- 句と無関係な雑談・長い講義\n"
        "- 内部キー名（biome や ID）を口にしない\n"
        "\n"
        "/no_think\n"
        "【材料】\n"
        f"句:\n{verse}\n"
        f"{materials_line}"
        f"プレイヤー:「{player_text}」\n"
        f"取り込み種別: {intent_kind}\n"
        "\n"
        "句の言葉の話として、セリフ1文だけ返す。"
    )
    # character_mode=workshop は details 経由で system に載る
    return leaf_dialog("haiku_workshop_reply", request, user_prompt)
