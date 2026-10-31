"""同じ送信器で、このフォルダの2要求だけをプレビュー・実行する。"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "catalog-candidates-v1"))
import run_experiment as probe

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preview", "run"])
    args = parser.parse_args()
    probe.HERE = ROOT
    probe.main(args.mode == "run")
