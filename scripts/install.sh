#!/bin/sh
# Keep execution in main so a truncated curl download cannot start an installation.
set -eu

usage() {
    cat <<'USAGE'
Usage: install.sh [OPTIONS]

Download the latest stable GitHub Release and install to ~/.local/bin.
  --version vX.Y.Z    Install a specific release
  --binary PATH       Install a supplied binary instead of downloading
  --source DIR        Build and install a local checkout (requires Cargo and a C compiler)
  --bin-dir DIR       Installation directory (default: ~/.local/bin)
  --cli-only          Register CLI hooks only
  --desktop-only      Register Desktop sidecar only
  --no-register       Install without changing Antigravity settings
  -h, --help          Show help

No sudo, Rust toolchain, or Python is needed for release installations.
USAGE
}
fail() { printf '%s\n' "install: $*" >&2; exit 1; }
cleanup() {
    if [ -n "$staged_binary" ]; then rm -f -- "$staged_binary"; fi
    if [ -n "$download_dir" ]; then rm -rf -- "$download_dir"; fi
}
fetch() {
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --retry 3 --connect-timeout 15 --max-time 180 "$@" </dev/null
}
valid_version() {
    printf '%s\n' "$1" | grep -Eq '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
}
download_release() {
    command -v curl >/dev/null 2>&1 || fail "curl is required"
    command -v tar >/dev/null 2>&1 || fail "tar is required"
    if command -v sha256sum >/dev/null 2>&1; then
        checksum_tool=sha256sum
    elif command -v shasum >/dev/null 2>&1; then
        checksum_tool=shasum
    else
        fail "sha256sum or shasum is required to verify the download"
    fi
    case "$(uname -s)" in
        Darwin) platform=apple-darwin ;;
        Linux) platform=unknown-linux-musl ;;
        *) fail "Only macOS and Linux are supported" ;;
    esac
    case "$(uname -m)" in
        arm64|aarch64) architecture=aarch64 ;;
        x86_64|amd64) architecture=x86_64 ;;
        *) fail "Unsupported CPU architecture: $(uname -m)" ;;
    esac
    release_base=https://github.com/jjyr/agy-auto-approve/releases
    if [ -z "$release_tag" ]; then
        # Resolve once, then pin both downloads to the same tag. No jq dependency.
        latest_url=$(fetch --output /dev/null --write-out '%{url_effective}' "$release_base/latest") \
            || fail "Cannot resolve the latest release; check connectivity or use --version vX.Y.Z"
        case "$latest_url" in
            "$release_base"/tag/*) release_tag=${latest_url##*/} ;;
            *) fail "No stable GitHub Release is available yet" ;;
        esac
    fi
    valid_version "$release_tag" || fail "Expected a stable release tag vX.Y.Z, got: $release_tag"
    asset="agy-auto-approve-$release_tag-$architecture-$platform.tar.gz"
    download_dir=$(mktemp -d "${TMPDIR:-/tmp}/agy-install.XXXXXX")
    printf 'Downloading %s\n' "$asset"
    fetch --output "$download_dir/$asset" "$release_base/download/$release_tag/$asset" \
        || fail "Release asset unavailable: $asset"
    fetch --output "$download_dir/SHA256SUMS" "$release_base/download/$release_tag/SHA256SUMS" \
        || fail "Cannot download SHA256SUMS for $release_tag"
    expected_hash=$(awk -v asset="$asset" '$2 == asset {print $1}' "$download_dir/SHA256SUMS")
    [ "${#expected_hash}" -eq 64 ] && printf '%s\n' "$expected_hash" | grep -Eq '^[0-9a-f]{64}$' \
        || fail "Missing, duplicate, or invalid checksum for $asset"
    if [ "$checksum_tool" = sha256sum ]; then
        actual_hash=$(sha256sum "$download_dir/$asset")
    else
        actual_hash=$(shasum -a 256 "$download_dir/$asset")
    fi
    actual_hash=${actual_hash%% *}
    [ "$actual_hash" = "$expected_hash" ] || fail "SHA-256 mismatch for $asset; installation unchanged"
    # Release archives contain exactly one regular executable at the archive root.
    archive_files=$(tar -tzf "$download_dir/$asset") || fail "Invalid release archive"
    [ "$archive_files" = agy-auto-approve ] || fail "Unexpected files in release archive"
    tar -xzf "$download_dir/$asset" -C "$download_dir" agy-auto-approve
    binary_path="$download_dir/agy-auto-approve"
    [ ! -L "$binary_path" ] && [ -f "$binary_path" ] || fail "Release binary is not a regular file"
    chmod 755 "$binary_path"
}
main() {
    install_dir=${AGY_INSTALL_DIR:-"$HOME/.local/bin"}
    binary_path=
    source_dir=
    release_tag=
    register_mode=
    register_enabled=1
    staged_binary=
    download_dir=
    trap cleanup EXIT
    trap 'exit 1' HUP INT TERM
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --binary|--source|--version|--bin-dir)
                [ "$#" -ge 2 ] && [ -n "$2" ] || fail "$1 requires a value"
                case "$1" in
                    --binary) binary_path=$2 ;;
                    --source) source_dir=$2 ;;
                    --version) release_tag=$2 ;;
                    --bin-dir) install_dir=$2 ;;
                esac
                shift 2 ;;
            --cli-only|--desktop-only)
                [ -z "$register_mode" ] || fail "Choose only one of --cli-only and --desktop-only"
                register_mode=$1; shift ;;
            --no-register) register_enabled=0; shift ;;
            -h|--help) usage; return 0 ;;
            *) fail "Unknown option: $1" ;;
        esac
    done
    [ -z "$binary_path" ] || { [ -z "$source_dir" ] && [ -z "$release_tag" ]; } \
        || fail "--binary cannot be combined with --source or --version"
    [ -z "$source_dir" ] || [ -z "$release_tag" ] || fail "--source cannot be combined with --version"
    if [ -n "$release_tag" ]; then valid_version "$release_tag" || fail "Version must be vX.Y.Z"; fi
    case "$(uname -s)" in Darwin|Linux) ;; *) fail "Only macOS and Linux are supported" ;; esac
    if [ -n "$source_dir" ]; then
        command -v cargo >/dev/null 2>&1 || fail "Cargo is required for --source"
        source_dir=$(CDPATH= cd -- "$source_dir" && pwd)
        [ -f "$source_dir/Cargo.toml" ] || fail "No Cargo.toml in $source_dir"
        cargo build --manifest-path "$source_dir/Cargo.toml" --release --locked --target-dir "$source_dir/target" </dev/null
        binary_path="$source_dir/target/release/agy-auto-approve"
    elif [ -z "$binary_path" ]; then
        download_release
    fi
    [ -f "$binary_path" ] && [ -x "$binary_path" ] || fail "Not an executable file: $binary_path"
    # Validate before touching the installed executable or configuration.
    version=$("$binary_path" --version </dev/null) || fail "Cannot run the binary on this machine"
    case "$version" in 'agy-auto-approve '*) ;; *) fail "Not an agy-auto-approve binary: $version" ;; esac
    if [ -n "$release_tag" ]; then
        [ "$version" = "agy-auto-approve ${release_tag#v}" ] || fail "Binary version does not match $release_tag"
    fi
    mkdir -p -- "$install_dir"
    install_dir=$(CDPATH= cd -- "$install_dir" && pwd)
    destination="$install_dir/agy-auto-approve"
    staged_binary=$(mktemp "$install_dir/.agy-auto-approve.XXXXXX")
    cp -- "$binary_path" "$staged_binary"
    chmod 755 "$staged_binary"
    mv -f -- "$staged_binary" "$destination"
    printf 'Installed %s to %s\n' "$version" "$destination"
    if [ "$register_enabled" -eq 1 ]; then
        if [ -n "$register_mode" ]; then
            "$destination" register "$register_mode" </dev/null
        else
            "$destination" register </dev/null
        fi
    fi
    case ":$PATH:" in
        *":$install_dir:"*) ;;
        *) printf 'Add this directory to your PATH: %s\n' "$install_dir" ;;
    esac
    printf '%s\n' 'If a daemon is already running, restart it to load this binary:'
    printf '  "%s" daemon stop\n  "%s" daemon start\n' "$destination" "$destination"
    printf '%s\n' 'For Desktop sidecar upgrades, restart Antigravity Desktop after registration.'
}
main "$@"
