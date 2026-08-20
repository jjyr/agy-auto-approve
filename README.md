# agy-auto-approve

**agy-auto-approve** is a global security and auto-approval plugin for [Antigravity CLI](https://antigravity.google) (`agy`).

It enables safe, hands-free autonomous workflows by leveraging a **Two-Tier Defense Architecture**:
1. **Tier 1 (Hard Blacklist)**: Instantly denies catastrophic commands (e.g., `rm -rf /`, `rm -rf .git`, disk formatters, fork bombs) with zero latency.
2. **Tier 2 (Read-Only LLM Guard)**: Dispatches a read-only Antigravity Agent in a secure sandbox to review proposed tool calls and local script contents before execution.

---

## Key Features

- ⚡ **Instant Read-Only Pass-Through**: Read-only tools (`view_file`, `grep_search`, `find_by_name`, `list_dir`, etc.) bypass LLM review and are approved immediately.
- 🛡️ **Hardcoded Blacklist Protection**:
  - Blocks deletion of root (`/`), home (`~`), or system directories (`/etc`, `/usr`, `/System`, etc.).
  - Blocks deletion or corruption of `.git` repository metadata.
  - Blocks dangerous disk partitioning/formatting tools (`mkfs`, `fdisk`, `dd if=`).
- 🔍 **Script Inspection & Directory Boundary Guard**:
  - Automatically extracts and inspects local scripts (e.g., `.sh`, `.py`, `.js`, `.ts`) before execution.
  - **Allowed**: Writing to the current workspace directory, reading/writing temporary directories (`/tmp`, `~/.tmp`), and launching local development environments (`npm run dev`, `python app.py`, `docker compose up`, etc.).
  - **Denied**: Any script or command attempting to delete or alter files **outside** the workspace directory.
- 🦺 **Fail-Safe Fallback**: If the evaluator encounters timeouts or unexpected errors, it automatically falls back to manual user confirmation (`decision: "ask"`).
- ⚙️ **Customizable Evaluator Prompt**: Easily inspect the default prompt or override it globally, per-workspace, or via environment variables.

---

## Directory Structure

```text
agy-auto-approve/
├── plugin.json              # Plugin manifest
├── hooks.json               # PreToolUse lifecycle hook registration
├── requirements.txt         # Python dependencies (google-antigravity)
├── scripts/
│   └── eval_guard.py        # Core evaluation engine (Hard blacklist + LLM guard)
└── README.md
```

---

## Installation

Install globally from the remote repository:

```bash
git clone git@github.com:jjyr/agy-auto-approve.git /tmp/agy-auto-approve && \
agy plugin install /tmp/agy-auto-approve && \
python3 /tmp/agy-auto-approve/scripts/register_hook.py && \
rm -rf /tmp/agy-auto-approve
```

Or if you have cloned the repository locally:

```bash
agy plugin install /path/to/agy-auto-approve && \
python3 /path/to/agy-auto-approve/scripts/register_hook.py
```

---

## Management & Verification

- **List active plugins**:
  ```bash
  agy plugin list
  ```
- **Real-Time Audit Log (Watch auto-approvals live)**:
  ```bash
  tail -f ~/.gemini/antigravity-cli/auto-approve.log
  ```
- **Validate plugin health**:
  ```bash
  agy plugin validate /path/to/agy-auto-approve
  ```
- **Uninstall plugin**:
  ```bash
  agy plugin uninstall agy-auto-approve
  ```

---

## Prompt Location & Customization

### Default Evaluator Prompt Location

The default system prompt used by the read-only evaluator is located in:
👉 **[`scripts/eval_guard.py`](scripts/eval_guard.py#L42-L66)** (variable: `DEFAULT_SYSTEM_PROMPT`)

```python
DEFAULT_SYSTEM_PROMPT = """You are a strict Security & Auto-Approval Evaluator for an AI coding assistant.
Your role is to review a proposed tool call and determine whether to "allow", "deny", or "ask" (prompt the user for manual confirmation).

### Allowed Actions:
1. Writing to current workspace: Creating, modifying, or overwriting code/build files within the current workspace directory.
2. Reading/Writing temporary directories: Reading or writing to temporary directories (e.g., /tmp, ~/.tmp, local scratch/cache folders).
3. Starting local dev environments: Starting dev servers, debugging tools, or build processes (e.g., npm run dev, yarn start, python app.py, flask run, uvicorn, cargo run, docker compose up).
4. Development script safety inspection:
   - Inspect script content if provided.
   - If the script performs standard project setup, compilation, testing, or server launch within workspace/temp paths, it MUST be allowed ("allow").
   - If the script attempts to delete, truncate, or modify contents OUTSIDE the workspace directory (except temp dirs), it MUST be strictly denied ("deny").

### Denied Actions:
1. Any command attempting to delete root `/`, user home `~`, or critical system directories.
2. Any command attempting to delete or corrupt the `.git` repository folder.
3. Any attempt to delete or alter files outside the workspace directory (excluding temp dirs).
4. Any attempt to exfiltrate sensitive credentials (e.g., `.env`, private keys, authentication tokens) to unauthorized external endpoints.

### Output Format:
You MUST return ONLY a valid JSON object matching this schema:
{
  "decision": "allow" | "deny" | "ask",
  "reason": "Brief, clear explanation of your judgment"
}
"""
```

---

### How to Override the Prompt

You can override the evaluator prompt without modifying the source code. Overrides are resolved in the following priority:

#### 1. Via Environment Variable (Highest Priority)
Set `AGY_AUTO_APPROVE_PROMPT` in your shell profile (`~/.zshrc` or `~/.bashrc`):
```bash
export AGY_AUTO_APPROVE_PROMPT="Your custom prompt instructions here..."
```

#### 2. Via Workspace Custom File (Project-Specific)
Place a custom prompt in `.agents/agy-auto-approve-prompt.txt` at the root of your project repository:
```bash
mkdir -p .agents
cat << 'EOF' > .agents/agy-auto-approve-prompt.txt
Your project-specific prompt rules here...
EOF
```

#### 3. Via Global Custom File (Machine-Wide)
Create `~/.gemini/config/agy-auto-approve-prompt.txt` for all projects:
```bash
cat << 'EOF' > ~/.gemini/config/agy-auto-approve-prompt.txt
Your global prompt rules here...
EOF
```

#### 4. Direct Source Modification
Directly edit `DEFAULT_SYSTEM_PROMPT` in [`scripts/eval_guard.py`](scripts/eval_guard.py#L42-L66).

---

## How It Works

Whenever `antigravity-cli` prepares to run a tool, the `PreToolUse` lifecycle hook triggers `scripts/eval_guard.py`:

```mermaid
sequenceDiagram
    participant MainAgent as Main Agent (agy)
    participant Hook as PreToolUse (eval_guard.py)
    participant ReadOnlyAgent as Read-Only Agent (Sandbox)
    participant User as User

    MainAgent->>Hook: Propose tool call (e.g., run_command)
    
    alt Tool is Read-Only (view_file, grep, etc.)
        Hook-->>MainAgent: allow (Instant pass-through)
    else Matches Hard Blacklist (rm -rf /, rm -rf .git)
        Hook-->>MainAgent: deny (Blocked immediately)
    else Requires Semantic Review
        Note over Hook: Extract local script content if any
        Hook->>ReadOnlyAgent: Analyze tool args & script content
        ReadOnlyAgent-->>Hook: Return verdict (allow / deny / ask)
        
        alt Verdict is "allow"
            Hook-->>MainAgent: allow (Auto-approved)
        else Verdict is "deny"
            Hook-->>MainAgent: deny (Blocked)
        else Verdict is "ask"
            Hook-->>User: Prompt user for confirmation
        end
    end
```

---

## Verification

To verify that the plugin is active:

1. Launch `agy` in any project directory:
   ```bash
   agy
   ```
2. Ask the agent to perform safe development actions (e.g., *"Create a hello world python script and run it"*). The actions will be approved automatically without interactive confirmation prompts.
3. If a dangerous command or out-of-boundary deletion is proposed, the guard will immediately block it.

---

## Uninstallation / Disabling

To remove the plugin globally:

```bash
rm -rf ~/.gemini/config/plugins/agy-auto-approve
```

Alternatively, you can temporarily disable the plugin in `hooks.json` by setting `"enabled": false`.

---

## License

MIT
