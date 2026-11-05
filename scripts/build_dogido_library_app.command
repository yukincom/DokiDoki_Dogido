#!/bin/zsh
# 承認済みの透過PNGを各倍率のMacアイコンへ変換し、本棚アプリへ同梱する。
set -eu
SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
SOURCE_DIR="${PROJECT_ROOT}/tools/dogido-library/macos"
APP_DIR="/Applications/ドギドの本棚.app"
LAUNCH_ROOT="${PROJECT_ROOT}"
ROLE="parent"
DEVICE=""
while (( $# )); do
  case "$1" in
    --app-dir) APP_DIR="$2"; shift 2 ;;
    --project-root) LAUNCH_ROOT="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    *) print -u2 -- "不明な引数: $1"; exit 2 ;;
  esac
done
[[ "$ROLE" == parent || "$ROLE" == child ]] || { print -u2 -- "roleはparentかchildです"; exit 2; }
ICON_SOURCE="${PROJECT_ROOT}/tools/dogido-library/assets/bookshelf-app-icon.png"
ICONSET_DIR="${PROJECT_ROOT}/.dogido_tmp/bookshelf-app.iconset"
mkdir -p "${APP_DIR}/Contents/MacOS" "${APP_DIR}/Contents/Resources" "${PROJECT_ROOT}/.dogido_tmp/swift-module-cache" "${ICONSET_DIR}"
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "${size}" "${size}" "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_${size}x${size}.png" >/dev/null
  retina_size=$((size * 2))
  /usr/bin/sips -z "${retina_size}" "${retina_size}" "${ICON_SOURCE}" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "${ICONSET_DIR}" -o "${APP_DIR}/Contents/Resources/BookshelfApp.icns"
xcrun swiftc -target arm64-apple-macos14.2 -module-cache-path "${PROJECT_ROOT}/.dogido_tmp/swift-module-cache" -framework AppKit -framework WebKit "${SOURCE_DIR}/main.swift" -o "${APP_DIR}/Contents/MacOS/DogidoBookshelf"
cp "${SOURCE_DIR}/Info.plist" "${APP_DIR}/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :DogidoProjectRoot string ${LAUNCH_ROOT}" "${APP_DIR}/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :DogidoBookshelfRole string ${ROLE}" "${APP_DIR}/Contents/Info.plist"
if [[ -n "$DEVICE" ]]; then
  /usr/libexec/PlistBuddy -c "Add :DogidoChildDevice string ${DEVICE}" "${APP_DIR}/Contents/Info.plist"
fi
/usr/bin/codesign --force --sign - "${APP_DIR}"
print -- "作成しました: ${APP_DIR}"
