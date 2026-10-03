#!/bin/zsh
set -u
cd "${0:A:h}"
python3 -u gateway.py
result=$?
if (( result != 0 )); then
  read -r "reply?エラーを確認したら Enter で閉じます: "
fi
exit "$result"
