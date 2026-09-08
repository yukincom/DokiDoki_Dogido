"""運営元だけでなく、対象学年と個別ページを確認した子ども用の送り先。"""

import json
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit


CATALOG = Path(__file__).with_name("child_resources.json")


def _normalized(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


class ChildResources:
    def __init__(self, path=CATALOG):
        self.records = json.loads(Path(path).read_text(encoding="utf-8"))["records"]

    def select(self, target, terms, *, school_grade, facet):
        words = {_normalized(t) for t in [target, *terms]}
        for resource in self.records:
            if not resource.get("auto_open") or not resource.get("reviewed_at"):
                continue
            grades = resource.get("school_grades", [])
            if school_grade not in grades:
                continue
            if facet not in resource.get("facets", []):
                continue
            if words.intersection(_normalized(t) for t in resource["lookup_terms"]):
                return resource
        return None

    @staticmethod
    def same_page(url, resource):
        # 公的なポータルに載る企業サイトを、掲載元と同じ扱いで通さない。
        p = urlsplit(url)
        expected = urlsplit(resource["url"])
        return (p.scheme, p.netloc, p.path, p.query) == (
            expected.scheme,
            expected.netloc,
            expected.path,
            expected.query,
        )
