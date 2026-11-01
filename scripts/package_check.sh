#!/usr/bin/env bash
# Does this .app work as a shipped app?
#
#   scripts/package_check.sh [path/to/SoloCoder.app]
#
# `swift build` and `pytest` say nothing about the bundle. The things that break
# a shipped app are properties of the bundle: a signature that rejects a Rust
# extension, a load path pointing at the developer's Homebrew, an interpreter
# that finds `openagent` only because the developer's shell exported a
# PYTHONPATH, a socket path that fits on this machine and not under
# translocation, a service that outlives an app killed with SIGKILL.
#
# So every check here runs against a copy of the bundle, in a temp directory,
# read-only, with a scrubbed environment — the shape a downloaded app actually
# launches in. The one check that needs the app to stay alive (parent death)
# opens a real window for a few seconds; run it when nobody is mid-sentence on
# this machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT/.build/app/SoloCoder.app}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/oapkg.XXXXXX")"
# The copy is made read-only on purpose, so cleanup has to undo that first —
# `rm -rf` cannot unlink inside a directory it has no write bit on.
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT
[[ -d "$APP" ]] || { echo "no bundle at $APP — run scripts/build_app.sh" >&2; exit 1; }
APP="$(cd "$APP" && pwd)"   # absolute, so the copy's paths read cleanly

failures=0
check() {  # check <name> <command…>
    local name="$1"; shift
    if "$@" > "$WORK/out" 2>&1; then
        echo "ok   $name"
    else
        echo "FAIL $name"
        sed 's/^/     /' "$WORK/out" | head -20
        failures=$((failures + 1))
    fi
}

echo "==> $APP"

# --- the signature ----------------------------------------------------------
# `--deep --strict` walks every nested Mach-O. A bundled CPython is ~400 of
# them, and one unsigned `.so` is a `Code signature is invalid` at exec time
# with nothing in the app's log to say which.
check "codesign" codesign --verify --deep --strict --verbose=0 "$APP"

# --- self-containment -------------------------------------------------------
# A load path outside the bundle and the system is the bundle borrowing the
# developer's machine: it works here and dyld-aborts on the next one.
find_selfcontained() {
    # A load path that is absolute and outside the system is the bundle
    # borrowing the developer's machine — /opt/homebrew, /usr/local, /Users/…,
    # ~/.local/share/uv, a .venv. It works here and dyld-aborts on the next one.
    # `@rpath`, `@executable_path` and `@loader_path` are the bundle referring
    # to itself, which is the point.
    #
    # Only the tab-indented lines are load commands. The unindented line is the
    # file's own path, repeated per architecture in a fat file, and the first
    # indented line is the file's install_name — its identity, which dyld never
    # searches for. Counting those, as this did first, produced a wall of
    # "offenders" that were each file's own name.
    #
    # Bare relative names (no `@`, no `/`) are left alone: python-build-
    # standalone's Tcl/Tk extensions name each other that way, dyld resolves them
    # against the cwd, nothing in the service imports tkinter, and they are
    # PBS's binaries and not ours.
    local offenders
    offenders=$(
        {
            find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -name '*.bundle' \) -print
            echo "$APP/Contents/MacOS/SoloCoder"
            # A native engine is a Mach-O of its own, borrowing nothing either.
            [[ -f "$APP/Contents/Resources/sidecar/openagent" ]] && echo "$APP/Contents/Resources/sidecar/openagent"
        } | while IFS= read -r macho; do
            otool -L "$macho" 2>/dev/null | awk '
                /^\t/ { n++; if (n > 1) { sub(/ \(.*$/, ""); sub(/^\t+/, ""); print } }'
        done | grep -E '^/' | grep -vE '^/usr/lib(/|$)|^/System/' | sort -u || true
    )
    if [[ -n "$offenders" ]]; then
        echo "load paths outside the bundle and the system:"
        echo "$offenders" | sed 's/^/  /'
        return 1
    fi
    return 0
}
check "self-contained load paths" find_selfcontained

# --- the copy a user actually launches --------------------------------------
# Relocated and read-only: Gatekeeper translocation runs a quarantined app from
# `/private/var/folder/…/AppTranslocation/<uuid>/d/`, and a bundle that writes
# into itself (`__pycache__`, a secrets folder beside the source) fails there
# while working fine in the build directory.
echo "==> relocated read-only copy"
ditto "$APP" "$WORK/SoloCoder.app"
chmod -R a-w "$WORK/SoloCoder.app"
check "self-test (relocated, read-only)" \
    "$WORK/SoloCoder.app/Contents/MacOS/SoloCoder" --self-test

