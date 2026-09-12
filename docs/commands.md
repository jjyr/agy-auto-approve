# Command reference

Complete command and option reference for `agy-auto-approve` and its installer.
AI reviews require the host's `agentapi` command and an active login.

## Installation

Install the latest stable release:

```bash
curl -fsSL https://raw.githubusercontent.com/jjyr/agy-auto-approve/main/scripts/install.sh | sh
```

The installer supports macOS and Linux on ARM64 and x86_64. Release installation requires `curl`, `tar`, and either `sha256sum` or `shasum`; it does not require Rust or Python. It verifies the checksum, installs to `~/.local/bin`, and registers both CLI hooks and the Desktop sidecar. Add the installation directory to your `PATH`.

To pass options to the remote installer:

```bash
curl -fsSL https://raw.githubusercontent.com/jjyr/agy-auto-approve/main/scripts/install.sh | \
  sh -s -- --version v0.3.0 --cli-only
```

From a local checkout:

```bash
sh scripts/install.sh --help
sh scripts/install.sh --bin-dir "$HOME/bin" --no-register
sh scripts/install.sh --binary /path/to/agy-auto-approve
sh scripts/install.sh --source .
```

| Option | Behavior |
| --- | --- |
| `--version vX.Y.Z` | Install a specific stable release instead of the latest. |
| `--binary PATH` | Install an existing executable. |
| `--source DIR` | Build and install a local checkout; requires Rust/Cargo and a C compiler. |
| `--bin-dir DIR` | Set the installation directory; defaults to `AGY_INSTALL_DIR` or `~/.local/bin`. |
| `--cli-only` | Register only CLI hooks. |
| `--desktop-only` | Register only the Desktop sidecar. |
| `--no-register` | Install without changing Antigravity settings. |
| `-h`, `--help` | Show installer help. |

`--version`, `--binary`, and `--source` are mutually exclusive. `--cli-only` and `--desktop-only` cannot be combined.

Alternatively, install from source with Cargo and register the executable:

```bash
cargo install --path . --locked
agy-auto-approve register
```

After upgrading, restart an existing CLI daemon with `daemon stop` and `daemon start`. For a Desktop-managed daemon, restart Desktop to load the new executable.

## Help and version

```bash
agy-auto-approve --help
agy-auto-approve --version
agy-auto-approve help logs
agy-auto-approve logs --help
agy-auto-approve logs show --help
agy-auto-approve daemon --help
agy-auto-approve daemon run --help
agy-auto-approve register --help
agy-auto-approve hook --help
```

Use `-h` as a short form of `--help`, or `-V` for `--version` on the top-level command.

## Registration

```bash
agy-auto-approve register                 # Register CLI hooks and Desktop sidecar
agy-auto-approve register --cli-only      # Register CLI hooks only
agy-auto-approve register --desktop-only  # Register Desktop sidecar only
```

Registration preserves unrelated settings and updates existing hooks. CLI registration writes `~/.gemini/config/hooks.json` and adds development command permissions to existing CLI settings. Desktop registration deploys and enables the sidecar. The two options are mutually exclusive.

Registration uses the executable's absolute path. If you move it, run registration again.

## Daemon

```bash
agy-auto-approve daemon start                 # Start in the background
agy-auto-approve daemon status                # Inspect the current daemon
agy-auto-approve daemon stop                  # Stop and wait for socket cleanup
agy-auto-approve daemon run                   # Run in the foreground
agy-auto-approve daemon run --idle-timeout 0   # Disable idle shutdown
```

`start` returns the existing daemon's status if it is already running. CLI hooks automatically start the daemon when an AI review is needed.

`status` does not start the daemon. It reports the PID, version, socket, uptime, and approval counts when running; it exits with code 1 when stopped. `start`, `status`, and `stop` write JSON to stdout and diagnostics to stderr.

`run` is intended for foreground use or service managers. Its `--idle-timeout SECONDS` option defaults to `1800` (30 minutes); `0` disables idle shutdown.

The default socket is `~/.gemini/antigravity-cli/approver.sock`.

## Approval logs

