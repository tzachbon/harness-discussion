#!/bin/bash
# Builds dist/HarnessDiscussion.dmg: an arm64 "Harness Discussion.app" for macOS 13+, signed ad hoc or with the local identity.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --locked
version=$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
if [[ "${GITHUB_REF_TYPE:-}" == tag && "${GITHUB_REF_NAME:-}" != "v$version" ]]; then
    echo "Release tag must match pyproject.toml version" >&2
    exit 1
fi
commit=${RELEASE_COMMIT:-$(git rev-parse HEAD)}
rm -rf build/icon.iconset build/dist/"Harness Discussion.app" build/dmg dist/HarnessDiscussion.dmg
mkdir -p build/icon.iconset dist
uv run python - <<'EOF'
from harness_discussion.icons import icon_image
for size in (16, 32, 128, 256, 512):
    icon_image(size).save(f"build/icon.iconset/icon_{size}x{size}.png")
    icon_image(size * 2).save(f"build/icon.iconset/icon_{size}x{size}@2x.png")
EOF
iconutil -c icns build/icon.iconset -o build/icon.icns
uv run pyinstaller --noconfirm --clean --windowed --name "Harness Discussion" \
    --osx-bundle-identifier com.tzachbon.harnessdiscussion --target-arch arm64 --icon "$PWD/build/icon.icns" \
    --hidden-import harness_discussion.mac.main --collect-submodules harness_discussion.mac --copy-metadata harness-discussion \
    --collect-all kokoro_onnx --collect-all espeakng_loader \
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all tokenizers \
    --distpath build/dist --workpath build/work --specpath build packaging/launcher.py
app="build/dist/Harness Discussion.app"
plist="$app/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$version" "$plist"
plutil -replace CFBundleVersion -string "$version" "$plist"
plutil -replace LSUIElement -bool true "$plist"
plutil -replace LSMinimumSystemVersion -string 13.0 "$plist"
plutil -replace NSMicrophoneUsageDescription -string "Harness Discussion records while you hold the dictation shortcut." "$plist"
plutil -replace HarnessDiscussionBuild -string "$version+$commit" "$plist"
# A Developer ID from the MACOS_* secrets gives a notarized dmg that Gatekeeper opens without a warning.
# Otherwise the local identity from setup_signing_mac.sh keeps the Accessibility grant across rebuilds,
# and anything else is signed ad hoc.
keychain="$HOME/Library/Keychains/harness-discussion-signing.keychain-db"
if [[ -n "${MACOS_CERTIFICATE:-}${MACOS_CERTIFICATE_PASSWORD:-}${MACOS_NOTARY_KEY:-}${MACOS_NOTARY_KEY_ID:-}${MACOS_NOTARY_ISSUER_ID:-}" ]]; then
    : "${MACOS_CERTIFICATE:?}" "${MACOS_CERTIFICATE_PASSWORD:?}" "${MACOS_NOTARY_KEY:?}" "${MACOS_NOTARY_KEY_ID:?}" "${MACOS_NOTARY_ISSUER_ID:?}"
    work=$(mktemp -d)
    keychain="$work/signing.keychain-db"
    trap 'security delete-keychain "$keychain" 2>/dev/null; rm -rf "$work"' EXIT
    password=$(openssl rand -hex 24)
    security create-keychain -p "$password" "$keychain"
    security set-keychain-settings -lut 3600 "$keychain"
    security unlock-keychain -p "$password" "$keychain"
    base64 --decode <<<"$MACOS_CERTIFICATE" > "$work/id.p12"
    security import "$work/id.p12" -k "$keychain" -P "$MACOS_CERTIFICATE_PASSWORD" -T /usr/bin/codesign >/dev/null
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$password" "$keychain" >/dev/null
    # codesign finds an identity only in a keychain on the search list
    listed=()
    while read -r path; do
        path=${path#\"}
        listed+=("${path%\"}")
    done < <(security list-keychains -d user)
    security list-keychains -d user -s "$keychain" "${listed[@]}"
    identity=$(security find-identity -v -p codesigning "$keychain" | awk '/Developer ID Application/ {print $2; exit}')
    [[ -n "$identity" ]] || { echo "MACOS_CERTIFICATE has no Developer ID Application identity" >&2; exit 1; }
    codesign --force --deep --options runtime --timestamp --entitlements packaging/entitlements.plist \
        --keychain "$keychain" --sign "$identity" "$app"
elif [[ -f "$keychain" ]] && password=$(security find-generic-password -s harness-discussion-signing -a "$USER" -w); then
    security unlock-keychain -p "$password" "$keychain"
    codesign --force --deep --keychain "$keychain" --sign "Harness Discussion Local Signing" "$app"
    security lock-keychain "$keychain"
else
    codesign --force --deep --sign - "$app"
fi
codesign --verify --deep --strict "$app"
"$app/Contents/MacOS/Harness Discussion" --self-test
mkdir -p build/dmg
cp -R "$app" build/dmg/
ln -s /Applications build/dmg/Applications
hdiutil create -volname "Harness Discussion" -srcfolder build/dmg -fs HFS+ -format UDZO -ov dist/HarnessDiscussion.dmg
if [[ -n "${identity:-}" ]]; then
    dmg=dist/HarnessDiscussion.dmg
    codesign --force --timestamp --keychain "$keychain" --sign "$identity" "$dmg"
    printf '%s\n' "$MACOS_NOTARY_KEY" > "$work/notary.p8"
    notary=(--key "$work/notary.p8" --key-id "$MACOS_NOTARY_KEY_ID" --issuer "$MACOS_NOTARY_ISSUER_ID")
    submission=$(xcrun notarytool submit "$dmg" "${notary[@]}" --wait --output-format json) || true
    id=$(plutil -extract id raw - <<<"$submission")
    if [[ $(plutil -extract status raw - <<<"$submission") != Accepted ]]; then
        xcrun notarytool log "$id" "${notary[@]}" >&2
        exit 1
    fi
    xcrun stapler staple "$dmg"
    spctl --assess --type open --context context:primary-signature --verbose "$dmg"
    spctl --assess --type execute --verbose "$app"
fi
ls -l dist/HarnessDiscussion.dmg
