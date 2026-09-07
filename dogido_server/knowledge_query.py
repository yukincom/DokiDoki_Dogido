"""明示的な知識質問を、ローカルの出典付きデータへ安全に接続する。

この層は二つの境界を守る。

* 入力経路では質問の分類と、発話中に実在する検索語の抽出だけを行う。
* 実データの検索は player chat / workshop の発話枝へ到達した一回だけ行う。

戦況判断、支援操作、保存判断には検索結果を渡さない。検索不能・0件・
出典欠落時は、LLM に補わせず固定の失敗結果を返す。
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import unicodedata
from typing import Literal, Protocol

from dogido_server.knowledge_subjects import (
    AMBIGUOUS_CURATED_SUBJECTS,
    CURATED_JAPANESE_SUBJECTS,
    CURATED_POETRY_SUBJECTS,
    GRAMMAR_PATTERN_EXACT_SUBJECTS,
    OFFICIAL_KANJI_TABLE_SUBJECTS,
)


LOGGER = logging.getLogger("uvicorn.error")

KnowledgeDomain = Literal["japanese_language", "poetry", "minecraft"]
KnowledgeIntent = Literal[
    "definition",
    "rules",
    "reading",
    "grade",
    "identifier",
    "change",
    "properties",
    "classification",
]
KnowledgeStatus = Literal["found", "not_found", "unavailable"]


@dataclass(frozen=True, slots=True)
class ExplicitKnowledgeQuery:
    """コードで確定した、現在ターン限りの検索要求。"""

    domain: KnowledgeDomain
    subject: str
    intent: KnowledgeIntent
    evidence: str


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    """一つの事実を裏付ける出典。URLは発話せず監査用に保持する。"""

    source_id: str
    title_ja: str
    citation_label_ja: str
    locator: str = ""
    url: str = ""
    source_kind: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeFact:
    """出典境界を保った短い一事実。"""

    record_id: str
    dataset_id: str
    title_ja: str
    text_ja: str
    claim_status: str
    sources: tuple[KnowledgeSource, ...]
    dialogue_text_ja: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeLookupResult:
    query: ExplicitKnowledgeQuery
    status: KnowledgeStatus
    facts: tuple[KnowledgeFact, ...] = ()
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class RenderedKnowledgeReply:
    """発話本文、同じ本文の音声配送計画、別表示用の参考資料。"""

    text: str
    speech_segments: tuple[str, ...]
    references: tuple[KnowledgeSource, ...] = ()


class KnowledgeProvider(Protocol):
    def lookup(
        self,
        query: ExplicitKnowledgeQuery,
        *,
        limit: int = 3,
    ) -> KnowledgeLookupResult: ...


_QUESTION_CUES = (
    "って何",
    "ってなに",
    "とは",
    "とは何",
    "とはなに",
    "どんなもの",
    "どんな意味",
    "どんな決まり",
    "どういうもの",
    "どういう意味",
    "どういう決まり",
    "教えて",
    "説明して",
    "知りたい",
    "何年生",
    "何年で",
    "読み",
    "ルール",
    "規則",
    "決まり",
    "形式",
    "特徴",
    "分類",
    "意味",
    "識別子",
    "ID",
    "id",
    "耐久値",
    "最大スタック",
    "接続",
    "どう変わ",
    "変更点",
    "作り方",
    "の漢字って",
)

_GENERATION_REQUESTS = (
    "作って",
    "詠んで",
    "書いて",
    "直して",
    "添削して",
    "考えて",
)

_JAPANESE_MARKERS = (
    "国語",
    "日本語",
    "文法",
    "品詞",
    "体言",
    "用言",
    "助詞",
    "助動詞",
    "活用",
    "主語",
    "述語",
    "主述の関係",
    "修飾語",
    "被修飾語",
    "指示する語句",
    "指示語",
    "接続する語句",
    "接続語",
    "語句の係り方",
    "語順",
    "単語の類別",
    "単語",
    "文の成分",
    "照応",
    "動詞",
    "形容詞",
    "形容動詞",
    "名詞",
    "副詞",
    "連体詞",
    "接続詞",
    "敬語",
    "常用漢字",
    "学年別漢字",
    "漢字配当",
    "音読み",
    "訓読み",
    "送り仮名",
    "仮名遣い",
    "歴史的仮名",
    "歴史的表記",
    "変体仮名",
    "使われないひらがな",
    "古いひらがな",
    "くずし字",
    "じ・ぢ",
    "ず・づ",
    "公用文",
    "仮名書き",
    "振り仮名",
    "枕詞",
    "まくらことば",
    "掛詞",
    "縁語",
    "序詞",
    "ゐ",
    "ゑ",
    "ヰ",
    "ヱ",
    "くゎ",
)

# 分類用の閉じた語彙であり、詩形の説明そのものではない。
# 事実本文は必ずDBから読む。
_POETRY_MARKERS = (
    "日本語詩歌",
    "和歌",
    "短歌",
    "長歌",
    "旋頭歌",
    "片歌",
    "仏足石歌体",
    "仏足跡歌体",
    "仏足石歌",
    "連歌",
    "俳諧",
    "発句",
    "連句",
    "俳句",
    "川柳",
    "せんりゅう",
    "狂歌",
    "都々逸",
    "都々一",
    "どどいつ",
    "琉歌",
    "りゅうか",
    "詩形",
    "韻律",
    "押韻",
    "抒情詩",
    "リリック",
    "叙事詩",
    "英雄叙事詩",
    "エピック",
    "劇的独白詩",
    "ペルソナ詩",
    "バラッド",
    "物語歌謡",
    "物語歌",
    "譚詩",
    "ソネット",
    "十四行詩",
    "頌歌",
    "オード",
    "哀歌",
    "エレジー",
    "哀悼詩",
    "ヴィラネル",
    "ビラネル",
    "セスティーナ",
    "無韻詩",
    "ブランク・ヴァース",
    "自由詩",
    "自由韻律詩",
    "散文詩",
    "連句",
    "俳文",
    "絶句",
    "截句",
    "律詩",
    "近体律詩",
    "宋詞",
    "曲子詞",
    "シジョ",
    "時調",
    "シュローカ",
    "ガザル",
    "ガゼル",
    "カスィーダ",
    "カシーダ",
    "ルバーイー",
    "ルバイヤート",
    "マスナヴィー",
    "マスナウィー",
    "パントゥン",
    "パントゥーム",
    "オリキ",
    "ヨルバ称賛詩",
    "ワイアタ",
    "マオリ歌謡",
    "デシマ",
    "エスピネーラ",
    "ブルース詩",
    "sonnet",
    "haiku",
    "villanelle",
    "sestina",
    "ghazal",
    "qasida",
)

# 一文字や短い英語名は部分一致に使わず、主題の完全一致だけで扱う。
_POETRY_EXACT_SUBJECTS = {
    "詞",
    "拍",
    "モーラ",
    "音数",
    "lyric",
    "persona poem",
    "vers libre",
    "linked verse",
    "haiku prose",
    "regulated verse",
    "song lyric",
    "sloka",
    "rhyming couplets",
    "praise poetry",
    "waiata tawhito",
    "blues poem",
} | set(CURATED_POETRY_SUBJECTS)

_MINECRAFT_MARKERS = (
    "minecraft",
    "マインクラフト",
    "マイクラ",
    "ゲームルール",
    "レジストリ",
    "データパック",
    "リソースパック",
    "名前空間",
    "スポーンエッグ",
    "ダイヤモンドの剣",
    "ネザライトの剣",
    "doMobSpawning",
    "doDaylightCycle",
    "doWeatherCycle",
    "snowAccumulationHeight",
)

_MINECRAFT_EXACT_SUBJECTS = {
    "剣",
    "この剣",
    "その剣",
    "あの剣",
}

_MINECRAFT_RESOURCE_LOCATION_RE = re.compile(
    r"(?P<namespace>[a-z0-9_.-]+):(?P<path>[a-z0-9_.-][a-z0-9_./-]*)\Z",
    flags=re.IGNORECASE,
)
_MINECRAFT_UNAMBIGUOUS_CONTEXT_MARKERS = tuple(
    marker
    for marker in _MINECRAFT_MARKERS
    if marker not in {"minecraft", "名前空間", "レジストリ"}
)

_GENERIC_SUBJECTS = {
    "これ",
    "それ",
    "あれ",
    "ここ",
    "そこ",
    "あそこ",
    "もの",
    "こと",
    "やつ",
    "意味",
    "ルール",
    "決まり",
    "形式",
}

_ASPECT_PATTERN = (
    r"ルール|規則|決まり|形式|特徴|分類|意味|読み(?:方)?|配当学年|学年|"
    r"ID|id|識別子|耐久値|最大スタック(?:数)?|接続|変更点|作り方"
)


def _normalize_text(value: object) -> str:
    # NFKC は全角チルダ ``～`` を ``~`` にするが、波ダッシュ ``〜`` は
    # 残す。文型DBでは両表記が混在するため、検索境界だけASCIIへ統一する。
    return (
        unicodedata.normalize("NFKC", str(value))
        .replace("～", "~")
        .replace("〜", "~")
        .strip()
    )


def _compact(value: object) -> str:
    return "".join(_normalize_text(value).casefold().split())


_JAPANESE_SUBJECT_KEYS = frozenset(
    _compact(value)
    for value in (
        *_JAPANESE_MARKERS,
        *CURATED_JAPANESE_SUBJECTS,
        *GRAMMAR_PATTERN_EXACT_SUBJECTS,
        *OFFICIAL_KANJI_TABLE_SUBJECTS,
    )
)
_POETRY_SUBJECT_KEYS = frozenset(
    _compact(value) for value in (*_POETRY_MARKERS, *_POETRY_EXACT_SUBJECTS)
)
_MINECRAFT_EXACT_SUBJECT_KEYS = frozenset(
    _compact(value) for value in _MINECRAFT_EXACT_SUBJECTS
)
_AMBIGUOUS_CURATED_SUBJECT_KEYS = frozenset(
    _compact(value) for value in AMBIGUOUS_CURATED_SUBJECTS
)
_GRAMMAR_PATTERN_EXACT_SUBJECT_KEYS = frozenset(
    _compact(value) for value in GRAMMAR_PATTERN_EXACT_SUBJECTS
)
_OFFICIAL_KANJI_TABLE_RECORDS = {
    **{
        _compact(value): "jp.bunka.joyo-kanji"
        for value in (
            "常用漢字",
            "常用漢字表",
            "文化庁 常用漢字表",
            "常用漢字表の音訓索引",
        )
    },
    **{
        _compact(value): "jp.mext.grade-level-kanji-allocation"
        for value in (
            "学年別漢字",
            "学年別漢字配当表",
            "現行学年別漢字配当表",
            "小学校配当漢字",
            "漢字配当",
        )
    },
}


def _strip_subject(value: str) -> str:
    # 文型の正式名には末尾の疑問符（例: ``～たら？``）や、一般の質問で
    # 後置句に見える文字列（例: ``～について``）そのものが含まれる。
    # ここで先に落とすと別文型へ誤一致するため、疑問符は後段まで保持する。
    subject = _normalize_text(value).strip(" \t\r\n、,。.!！:：\"'")

    def unwrap_outer_quotes(text: str) -> str:
        pairs = {"「": "」", "『": "』", "\"": "\"", "'": "'"}
        while len(text) >= 2 and pairs.get(text[0]) == text[-1]:
            text = text[1:-1].strip()
        return text

    subject = unwrap_outer_quotes(subject)
    subject = re.sub(r"^(?:ねえ|なあ|ドギド)[、, ]*", "", subject)
    prefixes = (
        r"(?:Minecraft|マインクラフト|マイクラ)\s*\d+\.\d+(?:\.\d+)?(?:版)?(?:で|の)",
        r"(?:Java(?:\s+Edition)?|Java版)\s*\d+\.\d+(?:\.\d+)?(?:版)?(?:で|の)",
        r"1\.\d+(?:\.\d+)?(?:版)?(?:で|の)",
        r"(?:Minecraft|マインクラフト|マイクラ)(?:で|の)",
        r"(?:国語|日本語)(?:で|の)",
        r"(?:世界の詩|世界の詩形|詩形)(?:で|の)",
    )
    for pattern in prefixes:
        subject = re.sub(rf"^{pattern}", "", subject, flags=re.IGNORECASE)

    # 波線始まりは国立国語研究所DBの文型表記として扱う。正式名の一部を
    # 一般的な質問接尾辞と解釈せず、そのまま検索キーへ渡す。
    grammar_pattern = unwrap_outer_quotes(
        subject.strip(" \t\r\n、,。.!！:：\"'")
    )
    if grammar_pattern.startswith(("~", "〜")):
        return grammar_pattern

    subject = re.sub(r"(?:について|に関して|のこと)$", "", subject)
    return unwrap_outer_quotes(subject.strip(" \t\r\n、,。.!！?？:：\"'"))


def _extract_subject(text: str) -> str | None:
    # 音声で一文字だけを言うと同音語になりやすいため、「漢字の一は何年生で
    # 習うの」のように文字種を先に明示できる形を受け付ける。返す主題は
    # 発話中に実在する一文字だけで、推測や読みからの変換はしない。
    numeric_kanji_grade = re.fullmatch(
        r"^(?:じゃあ[、, ]*)?数字の[「『]?(?P<subject>[0-9])[」』]?"
        r"(?:は漢字で(?:何年生で習う(?:漢字)?)?(?:の|のかな|ですか|か)?|"
        r"の漢字(?:って|とは)?)[。.!！?？]*$",
        text,
        flags=re.IGNORECASE,
    )
    if numeric_kanji_grade is not None:
        return numeric_kanji_grade.group("subject")

    contextual_kanji_grade = re.fullmatch(
        r"^(?:漢字の)?[「『]?(?P<context>[\u3400-\u4dbf\u4e00-\u9fff々]{2,12})の"
        r"(?P<subject>[\u3400-\u4dbf\u4e00-\u9fff々])[」』]?"
        r"(?:という漢字)?(?:は|って)(?:小学校)?何年生で習う(?:漢字)?"
        r"(?:ですか|なの|の|か)?[。.!！?？]*$",
        text,
        flags=re.IGNORECASE,
    )
    if contextual_kanji_grade is not None:
        context = contextual_kanji_grade.group("context")
        subject = contextual_kanji_grade.group("subject")
        if subject in context:
            return subject

    spoken_kanji_grade = re.fullmatch(
        r"^(?:漢字の)?[「『]?(?P<subject>[\u3400-\u4dbf\u4e00-\u9fff々0-9])[」』]?"
        r"(?:という漢字)?(?:は|って)(?:小学校)?何年生で習う(?:漢字)?"
        r"(?:ですか|なの|の|か)?[。.!！?？]*$",
        text,
        flags=re.IGNORECASE,
    )
    if spoken_kanji_grade is not None:
        return spoken_kanji_grade.group("subject")

    # 正式名自体に「の分類」「の形式」等を含む場合は、観点表現として
    # 途中で切らない。閉じた完全一致語彙にある主題だけを先取りする。
    exact_definition = re.fullmatch(
        r"(?P<subject>.+?)(?:って|とは)(?:何|なに|どんな(?:もの|形式|意味|決まり|ルール|規則)?|"
        r"どういう(?:もの|意味|形式|決まり|ルール|規則)?)(?:ですか|なん|なの|やろ|だろうか|か)?[。.!！?？]*",
        text,
        flags=re.IGNORECASE,
    )
    if exact_definition is not None:
        candidate = _strip_subject(exact_definition.group("subject"))
        candidate_key = _compact(candidate)
        if candidate_key in (_JAPANESE_SUBJECT_KEYS | _POETRY_SUBJECT_KEYS):
            return candidate

    patterns = (
        # 「Xとは？」の省略形。疑問符を必須にして単なる文の断片を拾わない。
        r"^(?P<subject>.+?)とは[?？]+$",
        # 観点付きの「Xの形式って何」。一般の「Xって何」より先に切り分ける。
        rf"^(?P<subject>.+?)の(?:{_ASPECT_PATTERN})(?:って|とは)"
        r"(?:何|なに|どんな(?:もの)?|どういう(?:もの|意味)?)"
        r"(?:ですか|なん|なの|やろ|だろうか|か)?[。.!！?？]*$",
        # 「Xって何」「Xとはどんなもの」の定義問い。
        r"^(?P<subject>.+?)(?:って|とは)(?:何|なに|どんな(?:もの|形式|意味|決まり|ルール|規則)?|"
        r"どういう(?:もの|意味|形式|決まり|ルール|規則)?)(?:ですか|なん|なの|やろ|だろうか|か)?[。.!！?？]*$",
        # 「Xにはどんな決まりがある？」のように、主題を明示した存在問い。
        rf"^(?P<subject>.+?)(?:に|で|において)は?(?:どんな|どのような|どういう)"
        rf"(?:{_ASPECT_PATTERN})(?:が)?(?:ある|あります)(?:の|ん)?"
        r"(?:ですか|なの|か)?[。.!！?？]*$",
        # 「Xの形式は？」「Xの耐久値を教えて」。
        rf"^(?P<subject>.+?)の(?:{_ASPECT_PATTERN})(?:"
        r"(?:は)?[?？]+|"
        r"(?:は|って)?(?:何|なに|どんな(?:もの)?)(?:ですか|なの|か)?[。.!！?？]*|"
        r"(?:を|について)(?:教えて(?:ください)?|説明して(?:ください)?|知りたい)"
        r"[。.!！?？]*"
        r")$",
        # 「Xは何年生」「Xは1.21.11でどう変わった」。
        r"^(?P<subject>.+?)(?:は|って)(?:(?:Minecraft\s*)?\d+\.\d+(?:\.\d+)?(?:版)?で)?"
        r"(?:何年生(?:で習う(?:漢字)?)?|何年で習う(?:漢字)?|どう変わ(?:った|る|りました)|"
        r"どう変更された|どんな(?:もの|形式)?|何|なに)"
        r"(?:ですか|なん|なの|やろ|だろうか|か)?"
        r"[。.!！?？]*$",
        # 「Xを教えて」「Xについて知りたい」「Xとは何か教えて」。
        r"^(?P<subject>.+?)(?:とは(?:何|なに)(?:か)?|について|のことを|を)?"
        r"(?:教えて(?:ください)?|説明して(?:ください)?|知りたい)[。.!！?？]*$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            subject = _strip_subject(match.group("subject"))
            if subject:
                return subject
    return None


def _intent_for(text: str) -> KnowledgeIntent:
    compact = _compact(text)
    if any(marker in compact for marker in ("何年生", "何年で", "配当学年", "学年")):
        return "grade"
    if "読み" in text:
        return "reading"
    if any(marker in text for marker in ("どう変わ", "変更点", "どう変更")):
        return "change"
    if (
        "識別子" in text
        or "名前空間" in text
        or re.search(r"(?:の|\b)(?:ID|id)(?:は|を|\b)", text)
    ):
        return "identifier"
    if any(marker in text for marker in ("耐久値", "最大スタック", "特徴")):
        return "properties"
    if "分類" in text:
        return "classification"
    if any(marker in text for marker in ("ルール", "規則", "決まり", "形式", "作り方", "接続")):
        return "rules"
    return "definition"


def _has_explicit_minecraft_context(text: str) -> bool:
    """URLや一般的な「名前空間」をMinecraftとみなさない文脈判定。"""

    normalized = _normalize_text(text)
    compact = _compact(normalized)
    if any(
        _compact(marker) in compact
        for marker in _MINECRAFT_UNAMBIGUOUS_CONTEXT_MARKERS
    ):
        return True
    if any(marker in normalized for marker in ("マインクラフト", "マイクラ")):
        return True
    # URLのhost名やpathに含まれる minecraft は製品文脈として扱わない。
    return (
        re.search(
            r"(?<![a-z0-9_./:-])minecraft(?![a-z0-9_./:-])",
            normalized,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _is_minecraft_resource_location(subject: str, text: str) -> bool:
    match = _MINECRAFT_RESOURCE_LOCATION_RE.fullmatch(_normalize_text(subject).casefold())
    if match is None:
        return False
    return bool(
        match.group("namespace") == "minecraft"
        or _has_explicit_minecraft_context(text)
    )


def _domain_for(subject: str, text: str, intent: KnowledgeIntent) -> KnowledgeDomain | None:
    subject_probe = _compact(subject)
    # 日本語・詩歌の閉じた語彙を先に確定する。「どう変わった」や英大文字を
    # Minecraft の十分条件にすると、変体仮名・ソネット・ChatGPT などの
    # 明示質問までゲームDBへ奪ってしまう。
    if subject_probe in _POETRY_SUBJECT_KEYS:
        return "poetry"
    if subject_probe in _JAPANESE_SUBJECT_KEYS:
        return "japanese_language"
    if re.match(r"^(?:国語|日本語)(?:で|の)", text, flags=re.IGNORECASE):
        return "japanese_language"
    if re.match(r"^(?:世界の詩|世界の詩形|詩形)(?:で|の)", text, flags=re.IGNORECASE):
        return "poetry"
    if intent in {"grade", "reading"} and len(subject) == 1 and _is_cjk_character(subject):
        return "japanese_language"
    # STTは一文字の漢数字を算用数字へ転写することがある。ここで漢字へ
    # 勝手に戻すと別字を答える危険があるため、知識経路で固定の聞き返しを
    # 返せるところまでだけ分類する。
    if (
        intent in {"grade", "definition"}
        and re.fullmatch(r"[0-9]", subject)
        and (re.match(r"^漢字の", text) or ("数字の" in text and "漢字" in text))
    ):
        return "japanese_language"
    if subject.startswith(("～", "〜", "~")):
        return "japanese_language"
    if (
        _has_explicit_minecraft_context(text)
        or subject_probe in _MINECRAFT_EXACT_SUBJECT_KEYS
        or _is_minecraft_resource_location(subject, text)
    ):
        return "minecraft"
    return None


def _is_cjk_character(value: str) -> bool:
    if len(value) != 1:
        return False
    codepoint = ord(value)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def extract_explicit_knowledge_query(raw_text: str | None) -> ExplicitKnowledgeQuery | None:
    """代表的な明示質問だけを分類し、検索語を発話から切り出す。

    任意語を補完しない。代名詞だけの問い、生成依頼、スラッシュコマンド、
    単なる話題言及は対象外にする。
    """

    text = _normalize_text(raw_text or "")
    if not text or text.startswith("/") or len(text) > 240:
        return None
    if any(marker in text for marker in _GENERATION_REQUESTS):
        return None
    if not any(marker.casefold() in text.casefold() for marker in _QUESTION_CUES):
        return None
    subject = _extract_subject(text)
    if not subject or _compact(subject) in {_compact(value) for value in _GENERIC_SUBJECTS}:
        return None
    # 抽出語は必ず発話中の連続部分。表記をAIや辞書で補作しない。
    if _compact(subject) not in _compact(text):
        return None
    intent = _intent_for(text)
    domain = _domain_for(subject, text, intent)
    if domain is None:
        return None
    return ExplicitKnowledgeQuery(
        domain=domain,
        subject=subject,
        intent=intent,
        evidence=text,
    )


_ORGANIZATION_LABELS = {
    "org.mext": "文部科学省",
    "org.bunka": "文化庁",
    "org.nier": "国立教育政策研究所",
    "org.ndl": "国立国会図書館",
    "org.nijl": "国文学研究資料館",
    "org.ninjal": "国立国語研究所",
    "org.u-gakugei": "東京学芸大学",
    "org.aap": "Academy of American Poets",
    "org.poetry-foundation": "Poetry Foundation",
    "org.unesco": "UNESCO",
    "org.iranica": "Encyclopaedia Iranica Foundation",
    "org.columbia": "Columbia University",
    "org.smithsonian": "Smithsonian Institution",
    "org.korea-net": "Korea.net",
    "org.web-japan": "Web Japan",
    "org.penn-state": "Pennsylvania State University",
    "org.te-ara": "Te Ara - The Encyclopedia of New Zealand",
    "org.nara-pref": "奈良県",
    "org.nara-city": "奈良市",
    "org.sakurai-city": "桜井市",
    "org.tufs": "東京外国語大学",
    "org.kagoshima": "鹿児島大学",
    "org.kyoto": "京都大学",
    "org.tmd": "東京医科歯科大学",
    "org.iwate": "岩手大学",
    "org.uryukyu": "琉球大学",
    "org.unicode": "Unicode Consortium",
}

_ALLOWED_CLAIM_STATUSES = {
    "source_stated",
    "official_normalized_extract",
    "official_artifact",
    "editorial_synthesis",
    "editorial_guardrail",
    "editorial_paraphrase",
}
_INSTITUTION_SOURCE_KINDS = {
    "organization_authored_or_issued",
    "bibliographic_or_authority_record",
    "public_institution_content_on_official_platform",
    "institution_published_scholarly_work",
}
_MINECRAFT_SOURCE_KINDS = {"official_web_page", "official_artifact"}
_TRUSTED_INSTITUTION_LABELS = frozenset(_ORGANIZATION_LABELS.values())


def _required_text(value: object, *, max_chars: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_chars
        and "\x00" not in value
    )


def _trusted_source(source: object, *, domain: KnowledgeDomain) -> bool:
    if not isinstance(source, KnowledgeSource):
        return False
    if not all(
        (
            _required_text(source.source_id, max_chars=240),
            _required_text(source.title_ja, max_chars=300),
            _required_text(source.citation_label_ja, max_chars=120),
            isinstance(source.locator, str) and len(source.locator) <= 500,
            isinstance(source.url, str) and len(source.url) <= 2048,
            _required_text(source.source_kind, max_chars=100),
        )
    ):
        return False
    if domain == "minecraft":
        if (
            source.source_kind not in _MINECRAFT_SOURCE_KINDS
            or not source.source_id.startswith("minecraft:official_")
            or source.citation_label_ja
            not in {"Minecraft公式リリースノート", "Minecraft公式配布物"}
        ):
            return False
        if source.source_kind == "official_web_page":
            return source.url.startswith("https://www.minecraft.net/")
        return bool(source.locator)
    return (
        source.source_kind in _INSTITUTION_SOURCE_KINDS
        and source.source_id.startswith("src.")
        and source.citation_label_ja in _TRUSTED_INSTITUTION_LABELS
        and source.url.startswith("https://")
    )


def validate_knowledge_lookup_result(
    value: object,
    *,
    expected_query: ExplicitKnowledgeQuery,
    limit: int = 3,
) -> KnowledgeLookupResult:
    """provider境界を再帰的に検証し、現在質問へ結び付いた結果だけを返す。"""

    if not isinstance(value, KnowledgeLookupResult):
        raise TypeError("knowledge provider returned an invalid result")
    if value.query != expected_query:
        raise ValueError("knowledge provider returned a result for another query")
    if value.status not in {"found", "not_found", "unavailable"}:
        raise ValueError("knowledge provider returned an invalid status")
    if not isinstance(value.error_code, str) or len(value.error_code) > 160:
        raise ValueError("knowledge provider returned an invalid error code")
    bounded_limit = min(max(int(limit), 1), 3)
    if not isinstance(value.facts, tuple) or len(value.facts) > bounded_limit:
        raise TypeError("knowledge provider returned invalid facts")
    if value.status == "found" and not value.facts:
        raise ValueError("knowledge provider returned found without facts")
    if value.status != "found" and value.facts:
        raise ValueError("knowledge provider returned facts for a non-found status")

    for fact in value.facts:
        if not isinstance(fact, KnowledgeFact):
            raise TypeError("knowledge provider returned an invalid fact")
        if not all(
            (
                _required_text(fact.record_id, max_chars=300),
                _required_text(fact.dataset_id, max_chars=160),
                _required_text(fact.title_ja, max_chars=300),
                _required_text(fact.text_ja, max_chars=1000),
                fact.claim_status in _ALLOWED_CLAIM_STATUSES,
                isinstance(fact.sources, tuple),
                1 <= len(fact.sources) <= 3,
                isinstance(fact.dialogue_text_ja, str),
                len(fact.dialogue_text_ja) <= 1000,
            )
        ):
            raise ValueError("knowledge provider returned an invalid fact payload")
        if not all(
            _trusted_source(source, domain=expected_query.domain)
            for source in fact.sources
        ):
            raise ValueError("knowledge provider returned an untrusted source")

    return value


def _shorten(text: object, *, max_chars: int = 220) -> str:
    value = " ".join(str(text or "").split()).strip()
    if len(value) <= max_chars:
        return value
    sentences = [part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]?", value)]
    kept = ""
    for sentence in sentences:
        candidate = kept + sentence
        if len(candidate) > max_chars:
            break
        kept = candidate
    if kept:
        return kept.rstrip()
    return value[: max_chars - 1].rstrip("、， ") + "…"


def _source_from_reference(
    source_id: str,
    *,
    locator: str = "",
    reference_dir: Path | None = None,
) -> KnowledgeSource | None:
    from dogido_server.reference_catalog import get_reference

    source = get_reference(source_id, reference_dir=reference_dir)
    if not isinstance(source, dict):
        return None
    title = _shorten(source.get("title_ja") or source_id, max_chars=100)
    organization = str(source.get("organization_id") or "")
    label = _ORGANIZATION_LABELS.get(organization, title)
    return KnowledgeSource(
        source_id=source_id,
        title_ja=title,
        citation_label_ja=label,
        locator=_shorten(locator, max_chars=120),
        url=str(source.get("canonical_url") or ""),
        source_kind=str(source.get("publication_role") or source.get("kind") or ""),
    )


def _language_sources(
    record: dict[str, object],
    *,
    reference_dir: Path | None,
) -> tuple[KnowledgeSource, ...]:
    refs: list[tuple[str, str]] = []
    source_refs = record.get("source_refs")
    if isinstance(source_refs, list):
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            source_id = str(ref.get("source_id") or "")
            if source_id:
                refs.append((source_id, str(ref.get("locator") or "")))
    source_id = str(record.get("source_id") or "")
    if source_id:
        refs.append((source_id, str(record.get("source_locator") or "")))
    sources: list[KnowledgeSource] = []
    seen: set[str] = set()
    for ref_id, locator in refs:
        if ref_id in seen:
            continue
        source = _source_from_reference(
            ref_id,
            locator=locator,
            reference_dir=reference_dir,
        )
        if source is not None:
            sources.append(source)
            seen.add(ref_id)
    return tuple(sources[:3])


def _minecraft_sources(record: dict[str, object]) -> tuple[KnowledgeSource, ...]:
    raw_sources = record.get("sources")
    if not isinstance(raw_sources, list):
        return ()
    version = str(record.get("minecraft_version") or "").strip()
    sources: list[KnowledgeSource] = []
    seen: set[tuple[str, str, str]] = set()
    for index, source in enumerate(raw_sources):
        if not isinstance(source, dict):
            continue
        kind = str(source.get("source_kind") or "")
        url = str(source.get("url") or "")
        relative_path = str(source.get("relative_path") or "")
        locator = str(source.get("section") or source.get("json_pointer") or relative_path)
        if kind == "official_web_page":
            title = f"Minecraft Java Edition {version} 公式リリースノート".strip()
            label = "Minecraft公式リリースノート"
        elif kind == "official_artifact":
            title = f"Minecraft Java Edition {version} 公式配布物".strip()
            label = "Minecraft公式配布物"
        else:
            continue
        key = (kind, url, relative_path)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            KnowledgeSource(
                source_id=f"minecraft:{kind}:{index}",
                title_ja=title,
                citation_label_ja=label,
                locator=_shorten(locator, max_chars=120),
                url=url,
                source_kind=kind,
            )
        )
    return tuple(sources[:3])


def _explicit_record_match_rank(record: dict[str, object], subject: str) -> int | None:
    query = _compact(subject)
    if any(
        _compact(value) == query
        for value in (
            record.get("title_ja", ""),
            record.get("entry_id", ""),
        )
        if value
    ):
        return 0
    aliases = record.get("aliases")
    if isinstance(aliases, list) and any(
        _compact(value) == query for value in aliases if value
    ):
        return 1
    if _compact(record.get("reading", "")) == query:
        return 1
    search_terms = record.get("search_terms")
    if isinstance(search_terms, list) and any(
        _compact(value) == query for value in search_terms if value
    ):
        return 2
    if _compact(record.get("id", "")) == query:
        return 3
    return None


def _explicit_record_match(record: dict[str, object], subject: str) -> bool:
    return _explicit_record_match_rank(record, subject) is not None


def _fact(
    record: dict[str, object],
    text: str,
    *,
    claim_status: str,
    sources: tuple[KnowledgeSource, ...],
    dialogue_text_ja: str = "",
) -> KnowledgeFact | None:
    clean_text = _shorten(text)
    clean_dialogue_text = _shorten(dialogue_text_ja)
    if not clean_text or not sources:
        return None
    return KnowledgeFact(
        record_id=str(record.get("id") or ""),
        dataset_id=str(record.get("dataset_id") or ""),
        title_ja=str(record.get("title_ja") or record.get("reading") or ""),
        text_ja=clean_text,
        claim_status=claim_status,
        sources=sources,
        dialogue_text_ja=clean_dialogue_text,
    )


def _kanji_facts(
    query: ExplicitKnowledgeQuery,
    *,
    reference_dir: Path | None,
    limit: int,
) -> list[KnowledgeFact]:
    from dogido_server.language_knowledge import get_kanji_profile

    if len(query.subject) != 1 or not _is_cjk_character(query.subject):
        return []
    profile = get_kanji_profile(query.subject, reference_dir=reference_dir)
    if not isinstance(profile, dict):
        return []
    if query.intent == "grade":
        grade = profile.get("grade_level_kanji_allocation")
        if not isinstance(grade, dict):
            return []
        sources = _language_sources(grade, reference_dir=reference_dir)
        school_stage = str(grade.get("school_stage_ja") or "")
        school_grade = str(grade.get("school_grade_ja") or "")
        text = f"「{query.subject}」は{school_stage}{school_grade}の配当漢字です。"
        fact = _fact(
            grade,
            text,
            claim_status="official_normalized_extract",
            sources=sources,
        )
        return [fact] if fact is not None else []
    joyo = profile.get("joyo_kanji")
    if not isinstance(joyo, dict):
        return []
    readings = joyo.get("readings")
    if not isinstance(readings, list):
        return []
    by_type: dict[str, list[str]] = {}
    for item in readings:
        if not isinstance(item, dict):
            continue
        reading = str(item.get("reading") or "")
        reading_type = str(item.get("reading_type_ja") or "読み")
        if reading:
            by_type.setdefault(reading_type, []).append(reading)
    # 常用漢字表の一字あたりの音訓は最大でも短い範囲なので、黙って切らない。
    parts = [f"{kind}は「{'・'.join(values)}」" for kind, values in by_type.items()]
    if not parts:
        return []
    sources = _language_sources(joyo, reference_dir=reference_dir)
    text = f"常用漢字表で「{query.subject}」の" + "、".join(parts) + "です。"
    fact = _fact(
        joyo,
        text,
        claim_status="official_normalized_extract",
        sources=sources,
    )
    return [fact][:limit] if fact is not None else []


def _grammar_pattern_fact(
    record: dict[str, object],
    *,
    intent: KnowledgeIntent,
    sources: tuple[KnowledgeSource, ...],
) -> KnowledgeFact | None:
    title = str(record.get("title_ja") or "")
    reading = str(record.get("reading") or "")
    categories: list[str] = []
    connections: list[str] = []
    explanations: list[str] = []
    general_explanation = record.get("general_explanation")
    if isinstance(general_explanation, dict):
        value = str(general_explanation.get("text") or "").strip()
        if value:
            explanations.append(value)
    senses = record.get("senses")
    if isinstance(senses, list):
        for sense in senses:
            if not isinstance(sense, dict):
                continue
            raw_categories = sense.get("categories")
            if isinstance(raw_categories, list):
                for category in raw_categories:
                    value = str(category or "")
                    if value and value not in categories:
                        categories.append(value)
            raw_connections = sense.get("connections")
            if isinstance(raw_connections, list):
                for connection in raw_connections:
                    if not isinstance(connection, dict):
                        continue
                    connection_type = connection.get("connection_type")
                    if isinstance(connection_type, dict):
                        value = str(connection_type.get("text") or "")
                        if value and value not in connections:
                            connections.append(value)
            usage = sense.get("usage")
            if isinstance(usage, dict):
                value = str(usage.get("text") or "").strip()
                if value and value not in explanations:
                    explanations.append(value)
    parts = [f"文型「{title or reading}」"]
    if reading and reading != title:
        parts.append(f"読みは「{reading}」")
    if intent == "definition" and explanations:
        explanation = _shorten(explanations[0], max_chars=135)
        notices: list[str] = []
        if explanation != " ".join(explanations[0].split()):
            notices.append("一部省略")
        if len(explanations) > 1:
            notices.append(f"ほか{len(explanations) - 1}件")
        suffix = f"（{'、'.join(notices)}）" if notices else ""
        parts.append(f"意味・用法は「{explanation}」{suffix}")
    # 接続を尋ねた結果から接続文そのものが落ちないよう、先に収める。
    # 個々の長い説明も上限内で明示的に省略し、_fact() の文単位短縮で
    # 「接続」の一文が丸ごと消えることを防ぐ。
    if intent != "definition" and connections:
        raw_shown = connections[:2]
        shown = [_shorten(value, max_chars=48) for value in raw_shown]
        shortened = any(value != compact for value, compact in zip(raw_shown, shown, strict=True))
        omitted_count = len(connections) - len(shown)
        notices: list[str] = []
        if shortened:
            notices.append("一部省略")
        if omitted_count:
            notices.append(f"ほか{omitted_count}件")
        suffix = f"（{'、'.join(notices)}）" if notices else ""
        label = "代表的な接続" if suffix else "接続"
        parts.append(f"{label}は「{'／'.join(shown)}」{suffix}")
    if categories:
        shown = [_shorten(value, max_chars=24) for value in categories[:3]]
        omitted_count = len(categories) - len(shown)
        suffix = f"（ほか{omitted_count}件）" if omitted_count else ""
        label = "代表的な分類" if suffix else "分類"
        parts.append(f"{label}は「{'・'.join(shown)}」{suffix}")
    text = "。".join(parts) + "。"
    return _fact(
        record,
        text,
        claim_status="official_normalized_extract",
        sources=sources,
    )


def _reference_record_facts(
    record: dict[str, object],
    query: ExplicitKnowledgeQuery,
    *,
    reference_dir: Path | None,
    limit: int,
) -> list[KnowledgeFact]:
    sources = _language_sources(record, reference_dir=reference_dir)
    if not sources:
        return []
    if str(record.get("kind") or "") == "grammar_pattern":
        grammar_fact = _grammar_pattern_fact(
            record,
            intent=query.intent,
            sources=sources,
        )
        return [grammar_fact] if grammar_fact is not None else []
    facts: list[KnowledgeFact] = []
    if query.intent == "rules":
        rules = record.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                fact = _fact(
                    record,
                    str(rule.get("statement_ja") or ""),
                    claim_status=str(rule.get("claim_status") or "source_stated"),
                    sources=sources,
                )
                if fact is not None:
                    facts.append(fact)
                if len(facts) >= limit:
                    return facts
    dialogue_text = (
        str(record.get("dialogue_text_ja") or "")
        if query.intent == "definition"
        else ""
    )
    summary = str(record.get("definition_ja") or record.get("summary_ja") or "")
    if summary:
        fact = _fact(
            record,
            summary,
            claim_status=str(
                record.get("definition_status")
                or record.get("classification_status")
                or "source_stated"
            ),
            sources=sources,
            dialogue_text_ja=dialogue_text,
        )
        if fact is not None:
            facts.append(fact)
    return facts[:limit]


_ENTITY_KIND_LABELS = {
    "genre": "詩種",
    "form": "詩形",
    "tradition_family": "詩歌の伝統",
    "hybrid": "複合的な詩形",
}
_EXPRESSION_MODE_LABELS = {
    "lyric": "抒情",
    "narrative": "叙事",
    "dramatic": "劇的表現",
    "didactic": "教訓",
    "devotional": "宗教・信仰",
    "praise": "頌徳・称揚",
    "elegiac": "哀悼",
    "satirical": "風刺",
    "occasional": "機会詩",
}
_FORMAL_CONSTRAINT_LABELS = {
    "fixed": "定型",
    "regulated": "規則形式",
    "template_based": "詞牌・曲調による定型",
    "open": "自由形式",
    "form_independent": "詩形によらない分類",
    "hybrid": "複合形式",
}


def _world_classification_fact(
    record: dict[str, object],
    *,
    sources: tuple[KnowledgeSource, ...],
) -> KnowledgeFact | None:
    title = str(record.get("title_ja") or "この詩")
    labels: list[str] = []
    entity_kind = str(record.get("entity_kind") or "")
    if entity_kind in _ENTITY_KIND_LABELS:
        labels.append(f"分類単位は{_ENTITY_KIND_LABELS[entity_kind]}")
    classification = record.get("classification")
    if isinstance(classification, dict):
        modes = classification.get("expression_modes")
        if isinstance(modes, list):
            values = [_EXPRESSION_MODE_LABELS[value] for value in modes if value in _EXPRESSION_MODE_LABELS]
            if values:
                labels.append(f"表現様式は{'・'.join(values)}")
        constraints = classification.get("formal_constraints")
        if isinstance(constraints, list):
            values = [
                _FORMAL_CONSTRAINT_LABELS[value]
                for value in constraints
                if value in _FORMAL_CONSTRAINT_LABELS
            ]
            if values:
                labels.append(f"形式上の区分は{'・'.join(values)}")
    if not labels:
        return None
    return _fact(
        record,
        f"{title}の" + "、".join(labels) + "です。",
        claim_status=str(record.get("classification_status") or "editorial_synthesis"),
        sources=sources,
    )


def _lookup_japanese_or_poetry(
    query: ExplicitKnowledgeQuery,
    *,
    reference_dir: Path | None,
    limit: int,
) -> list[KnowledgeFact]:
    from dogido_server.language_knowledge import search_japanese_knowledge
    from dogido_server.reference_catalog import get_reference, search_references

    if _compact(query.subject) in _AMBIGUOUS_CURATED_SUBJECT_KEYS:
        return []

    table_record_id = _OFFICIAL_KANJI_TABLE_RECORDS.get(_compact(query.subject))
    if query.domain == "japanese_language" and table_record_id is not None:
        record = get_reference(table_record_id, reference_dir=reference_dir)
        if not isinstance(record, dict):
            return []
        record = {"dataset_id": "japanese_language_education", **record}
        sources = _language_sources(record, reference_dir=reference_dir)
        if table_record_id == "jp.bunka.joyo-kanji":
            text = (
                "「常用漢字表」は、平成22年内閣告示第2号の"
                "2,136字を収めた漢字表です。本データベースでは字種と音訓を検索できます。"
            )
        else:
            text = (
                "「学年別漢字配当表」は、小学校第1学年から第6学年までの"
                "配当漢字1,026字を示す別表です。本データベースでは文字ごとの配当学年を検索できます。"
            )
        fact = _fact(
            record,
            text,
            claim_status="official_normalized_extract",
            sources=sources,
        )
        return [fact] if fact is not None else []

    if (
        query.intent in {"grade", "reading"}
        and len(query.subject) == 1
        and _is_cjk_character(query.subject)
    ):
        return _kanji_facts(
            query,
            reference_dir=reference_dir,
            limit=limit,
        )

    records: list[dict[str, object]] = []
    if query.domain == "poetry":
        records.extend(
            search_japanese_knowledge(
                query.subject,
                dataset_ids=("japanese_poetry_forms",),
                limit=limit,
                reference_dir=reference_dir,
            )
        )
        for hit in search_references(
            query.subject,
            dataset_ids=("world_poetry",),
            limit=limit,
            reference_dir=reference_dir,
        ):
            record = get_reference(str(hit.get("id") or ""), reference_dir=reference_dir)
            if isinstance(record, dict):
                records.append({"dataset_id": "world_poetry", **record})
    else:
        subject = query.subject
        dataset_ids = (
            "japanese_grammar",
            "historical_kana_and_scripts",
            "makurakotoba",
            "japanese_poetry_forms",
        )
        if (
            subject.startswith(("～", "〜", "~"))
            or _compact(subject) in _GRAMMAR_PATTERN_EXACT_SUBJECT_KEYS
        ):
            dataset_ids += ("grammar_patterns",)
        records.extend(
            search_japanese_knowledge(
                subject,
                dataset_ids=dataset_ids,
                limit=limit,
                reference_dir=reference_dir,
            )
        )

    # 同じ語が常用漢字例などへ部分一致しても、明示語完全一致のレコードだけ使う。
    exact_records = [record for record in records if _explicit_record_match(record, query.subject)]
    exact_grammar_titles = {
        _compact(record.get("title_ja", ""))
        for record in exact_records
        if str(record.get("dataset_id") or "") == "grammar_patterns"
        and _compact(record.get("title_ja", "")) == _compact(query.subject)
    }
    if exact_grammar_titles:
        # 正式名で引いたときは同じ正式名の別義だけを束ねる。別文型の
        # search_terms に同じ表記が含まれていても、定義へ混ぜない。
        exact_records = [
            record
            for record in exact_records
            if str(record.get("dataset_id") or "") != "grammar_patterns"
            or _compact(record.get("title_ja", "")) in exact_grammar_titles
        ]
    exact_records.sort(
        key=lambda record: (
            _explicit_record_match_rank(record, query.subject) or 0,
            (
                0
                if query.intent == "classification"
                and str(record.get("dataset_id") or "") == "world_poetry"
                else 1
            ),
            str(record.get("id") or ""),
        )
    )
    facts: list[KnowledgeFact] = []
    seen_texts: set[str] = set()
    for record in exact_records:
        if query.intent == "classification" and record.get("dataset_id") == "world_poetry":
            sources = _language_sources(record, reference_dir=reference_dir)
            candidate = _world_classification_fact(record, sources=sources)
            candidates = [candidate] if candidate is not None else []
        else:
            candidates = _reference_record_facts(
                record,
                query,
                reference_dir=reference_dir,
                limit=limit - len(facts),
            )
        for candidate in candidates:
            key = _compact(candidate.text_ja)
            if key in seen_texts:
                continue
            seen_texts.add(key)
            facts.append(candidate)
            if len(facts) >= limit:
                return facts
        # 日本語文型バンクには同じ正式名で別の意味・用法を持つレコードが
        # ある（例: ～そうだ）。定義質問では最大3件の枠を使って別義も返し、
        # 最初の1件だけを全体定義のように見せない。
        if (
            candidates
            and query.intent in {"definition", "classification", "reading"}
            and str(record.get("dataset_id") or "") != "grammar_patterns"
        ):
            return facts
    return facts


def _minecraft_record_facts(
    record: dict[str, object],
    query: ExplicitKnowledgeQuery,
    *,
    limit: int,
) -> list[KnowledgeFact]:
    sources = _minecraft_sources(record)
    if not sources:
        return []
    facts: list[KnowledgeFact] = []
    record_type = str(record.get("record_type") or "")
    version = str(record.get("minecraft_version") or "")
    if record_type == "official_change":
        if query.intent in {"change", "definition", "identifier"}:
            mappings = record.get("identifier_mappings")
            if isinstance(mappings, list):
                for mapping in mappings:
                    if not isinstance(mapping, dict):
                        continue
                    old = str(mapping.get("from") or "")
                    new = str(mapping.get("to") or "")
                    if _compact(query.subject) not in {_compact(old), _compact(new)}:
                        continue
                    inversion = "。値の意味も反転します" if mapping.get("value_inverted") else ""
                    candidate = _fact(
                        record,
                        f"Minecraft Java Edition {version}では、旧名「{old}」は"
                        f"「{new}」に改名されました{inversion}。",
                        claim_status="source_stated",
                        sources=sources,
                    )
                    if candidate is not None:
                        facts.append(candidate)
                    break
        if not facts:
            summary = str(record.get("summary_ja") or "")
            coverage_note = (
                "これは収録した主要変更の抜粋です。"
                if record.get("coverage") == "representative_selection"
                else ""
            )
            candidate = _fact(
                record,
                f"Minecraft Java Edition {version}では、{summary}{coverage_note}",
                claim_status=str(record.get("summary_method") or "editorial_paraphrase"),
                sources=sources,
            )
            if candidate is not None:
                facts.append(candidate)
        return facts[:limit]

    title = str(record.get("title_ja") or query.subject)
    entry_id = str(record.get("entry_id") or "")
    if query.intent == "identifier" and entry_id:
        candidate = _fact(
            record,
            f"Minecraft Java Edition {version}では、{title}の公式IDは「{entry_id}」です。",
            claim_status="official_artifact",
            sources=sources,
        )
        return [candidate] if candidate is not None else []

    item_summary = record.get("item_summary")
    if query.intent == "properties" and isinstance(item_summary, dict):
        if "耐久値" in query.evidence or "耐久値" in query.subject:
            max_damage = item_summary.get("max_damage")
            if isinstance(max_damage, int):
                candidate = _fact(
                    record,
                    f"Minecraft Java Edition {version}では、{title}の最大耐久値は"
                    f"{max_damage}です。",
                    claim_status="official_artifact",
                    sources=sources,
                )
                if candidate is not None:
                    facts.append(candidate)
        # evidenceは検索語だけなので、元質問に依存せず主要な許可項目を短く返す。
        if not facts:
            max_damage = item_summary.get("max_damage")
            max_stack = item_summary.get("max_stack_size")
            values: list[str] = []
            if isinstance(max_damage, int):
                values.append(f"最大耐久値は{max_damage}")
            if isinstance(max_stack, int):
                values.append(f"最大スタック数は{max_stack}")
            if values:
                candidate = _fact(
                    record,
                    f"Minecraft Java Edition {version}では、{title}は、"
                    + "、".join(values)
                    + "です。",
                    claim_status="official_artifact",
                    sources=sources,
                )
                if candidate is not None:
                    facts.append(candidate)
    if not facts and entry_id:
        candidate = _fact(
            record,
            f"Minecraft Java Edition {version}では、{title}は「{entry_id}」として"
            "登録されています。",
            claim_status="official_artifact",
            sources=sources,
        )
        if candidate is not None:
            facts.append(candidate)
    return facts[:limit]


_RECIPE_TYPE_LABELS = {
    "minecraft:crafting_shaped": "配置を定めるクラフト",
    "minecraft:crafting_shapeless": "配置を問わないクラフト",
    "minecraft:smithing_transform": "鍛冶台での変換",
    "minecraft:smithing_trim": "鍛冶台での装飾",
    "minecraft:smelting": "かまどでの精錬",
    "minecraft:blasting": "溶鉱炉での精錬",
    "minecraft:smoking": "燻製器での調理",
    "minecraft:campfire_cooking": "焚き火での調理",
    "minecraft:stonecutting": "石切台での加工",
}


def _is_recipe_record(record: dict[str, object]) -> bool:
    if ".recipe." in str(record.get("id") or ""):
        return True
    sources = record.get("sources")
    return bool(
        isinstance(sources, list)
        and any(
            isinstance(source, dict)
            and "/recipe/" in str(source.get("relative_path") or "")
            for source in sources
        )
    )


def _minecraft_recipe_facts(
    query: ExplicitKnowledgeQuery,
    *,
    cache_root: Path | None,
) -> list[KnowledgeFact]:
    """公式配布物の縮約レシピだけを答え、保持していない配置は推測しない。"""

    from dogido_server.minecraft_knowledge import search_minecraft_knowledge

    target_id = query.subject if re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", query.subject) else ""
    if not target_id:
        registry_records = search_minecraft_knowledge(
            query.subject,
            dataset_ids=("registry_entries",),
            limit=5,
            cache_root=cache_root,
        )
        exact_registry = [
            record
            for record in registry_records
            if _explicit_record_match(record, query.subject)
        ]
        if exact_registry:
            target_id = str(exact_registry[0].get("entry_id") or "")
    if not target_id:
        return []

    records = search_minecraft_knowledge(
        target_id,
        dataset_ids=("datapack_entries",),
        record_types=("datapack_entry",),
        limit=20,
        cache_root=cache_root,
    )
    for record in records:
        if (
            not _is_recipe_record(record)
            or str(record.get("registry_id") or "") != "minecraft:recipe"
            or str(record.get("entry_id") or "") != target_id
        ):
            continue
        summary = record.get("document_summary")
        if not isinstance(summary, dict):
            continue
        references = summary.get("referenced_resource_ids")
        if not isinstance(references, list):
            continue
        declared_type = str(summary.get("declared_type") or "")
        ingredients: list[str] = []
        for value in references:
            resource_id = str(value or "")
            if not resource_id or resource_id in {target_id, declared_type}:
                continue
            if resource_id not in ingredients:
                ingredients.append(resource_id)
        type_label = _RECIPE_TYPE_LABELS.get(declared_type, declared_type or "方式不明")
        ingredient_text = "・".join(ingredients[:4]) or "縮約データでは確認できません"
        omitted = f"（ほか{len(ingredients) - 4}件）" if len(ingredients) > 4 else ""
        version = str(record.get("minecraft_version") or "")
        text = (
            f"Minecraft Java Edition {version}の公式レシピ定義では、{query.subject}は"
            f"{type_label}で、素材参照は「{ingredient_text}」{omitted}です。"
            "配置の詳細は縮約データにないため推測しません。"
        )
        fact = _fact(
            record,
            text,
            claim_status="official_artifact",
            sources=_minecraft_sources(record),
        )
        return [fact] if fact is not None else []
    return []


def _lookup_minecraft(
    query: ExplicitKnowledgeQuery,
    *,
    cache_root: Path | None,
    limit: int,
) -> list[KnowledgeFact]:
    from dogido_server.minecraft_knowledge import search_minecraft_knowledge

    if query.intent == "rules" and "作り方" in query.evidence:
        return _minecraft_recipe_facts(query, cache_root=cache_root)
    if query.intent == "change":
        dataset_ids = ("official_changes",)
    elif query.intent in {"identifier", "properties"}:
        dataset_ids = ("registry_entries",)
    elif query.intent == "definition":
        dataset_ids = ("official_changes", "registry_entries")
    else:
        dataset_ids = ("official_changes", "registry_entries", "datapack_entries", "tag_definitions")
    records = search_minecraft_knowledge(
        query.subject,
        dataset_ids=dataset_ids,
        limit=limit,
        cache_root=cache_root,
    )
    exact_records = [record for record in records if _explicit_record_match(record, query.subject)]
    facts: list[KnowledgeFact] = []
    for record in exact_records:
        facts.extend(
            _minecraft_record_facts(
                record,
                query,
                limit=limit - len(facts),
            )
        )
        if len(facts) >= limit:
            break
    return facts[:limit]


def _explicit_minecraft_versions(evidence: str) -> set[str]:
    """発話中でMinecraft本体の版として明示された番号だけを返す。"""

    patterns = (
        # Minecraft 1.21.11 / Java Edition 1.21.11 / マイクラ1.21.11
        r"(?:Minecraft(?:\s+Java(?:\s+Edition)?)?|Java(?:\s+Edition)?|"
        r"マインクラフト|マイクラ|Java版)\s*(?:版|バージョン)?\s*"
        r"(?P<version>\d+\.\d+(?:\.\d+)?)",
        # doMobSpawningは1.20.1でどう変わった、のような変更質問。
        r"(?P<version>1\.\d+(?:\.\d+)?)\s*(?:版)?(?:で|では)\s*"
        r"(?:どう|どのように|何が|なにが)?\s*(?:変わ|変更)",
        # 1.20.1版のdoMobSpawningは…（先頭の1.xはJava版指定）。
        r"^\s*(?P<version>1\.\d+(?:\.\d+)?)\s*版(?:で|の)",
    )
    versions: set[str] = set()
    for pattern in patterns:
        versions.update(
            match.group("version")
            for match in re.finditer(pattern, evidence, flags=re.IGNORECASE)
        )
    return versions


class LocalKnowledgeProvider:
    """生成済みローカルDBだけを読む、既定の読み取り専用provider。"""

    def __init__(
        self,
        *,
        language_reference_dir: Path | None = None,
        minecraft_cache_root: Path | None = None,
    ) -> None:
        self.language_reference_dir = language_reference_dir
        self.minecraft_cache_root = minecraft_cache_root

    def lookup(
        self,
        query: ExplicitKnowledgeQuery,
        *,
        limit: int = 3,
    ) -> KnowledgeLookupResult:
        bounded_limit = min(max(int(limit), 1), 3)
        if (
            query.domain == "japanese_language"
            and query.intent in {"grade", "definition"}
            and re.fullmatch(r"[0-9]", query.subject)
        ):
            return KnowledgeLookupResult(
                query=query,
                status="not_found",
                error_code="ambiguous_kanji_numeric_notation",
            )
        if query.domain == "minecraft":
            # Data Pack 94.1、Resource Pack 75.0、protocol 2.0.0 も正当な
            # 検索対象である。任意の小数をゲーム版とみなさず、Minecraft/Java
            # に直結する表記か「その版でどう変わった」の位置だけを版指定とする。
            explicit_versions = _explicit_minecraft_versions(query.evidence)
            if explicit_versions and explicit_versions != {"1.21.11"}:
                return KnowledgeLookupResult(
                    query=query,
                    status="not_found",
                    error_code="unsupported_minecraft_version",
                )
        try:
            if query.domain == "minecraft":
                facts = _lookup_minecraft(
                    query,
                    cache_root=self.minecraft_cache_root,
                    limit=bounded_limit,
                )
            else:
                facts = _lookup_japanese_or_poetry(
                    query,
                    reference_dir=self.language_reference_dir,
                    limit=bounded_limit,
                )
        except Exception as exc:  # noqa: BLE001 - DB障害を会話・戦況へ伝播させない
            LOGGER.warning(
                "knowledge_lookup_failed domain=%s intent=%s subject=%s detail=%s",
                query.domain,
                query.intent,
                query.subject[:80],
                exc,
            )
            return KnowledgeLookupResult(
                query=query,
                status="unavailable",
                error_code=type(exc).__name__,
            )
        if not facts:
            return KnowledgeLookupResult(query=query, status="not_found")
        return KnowledgeLookupResult(
            query=query,
            status="found",
            facts=tuple(facts[:bounded_limit]),
        )


def attest_knowledge_lookup_result(
    candidate: object,
    *,
    expected_query: ExplicitKnowledgeQuery,
    authoritative_provider: LocalKnowledgeProvider,
    limit: int = 3,
) -> KnowledgeLookupResult:
    """差替えproviderの事実一式を、正規ローカルDBの再構成結果と照合する。"""

    checked = validate_knowledge_lookup_result(
        candidate,
        expected_query=expected_query,
        limit=limit,
    )
    if checked.status != "found":
        return checked
    authoritative = validate_knowledge_lookup_result(
        authoritative_provider.lookup(expected_query, limit=limit),
        expected_query=expected_query,
        limit=limit,
    )
    if authoritative.status != "found" or checked.facts != authoritative.facts:
        raise ValueError("knowledge facts do not match the authoritative local database")
    return authoritative


def _render_knowledge_reply_content(
    result: KnowledgeLookupResult,
) -> tuple[str, tuple[KnowledgeSource, ...]]:
    """発話本文と別表示用の参考資料をコード固定で組み立てる。"""

    try:
        result = validate_knowledge_lookup_result(
            result,
            expected_query=result.query,
            limit=3,
        )
    except Exception:  # noqa: BLE001 - 公開renderer単体でもfail-closed
        return "手元の公式資料を今は読めへんわ。推測では答えんとくで。", ()
    if result.status == "unavailable":
        return "手元の公式資料を今は読めへんわ。推測では答えんとくで。", ()
    if result.error_code == "unsupported_minecraft_version":
        return (
            "このMinecraft技術DBはJava Edition 1.21.11専用やで。別の版は推測せんとくで。",
            (),
        )
    if result.error_code == "ambiguous_kanji_numeric_notation":
        return (
            f"「{result.query.subject}」だけやと、どの漢字か決められへんわ。"
            "「一二三の一」みたいに言うてみてな。",
            (),
        )
    if result.status != "found" or not result.facts:
        return "手元の公式資料では確認できへんかったわ。推測はせんとくで。", ()

    status_prefixes = {
        "editorial_synthesis": "資料を基に整理すると、",
        "editorial_guardrail": "資料を基にした注意点として、",
        "editorial_paraphrase": "公式リリースノートを要約すると、",
    }

    max_reply_chars = 420
    rendered_parts: list[str] = []
    rendered_facts: list[KnowledgeFact] = []
    omitted = False
    for fact in result.facts[:3]:
        text = _shorten(
            fact.dialogue_text_ja or fact.text_ja,
            max_chars=220,
        ).strip()
        if text and text[-1] not in "。！？!?":
            text += "。"
        prefix = (
            ""
            if fact.dialogue_text_ja
            else status_prefixes.get(fact.claim_status, "")
        )
        part = prefix + text
        candidate = "".join([*rendered_parts, part])
        if len(candidate) > max_reply_chars and rendered_parts:
            omitted = True
            break
        if len(candidate) > max_reply_chars:
            part = _shorten(part, max_chars=max_reply_chars)
        rendered_parts.append(part)
        rendered_facts.append(fact)

    reply = "".join(rendered_parts)
    if omitted and len(reply) + len("ほかの項目は省略したで。") <= max_reply_chars:
        reply += "ほかの項目は省略したで。"

    references: list[KnowledgeSource] = []
    for fact in rendered_facts:
        for source in fact.sources:
            if source not in references:
                references.append(source)
    return reply[:max_reply_chars], tuple(references)


def _render_knowledge_reply_text(result: KnowledgeLookupResult) -> str:
    """後方互換の本文renderer。参考資料名は発話本文へ混ぜない。"""

    return _render_knowledge_reply_content(result)[0]


def split_knowledge_speech(text: str) -> tuple[str, ...]:
    """確定済みの回答を文末で分ける。文字の追加・削除・言い換えはしない。"""

    if not text:
        return ()
    segments: list[str] = []
    start = 0
    for index, character in enumerate(text):
        if character not in "。！？!?":
            continue
        segment = text[start : index + 1]
        if segment:
            segments.append(segment)
        start = index + 1
    if start < len(text):
        segments.append(text[start:])
    if not segments:
        return (text,)
    # 音声計画が監査本文とずれたら、分割せず全文へfail-closedする。
    if "".join(segments) != text:
        return (text,)
    return tuple(segments)


def render_knowledge_reply_plan(result: KnowledgeLookupResult) -> RenderedKnowledgeReply:
    """回答本文を一度だけ確定し、同じ本文から音声配送計画を作る。"""

    text, references = _render_knowledge_reply_content(result)
    return RenderedKnowledgeReply(
        text=text,
        speech_segments=split_knowledge_speech(text),
        references=references,
    )


def render_knowledge_reply(result: KnowledgeLookupResult) -> str:
    """後方互換の文字列renderer。音声ではrender planと同じ分割規則を使う。"""

    return render_knowledge_reply_plan(result).text


__all__ = [
    "ExplicitKnowledgeQuery",
    "KnowledgeFact",
    "KnowledgeLookupResult",
    "KnowledgeProvider",
    "KnowledgeSource",
    "LocalKnowledgeProvider",
    "RenderedKnowledgeReply",
    "attest_knowledge_lookup_result",
    "extract_explicit_knowledge_query",
    "render_knowledge_reply",
    "render_knowledge_reply_plan",
    "split_knowledge_speech",
    "validate_knowledge_lookup_result",
]
