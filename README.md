# agy-auto-approve

Automatic approval hooks and a persistent approval daemon for Antigravity CLI and Desktop, built as a single Rust executable. AI reviews use the host's `agentapi` command and require an active login.

## Install

Install the latest release on macOS or Linux (ARM64 and x86_64):

```bash
curl -fsSL https://raw.githubusercontent.com/jjyr/agy-auto-approve/main/scripts/install.sh | sh
```

The installer verifies the download, installs to `~/.local/bin`, and registers the CLI hook and Desktop sidecar. No Rust or Python is required. Make sure `~/.local/bin` is on your `PATH`.

## Development install

Requires Git, Rust/Cargo, and a C compiler.

```bash
git clone https://github.com/jjyr/agy-auto-approve.git
cd agy-auto-approve
sh scripts/install.sh --source .
```

## Logs

```bash
agy-auto-approve logs                     # Show recent approvals
agy-auto-approve logs -f                  # Follow new approvals
agy-auto-approve logs --decision deny     # Show denied approvals
agy-auto-approve logs show APPROVAL_ID    # Show the full approval record
```

Logs are stored in `~/.gemini/agy-auto-approve` and can be read without a running daemon.

For all commands and options, see the [command reference](docs/commands.md). For more details, see the [approval architecture](docs/auto_approver_architecture.md) and [sidecar documentation](docs/sidecars.md).

## License

MIT
