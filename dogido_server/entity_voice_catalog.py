"""モブ表示名と、コールアウト断片音声用のテキスト定義。

実行時のコールアウトは当面全文 TTS もあるが、方針としては
敵対・中立の名称断片 + 体数 + 定型句をパズル連結する（再生配線は段階実装）。

友好（passive のみ）の名称 mp3 はリポに置かない。
"""
from __future__ import annotations

from dogido_server.entry_catalog import (
    hostile_mob_labels,
    neutral_mob_labels,
    passive_mob_labels,
    threat_mob_labels,
)

# カタログ上の区分（entry_catalog の hostile / neutral / passive）
HOSTILE_MOB_VOICE_LABELS: dict[str, str] = hostile_mob_labels()
NEUTRAL_MOB_VOICE_LABELS: dict[str, str] = neutral_mob_labels()
# 注意: entry_catalog.passive_mob_labels は neutral をマージした「非脅威寄り」用
PASSIVE_MOB_VOICE_LABELS: dict[str, str] = passive_mob_labels()

# 戦況コールアウト用: 敵対 + 中立のみ（友好 pure passive は含めない）
CALLOUT_MOB_VOICE_LABELS: dict[str, str] = threat_mob_labels()

# 表示名の総覧（文言生成・ラベル解決用。音声ファイルの有無とは別）
MOB_VOICE_LABELS: dict[str, str] = {
    **PASSIVE_MOB_VOICE_LABELS,
    **HOSTILE_MOB_VOICE_LABELS,
    **NEUTRAL_MOB_VOICE_LABELS,
}

# 断片コールアウト用（scripts/generate_entity_voice_cache.py）
COUNT_FRAGMENT_TEXTS: dict[str, str] = {
    str(number): f"{number}体"
    for number in range(1, 9)
}

PHRASE_FRAGMENT_TEXTS: dict[str, str] = {
    "ga_orude": "がおるで",
    "orude": "おるで",
}

RUNTIME_HOSTILE_LABELS: dict[str, str] = dict(CALLOUT_MOB_VOICE_LABELS)
