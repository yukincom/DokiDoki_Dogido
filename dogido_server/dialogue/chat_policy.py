"""player_chat の答え方スタンス（dialogue ドメイン）。

雑談3本柱:
  1. none を守る（弱い topic で identify に引きずらない）
  2. 本当の観測だけ短く
  3. 5往復＋LLM で相槌（履歴長は触らない）

実装の正本。旧 import は dogido_server.player_chat_policy が re-export。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

ReplyStance = Literal["saw", "hypothesis", "clarify", "none"]

_POLICY_LINES: dict[ReplyStance, str] = {
    "saw": (
        "脅威メモ・観測の視認を優先してよい。"
        "プレイヤーの話と一致するなら種名を言ってよい。"
        "メモ・話題ヒントに無い種族やNPCは作らない。"
    ),
    "hypothesis": (
        "自分は視認できていない。『見えてへん』はよいが、"
        "『おらへん』『気のせい』でプレイヤーを否定しない。"
        "話題ヒントの候補だけを『かもしれん』と弱く触れてよい。"
        "ヒント外の種族・NPCは禁止。"
    ),
    "clarify": (
        "種名を当てず、色・形・動きなど特徴を聞き返してよい。"
        "カタログに無い名前を作らない。"
        "プレイヤーの『いる／見えてる』を否定しない。"
    ),
    "none": (
        "雑談として自然に返す。"
        "根拠のない種名・敵・音の捏造はしない。"
        "観測メモにある生き物には触れてよい。"
    ),
}

# 単独では identify に使わない語（描写タグとしてはカタログに残してよい）
GENERIC_TOPIC_TERMS: frozenset[str] = frozenset(
    {
        "大きい",
        "小さい",
        "きれい",
        "白い",
        "黒い",
        "赤い",
        "青い",
        "緑",
        "黄色い",
        "長い",
        "短い",
        "丸い",
        "速い",
        "遅い",
        "怖い",
        "変な",
        "強い",
        "弱い",
        "暗い",
        "明るい",
        "古い",
        "新しい",
        "沼",
        "海",
        "夜",
        "森",
        "山",
        "空",
        "村",
        "平地",
        "地下",
        "洞窟",
    }
)

# 1 文字でも identify 信号として認める語
_SPECIFIC_SHORT_TERMS: frozenset[str] = frozenset({"旗"})

# identify 意図（hypothesis ではなく clarify 判定用が主）
_IDENTIFY_INTENT_MARKERS: tuple[str, ...] = (
    "なんだ",
    "なにもの",
    "何物",
    "なんや",
    "なに",
    "何",
    "だれ",
    "誰",
    "あいつ",
    "どれ",
    "何て",
    "なんて",
    "どういう",
)


def is_generic_topic_term(term: str) -> bool:
    text = str(term or "").strip()
    if not text:
        return True
    if text in GENERIC_TOPIC_TERMS:
        return True
    # 1 文字は原則弱い（旗だけ例外）
    if len(text) < 2 and text not in _SPECIFIC_SHORT_TERMS:
        return True
    return False


def term_is_identify_signal(term: str) -> bool:
    """旗・ババア・前哨基地など、identify に使える語か。"""
    return not is_generic_topic_term(term)


def hit_has_identify_signal(hit: dict[str, Any]) -> bool:
    matched = [str(t).strip() for t in (hit.get("matched_terms") or ()) if str(t).strip()]
    if not matched:
        return False
    return any(term_is_identify_signal(term) for term in matched)


def filter_usable_topic_hits(
    hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    """GENERIC のみの hit を除いた identify 用候補。"""
    usable: list[dict[str, Any]] = []
    for hit in hits or ():
        if not isinstance(hit, dict):
            continue
        if hit_has_identify_signal(hit):
            usable.append(hit)
    return usable


def has_identify_intent(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _IDENTIFY_INTENT_MARKERS):
        return True
    # 「いる？」「おる？」系の質問
    if ("？" in text or "?" in text) and any(token in text for token in ("いる", "おる", "誰", "何", "なに")):
        return True
    return False


def resolve_reply_stance(
    *,
    has_visual_threats: bool,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    threat_summary: str = "",
    user_text: str = "",
    observed_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> ReplyStance:
    """観測と（フィルタ後の）トピックから reply_stance を決める。

    - topic あり ≠ 即 hypothesis
    - usable（非 GENERIC 語）があるときだけ hypothesis
    """
    threat = (threat_summary or "").strip()
    if has_visual_threats or "視認" in threat:
        return "saw"

    raw_hits = list(topic_hits or ())
    usable = filter_usable_topic_hits(raw_hits)
    observed = {
        str(item).removeprefix("minecraft:").strip().lower()
        for item in (observed_ids or ())
        if str(item or "").strip()
    }

    if usable:
        # 観測と一致する usable hit があれば hypothesis（念のため）
        # usable 自体が識別語を持つので、基本はすべて hypothesis でよい
        return "hypothesis"

    # 観測 id だけが raw hit と一致し GENERIC のみ…は usable 空のまま。saw は上で処理済み。
    _ = observed  # 将来: GENERIC+観測一致の救済に使える

    if has_identify_intent(user_text):
        return "clarify"
    return "none"


def reply_policy_line(stance: ReplyStance | str) -> str:
    key = str(stance or "none").strip().lower()
    if key not in _POLICY_LINES:
        key = "none"
    return _POLICY_LINES[key]  # type: ignore[index]


def build_allowed_speech_labels(
    *,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    visual_types: list[str] | tuple[str, ...] | None = None,
    passive_types: list[str] | tuple[str, ...] | None = None,
    hearing_named_mobs: list[str] | tuple[str, ...] | None = None,
    recent_mob_types: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """出力に使ってよい表示名。

    topic ∪ 視認脅威 ∪ 平和/ambient 観測 ∪ hearing の union。
    観測・討伐された mob だけは、カタログで明示した安全な上位名・略称も許可する。
    topic だけからは略称を展開しない。
    """
    from dogido_server.entry_catalog import all_mob_entries, mob_entry, structure_entries

    labels: list[str] = []
    seen: set[str] = set()

    def add(raw: object | None) -> None:
        text = str(raw or "").strip()
        if len(text) < 2 or text in seen:
            return
        seen.add(text)
        labels.append(text)

    def add_observed_entry(entry: dict[str, Any]) -> None:
        add(entry.get("label"))
        aliases = entry.get("observed_speech_aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                add(alias)

    def add_observed_mob_type(raw_type: object | None) -> None:
        mob_id = str(raw_type or "").removeprefix("minecraft:").strip().lower()
        if not mob_id:
            return
        entry = mob_entry(mob_id)
        if entry:
            add_observed_entry(entry)
        else:
            add(mob_id)

    def add_observed_mob_label(raw_label: object | None) -> None:
        label = str(raw_label or "").strip()
        add(label)
        if not label:
            return
        for entry in all_mob_entries().values():
            if str(entry.get("label") or "").strip() == label:
                add_observed_entry(entry)

    for hit in topic_hits or ():
        add(hit.get("label_ja"))
        entry_id = str(hit.get("entry_id") or "").strip()
        kind = str(hit.get("kind") or "mob")
        if kind == "mob" and entry_id:
            entry = mob_entry(entry_id)
            if entry:
                add(entry.get("label"))
        elif kind == "structure" and entry_id:
            entry = structure_entries().get(entry_id) or {}
            add(entry.get("label"))

    for raw_type in visual_types or ():
        add_observed_mob_type(raw_type)
    for raw_type in passive_types or ():
        add_observed_mob_type(raw_type)
    for name in hearing_named_mobs or ():
        add_observed_mob_label(name)
    for raw_type in recent_mob_types or ():
        add_observed_mob_type(raw_type)

    return labels


def build_observed_speech_name_corrections(
    observed_mob_types: list[str] | tuple[str, ...] | set[str] | None,
) -> dict[str, str]:
    """直近に確定した mob から、一般名→正式名の一意な修正表を作る。

    一般種そのものも観測済みならプレイヤーに合わせるため修正しない。
    同じ一般名に複数の修正先がある場合も、曖昧なので修正しない。
    """
    from dogido_server.entry_catalog import mob_entry

    observed = {
        str(raw or "").removeprefix("minecraft:").strip().lower()
        for raw in (observed_mob_types or ())
        if str(raw or "").strip()
    }
    candidates: dict[str, set[str]] = {}
    for target_id in observed:
        target = mob_entry(target_id)
        if not target:
            continue
        target_label = str(target.get("label") or "").strip()
        rewrite_from_ids = target.get("observed_speech_rewrite_from_ids")
        if not target_label or not isinstance(rewrite_from_ids, list):
            continue
        for raw_source_id in rewrite_from_ids:
            source_id = str(raw_source_id or "").removeprefix("minecraft:").strip().lower()
            if not source_id or source_id in observed:
                continue
            source = mob_entry(source_id)
            source_label = str((source or {}).get("label") or "").strip()
            if not source_label or source_label == target_label:
                continue
            candidates.setdefault(source_label, set()).add(target_label)
    return {
        source_label: next(iter(target_labels))
        for source_label, target_labels in candidates.items()
        if len(target_labels) == 1
    }


def should_enforce_speech_whitelist(
    stance: str | None,
    allowed_labels: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """白リスト検査を行うか。saw / hypothesis のみ。"""
    key = str(stance or "none").strip().lower()
    return key in {"saw", "hypothesis"}


@lru_cache(maxsize=1)
def catalog_speech_labels() -> tuple[str, ...]:
    """照合用: カタログ上の表示名と安全な観測時略称（長い順）。"""
    from dogido_server.entry_catalog import all_mob_entries, structure_entries

    labels: set[str] = set()
    for entry in all_mob_entries().values():
        label = str(entry.get("label") or "").strip()
        if len(label) >= 2:
            labels.add(label)
        aliases = entry.get("observed_speech_aliases")
        if isinstance(aliases, list):
            labels.update(
                str(alias).strip()
                for alias in aliases
                if len(str(alias).strip()) >= 2
            )
    for entry in structure_entries().values():
        label = str(entry.get("label") or "").strip()
        if len(label) >= 2:
            labels.add(label)
    return tuple(sorted(labels, key=lambda item: (-len(item), item)))


def catalog_labels_mentioned_in_text(text: str) -> list[str]:
    """文中に含まれるカタログ表示名（長い一致を優先し、短い部分一致はマスク）。"""
    raw = text or ""
    if not raw:
        return []
    covered = [False] * len(raw)
    found: list[str] = []
    for label in catalog_speech_labels():
        start = 0
        while True:
            index = raw.find(label, start)
            if index < 0:
                break
            end = index + len(label)
            if not any(covered[index:end]):
                found.append(label)
                for pos in range(index, end):
                    covered[pos] = True
            start = index + 1
    return found


def rewrite_observed_speech_names(
    text: str,
    corrections: dict[str, str] | None,
) -> tuple[str, list[tuple[str, str]]]:
    """既知の一般名だけを、長い正式名の内側を壊さず一意な正式名へ戻す。"""
    raw = text or ""
    rules = {
        str(source).strip(): str(target).strip()
        for source, target in (corrections or {}).items()
        if str(source).strip() and str(target).strip() and str(source).strip() != str(target).strip()
    }
    if not raw or not rules:
        return raw, []

    covered = [False] * len(raw)
    replacements: list[tuple[int, int, str, str]] = []
    for label in catalog_speech_labels():
        start = 0
        while True:
            index = raw.find(label, start)
            if index < 0:
                break
            end = index + len(label)
            if not any(covered[index:end]):
                for pos in range(index, end):
                    covered[pos] = True
                target = rules.get(label)
                if target:
                    replacements.append((index, end, label, target))
            start = index + 1
    if not replacements:
        return raw, []

    corrected = raw
    applied: list[tuple[str, str]] = []
    for start, end, source, target in sorted(replacements, reverse=True):
        corrected = corrected[:start] + target + corrected[end:]
        applied.append((source, target))
    applied.reverse()
    return corrected, applied


def contains_unlisted_speech_names(
    text: str,
    allowed_labels: list[str] | tuple[str, ...] | set[str] | None,
) -> bool:
    """白リスト外のカタログ種名／構造物名が出力に含まれるか。"""
    if not text:
        return False
    allowed = {str(item).strip() for item in (allowed_labels or ()) if str(item).strip()}
    for label in catalog_labels_mentioned_in_text(text):
        if label not in allowed:
            return True
    return False


_IDENTIFY_MIN_SCORE = 6.0


def build_identify_skeleton(
    *,
    stance: ReplyStance | str,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    min_score: float = _IDENTIFY_MIN_SCORE,
) -> str | None:
    """高信頼トピックの固定骨子。usable（非 GENERIC）hit のみ。"""
    if str(stance or "") != "hypothesis":
        return None
    hits = filter_usable_topic_hits(topic_hits)
    if not hits:
        return None
    top = hits[0]
    score = float(top.get("score") or 0.0)
    if score < min_score:
        return None
    matched = tuple(str(t) for t in (top.get("matched_terms") or ()) if str(t).strip())
    if matched and not any(term_is_identify_signal(t) for t in matched):
        return None
    # 同点トップが複数なら決めつけない
    if len(hits) >= 2 and abs(float(hits[1].get("score") or 0.0) - score) < 0.01:
        labels = [str(h.get("label_ja") or "") for h in hits[:2] if h.get("label_ja")]
        if len(labels) >= 2:
            return f"俺には見えんけど、{labels[0]}か{labels[1]}あたりかもしれんな"
    label = str(top.get("label_ja") or "").strip()
    if not label:
        return None
    return f"俺にははっきり見えんけど、{label}かもしれんな"
