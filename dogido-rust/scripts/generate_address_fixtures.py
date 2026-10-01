#!/usr/bin/env python3
"""既存Pythonの宛先・話題変更・確認・引用短縮をそのまま比較fixtureへ出す。"""
import json
from pathlib import Path
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dogido_server.dialogue.main_runtime import MainLanguageRuntime
from dogido_server.language_dialogue.participation import direct_call_body, has_topic_shift_cue, corrects_false_suppression


def main():
    texts = [
        "", "ドギド", "ドギド！", "ﾄﾞｷﾞﾄﾞ：家を作りたい", "ねえ、ドギド", "ねぇドギドいる？",
        "あれ。ドギドおる", "なあドギド聞いてる", "おーい！ドギドきいてますか？", "ドギドさん", "ドギドの話", "ドギドいるかな",
        "あれねえドギド", "ドギド、\n話そう", "ドギド、話そう\n次", "『ドギド』って誰？", "ドギドに言った", "ドギドに言うた", "あなたに言った",
        "お前に言ったんだ", "君に言ったよ", "聞いてる？", "聞いてますか？", "聞いてるわけではない",
        "ところで家を建てたい", "あの、ところで", "えっと。さて", "そういえば", "それはそうと", "話変わるけど", "話は変わるけど", "話を変わるけど",
        "別の話やけど", "別の話だけど", "別の話", "さっきところでと言った", "えっとあのところで",
        "うん", "はい", "ええで", "いいよ", "聞いて", "お願い", "そうして", "いや", "いいえ", "違う", "ちがう", "やめとく", "もういい",
        "\u3000は　い！", "はいと言ったら？", "『うん』", "まだわからない", "もういいかどうか", "は\u001eい",
        "「 家を\n\t建てたい！ 」", "桜"*48, "桜"*49, "あ🦮"*30,
    ]
    rows = [{"text":t, "addressed":direct_call_body(t) is not None or corrects_false_suppression(t),
             "explicit_transition":direct_call_body(t) is not None or has_topic_shift_cue(t),
             "confirmation":MainLanguageRuntime._confirmation_decision(unicodedata.normalize("NFKC",t).strip()),
             "summary":MainLanguageRuntime._topic_summary(t)} for t in texts]
    path = ROOT / "dogido-rust/fixtures/address.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2)+"\n")
    print(f"{len(rows)} Python address cases")


if __name__ == "__main__": main()
