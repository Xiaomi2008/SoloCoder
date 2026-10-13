#!/usr/bin/env bash
# Build SoloCoder.app: the Swift app, and a self-contained Python service inside it.
#
#   scripts/build_app.sh [--clean] [--python 3.14] [--deps "mcp openai …"]
#
# The service is not frozen with PyInstaller. It is a python-build-standalone
# interpreter (the kind `uv python install` downloads) with the runtime
# dependencies installed into its own site-packages, next to the project's own
# source. That layout is relocatable as a directory tree — `sys.prefix` follows
# the executable — so it survives the bundle moving, which on macOS it does:
# Gatekeeper translocation runs a quarantined app from
# `/private/var/folder/…/AppTranslocation/<uuid>/d/`. A venv cannot say that:
# `pyvenv.cfg` pins `home = <absolute path>` outside the bundle, and PyInstaller's
# onefile unpacks ~150 MB to a temp dir on every launch and breaks pyobjc's
# Objective-C classes along the way.
#
# Signing is ad-hoc (`-s -`) and innermost-first, with no hardened runtime: a
# bundled CPython dlopens signed-by-Apple-extension `.so` files that carry no
# Team ID, so library validation would refuse them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$ROOT/.build/app"
PKG="$ROOT/macapp/AgentClient"
APP="$STAGE/SoloCoder.app"
PY_VERSION="3.14"
# The service's own import set. `streamlit` is a core dependency of the project
# (the legacy `server.py` web UI) and is not in this list; the `web` extra is
# lxml and primp, 32 MB for a search tool that needs an API key, and
# `computer-use` is pyobjc, which needs Accessibility grants a bundled app does
# not have. Both are flags to add later, not omissions to rediscover.
DEPS="mcp openai anthropic httpx google-genai ollama"

# The Swift build, with its output kept. `| tail -1` on a *failing* build prints
# a code frame and hides the error line, and the script then exits with that
# frame as its only output — the one thing a build script must not do.
build_app_binary() {
    mkdir -p "$ROOT/.build"
    local log="$ROOT/.build/swift-release.log"
    if ! swift build --package-path "$PKG" -c release --product solocoder > "$log" 2>&1; then
        echo "swift build failed; full output:" >&2
        cat "$log" >&2
        exit 1
    fi
    tail -1 "$log"
}

# The project's own source, into the bundle: small, and the thing most likely to
# be stale after a Python edit. Byte-compiled with the bundle's interpreter, so
# the magic number matches what will run it.
copy_app_source() {
    local sidecar="$APP/Contents/Resources/sidecar"
    local sidecar_app="$sidecar/app"
    local python="$sidecar/python/bin/python3"
    [[ -x "$python" ]] || { echo "no sidecar interpreter in $APP; run without --binary first" >&2; exit 1; }
    # Start from a tree with no `.pyc` anywhere in it, the interpreter's included.
    # A `.pyc` that appears after the seal is a `sealed resource is missing or
    # invalid` on the user's machine, and there are two ways this script makes
    # one: the interpreter tarball arrives with some, and `compileall` below
    # imports stdlib modules, which an interpreter writes the byte code for.
    find "$sidecar" -name '__pycache__' -type d -prune -exec rm -rf {} +
    mkdir -p "$sidecar_app"
    rm -rf "$sidecar_app/openagent"
    cp -R "$ROOT/openagent" "$sidecar_app/"
    cp "$ROOT/cli_coder.py" "$ROOT/pyproject.toml" "$sidecar_app/"
    # `unchecked-hash` trusts the `.pyc` without stat-ing or hashing the source:
    # the bundle is signed and, translocated, read-only, so the source cannot
    # have changed under it, and the alternative costs a stat per module on every
    # launch. The app passes `-B`, which stops *writing* `.pyc` — a write inside
    # a signed bundle breaks its seal — while still reading these.
    "$python" -m compileall -q --invalidation-mode unchecked-hash "$sidecar_app" >/dev/null
    # Only the app's tree is meant to keep byte code: the app is launched with
    # `-B`, so the interpreter writes none of its own at runtime, and the ones
    # the compileall above just imported are not in the seal.
    find "$sidecar/python" -name '__pycache__' -type d -prune -exec rm -rf {} +
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean) rm -rf "$STAGE" "$ROOT/.build/pythons"; shift ;;
        --python) PY_VERSION="$2"; shift 2 ;;
        --deps) DEPS="$2"; shift 2 ;;
        # The app binary alone, into an existing bundle: the interpreter copy is
        # the slow part of a full build, and iterating on the Swift side should
        # not pay for 118 MB of `cp` each time.
        --binary)
            build_app_binary
            copy_app_source
            [[ -f "$APP/Contents/MacOS/SoloCoder" ]] || { echo "no bundle to update; run without --binary first" >&2; exit 1; }
            cp "$PKG/.build/release/solocoder" "$APP/Contents/MacOS/SoloCoder"
            codesign --force --sign - --timestamp=none "$APP/Contents/MacOS/SoloCoder" >/dev/null
            codesign --force --sign - --entitlements "$ROOT/macapp/entitlements.plist" --timestamp=none "$APP" >/dev/null
            codesign --verify --deep --strict "$APP" && echo "==> $APP"
            exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v swift >/dev/null || { echo "the Swift toolchain is required" >&2; exit 1; }

