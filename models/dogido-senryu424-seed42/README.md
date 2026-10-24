# 川柳専用の採用分類モデル

424件版のseed42、8分類（行指定3・具体変更3・終了・対象外）。受領した重み・tokenizer・設定のバイト列を保っています。採用記録とSHA-256は `adopted-model.json` にあります。

基盤は [sbintuitions/modernbert-ja-30m](https://huggingface.co/sbintuitions/modernbert-ja-30m)（MIT、同梱 `LICENSE`）。会話文の生成には使いません。重みはGit LFSで配布します。

起動は [本体README](../../README.md#1-本体の準備と起動)、配送先と入力取消の仕様は [モード分離](../../docs/2026-10-24-bert-mode-routing.md) を参照してください。通常16分類は [seed48-mob](../dogido-normal16-seed48-mob/README.md) を同梱しています。
