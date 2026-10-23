#!/bin/sh
# 指定uvの正式版を通信・書込み前に検査する。成功時は確認した版だけを出力する。
# Python導入前にも動かすため、POSIX shとawkだけを使う。
set -eu
if [ "$#" -ne 1 ]; then
    echo "usage: check_uv.sh /path/to/uv" >&2
    exit 2
fi
task_uv_version=$("$1" --version) || {
    echo "uvの版を確認できません。uv>=0.13.4を用意してください。" >&2
    exit 1
}
printf '%s\n' "$task_uv_version" | awk '
    NR == 1 && $1 == "uv" && $2 ~ /^[0-9]+[.][0-9]+[.][0-9]+$/ {
        split($2, v, ".")
        if (v[1] > 0 || (v[1] == 0 && (v[2] > 13 || (v[2] == 13 && v[3] >= 4)))) {
            accepted = 1
            version = $2
        }
    }
    END { if (NR == 1 && accepted) print version; else exit 1 }
' || {
    echo "古い、または不明なuvです: ${task_uv_version}。uv>=0.13.4が必要です。" >&2
    exit 1
}
