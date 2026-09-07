"""公式資料の検索。語彙選定レベルを学年として投影しない。外部通信なし。"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import unicodedata

from dogido_server.knowledge_query import _language_sources
from dogido_server.language_knowledge import (
    CORE_DATASET_IDS,
    get_kanji_profile,
    search_japanese_knowledge,
)


@dataclass(frozen=True)
class SearchResult:
    terms: list[str]
    facts: list[dict]
    status: str = "searched"
    error: str = ""


def compact(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value if not c.isspace())


class LocalDialogueSearch:
    def __init__(self, *, reference_dir: Path | None = None, cards_path: Path | None = None):
        self.reference_dir = reference_dir
        self.cards_path = cards_path or Path(__file__).with_name("source_cards.json")

    def search(self, terms: list[str], *, facet: str, target: str) -> SearchResult:
        terms = list(dict.fromkeys(t.strip() for t in terms if t.strip()))[:4]
        # モデルが複数語を一要素にした場合も語単位で検索。長い問いから字を拾わない。
        tokens = list(dict.fromkeys(piece for term in terms for piece in term.split()))[:12]
        facts: dict[str, dict] = {}
        try:
            cards = json.loads(self.cards_path.read_text(encoding="utf-8"))["records"]
            for card in cards:
                keys = {compact(s) for s in card["search_terms"]}
                if any(compact(term) in keys for term in terms + tokens):
                    facts[card["id"]] = card
            # 配当・音訓は「対象」にある字だけ。質問中の「年」「習」を検索しない。
            if facet in {"grade", "reading", "spelling", "comparison"}:
                candidates = [t for t in tokens if len(t) == 1]
                if target and all("一" <= c <= "鿿" for c in target) and len(target) <= 4:
                    candidates.extend(target)
                if facet == "grade":
                    # 数字の「漢字」が明示され、意図確認を通過した問いだけの表記変換。
                    digits = dict(zip("0123456789", "零一二三四五六七八九"))
                    candidates = [digits.get(c, c) for c in candidates]
                characters = list(dict.fromkeys(c for c in candidates if "一" <= c <= "鿿"))[:8]
                for char in characters:
                    profile = get_kanji_profile(char, reference_dir=self.reference_dir)
                    if not profile:
                        continue
                    for key in ("grade_level_kanji_allocation", "joyo_kanji"):
                        record = profile[key]
                        if not record:
                            continue
                        if key == "grade_level_kanji_allocation":
                            text = f"漢字『{char}』の小学校配当学年は第{record['school_grade']}学年。字種の配当であり、全ての読みや熟語の学年ではない。"
                        else:
                            readings = "、".join(r["reading"] for r in record["readings"])
                            text = f"常用漢字表の『{char}』の音訓: {readings}。熟語の語源を示す情報ではない。"
                        facts[record["id"]] = self._fact(record, text, "official_table")
                    if facet == "grade" and not profile["grade_level_kanji_allocation"]:
                        record = profile["joyo_kanji"]
                        facts[f"allocation-absence:{char}"] = {
                            "id": f"allocation-absence:{char}",
                            "title_ja": char,
                            "text_ja": "手元の小学校学年別漢字配当表にこの字の配当なし。中学校の特定学年や、習わないことは示さない。",
                            "claim_status": "local_table_lookup",
                            "sources": [],
                        }
            for term in dict.fromkeys(terms + tokens):
                for record in search_japanese_knowledge(
                    term,
                    dataset_ids=CORE_DATASET_IDS,
                    limit=2,
                    reference_dir=self.reference_dir,
                ):
                    text = record.get("definition_ja") or record.get("summary_ja")
                    if text:
                        facts[record["id"]] = self._fact(
                            record,
                            text,
                            record.get("definition_status", "editorial_synthesis"),
                        )
            return SearchResult(terms, list(facts.values())[:10])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # 欠損を「検索したがない」とは区別する。既に取得済みの資料は保持。
            return SearchResult(terms, list(facts.values())[:10], "unavailable", type(exc).__name__)

    def _fact(self, record, text, status):
        return {
            "id": record["id"],
            "title_ja": record["title_ja"],
            "text_ja": text,
            "claim_status": status,
            "scope": record.get("structured_data", {}).get("source_scope", ""),
            "sources": [
                asdict(s) for s in _language_sources(record, reference_dir=self.reference_dir)
            ],
        }
