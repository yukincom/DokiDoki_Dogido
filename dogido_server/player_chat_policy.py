"""player_chat の答え方スタンス（状態機械が決める骨格）。

プロンプト長文に方針を積まず、ここで saw / hypothesis / clarify / none を決める。
S2: 発話に使ってよい種名白リストもここで組み立てる。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

ReplyStance = Literal["saw", "hypothesis", "clarify", "none"]

_POLICY_LINES: dict[ReplyStance, str] = {
    "saw": (
        "脅威メモの視認を優先してよい。"
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
    ),
}

# 「あれ何？」系で topic も視認も無いときの clarify ヒント（粗い）
_CLARIFY_HINTS = (
    "何",
    "なに",
    "なんや",
    "だれ",
    "誰",
    "あいつ",
    "あれ",
    "それ",
    "どれ",
    "いる",
    "おる",
    "見",
    "みて",
)


def resolve_reply_stance(
    *,
    has_visual_threats: bool,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    threat_summary: str = "",
    user_text: str = "",
) -> ReplyStance:
    """観測とトピック候補から reply_stance を決める。"""
    threat = (threat_summary or "").strip()
    # 「ついさっき 視認 …」も saw（visual バッファ経由）
    if has_visual_threats or "視認" in threat:
        return "saw"
    hits = list(topic_hits or ())
    if hits:
        return "hypothesis"
    text = (user_text or "").strip()
    if text and any(hint in text for hint in _CLARIFY_HINTS):
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
) -> list[str]:
    """出力に使ってよい表示名。

    topic ∪ 視認脅威 ∪ 平和/ambient 観測 ∪ hearing の union。
    種 id は呼び出し側が渡す（特定 mob 専用ではない）。
    """
    from dogido_server.entry_catalog import mob_entry, structure_entries

    labels: list[str] = []
    seen: set[str] = set()

    def add(raw: object | None) -> None:
        text = str(raw or "").strip()
        if len(text) < 2 or text in seen:
            return
        seen.add(text)
        labels.append(text)

    def add_mob_type(raw_type: object | None) -> None:
        mob_id = str(raw_type or "").removeprefix("minecraft:").strip().lower()
        if not mob_id:
            return
        entry = mob_entry(mob_id)
        if entry:
            add(entry.get("label"))
        else:
            add(mob_id)

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
        add_mob_type(raw_type)
    for raw_type in passive_types or ():
        add_mob_type(raw_type)
    for name in hearing_named_mobs or ():
        add(name)

    return labels


def should_enforce_speech_whitelist(stance: str | None, allowed_labels: list[str] | tuple[str, ...] | None) -> bool:
    """白リスト検査を行うか。

    - saw / hypothesis: 候補外の種名捏造を止めるため常に検査
    - none / clarify: 雑談を殺さない。検査しない
      （観測名は allowed に載せても、雑談全体を空白リスト全禁止にはしない）
    """
    key = str(stance or "none").strip().lower()
    return key in {"saw", "hypothesis"}


@lru_cache(maxsize=1)
def catalog_speech_labels() -> tuple[str, ...]:
    """照合用: カタログ上の表示名（長い順）。"""
    from dogido_server.entry_catalog import all_mob_entries, structure_entries

    labels: set[str] = set()
    for entry in all_mob_entries().values():
        label = str(entry.get("label") or "").strip()
        if len(label) >= 2:
            labels.add(label)
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


# topic score の目安（visual_tags 1 語 ≈ 6, 長い語はそれ以上）
_IDENTIFY_MIN_SCORE = 6.0


def build_identify_skeleton(
    *,
    stance: ReplyStance | str,
    topic_hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    min_score: float = _IDENTIFY_MIN_SCORE,
) -> str | None:
    """高信頼トピックの固定骨子（S3）。LLM オフや style reject 時の最低限の返事。"""
    if str(stance or "") != "hypothesis":
        return None
    hits = list(topic_hits or ())
    if not hits:
        return None
    top = hits[0]
    score = float(top.get("score") or 0.0)
    if score < min_score:
        return None
    # 1文字タグ単独（例: お風呂⊃風）では骨子を出さない。旗など1文字は LLM/ヒントのみ。
    matched = tuple(str(t) for t in (top.get("matched_terms") or ()) if str(t).strip())
    if matched and max(len(t) for t in matched) < 2:
        return None
    # 同点トップが複数なら決めつけない
    if len(hits) >= 2 and abs(float(hits[1].get("score") or 0.0) - score) < 0.01:
        labels = [str(h.get("label_ja") or "") for h in hits[:2] if h.get("label_ja")]
        if len(labels) >= 2:
            return f"俺には見えんけど、{labels[0]}か{labels[1]}あたりかもしれんな"
    label = str(top.get("label_ja") or "").strip()
    if not label:
        return None
    kind = str(top.get("kind") or "mob")
    if kind == "structure":
        return f"俺にははっきり見えんけど、{label}かもしれんな"
    return f"俺にははっきり見えんけど、{label}かもしれんな"
