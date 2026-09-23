#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
app_bundle="$PWD/出运工作台.app"
mkdir -p "$app_bundle/Contents/MacOS" "$app_bundle/Contents/Resources"
xcrun clang -fobjc-arc -O2 -framework Foundation -framework Security -framework LocalAuthentication desktop/Keychain.m -o desktop/keychain
xcrun clang -fobjc-arc -O2 -framework Foundation -framework Vision -framework ImageIO desktop/OCR.m -o desktop/ocr
xcrun clang -fobjc-arc -fblocks -mmacosx-version-min=12.0 -O2 -framework Cocoa -framework WebKit desktop/Workbench.m -o "$app_bundle/Contents/MacOS/Workbench"
/usr/bin/plutil -create xml1 "$app_bundle/Contents/Info.plist" 2>/dev/null || true
.venv/bin/python desktop/build_metadata.py "$app_bundle"
if [ -f desktop/AppIcon.icns ]; then cp desktop/AppIcon.icns "$app_bundle/Contents/Resources/AppIcon.icns"; fi
codesign --force --sign - "$app_bundle"
echo "$app_bundle"
