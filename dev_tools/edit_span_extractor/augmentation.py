"""依頼文と語句を挿入前に変形し、認識崩れ後の文字位置で教材を作る。

見込み: ひらがな依頼・長音・脱落を学ぶと、漢字の依頼動詞への依存が減る。
合格: 生の変形後文字列にspanが一致。音声認識そのものの精度は評価しない。
"""
from __future__ import annotations
import re
from string import Formatter

# 句の読みを推測せず、仮名だけで書かれた材料を全ひらがな教材に使う。
def kana(text):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)


def kana_only(text):
    return bool(re.fullmatch(r'[ぁ-ゖー、。・！？?!「」『』 　]+', kana(text)))


READINGS = {
    '変更': 'へんこう', '変え': 'かえ', '直し': 'なおし', '直せ': 'なおせ',
    '直したい': 'なおしたい', '差し替え': 'さしかえ', '置き換え': 'おきかえ',
    '使': 'つか', '代わり': 'かわり', '部分': 'ぶぶん', '考え': 'かんがえ',
    '言い回し': 'いいまわし', '言葉': 'ことば', '丸ごと': 'まるごと',
    '次': 'つぎ', '願い': 'ねがい', '少し': 'すこし', '春': 'はる',
    '今日': 'きょう', '句': 'く', '読み方': 'よみかた', '教え': 'おしえ',
    '案': 'あん', '戻し': 'もどし', '景色': 'けしき', '浮かぶ': 'うかぶ',
    '違う': 'ちがう', '感じ': 'かんじ', '表現': 'ひょうげん', '好き': 'すき',
    '別': 'べつ', '意味': 'いみ', '聞かせ': 'きかせ', '明るい': 'あかるい',
}
KANA_LINES = [['かみのく', 'かみご'], ['なかのく', 'なかしち'], ['しものく', 'しもご']]
KANA_PREFIXES = ['', '', 'じゃあ', 'そうだな、', 'えっと、', 'うーんと', 'まず', 'つぎに', 'それなら']
KANA_SUFFIXES = ['', '', '。', '！', '。そのほうがけしきにあうとおもう', '、ほかはそのままで']


def kana_template(template):
    for source, target in sorted(READINGS.items(), key=lambda x: -len(x[0])):
        template = template.replace(source, target)
    literals = ''.join(literal for literal, _, _, _ in Formatter().parse(template))
    if re.search(r'[一-龯]', literals):
        raise ValueError(f'Unspecified template reading: {template}')
    return template


def noise(text, rng, *, slot=False, line=False):
    """原文へ後付け修復をせず、ラベル付け前の部品に一般的な揺れを与える。"""
    if not text:
        return text
    result = text
    # 長音表記は既知3件の語を辞書登録せず、一般の仮名列へ適用。
    for source, target in [('しょう', 'しょー'), ('こう', 'こー'), ('よう', 'よー'),
                           ('そう', 'そー'), ('とう', 'とー'), ('ろう', 'ろー')]:
        if rng.random() < 0.65:
            result = result.replace(source, target)
    if line and len(result) >= 3 and rng.random() < 0.4:
        result = result[rng.choice([1, 2]):]
    if not slot and rng.random() < 0.2:
        result = result.replace('を', rng.choice(['お', 'オ']))
    if slot and len(result) >= 4 and rng.random() < 0.18:
        at = rng.randrange(len(result))
        if rng.random() < 0.5:
            result = result[:at] + result[at] + result[at:]
        else:
            result = result[:at] + result[at + 1:]
    if not slot and len(result) > 3 and rng.random() < 0.12:
        at = rng.randrange(len(result))
        result = result[:at] + result[at + 1:]
    return result or text


def noise_template(template, rng):
    """placeholder名を壊さず、literalだけを変形する。"""
    out = ''
    for literal, field, _, _ in Formatter().parse(template):
        out += noise(literal, rng)
        if field is not None:
            out += '{' + field + '}'
    return out


def prepare(template, values, prefix, suffix, variant, rng):
    if variant == 'clean':
        return template, values, prefix, suffix
    template = kana_template(template)
    values = {key: kana(value) for key, value in values.items()}
    if variant == 'kana':
        return template, values, prefix, suffix
    if variant != 'asr_noise':
        raise ValueError(variant)
    template = noise_template(template, rng)
    values = {key: noise(value, rng, slot=key in {'old', 'new'}, line=key == 'line') for key, value in values.items()}
    return template, values, noise(prefix, rng), noise(suffix, rng)
