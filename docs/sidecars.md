# Sidecars and daemons

CLI and Antigravity Desktop share the approval pipeline and use separate reviewer backends:

```text
Antigravity → sidecar daemon → agentapi
CLI         → cli daemon     → agy
```

`--mode cli|sidecar` selects the backend for the lifetime of a process. Daemon management commands default to `cli`; the installed Desktop manifest runs `daemon run --mode sidecar`. A hook without `--mode` selects `sidecar` when the host injects a nonempty `ANTIGRAVITY_LS_ADDRESS`, otherwise `cli`. An explicit mode overrides detection. Shared hook registration intentionally leaves mode unspecified.

Both backends preserve inherited PATH and append `$HOME/.gemini/antigravity-cli/bin`. Sidecar mode requires the host's agentapi connection environment and login; CLI mode invokes `agy` directly and uses its login. Finding the agentapi shim alone does not provide a host connection. Backend errors never switch modes or executables.

## Startup and installation

```bash
agy-auto-approve install                  # Install both integrations
agy-auto-approve install --cli-only
agy-auto-approve install --desktop-only
agy-auto-approve daemon start --mode cli
agy-auto-approve daemon status --mode sidecar
agy-auto-approve daemon stop --mode cli
agy-auto-approve daemon restart --mode cli
agy-auto-approve daemon run --mode sidecar --idle-timeout 0
```

A hook probes the selected socket and starts the same executable with the selected mode when needed. Read-only allowlist decisions, blocklist decisions, and an already-tripped circuit breaker do not start a daemon. Background startup has a three-second deadline.

Desktop registration writes a manifest with the absolute executable path and arguments `["daemon", "run", "--mode", "sidecar"]`, enabled under `sidecars.agy-auto-approve/approver` in `~/.gemini/config/config.json`.

Each daemon exits after 1,800 idle seconds by default; `--idle-timeout 0` disables idle shutdown. Active reviews prevent idle shutdown. Status and stop remain responsive during reviews. `update` refreshes enabled integration configurations and stops both mode daemons; restart Desktop to reload its sidecar.

## Independent sockets and state

| Resource | CLI | Sidecar |
| --- | --- | --- |
| Socket under `~/.gemini/antigravity-cli/` | `approver-cli.sock` | `approver-sidecar.sock` |
| State under `~/.gemini/antigravity-cli/` | `state/cli/` | `state/sidecar/` |
| Backend | `agy` | `agentapi` |

Each mode has its own lifetime lock and session pool. Each user conversation has an independent reviewer session, workspace, and circuit breaker state. At most one daemon per mode runs for a given socket base. Stopping or restarting one mode leaves the other running.

`AGY_APPROVER_SOCKET` sets the socket **base**: `/tmp/review.sock` becomes `/tmp/review-cli.sock` and `/tmp/review-sidecar.sock`. `AGY_APPROVER_STATE_DIR` sets the state **base**, with `cli/` or `sidecar/` appended. If only `AGY_AUTO_APPROVE_LOG_DIR` is configured, the state base is its `state/` subdirectory.

Legacy `approver.sock`, unscoped session files, and the old per-mode `reviewer_session.json` are not reused. Before replacing an older installed binary, stop its daemon using that older binary's `daemon stop`, then install the new binary and refresh registration. New modes create fresh sessions.

Sockets have permissions `0600`. Lifetime locks prevent competing starters from replacing a live socket. Stale sockets may be removed; regular files and symlinks are preserved.

## Session abstraction

`Backend` is a trait exposing `create_session` and `send_message`, implemented separately by `AgentApiBackend` and `AgyBackend`. `Bridge` holds a `Box<dyn Backend>` and owns persistence, reuse, and retry. Mode selection happens in the backend factory; both implementations share subprocess timeouts, exit handling, and audit logging.

- Agentapi creates a conversation with the reviewer prompt and sends actions through `send-message`.
- Agy creates a conversation using `-p`, `--disable-slash-commands`, `--output-format json`, and `--mode plan`. Later messages use `--conversation ID`. The adapter validates `status: SUCCESS`, the conversation ID, and the string `response` before parsing the assessment.

The CLI backend runs in the corresponding user session’s workspace, passes the prompt and action as literal arguments, and sets `AGY_AUTO_APPROVE_REVIEWER=1`. A hook inheriting this marker denies tool calls before any allowlist or daemon access. This prevents reviewer tool calls from recursively waiting on the same daemon; it never bypasses approval. Refresh hook registration when installing this version so nested hooks use this guard.

A failed cached session is cleared and recreated once, without affecting other user conversations. Initial failures are denied immediately. Ordinary stop/start preserves sessions; `restart --mode MODE` removes that mode’s entire `sessions/` directory (cached reviewer IDs and workspaces), while retaining circuit breaker history. The other mode remains untouched. Global prompt/model changes apply to new sessions. `model` selects the sidecar tier; `cli_model` selects an agy model ID.

## Per-user session isolation

Hooks forward `conversationId` (or `conversation_id`) as `user_session_id` in IPC. This is the caller's conversation ID, distinct from the backend's reviewer conversation ID. The effective key is `(mode, user_session_id)`.

```text
state/<mode>/sessions/<SHA-256 of user_session_id>/
  reviewer_session.json
  workspace/
```

The session pool holds one Bridge per key. A short table lock handles lookup and creation; each Bridge has its own async lock. Concurrent first requests for one user create only one reviewer; requests for different users can run concurrently. Same-user requests serialize, including circuit breaker checks in the hook. Circuit breaker filenames also hash the raw user ID, avoiding collisions from replacing punctuation.

Missing, empty, or whitespace-only user IDs use a fresh temporary Bridge and workspace for every request. They do not persist reviewer sessions or circuit breaker history. Temporary workspaces are removed when the request completes or is cancelled.

Every second the daemon checks for Bridges idle for five minutes. Only entries with no active or queued request leases are evicted. Persisted reviewer IDs and workspaces remain on disk and are loaded on demand. Status exposes `cached_sessions`; reviewer input audit records include `user_session_id`.

## IPC and failure handling

IPC is newline-delimited JSON, with `ping`, `status`, `stop`, and `evaluate` actions. Reviews carry `mode`, `user_session_id`, `toolCall`, `workspacePaths`, and `request_id`. A mode mismatch is rejected. Status includes the mode, PID, socket, version, uptime, and counters.

The hook has a 28-second overall deadline, including circuit breaker lock waiting and daemon startup. Its daemon request timeout is 25 seconds. The daemon review deadline is 24 seconds, including lock waiting and cached-session retry. Each backend subprocess has a 20-second timeout and is killed when cancelled.

Unavailable backends, timeout, invalid envelopes, and unparseable assessments result in `deny`. Invalid hook input returns `ask`; a tripped circuit breaker returns `force_ask`.

## Logs

Both modes append to `~/.gemini/agy-auto-approve/approvals.jsonl` and `auto-approve.log`. Events and summaries include `mode`. Backend traces use `agentapi_request/response/error` or `agy_request/response/error`; nonzero exits report both stdout and stderr.

```bash
agy-auto-approve logs
agy-auto-approve logs -f
agy-auto-approve logs show APPROVAL_ID
```

Logs combine both modes and are readable without either daemon running.
