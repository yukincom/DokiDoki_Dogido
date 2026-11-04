"""学習済みの実験用抽出器で、一発話のfrom/toを表示する。句集へ書き込まない。"""
import argparse
import json
from pathlib import Path
import torch
from transformers import BertForTokenClassification
from span_model import CharacterCodec, predict_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--current-verse", default="")
    parser.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    args = parser.parse_args()
    torch.set_num_threads(4)
    codec = CharacterCodec(args.model / "vocab.txt")
    model = BertForTokenClassification.from_pretrained(args.model, local_files_only=True,
        attn_implementation="sdpa").to(args.device)
    result = predict_rows(model, codec, [{"text": args.text, "current_verse": args.current_verse}], args.device)[0]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
