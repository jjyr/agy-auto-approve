#!/usr/bin/env python3
"""
agy-auto-approve: PreToolUse Security & Intent Guard
Evaluates tool calls using a multi-tier defense:
1. Fast-path whitelist for pure read-only tools and safe workspace file writes.
2. Hardcoded regex blacklist for immediate blocking of catastrophic commands.
3. Read-only AI Evaluator (via agy CLI) for semantic reasoning.
4. Dynamic permissionOverrides emission for full auto-execution.
5. Real-time visible terminal notifications & persistent audit log (~/.gemini/antigravity-cli/auto-approve.log).
"""
import sys
import json
import os
import re
import shlex
import subprocess
import datetime

# Default Model Configuration for AI Evaluation
DEFAULT_MODEL = "gemini-3.7-flash"
DEFAULT_EFFORT = "medium"
MAX_COMMAND_OVERRIDE_LENGTH = 80

# 1. Read-only tools whitelist (Instant pass-through)
READ_ONLY_TOOLS = {
    "view_file",
    "grep_search",
    "find_by_name",
    "list_dir",
    "read_url_content",
    "search_web",
    "read_browser_page"
}

# 2. Hardcoded Blacklist (0-latency defense against catastrophic commands)
HARD_BLACKLIST_PATTERNS = [
    # Prohibit root / home / system-level directory deletion
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+/(?:\*|\s*$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+~(?:\/.*|\s*$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+\$HOME(?:\/.*|\s*$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+/(?:etc|usr|var|bin|System|boot|sbin|Users|home)(?:/|\s+|$)",
    # Prohibit deletion of .git repository metadata
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+(?:.*/)?\.git(?:/|\s+|$)",
    # Destructive disk / permission / fork bomb patterns
    r"\bmkfs\b",
    r"\bfdisk\b",
    r"\bdd\s+if=",
    r":\(\)\{\s*:\|:&\s*\};:",
    r"(?:^|[;&|\n])\s*chmod\s+-[rwxRWX0-7]*\s+777\s+/",
]

# 3. Default Evaluator Prompt (Can be overridden via AGY_AUTO_APPROVE_PROMPT or custom prompt file)
DEFAULT_SYSTEM_PROMPT = """You are a strict Security & Auto-Approval Evaluator for an AI coding assistant.
Your role is to review a proposed tool call and determine whether to "allow", "deny", or "ask" (prompt the user for manual confirmation).

### Allowed Actions:
1. **Writing to current workspace**: Creating, modifying, or overwriting code/build files within the current workspace directory.
2. **Reading/Writing temporary directories and agent configurations**: Reading or writing to temporary directories (e.g., `/tmp`, `~/.tmp`, local scratch/cache folders) or agent tool configuration directories (`~/.gemini`).
3. **Starting local dev environments**: Starting dev servers, debugging tools, or build processes (e.g., `npm run dev`, `yarn start`, `python app.py`, `flask run`, `uvicorn`, `cargo run`, `docker compose up`).
4. **Development script safety inspection**:
   - Inspect script content if provided.
   - If the script performs standard project setup, compilation, testing, server launch, or agent configuration within workspace/temp/~/.gemini paths, it MUST be allowed ("allow").
   - **If the script attempts to delete, truncate, or modify user files OUTSIDE the workspace directory (except temp dirs and ~/.gemini config), it MUST be strictly denied ("deny").**

### Denied Actions:
1. Any command attempting to delete root `/`, user home `~`, or critical system directories.
2. Any command attempting to delete or corrupt the `.git` repository folder.
3. Any attempt to delete or alter user files outside the workspace directory (excluding temp dirs and ~/.gemini config).
4. Any attempt to exfiltrate sensitive credentials (e.g., `.env`, private keys, authentication tokens) to unauthorized external endpoints.

### Output Format:
You MUST return ONLY a valid JSON object matching this schema:
{
  "decision": "allow" | "deny" | "ask",
  "reason": "Brief, clear explanation of your judgment"
}
"""

def log_audit(decision: str, tool_name: str, reason: str):
    """Write an audit log entry to ~/.gemini/antigravity-cli/auto-approve.log and stderr."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now}] [{decision.upper():<5}] tool={tool_name} | reason={reason}\n"
    
    # 1. Write to persistent audit log
    log_dir = os.environ.get("AGY_AUTO_APPROVE_LOG_DIR") or os.path.expanduser("~/.gemini/antigravity-cli")
    os.makedirs(log_dir, exist_ok=True)
    try:
        with open(os.path.join(log_dir, "auto-approve.log"), "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

    # 2. Print visible colored notification to stderr for real-time visibility
    if not os.environ.get("AGY_AUTO_APPROVE_SILENT"):
        color = "\033[32m" if decision == "allow" else ("\033[31m" if decision == "deny" else "\033[33m")
        reset = "\033[0m"
        emoji = "⚡" if decision == "allow" else ("🛑" if decision == "deny" else "⚠️")
        sys.stderr.write(f"\n{color}{emoji} [agy-auto-approve: {decision.upper()}]{reset} {tool_name} -> {reason}\n")
        sys.stderr.flush()

def format_reason(decision: str, reason: str) -> str:
    """Ensure standard [agy-auto-approve: TAG] prefix in reason."""
    tag_map = {
        "allow": "[agy-auto-approve: ALLOWED]",
        "deny": "[agy-auto-approve: DENIED]",
        "ask": "[agy-auto-approve: REVIEW REQUIRED]",
        "force_ask": "[agy-auto-approve: REVIEW REQUIRED]"
    }
    tag = tag_map.get(decision, "[agy-auto-approve]")
    clean_reason = reason.strip()
    if clean_reason.startswith("[agy-auto-approve"):
        return clean_reason
    return f"{tag} {clean_reason}"

HEREDOC_START_PATTERN = re.compile(
    r"(?<!<)<<(-?)\s*(?:'([^']*)'|\"([^\"]*)\"|\\?([^\s;&|<>()]+))"
)

def clean_subcommand(cmd: str) -> str:
    """Strip grouping parentheses/braces and leading environment variables from subcommand."""
    cmd = cmd.strip()
    while (cmd.startswith("(") and cmd.endswith(")")) or (cmd.startswith("{") and cmd.endswith("}")):
        cmd = cmd[1:-1].strip()
    cmd = cmd.lstrip("(").rstrip(")")
    
    while True:
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S+)\s*", cmd)
        if m:
            cmd = cmd[m.end():].lstrip()
        else:
            break
        
    return cmd.strip()

def extract_shell_commands(cmd_str: str) -> list[str]:
    """Parse shell command string into individual sub-commands preserving exact sub-command strings."""
    if not cmd_str or not cmd_str.strip():
        return []
    commands = []
    current = []
    in_single = False
    in_double = False
    pending_heredocs = []
    i = 0
    n = len(cmd_str)

    while i < n:
        char = cmd_str[i]
        if char == "\\":
            if i + 1 < n and cmd_str[i+1] == "\n":
                current.append(" ")
                i += 2
                continue
            elif i + 2 < n and cmd_str[i:i+3] == "\r\n":
                current.append(" ")
                i += 3
                continue
            current.append(char)
            if i + 1 < n:
                i += 1
                current.append(cmd_str[i])
            i += 1
            continue
        elif char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
            i += 1
            continue
        elif char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            i += 1
            continue

        if not in_single and not in_double:
            # Detect heredoc operator << or <<-
            if char == "<" and i + 1 < n and cmd_str[i+1] == "<":
                prev_char = cmd_str[i-1] if i > 0 else ""
                next_char = cmd_str[i+2] if i + 2 < n else ""
                if prev_char != "<" and next_char != "<":
                    m = HEREDOC_START_PATTERN.match(cmd_str, i)
                    if m:
                        strip_tab = bool(m.group(1))
                        delim = m.group(2) or m.group(3) or m.group(4)
                        pending_heredocs.append((delim, strip_tab))

            # Consume heredoc body when reaching newline after heredoc declaration
            if char == "\n" and pending_heredocs:
                current.append(char)
                i += 1
                while pending_heredocs and i < n:
                    delim, strip_tab = pending_heredocs[0]
                    line_end = cmd_str.find("\n", i)
                    if line_end == -1:
                        line_content = cmd_str[i:]
                        next_i = n
                    else:
                        line_content = cmd_str[i:line_end]
                        next_i = line_end + 1

                    check_line = line_content.rstrip("\r")
                    if strip_tab:
                        check_line = check_line.lstrip("\t")

                    if check_line == delim:
                        pending_heredocs.pop(0)
                        current.append(line_content)
                        if line_end != -1:
                            i = line_end
                        else:
                            i = n
                        break

                    current.append(cmd_str[i:next_i])
                    i = next_i
                continue

            if i + 1 < n and cmd_str[i:i+2] in ("&&", "||", "|&"):
                cmd_part = clean_subcommand("".join(current))
                if cmd_part:
                    commands.append(cmd_part)
                current = []
                i += 2
                continue
            elif char in (";", "\n", "|"):
                cmd_part = clean_subcommand("".join(current))
                if cmd_part:
                    commands.append(cmd_part)
                current = []
                i += 1
                continue
            elif char == "&":
                prev_char = cmd_str[i-1] if i > 0 else ""
                next_char = cmd_str[i+1] if i + 1 < n else ""
                if prev_char in (">", "<") or next_char == ">":
                    current.append(char)
                    i += 1
                    continue
                else:
                    cmd_part = clean_subcommand("".join(current))
                    if cmd_part:
                        commands.append(cmd_part)
                    current = []
                    i += 1
                    continue

        current.append(char)
        i += 1

    cmd_part = clean_subcommand("".join(current))
    if cmd_part:
        commands.append(cmd_part)

    return commands or [cmd_str.strip()]

def format_single_command_override(cmd: str) -> str:
    """Format an individual parsed sub-command into a permission override.
    
    If length <= 80, emit exact command override.
    If length > 80, directly return the first 80 characters prefix.
    """
    cmd = cmd.strip()
    if not cmd:
        return ""
    tokens = cmd.split()
    if tokens and tokens[0] == "gh":
        if len(tokens) >= 3 and not tokens[1].startswith("-") and not tokens[2].startswith("-"):
            return f"command(gh {tokens[1]} {tokens[2]})"
        elif len(tokens) >= 2 and not tokens[1].startswith("-"):
            return f"command(gh {tokens[1]})"
        return "command(gh)"
    if len(cmd) <= MAX_COMMAND_OVERRIDE_LENGTH:
        return f"command({cmd})"
    return f"command({cmd[:MAX_COMMAND_OVERRIDE_LENGTH]})"

def get_permission_overrides(tool_name: str, tool_args: dict) -> list[str]:
    """Generate dynamic permission grants to bypass interactive confirmation prompts."""
    overrides = []
    if tool_name == "run_command":
        raw_cmd = tool_args.get("CommandLine", "").strip()
        sub_cmds = extract_shell_commands(raw_cmd)
        if not sub_cmds and raw_cmd:
            sub_cmds = [raw_cmd]
        for sc in sub_cmds:
            entry = format_single_command_override(sc)
            if entry:
                overrides.append(entry)
    elif tool_name in {"write_to_file", "replace_file_content", "edit_file"}:
        target = tool_args.get("TargetFile") or tool_args.get("target_file") or ""
        if target:
            overrides.append(f"file({target})")
    return overrides

def build_result(decision: str, reason: str, tool_name: str, tool_args: dict) -> dict:
    """Construct complete PreToolHookResult with permissionOverrides and audit logging."""
    clean_reason = format_reason(decision, reason)
    permission_overrides = None
    
    res = {
        "decision": decision,
        "reason": clean_reason,
    }
    if decision == "allow":
        permission_overrides = get_permission_overrides(tool_name, tool_args)

    if permission_overrides is not None:
        res["permissionOverrides"] = permission_overrides

    log_audit(decision, tool_name, clean_reason)
    return res

def get_evaluator_prompt() -> str:
    """Retrieves the active evaluator prompt with priority override."""
    if os.environ.get("AGY_AUTO_APPROVE_PROMPT"):
        return os.environ["AGY_AUTO_APPROVE_PROMPT"]
    if os.path.exists(".agents/agy-auto-approve-prompt.txt"):
        try:
            with open(".agents/agy-auto-approve-prompt.txt", "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    global_custom_path = os.path.expanduser("~/.gemini/config/agy-auto-approve-prompt.txt")
    if os.path.exists(global_custom_path):
        try:
            with open(global_custom_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return DEFAULT_SYSTEM_PROMPT

def check_hard_blacklist(cmd: str) -> tuple[bool, str]:
    """Check command against hardcoded blacklist regex patterns."""
    for pattern in HARD_BLACKLIST_PATTERNS:
        if re.search(pattern, cmd):
            return True, f"Blocked by hard blacklist: matched pattern '{pattern}'"
    return False, ""

def extract_script_content_if_any(cmd: str, workspace_paths: list) -> str:
    """If the command executes a local script file, extract its content for inspection."""
    sub_cmds = extract_shell_commands(cmd)
    tokens = []
    for sc in sub_cmds:
        tokens.extend(sc.split())
    if not tokens:
        tokens = cmd.strip().split()
    if not tokens:
        return ""
    script_extensions = (".sh", ".py", ".js", ".ts", ".bash", ".zsh", ".rb", ".mjs", ".cjs")
    script_candidates = [t for t in tokens if t.endswith(script_extensions)]
    for candidate in script_candidates:
        candidate = candidate.strip("'\"")
        for ws in workspace_paths:
            possible_path = os.path.join(ws, candidate) if not os.path.isabs(candidate) else candidate
            if os.path.isfile(possible_path):
                try:
                    with open(possible_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(4000)
                        return f"\n[Extracted Content of Script '{candidate}']:\n```\n{content}\n```\n"
                except Exception:
                    pass
    return ""

def get_evaluator_effort() -> str:
    """Retrieve effort for agy evaluation with priority overrides."""
    if os.environ.get("AGY_AUTO_APPROVE_EFFORT"):
        return os.environ["AGY_AUTO_APPROVE_EFFORT"]

    if os.path.exists(".agents/agy-auto-approve-effort.txt"):
        try:
            with open(".agents/agy-auto-approve-effort.txt", "r", encoding="utf-8") as f:
                effort = f.read().strip()
                if effort:
                    return effort
        except Exception:
            pass

    global_effort_file = os.path.expanduser("~/.gemini/config/agy-auto-approve-effort.txt")
    if os.path.exists(global_effort_file):
        try:
            with open(global_effort_file, "r", encoding="utf-8") as f:
                effort = f.read().strip()
                if effort:
                    return effort
        except Exception:
            pass

    return DEFAULT_EFFORT

def get_evaluator_model() -> tuple[str, str]:
    """Retrieve model and effort for agy evaluation with priority overrides."""
    effort = get_evaluator_effort()

    if os.environ.get("AGY_AUTO_APPROVE_MODEL"):
        model = os.environ["AGY_AUTO_APPROVE_MODEL"]
        return model, effort

    if os.path.exists(".agents/agy-auto-approve-model.txt"):
        try:
            with open(".agents/agy-auto-approve-model.txt", "r", encoding="utf-8") as f:
                model = f.read().strip()
                if model:
                    return model, effort
        except Exception:
            pass

    global_model_file = os.path.expanduser("~/.gemini/config/agy-auto-approve-model.txt")
    if os.path.exists(global_model_file):
        try:
            with open(global_model_file, "r", encoding="utf-8") as f:
                model = f.read().strip()
                if model:
                    return model, effort
        except Exception:
            pass

    return DEFAULT_MODEL, effort

def evaluate_with_agy_cli(system_prompt: str, prompt: str) -> tuple[str, str]:
    """Call agy CLI print mode to evaluate tool call safety with Gemini."""
    model, effort = get_evaluator_model()
    full_prompt = f"{system_prompt}\n\n{prompt}"
    cmd = [
        "agy",
        "-p", full_prompt,
        "--model", model,
        "--effort", effort,
        "--disable-slash-commands",
        "--output-format", "json"
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            raw_response = data.get("response", "")
            json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_response)
            clean_text = json_match.group(1) if json_match else raw_response.strip()
            parsed = json.loads(clean_text)
            dec = parsed.get("decision", "ask")
            reason = parsed.get("reason", "AI evaluation completed via agy.")
            return dec, reason
    except Exception:
        pass
    return "ask", "AI evaluation via agy was unavailable or timed out."

def evaluate(tool_name: str, tool_args: dict, workspace_paths: list, script_content: str) -> tuple[str, str]:
    """Evaluate tool call safety via agy CLI."""
    system_prompt = get_evaluator_prompt()
    ws_str = ", ".join(workspace_paths) if workspace_paths else "Current Workspace"
    prompt = f"""[Environment Context]
Workspace Paths: {ws_str}

[Proposed Tool Call]
Tool Name: {tool_name}
Arguments:
{json.dumps(tool_args, ensure_ascii=False, indent=2)}
{script_content}
Please evaluate this tool call strictly following your instructions and return JSON."""

    return evaluate_with_agy_cli(system_prompt, prompt)

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps(build_result("ask", "Failed to parse hook stdin payload.", "", {})))
        return

    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    workspace_paths = payload.get("workspacePaths", [])

    # 1. Fast path: Read-only tools are approved immediately
    if tool_name in READ_ONLY_TOOLS:
        print(json.dumps(build_result("allow", "Read-only tool automatically approved.", tool_name, tool_args)))
        return

    # 2. Hard blacklist check for command execution
    if tool_name == "run_command":
        cmd = tool_args.get("CommandLine", "")
        blocked, reason = check_hard_blacklist(cmd)
        if blocked:
            print(json.dumps(build_result("deny", reason, tool_name, tool_args)))
            return

    # 3. Extract script content if a local script is being executed
    script_content = ""
    if tool_name == "run_command":
        script_content = extract_script_content_if_any(tool_args.get("CommandLine", ""), workspace_paths)

    # 4. Evaluate tool call
    decision, reason = evaluate(tool_name, tool_args, workspace_paths, script_content)
    result = build_result(decision, reason, tool_name, tool_args)
    print(json.dumps(result))

if __name__ == "__main__":
    main()
