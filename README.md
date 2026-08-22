# agy-auto-approve

Adds intelligent **auto-approve** capabilities to [Antigravity CLI](https://antigravity.google) (`agy`).

---

## Features

Evaluates tool calls before execution via the `PreToolUse` lifecycle hook:

1. **Instant Pass-Through for Read-Only Tools**: `view_file`, `grep_search`, `find_by_name`, `list_dir`, etc. are approved immediately without confirmation.
2. **Hard Blacklist Defense**: Instantly denies catastrophic commands (e.g., `rm -rf /`, deleting `.git`, disk partition/format commands) with zero latency.
3. **Smart Dynamic Permission Overrides**: For safe commands and in-workspace edits, automatically evaluates safety and injects matching `permissionOverrides` (including full multi-command resolution for chained shell operators like `;`, `&&`, `||`, `|`), bypassing interactive confirmation prompts.
4. **Real-Time Audit Logging**: Emits live status indicators to the terminal and maintains a persistent audit log at `~/.gemini/antigravity-cli/auto-approve.log`.

---

## Installation & Usage

### Installation
```bash
# Local installation
agy plugin install /path/to/agy-auto-approve && \
python3 /path/to/agy-auto-approve/scripts/register_hook.py

# Or install from GitHub
git clone git@github.com:jjyr/agy-auto-approve.git /tmp/agy-auto-approve && \
agy plugin install /tmp/agy-auto-approve && \
python3 /tmp/agy-auto-approve/scripts/register_hook.py && \
rm -rf /tmp/agy-auto-approve
```

### Management
- **List installed plugins**: `agy plugin list`
- **Tail live approval audit logs**: `tail -f ~/.gemini/antigravity-cli/auto-approve.log`
- **Uninstall plugin**: `agy plugin uninstall agy-auto-approve`

---

## Configuration & Customization

### Defaults
- **Model**: `gemini-3.7-flash`
- **Effort**: `medium`
- **Prompt**: Defined in [`scripts/eval_guard.py`](scripts/eval_guard.py) (`DEFAULT_SYSTEM_PROMPT`)

### Customizing Model & Effort
You can customize the model and reasoning effort used by the AI evaluator. Overrides are resolved in the following priority order:

1. **Environment Variables** (Highest Priority):
   ```bash
   export AGY_AUTO_APPROVE_MODEL="gemini-3.7-flash"
   export AGY_AUTO_APPROVE_EFFORT="medium" # low | medium | high
   ```
2. **Workspace-Specific Files** (Applies to current project only):
   - Model: `.agents/agy-auto-approve-model.txt`
   - Effort: `.agents/agy-auto-approve-effort.txt`
3. **Global Custom Files** (Applies to all projects):
   - Model: `~/.gemini/config/agy-auto-approve-model.txt`
   - Effort: `~/.gemini/config/agy-auto-approve-effort.txt`

### Customizing the Evaluator Prompt
You can customize the evaluation prompt without modifying source code:

1. **Environment Variable** (Highest Priority):
   ```bash
   export AGY_AUTO_APPROVE_PROMPT="Your custom evaluator prompt here..."
   ```
2. **Workspace-Specific File** (Applies to current project only):
   Create `.agents/agy-auto-approve-prompt.txt` at the root of your workspace repository.
3. **Global Custom File** (Applies to all projects):
   Create `~/.gemini/config/agy-auto-approve-prompt.txt`.

---

## License

MIT
