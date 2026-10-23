"""Python補助のCA・installer版を検査し、明示操作だけで専用venvを更新する。

検査はmetadataの読取りだけ。bootstrapは公式hash固定pipを先に導入し、
そのpipでcertifiを更新する。既存サービスの再起動やOS証明書の変更はしない。
"""
from __future__ import annotations

import argparse
from importlib import metadata
import re
import subprocess
import sys

MIN_CERTIFI = "2026.10.19"
MIN_PIP = "26.2.2"
# Python公式告知の直URLとSHA-256。最初の取得ではindexや依存解決を使わない。
PIP_BOOTSTRAP_URL = (
    "https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/"
    "pip-26.2.2-py3-none-any.whl"
    "#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
)


def stable_version(value: str) -> tuple[int, int, int]:
    """正式版の2〜3整数だけを受け取り、省略patchは0。開発版は受け入れない。"""
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", value):
        raise ValueError(f"正式版ではないversion: {value!r}")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (3 - len(parts))


def require_package(name: str, minimum: str, *, optional: bool = False) -> None:
    """現在のPython環境を検査。任意のpipも存在する場合は同じ下限を守る。"""
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        if optional:
            return
        installed = "未導入"
    try:
        accepted = stable_version(installed) >= stable_version(minimum)
    except ValueError:
        accepted = False
    if not accepted:
        raise RuntimeError(
            f"{name}>={minimum} が必要です（現在: {installed}）。"
            "専用venvで python -m dogido_server.tls_security --bootstrap を実行してください。"
        )


def check_python_tls_packages() -> None:
    """保守CLIでCAとinstallerを検査。pipなしのuv環境はそのまま使える。"""
    require_package("certifi", MIN_CERTIFI)
    require_package("pip", MIN_PIP, optional=True)


def bootstrap_pip_command(python: str, *, target: str | None = None) -> list[str]:
    """公式hash付きwheelだけを取得するargv。target指定時も他の依存を取得しない。"""
    command = [python, "-m", "pip", "--isolated", "--disable-pip-version-check",
               "--no-cache-dir", "install", "--no-index", "--no-deps", "--upgrade"]
    if target is not None:
        command.extend(["--target", target])
    return [*command, PIP_BOOTSTRAP_URL]


def bootstrap() -> None:
    """専用venvだけを更新。pip不在ならofflineのensurepip後にhash固定版へ交換する。"""
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("専用venvを作成・有効化してから実行してください。")
    try:
        metadata.version("pip")
    except metadata.PackageNotFoundError:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=True)
    try:
        require_package("pip", MIN_PIP)
    except RuntimeError:
        subprocess.run(bootstrap_pip_command(sys.executable), check=True)
    # 新しい正式版が既にあれば下げない。更新後も最低版を再照合する。
    require_package("pip", MIN_PIP)
    subprocess.run([sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check",
                    "install", "--upgrade", f"certifi>={MIN_CERTIFI}"], check=True)
    check_python_tls_packages()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    if args.bootstrap:
        bootstrap()
    else:
        check_python_tls_packages()
    print(f"Python TLS部品確認済み: certifi>={MIN_CERTIFI}, pip>={MIN_PIP}（導入時）")


if __name__ == "__main__":
    main()