```bash
agy-auto-approve logs
agy-auto-approve logs --limit 100
agy-auto-approve logs --decision deny
agy-auto-approve logs --tool run_command --conversation CONVERSATION_ID
agy-auto-approve logs --json
agy-auto-approve logs -f
agy-auto-approve logs --follow --decision deny
agy-auto-approve logs -f --json
agy-auto-approve logs show APPROVAL_ID
```

| Option | Behavior |
| --- | --- |
| `--limit N` | Show up to N recent completed approvals; defaults to 20 and must be at least 1. |
| `--decision VALUE` | Filter by `allow`, `deny`, `ask`, or `force_ask`. |
| `--tool NAME` | Filter by tool name. |
| `--conversation ID` | Filter by conversation ID. |
| `--json` | Output a JSON array, or JSON Lines when following. |
| `-f`, `--follow` | Show recent matching approvals, then follow newly completed approvals until Ctrl-C. |

List options can be combined. Normal lists show newest records first. Follow mode shows the initial matching records in chronological order, waits for the log file if necessary, and handles detected replacement or truncation.

`logs show APPROVAL_ID` outputs all recorded events for an exact approval ID as JSON, including hook input, reviewer input/output, and the final decision. It cannot be combined with list options. Incomplete approvals may have events available through `show` even though they do not appear in summaries.

Logs are read directly from `~/.gemini/agy-auto-approve/approvals.jsonl`; the daemon does not need to be running. These records contain approval-service and `agentapi` output, not the subsequent tool execution's stdout/stderr. Logs are not automatically rotated or cleaned up.

New approval summaries include the command and working directory when supplied in
`CommandLine` and `Cwd`, and display the pipeline stage. `reviewer_error` indicates
an approval infrastructure or response-format failure, whereas `reviewer` indicates
a parsed reviewer decision. Older records may lack the command and directory;
`logs show ID` still exposes their original hook input.

Detailed `agentapi_request` events include the daemon PID, operation, effective
search PATH, and CLI fallback directory. Process errors include the operation and
failure stage; session persistence errors identify the affected path. Logs do not
dump the full process environment.

To inspect records in a different directory, including the legacy location:

```bash
AGY_AUTO_APPROVE_LOG_DIR="$HOME/.gemini/antigravity-cli" agy-auto-approve logs
```

## Hook

The host normally invokes this command automatically. It reads a PreToolUse JSON payload from stdin and emits one JSON approval result to stdout:

```bash
printf '%s\n' '{"toolCall":{"name":"view_file","args":{}},"workspacePaths":[]}' | agy-auto-approve hook
```

The payload must contain `toolCall.name` as a string and `toolCall.args` as an object. Input is limited to 1 MiB. Invalid input produces an `ask` result. Approval decisions are `allow`, `deny`, `ask`, or `force_ask`.

## Environment variables

| Variable | Purpose / default |
| --- | --- |
| `AGY_INSTALL_DIR` | Installer destination; `~/.local/bin`. Overridden by `--bin-dir`. |
| `AGY_APPROVER_SOCKET` | Daemon socket; `~/.gemini/antigravity-cli/approver.sock`. |
| `AGY_APPROVER_STATE_DIR` | State directory; `~/.gemini/antigravity-cli/state`. |
| `AGY_AUTO_APPROVE_LOG_DIR` | Log directory; `~/.gemini/agy-auto-approve`. |
| `AGY_AUTO_APPROVE_SILENT` | Set to `1` to suppress approval notices on stderr; structured logs remain enabled. |
| `AGY_AUTO_APPROVE_PROMPT` | Override the reviewer prompt. |

When a nonempty log directory is explicitly configured and no nonempty state directory is set, state is stored in the log directory's `state/` subdirectory.

Prompt lookup order is the environment variable, `.agents/agy-auto-approve-prompt.txt` relative to the daemon's working directory, `~/.gemini/config/agy-auto-approve-prompt.txt`, then the [built-in prompt](../src/prompt.txt). Prompts are supplied when reviewer sessions are created; existing sessions retain their original prompt.

Model and effort settings are parsed (`AGY_AUTO_APPROVE_MODEL` and `AGY_AUTO_APPROVE_EFFORT`, defaulting to `gemini-3.7-flash` and `medium`), but are currently not forwarded to `agentapi`. The host determines the actual model.

## Development checks

Run from the repository root:

```bash
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
```

Integration tests use temporary home directories and a mock `agentapi`; no login or Python is required.
