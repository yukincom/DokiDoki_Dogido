"""専用Chrome SDKとRust IPCの結果形式。対話や検索の開始判断は持たない。"""
from dataclasses import dataclass, field

SEARCH_NOTICE = "ちょっと調べてみよか！"
FACET_LABELS = {
    "grade": "漢字 配当学年",
    "reading": "読み方",
    "meaning": "意味",
    "spelling": "表記",
    "grammar": "文法",
    "usage": "用法",
    "etymology": "語源",
    "translation": "翻訳",
    "comparison": "違い",
    "classification": "分類",
    "other": "",
}


@dataclass
class WebResult:
    query: str = ""
    status: str = "not_requested"
    pages: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    search_status: str = "not_requested"
    child_status: str = "not_requested"
    search_results: list[dict] = field(default_factory=list)
    search_url: str = ""
    timing: dict = field(default_factory=dict)  # 診断専用。ResearchContextへ渡さない。
