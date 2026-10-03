"""Cargoが記録したrelease依存の更新確認。起動・ビルド・設定変更はしない。"""
from pathlib import Path
import re


def check_binary_freshness(root: Path, binary: Path) -> None:
    if not (root / "dogido-rust/src/main.rs").is_file():
        return  # 作成元で確認済みの家庭用セットはRustソースを同梱しない。
    dependency_file = binary.with_suffix(".d")
    if not dependency_file.is_file():
        raise RuntimeError("Rust本体のビルド依存情報がありません。releaseビルドを実行してください。")
    # Make形式の最初のrule。空白入りパスのbackslash escapeを維持して分割する。
    rules = dependency_file.read_text(encoding="utf-8").replace("\\\n", "").splitlines()
    dependencies = rules[0].partition(": ")[2] if rules else ""
    if not dependencies:
        raise RuntimeError("Rust本体のビルド依存情報が不正です。releaseビルドを実行してください。")
    inputs = []
    for word in re.findall(r"(?:\\.|[^\s])+", dependencies):
        path = Path(re.sub(r"\\(.)", r"\1", word))
        inputs.append(path if path.is_absolute() else root / "dogido-rust" / path)
    built_at = binary.stat().st_mtime_ns
    changed = [path for path in inputs if not path.is_file() or path.stat().st_mtime_ns > built_at]
    if changed:
        names = [str(path.relative_to(root)) if path.is_relative_to(root) else path.name for path in changed]
        raise RuntimeError("Rust本体より新しい、または削除されたソース・埋込資料があります。releaseビルドを更新してください: "
                           + ", ".join(sorted(names)[:3]))
