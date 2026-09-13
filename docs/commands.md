# Command reference

Complete command and option reference for `agy-auto-approve` and its installation commands.
AI reviews require the host's `agentapi` command and an active login.

## Installation

Install the binary using the Release download or Cargo registry commands in the
[README](../README.md#install), then run `agy-auto-approve install`.
Release archives support macOS and Linux on ARM64 and x86_64.
Release downloads also include `SHA256SUMS`; you can verify the archive against
its matching entry using `sha256sum` (Linux) or `shasum -a 256` (macOS).

## Updates

```bash
agy-auto-approve update                  # Upgrade to the latest stable release
agy-auto-approve update --version v0.4.2  # Install a specific stable version
```

`update` checks the current executable's installation root and Cargo's
`.crates2.json` records. Registry installations use `cargo install --locked --force`
with the original root and registry index. Other standalone binaries download a
GitHub Release using `curl`, verify SHA-256 and the executable version, then
atomically replace the current binary. Download or validation failures leave the
existing binary unchanged. The executable directory must be writable.
Cargo failures are reported without falling back to Release downloads.
Cargo Git and local-path installation sources are unsupported.

After upgrading, the new executable refreshes only the currently enabled CLI
hook and/or Desktop sidecar. Existing configurations from older versions are
recognized; disabled or missing integrations remain unchanged. If neither is
enabled, run `install` to enable the plugin. Configuration refresh failures are
reported separately from the completed binary upgrade.

A running daemon is stopped after the upgrade; the CLI starts the new version
on its next AI review request. Restart Antigravity Desktop when its sidecar is
enabled. A failed daemon stop is reported and may require a manual restart.

## Help and version

```bash
agy-auto-approve --help
agy-auto-approve --version
agy-auto-approve help logs
agy-auto-approve logs --help
agy-auto-approve logs show --help
agy-auto-approve daemon --help
agy-auto-approve daemon run --help
agy-auto-approve install --help
agy-auto-approve update --help
agy-auto-approve hook --help
```

Use `-h` as a short form of `--help`, or `-V` for `--version` on the top-level command.

## Plugin installation

```bash
agy-auto-approve install                 # Register CLI hooks and Desktop sidecar
agy-auto-approve install --cli-only      # Register CLI hooks only
agy-auto-approve install --desktop-only  # Register Desktop sidecar only
```

Installation preserves unrelated settings and updates existing hooks. CLI registration writes `~/.gemini/config/hooks.json` and adds development command permissions to existing CLI settings. Desktop registration deploys and enables the sidecar. The two options are mutually exclusive.

Installation uses the executable's absolute path. If you move it, run `install` again.

## Daemon

```bash
agy-auto-approve daemon start                 # Start in the background
agy-auto-approve daemon status                # Inspect the current daemon
agy-auto-approve daemon stop                  # Stop and wait for socket cleanup
agy-auto-approve daemon restart               # Restart with a fresh reviewer conversation
agy-auto-approve daemon run                   # Run in the foreground
agy-auto-approve daemon run --idle-timeout 0   # Disable idle shutdown
```

`start` returns the existing daemon's status if it is already running. CLI hooks automatically start the daemon when an AI review is needed.

`restart` stops the daemon, waits for its lifetime lock to be released, removes the
local cached reviewer conversation ID, and starts the daemon. If already stopped,
it clears the cache and starts it. The next approval creates a new conversation
using the latest configuration; remote conversations are not deleted. Ordinary
`stop` / `start` retains the cached conversation. Restart interrupts active reviews.
For Desktop sidecars, restart the host if it needs to reattach to the daemon.

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
| `AGY_APPROVER_SOCKET` | Daemon socket; `~/.gemini/antigravity-cli/approver.sock`. |
| `AGY_APPROVER_STATE_DIR` | State directory; `~/.gemini/antigravity-cli/state`. |
| `AGY_AUTO_APPROVE_LOG_DIR` | Log directory; `~/.gemini/agy-auto-approve`. |
| `AGY_AUTO_APPROVE_SILENT` | Set to `1` to suppress approval notices on stderr; structured logs remain enabled. |
| `AGY_AUTO_APPROVE_PROMPT` | Override the reviewer prompt. |
| `AGY_AUTO_APPROVE_MODEL` | Override the model tier: `flash_lite`, `flash`, or `pro`. |

When a nonempty log directory is explicitly configured and no nonempty state directory is set, state is stored in the log directory's `state/` subdirectory.

## Configuration

```bash
agy-auto-approve config         # Show global settings, sources, and runtime paths
agy-auto-approve config --json  # Machine-readable settings (includes the full prompt)
agy-auto-approve config --edit  # Open the global TOML file
agy-auto-approve daemon restart # Apply settings in a new reviewer conversation
```

The global file is `~/.gemini/config/agy-auto-approve.toml`:

```toml
# Empty means the host's default model; otherwise flash_lite, flash, or pro.
model = ""
prompt = """Your reviewer instructions here."""
```

`config --edit` creates a template when absent, using the host default model
and the built-in prompt. It opens `$VISUAL`, then `$EDITOR`, falling
back to `vi`. Editor arguments are supported (for example `EDITOR='code --wait'`);
use a blocking editor so validation runs after saving. Invalid TOML, unknown keys,
and unsupported model values produce an error. Editing does not restart the daemon.

Precedence per setting: nonempty `AGY_AUTO_APPROVE_PROMPT` / `AGY_AUTO_APPROVE_MODEL`,
then the TOML field, then built-in defaults. An empty TOML model explicitly
selects the host default. An empty prompt is used literally.
Old global `~/.gemini/config/agy-auto-approve-*.txt` files and project
`.agents/agy-auto-approve-*.txt` files are not read or migrated. `--local`
is unsupported. `effort` and `AGY_AUTO_APPROVE_EFFORT` are no longer supported:
`agentapi` does not expose an effort option. Old full model identifiers such as
`gemini-3.7-flash` must be replaced with a supported tier.

Model tiers are passed as `agentapi new-conversation --model=<tier>` (supported
by agy 1.2.2). No model flag is passed when using the host default.
Settings are read when a reviewer conversation is created. Existing conversations
keep their configuration, including across ordinary daemon stops and starts.
There is no change detection or hot reload; use `daemon restart` to forget the
cached conversation. Environment overrides use the daemon's startup environment.
`config` shows settings resolved in the invoking process, not the active conversation.
Runtime paths (socket, state, logs) remain controlled by the environment variables above.

## Development checks

Run from the repository root:

```bash
cargo fmt --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
```

Integration tests use temporary home directories and a mock `agentapi`; no login or Python is required.
