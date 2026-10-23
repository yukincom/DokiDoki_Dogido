"""川柳HTTP検証用の固定分類ケース。実BERTの精度検証とは分ける。"""
from contextlib import contextmanager

from check_haiku_runtime import fixture as haiku_fixture
from request_classifier_fixture import mock_request_classifier


# BERTへ変更済み。旧判定へ戻さない。
# 原文ごとにケースが指定する期待ラベルであり、キーワード分類器ではない。
# 未登録文はnot_target。各ケースはbert_labelsで必要な分類を明示できる。
BERT_LABELS = {
    **dict.fromkeys([
        "上五のさくらのはを別の表現に直して", "上五を直して",
    ], "senryu_line_upper"),
    **dict.fromkeys([
        "下五の意味を教えて", "下の行を説明して",
    ], "senryu_line_lower"),
    **dict.fromkeys([
        "上五を『さくらいろ』にして", "上五を『さくら』にして",
        "上五を『さくらいろ』にしないで", "上五を『あさのいろ』にして",
        "『上五をさくらいろにして』って言っただけ", "上五をさくらいろにしたらどうなる？",
        "上五を『桜色』にして", "桜の葉を桜色に変えて", "さくらいろをさくらさくに変えて",
        "冒頭の五音を『さくらいろ』にして", "それを『さくら』にして",
        "上五の言葉はさくらいろがいい。そこへ変更をお願い", "上五ならあさひかるがいい。変更をお願い",
    ], "senryu_change_upper"),
    **dict.fromkeys([
        "やっぱり『あおい』にして",
        "中七のくろいをしろいに変えて", "中七のしろいをくろいに変えて",
    ], "senryu_change_middle"),
    **dict.fromkeys([
        "下五を『あさひかる』にして", "それを『あさひかる』にして",
    ], "senryu_change_lower"),
    **dict.fromkeys([
        "終了", "終了ー", "お開きー", "終了でお願いします", "終了でいいよ",
        "今日は相談はここでおしまいにしよう", "この句の相談はここで終わりにしよう。",
        "相談はここでおしまい", "そろそろ相談はおしまいにしよう",
        "<では終了で！>", "今回はここまでにしよう", "きょうはここらで区切っとこか",
        "この子はもう完成でいいんじゃないかな", "そうおしまいにしてください",
        "その案を却下して、ここでおしまいにしよう", "その案を採用して、ここでおしまいにしよう",
        "余韻が心に残る。今日はここまでにしよう",
    ], "senryu_end"),
}


@contextmanager
def fixture(*, bert_labels=None, extra_args=(), **settings):
    labels = {**BERT_LABELS, **(bert_labels or {})}
    with mock_request_classifier(labels, extra_args=extra_args) as (args, _), haiku_fixture(extra_args=args, **settings) as state:
        yield state
