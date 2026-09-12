#!/bin/sh
# Install a locally built or supplied binary, then register its absolute path.
set -eu

usage() {
    cat <<'USAGE'
Usage: scripts/install.sh [OPTIONS]

By default, build this checkout with Cargo and install to ~/.local/bin.
  --binary PATH       Install a prebuilt binary (no Rust toolchain required)
  --bin-dir DIR       Installation directory (default: ~/.local/bin)
  --cli-only          Register CLI hooks only
  --desktop-only      Register Desktop sidecar only
  --no-register       Install the binary without changing Antigravity settings
  -h, --help          Show help

No sudo is needed. Existing approvals and reviewer state are preserved.
USAGE
}
fail() { printf '%s\n' "install: $*" >&2; exit 1; }
install_dir=${AGY_INSTALL_DIR:-"$HOME/.local/bin"}
binary_path=
register_mode=
register_enabled=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --binary|--bin-dir)
            [ "$#" -ge 2 ] || fail "$1 requires a path"
            case "$1" in
                --binary) binary_path=$2 ;;
                --bin-dir) install_dir=$2 ;;
            esac
            shift 2 ;;
        --cli-only|--desktop-only)
            [ -z "$register_mode" ] || fail "Choose only one of --cli-only and --desktop-only"
            register_mode=$1; shift ;;
        --no-register) register_enabled=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done
case "$(uname -s)" in Darwin|Linux) ;; *) fail "Only macOS and Linux are supported" ;; esac
if [ -z "$binary_path" ]; then
    command -v cargo >/dev/null 2>&1 || fail "Cargo is required to build; install Rust or use --binary PATH"
    source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
    [ -f "$source_dir/Cargo.toml" ] || fail "Run this script from a source checkout or pass --binary PATH"
    # Explicit target-dir avoids global Cargo target directory overrides.
    cargo build --manifest-path "$source_dir/Cargo.toml" --release --locked --target-dir "$source_dir/target"
    binary_path="$source_dir/target/release/agy-auto-approve"
fi
[ -f "$binary_path" ] && [ -x "$binary_path" ] || fail "Not an executable file: $binary_path"
# Validate before touching the installed executable or configuration.
version=$("$binary_path" --version) || fail "Cannot run the supplied binary on this machine"
case "$version" in 'agy-auto-approve '*) ;; *) fail "Not an agy-auto-approve binary: $version" ;; esac
mkdir -p -- "$install_dir"
install_dir=$(CDPATH= cd -- "$install_dir" && pwd)
destination="$install_dir/agy-auto-approve"
staged_binary=$(mktemp "$install_dir/.agy-auto-approve.XXXXXX")
trap 'rm -f -- "$staged_binary"' EXIT HUP INT TERM
cp -- "$binary_path" "$staged_binary"
chmod 755 "$staged_binary"
# Rename permits upgrading a running executable without ETXTBSY / partial writes.
mv -f -- "$staged_binary" "$destination"
printf 'Installed %s to %s\n' "$version" "$destination"
if [ "$register_enabled" -eq 1 ]; then
    if [ -n "$register_mode" ]; then
        "$destination" register "$register_mode"
    else
        "$destination" register
    fi
fi
case ":$PATH:" in
    *":$install_dir:"*) ;;
    *) printf 'Add this directory to your PATH: %s\n' "$install_dir" ;;
esac
printf '%s\n' 'If a daemon is already running, restart it to load this binary:'
printf '  "%s" daemon stop\n  "%s" daemon start\n' "$destination" "$destination"
printf '%s\n' 'For Desktop sidecar upgrades, restart Antigravity Desktop after registration.'
