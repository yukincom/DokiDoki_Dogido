#!/usr/bin/env python3
"""Python正本の入力正規化と国語入口候補を記録。モデル呼出しなし。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dogido_server.player_input.normalize import normalize_player_text
from dogido_server.player_input.asr_fixes import asr_replacements, apply_asr_fixes
from dogido_server.service import DogidoService


def prepared(text):
    _, applied = apply_asr_fixes(" ".join(text.split()))
    return {"raw_text":text,"normalized_text":normalize_player_text(text),"applied_fixes":applied,
        "explicit_language":DogidoService._looks_like_main_language_request(text)}


def main():
    cases = ["", "こんにちは", "うん", "ドギド", "漢字が書いてあった", "いい川柳だね", "川柳の話をしよう",
        "枕詞ってどういう意味？", "3は何年生で習うの？", "音数\n を教えて", "ドギドってなんて読む？",
        "持ち物を教えて", "ゾンビどこ？", "静かにして", "川柳保存: 今日は\n何でもない\n一日だ",
        "直し: 桜の葉\n黒い斧へと\n朝の色", "/say 漢字を教えて", "\n/help", "県に持ち替えて",
        "環圧板", "ｶﾝｱﾂﾊﾞﾝ", "ＡＢＣ", "\u200b言葉の意味", "🐕"]
    for wrong,right in asr_replacements():
        cases.extend([wrong, wrong+"って何？", wrong+wrong, right, "「"+wrong+"」と話した"])
    cases.append(" ".join(w for w,r in asr_replacements()))
    for char in map(chr,range(0x110000)):
        if char.isspace():
            cases.extend([char+"和行為為団"+char+"教えて"+char, "言葉"+char+"の意味", char*3])
    for topic in ["漢字","短歌","俳句","川柳","音数","文法","熟語","ことわざ","枕詞","語句","ブロック"]:
        for cue in ["?","？","教えて","知りたい","調べて","勉強","習う","習った","話をしよう","話しよう","話そう","がある"]:
            cases.append(topic+"のこと"+cue)
    for phrase in ["ってどういう意味","って何て読む","ってなんて読む","の読み方","言葉の意味","ことばの意味","何年生で習","何年生の漢字"]:
        cases.extend([phrase,"犬"+phrase+"？","そんな"+phrase+"とは言ってない"])
    cases=list(dict.fromkeys(cases))
    (ROOT/"dogido-rust/fixtures/player-text.json").write_text("[\n"+",\n".join(
        "  "+json.dumps(prepared(text),ensure_ascii=False) for text in cases)+"\n]\n")
    print(f"Captured {len(cases)} normalization and language-entry cases")
    print("Replacement order:", asr_replacements())


if __name__ == "__main__":main()