# --- the interpreter, with the developer's environment gone -----------------
# An import that succeeds in the developer's shell and fails under `env -i` means
# the bundle was relying on a PYTHONPATH, a user site-packages, or a Homebrew
# OpenSSL.
#
# The first half runs the app's own launch shape — the interpreter, the flags,
# the script — because that is the thing under test. `import openagent` works
# there through `cli_coder.py`'s own `sys.path.insert(0, __file__.parent)`:
# isolated mode (`-I`) drops the script's directory *and* the cwd from
# `sys.path`, so a `-c "import openagent"` probe would fail on a bundle that
# launches fine, and that is a fact about `-I`, not a defect to chase.
# Run against the read-only copy, and with the app's `-B`. A probe that imports
# the dependency set with byte-code writing on fills the bundle's site-packages
# with `__pycache__`, and that is a resource added after the seal: this check,
# written the first way, broke the seal of the bundle the gate was verifying.
if [[ -x "$APP/Contents/Resources/sidecar/openagent" ]]; then
# A native engine has no imports to resolve; the equivalent question is whether
# it starts at all with the developer's environment gone.
check "native engine (scrubbed env)" bash -c '
    env -i HOME="$HOME" PATH=/usr/bin:/bin "$1/Contents/Resources/sidecar/openagent" serve --help > /dev/null
' _ "$WORK/SoloCoder.app"
else
check "imports (scrubbed env)" bash -c '
    scrub=(env -i HOME="$HOME" PATH=/usr/bin:/bin)
    py="$1/Contents/Resources/sidecar/python/bin/python3"
    "${scrub[@]}" "$py" -I -B -u "$1/Contents/Resources/sidecar/app/cli_coder.py" --help > /dev/null
    "${scrub[@]}" "$py" -I -B -c "
import mcp, anthropic, openai, httpx, google.genai, ollama
print(\"deps ok\")
" ' _ "$WORK/SoloCoder.app"
fi

# A real run, against the read-only copy: the provider path, the TLS roots, the
# model's base_url, the tool set, from inside a bundle that cannot write. Off by
# default, because a gate that needs the network goes red for reasons that are
# not the bundle's.
if [[ -n "${AGENT_LIVE_MODEL:-}" ]]; then
    check "run (live, $AGENT_LIVE_MODEL)" \
        "$WORK/SoloCoder.app/Contents/MacOS/SoloCoder" --self-test-run "$AGENT_LIVE_MODEL"
fi

# --- parent death -----------------------------------------------------------
# The app is killed; the service must go with it. `kill -9` posts no
# notification and runs no teardown, so the only thing that can save the machine
# from an orphan per launch is the service noticing its stdin closed.
parent_death() {
    local app_pid sidecar_pid=""
    "$APP/Contents/MacOS/SoloCoder" >/dev/null 2>&1 &
    app_pid=$!
    # The socket path carries the app's pid, so this names *this* app's sidecar
    # and not one left by an earlier launch.
    for _ in $(seq 1 60); do
        sidecar_pid=$(pgrep -f "serve --socket /tmp/oa-$(id -u)/$app_pid/" | head -1 || true)
        if [[ -n "$sidecar_pid" ]]; then break; fi
        sleep 0.5
    done
    if [[ -z "$sidecar_pid" ]]; then
        kill -9 "$app_pid" 2>/dev/null || true
        echo "the app never started a sidecar"
        return 1
    fi
    kill -9 "$app_pid"
    for _ in $(seq 1 20); do
        if ! kill -0 "$sidecar_pid" 2>/dev/null; then
            echo "the sidecar died with its app"
            return 0
        fi
        sleep 0.5
    done
    echo "the sidecar (pid $sidecar_pid) outlived a SIGKILLed app"
    kill -9 "$sidecar_pid" 2>/dev/null || true
    return 1
}
check "parent death (opens a window for ~3s)" parent_death

# Last, and the reason this gate has a second codesign: running the app must not
# write into the bundle it lives in. The seal is what Gatekeeper checks, and a
# `.pyc` written by any interpreter that did not get `-B` — a probe, a child, a
# build step — invalidates it on the user's machine, where the bundle is
# writable and nothing tells them.
check "still sealed after running it" codesign --verify --deep --strict --verbose=0 "$APP"

echo
if (( failures )); then
    echo "package check failed: $failures"
    exit 1
fi
echo "package check passed"
