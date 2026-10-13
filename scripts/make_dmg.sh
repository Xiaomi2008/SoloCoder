#!/usr/bin/env bash
# Wrap the .app in a dmg: the shape a person downloads and drags to Applications.
#
#   scripts/make_dmg.sh [path/to/SoloCoder.app] [out.dmg]
#
# The volume carries an `Applications` symlink beside the app because that is the
# drag target every Mac user expects, and nothing else here is decoration: no
# background picture, no custom window geometry.
#
# The image is verified by mounting it read-only and running the app's own
# `--self-test` from inside the mount. A mounted dmg is the closest cheap
# stand-in for what Gatekeeper does to a downloaded app — translocation runs it
# from a read-only mount at a path nobody predicted — so a bundle that passes
# here has already shown it can launch, find its imports, bind its socket and
# leave nothing behind, all while unable to write into itself.
#
# Distribution caveat, said once, here: the app is ad-hoc signed (`-s -`). A dmg
# built on this machine opens on this machine, because a file built locally
# carries no `com.apple.quarantine`. Send the same file to anyone else and the
# quarantine bit arrives with it, and macOS refuses an ad-hoc signed app that
# came off the network. The fix is Developer ID signing plus notarization — the
# open item in dev_docs/HANDOFF.md — and not anything this script can do.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT/.build/app/SoloCoder.app}"
[[ -d "$APP" ]] || { echo "no bundle at $APP — run scripts/build_app.sh" >&2; exit 1; }
APP="$(cd "$APP" && pwd)"

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo 0.1.0)"
OUT="${2:-$ROOT/.build/$(basename "$APP" .app)-${VERSION}.dmg}"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/oadmg.XXXXXX")"
MP=""
# The stage holds a read-only-by-`ditto`-of-a-signed-bundle tree, and a mounted
# volume cannot be `rm -rf`'d, so both get undone here on any exit path.
cleanup() {
    [[ -n "$MP" ]] && hdiutil detach "$MP" >/dev/null 2>&1 || true
    chmod -R u+w "$STAGE" 2>/dev/null || true
    rm -rf "$STAGE"
}
trap cleanup EXIT

echo "==> staging"
ditto "$APP" "$STAGE/$(basename "$APP")"
ln -s /Applications "$STAGE/Applications"

echo "==> compressing"
# `UDZO` is zlib: the interpreter tree is ~118 MB of binaries and libraries that
# compress about in half, and a dmg a person downloads is measured in megabytes.
# `UDBZ` (bzip2) is smaller and slower; a dmg nobody mounts read-write needs no
# read-write-capable format.
hdiutil create -volname "$(basename "$APP" .app)" -srcfolder "$STAGE" -ov -format UDZO "$OUT" >/dev/null

# The image itself, not only the app inside it: `hdiutil verify` checks the
# checksum, and a signed image is what a browser can hand over with its identity
# attached. Ad-hoc, because that is all the app inside it has.
codesign --force --sign - "$OUT" >/dev/null
hdiutil verify "$OUT" >/dev/null

echo "==> mounting read-only"
MP="$(hdiutil attach -nobrowse -readonly "$OUT" | awk 'END{print $NF}')"
VOL_APP="$MP/$(basename "$APP")"

echo "==> the app inside the image"
codesign --verify --deep --strict --verbose=0 "$VOL_APP"
"$VOL_APP/Contents/MacOS/$(defaults read "$VOL_APP/Contents/Info" CFBundleExecutable)" --self-test

echo
echo "==> $OUT"
du -sh "$OUT" | awk '{print "    " $1 "  from "}'
du -sh "$APP" | awk '{print "    " $1}'
