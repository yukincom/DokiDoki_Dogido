"""戦況コールアウト用の音声断片パズル。

名称（敵対・中立）+ 体数 + 定型句を cue_voice 下の mp3 で連結する。
ファイルが揃わない・パターン外は None を返し、呼び出し側が全文 TTS に落とす。
"""
from __future__ import annotations

from pathlib import Path

from dogido_server.cues import resolve_cue_path

# 体数クリップは 1〜8 のみ（9 以上は massive 文言側）
_MAX_COUNT_CLIP = 8


def mob_fragment_id(mob_type: str) -> str:
    return f"mob/{(mob_type or '').strip().lower()}"


def count_fragment_id(count: int) -> str | None:
    if count <= 0:
        return None
    n = min(int(count), _MAX_COUNT_CLIP)
    return f"common/counts/{n}"


def phrase_fragment_id(*, suppressed: bool) -> str:
    # 「がおるで」は suppressed 向けの柔らかい締め（素材あり）
    return "common/phrases/ga_orude" if suppressed else "common/phrases/orude"


def fragment_path(cue_dir: Path | None, fragment_id: str) -> Path | None:
    return resolve_cue_path(cue_dir, fragment_id)


def all_fragments_exist(cue_dir: Path | None, fragment_ids: list[str] | tuple[str, ...]) -> bool:
    if not fragment_ids:
        return False
    return all(fragment_path(cue_dir, fid) is not None for fid in fragment_ids)


def build_count_summary_sequence(
    counts: dict[str, int],
    *,
    suppressed: bool = False,
    max_types: int = 3,
    cue_dir: Path | None = None,
) -> list[str] | None:
    """種別ごと「名前 + N体」を並べ、最後に「おるで」系を付ける。

    counts のキーはモブ type id。値が 9 以上の体は 8 体クリップに丸める。
    いずれかの断片が無ければ None（TTS フォールバック）。
    """
    if not counts:
        return None
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:max_types]
    sequence: list[str] = []
    for mob_type, count in ordered:
        if count <= 0:
            continue
        mob_id = mob_fragment_id(mob_type)
        count_id = count_fragment_id(count)
        if count_id is None:
            return None
        sequence.extend([mob_id, count_id])
    if not sequence:
        return None
    sequence.append(phrase_fragment_id(suppressed=suppressed))
    if cue_dir is not None and not all_fragments_exist(cue_dir, sequence):
        return None
    return sequence


def build_single_mob_presence_sequence(
    mob_type: str,
    *,
    count: int = 1,
    suppressed: bool = False,
    cue_dir: Path | None = None,
) -> list[str] | None:
    """単一種の存在コール: 名前 +（任意で体数）+ おるで。"""
    counts = {(mob_type or "").strip().lower(): max(1, int(count))}
    if not counts or not next(iter(counts.keys())):
        return None
    return build_count_summary_sequence(counts, suppressed=suppressed, max_types=1, cue_dir=cue_dir)