PY_HOME="$ROOT/.build/pythons"
# The installed interpreter's directory is named by the full version, so resolve
# it rather than spelling it: `uv python list --installed-with-urls` is not a
# thing, and globbing the install dir is what uv itself does.
install_python() {
    uv python install "$PY_VERSION" --install-dir "$PY_HOME" --no-bin >/dev/null 2>&1 || true
    local found
    found=$(find "$PY_HOME" -maxdepth 1 -type d -name 'cpython-*' | sort | tail -1)
    [[ -n "$found" ]] || { echo "no standalone CPython in $PY_HOME" >&2; exit 1; }
    echo "$found"
}

echo "==> python"
PY_DIR="$(install_python)"
PY_BIN="$PY_DIR/bin/python3"
SITE="$PY_DIR/lib/python3.14/site-packages"
echo "    $("$PY_BIN" -V) at ${PY_DIR#"$ROOT"/}"

echo "==> service dependencies"
uv pip install --python "$PY_BIN" --link-mode=clone --target "$SITE" $DEPS --quiet
# Not needed at runtime, and 6 MB of the payload.
rm -rf "$SITE/pip" "$SITE/pip-"*.dist-info

echo "==> app"
build_app_binary

echo "==> bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/sidecar"
cp "$PKG/.build/release/solocoder" "$APP/Contents/MacOS/SoloCoder"

# The sidecar: the interpreter, and the project's own source. `cp -R` of the
# interpreter keeps its symlinks (`bin/python3` → `python3.14`), which is what
# makes the tree relocatable.
cp -R "$PY_DIR" "$APP/Contents/Resources/sidecar/python"
copy_app_source

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>              <string>SoloCoder</string>
    <key>CFBundleIdentifier</key>              <string>dev.solocoder.app</string>
    <key>CFBundleName</key>                    <string>SoloCoder</string>
    <key>CFBundleDisplayName</key>             <string>SoloCoder</string>
    <key>CFBundlePackageType</key>             <string>APPL</string>
    <key>CFBundleShortVersionString</key>      <string>0.1.0</string>
    <key>CFBundleVersion</key>                 <string>1</string>
    <key>CFBundleInfoDictionaryVersion</key>   <string>6.0</string>
    <key>LSMinimumSystemVersion</key>          <string>14.0</string>
    <key>NSHighResolutionCapable</key>         <true/>
    <key>NSHumanReadableCopyright</key>        <string>MIT</string>
    <key>NSSupportsAutomaticTermination</key>  <false/>
    <key>NSSupportsSuddenTermination</key>     <false/>
    <!--
      macOS 15+ asks the user before a process touches the local network, and
      the key is what makes that ask legible. The app's whole reason to exist is
      a model server on the LAN, so this is not a corner case. ATS is not the
      issue: it gates Apple's networking APIs in the app's own process, and the
      http:// a user adds to a LAN endpoint is spoken by Python's httpx in the
      sidecar, which ATS does not see.
    -->
    <key>NSLocalNetworkUsageDescription</key>
    <string>SoloCoder talks to the model servers you add, including ones on your local network.</string>
</dict>
</plist>
PLIST
# No `LSUIElement`: the app is a windowed app and needs a Dock tile and a menu
# bar. It sets the activation policy itself for the `swift run` case, where there
# is no bundle to read one from.

echo "==> sign"
ENTITLEMENTS="$ROOT/macapp/entitlements.plist"
# Scrub extended attributes before sealing, not after. The interpreter tarball
# uv downloaded arrives with `com.apple.quarantine`, and `cp` adds
# `com.apple.provenance` as files move; both end up inside the seal, so a bundle
# signed without this is not reproducible, and a quarantined `.so` in
# site-packages is a first-exec prompt the user cannot answer usefully.
xattr -cr "$APP"
# Innermost first: signing a directory before its contents invalidates it.
find "$APP/Contents/Resources/sidecar/python" -type f \
    \( -name '*.dylib' -o -name '*.so' -o -name '*.bundle' \) -print0 |
    while IFS= read -r -d '' item; do
        codesign --force --sign - --timestamp=none "$item" >/dev/null 2>&1 || true
    done
find "$APP/Contents/Resources/sidecar/python/bin" -type f -print0 |
    while IFS= read -r -d '' item; do
        codesign --force --sign - --timestamp=none "$item" >/dev/null 2>&1 || true
    done
codesign --force --sign - --timestamp=none "$APP/Contents/MacOS/SoloCoder"
codesign --force --sign - --entitlements "$ENTITLEMENTS" --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=1 "$APP" 2>&1 | tail -1

echo "==> $APP"
du -sh "$APP" | awk '{print "    " $1 "  "}'
