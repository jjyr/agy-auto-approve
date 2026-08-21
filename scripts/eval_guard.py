#!/usr/bin/env python3
"""
agy-auto-approve: PreToolUse Security & Intent Guard
Evaluates tool calls using a multi-tier defense:
1. Fast-path whitelist for pure read-only tools and safe workspace file writes.
2. Hardcoded regex blacklist for immediate blocking of catastrophic commands.
3. Read-only AI Evaluator (via Gemini API / Antigravity SDK) for semantic reasoning.
4. Dynamic permissionOverrides emission for full auto-execution.
5. Real-time visible terminal notifications & persistent audit log (~/.gemini/antigravity-cli/auto-approve.log).
"""
import sys
import json
import os
import re
import shlex
import datetime
import urllib.request
import urllib.error

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
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+~/(?:\*|\s*$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+~(?:\s+|$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+\$HOME/(?:\*|\s*$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+\$HOME(?:\s+|$)",
    r"(?:^|[;&|\n])\s*rm\s+-[rfRF]*\s+/(?:etc|usr|var|bin|System|boot|sbin)(?:/|\s+|$)",
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
2. **Reading/Writing temporary directories**: Reading or writing to temporary directories (e.g., /tmp, ~/.tmp, local scratch/cache folders).
3. **Starting local dev environments**: Starting dev servers, debugging tools, or build processes (e.g., `npm run dev`, `yarn start`, `python app.py`, `flask run`, `uvicorn`, `cargo run`, `docker compose up`).
4. **Development script safety inspection**:
   - Inspect script content if provided.
   - If the script performs standard project setup, compilation, testing, or server launch within workspace/temp paths, it MUST be allowed ("allow").
   - **If the script attempts to delete, truncate, or modify contents OUTSIDE the workspace directory (except temp dirs), it MUST be strictly denied ("deny").**

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

def log_audit(decision: str, tool_name: str, tool_args: dict, reason: str, permission_overrides: list):
    """Write an audit log entry to ~/.gemini/antigravity-cli/auto-approve.log and stderr."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    arg_summary = ""
    if tool_name == "run_command":
        arg_summary = f'cmd="{tool_args.get("CommandLine", "")}"'
    elif "TargetFile" in tool_args:
        arg_summary = f'file="{tool_args.get("TargetFile")}"'
    else:
        arg_summary = json.dumps(tool_args, ensure_ascii=False)

    permission_summary = ""
    if permission_overrides is not None:
        permission_summary = json.dumps(permission_overrides, ensure_ascii=False)

    log_line = f"[{now}] [{decision.upper():<5}] tool={tool_name} | {arg_summary} | reason={reason} | overrides={permission_summary}\n"
    
    # 1. Write to persistent audit log
    log_dir = os.path.expanduser("~/.gemini/antigravity-cli")
    os.makedirs(log_dir, exist_ok=True)
    try:
        with open(os.path.join(log_dir, "auto-approve.log"), "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

    # 2. Print visible colored notification to stderr for real-time visibility
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

SHELL_COMMAND_SEPARATORS = {";", "\n", "&&", "||", "|", "|&", "&"}

def extract_shell_commands(cmd_str: str) -> list[str]:
    """Parse shell command string into individual sub-commands (for compound commands/pipelines)."""
    if not cmd_str or not cmd_str.strip():
        return []
    try:
        lexer = shlex.shlex(cmd_str, posix=True, punctuation_chars=True)
        tokens = list(lexer)
    except Exception:
        parts = [p.strip() for p in re.split(r";|&&|\|\||\||\n|&", cmd_str) if p.strip()]
        return parts or [cmd_str]

    commands = []
    current_cmd = []
    for t in tokens:
        if t in SHELL_COMMAND_SEPARATORS:
            if current_cmd:
                commands.append(" ".join(current_cmd))
                current_cmd = []
        else:
            current_cmd.append(t)
    if current_cmd:
        commands.append(" ".join(current_cmd))

    valid_cmds = [c for c in commands if c.strip()]
    return valid_cmds or [cmd_str]

def get_permission_overrides(tool_name: str, tool_args: dict) -> list[str]:
    """Generate dynamic permission grants to bypass interactive confirmation prompts."""
    overrides = []
    if tool_name == "run_command":
        cmd = tool_args.get("CommandLine", "").strip()
        num_commands = max(1, len(extract_shell_commands(cmd)))
        overrides.extend(["command(*)"] * num_commands)
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

    log_audit(decision, tool_name, tool_args, clean_reason, permission_overrides)
    return res

def get_api_key() -> str:
    """Retrieve Gemini API key from environment or ~/.gemini/.env"""
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env_path = os.path.expanduser("~/.gemini/.env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY="):
                        return line.split("=", 1)[1].strip("\"'")
        except Exception:
            pass
    return ""

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

def evaluate_with_gemini_api(system_prompt: str, prompt: str, api_key: str) -> tuple[str, str]:
    """Call Gemini REST API using Python standard library urllib."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
        parsed = json.loads(text)
        dec = parsed.get("decision", "ask")
        reason = parsed.get("reason", "AI evaluation completed.")
        return dec, reason
    return "ask", "Failed to parse AI evaluation response."

def evaluate_local_heuristics(tool_name: str, tool_args: dict, workspace_paths: list) -> tuple[str, str]:
    """Intelligent fallback heuristic evaluation when AI service is unavailable."""
    if tool_name in {"write_to_file", "replace_file_content", "edit_file"}:
        target = tool_args.get("TargetFile") or tool_args.get("target_file") or ""
        if target.startswith(("/tmp", "/var/tmp")) or any(target.startswith(ws) for ws in workspace_paths):
            return "allow", "In-workspace file modification permitted."
        return "allow", "File modification permitted."

    if tool_name == "run_command":
        cmd = tool_args.get("CommandLine", "").strip()
        safe_prefixes = (
            "npm ", "npx ", "yarn ", "pnpm ", "python ", "python3 ", "pytest ", "node ",
            "cargo ", "go ", "git ", "make ", "docker ", "docker-compose ", "uvicorn ",
            "flask ", "pip ", "pip3 ", "mkdir ", "cp ", "touch ", "cat ", "echo ", "ls ", "find ", "gh "
        )
        if any(cmd.startswith(p) for p in safe_prefixes):
            return "allow", "Standard development command permitted."

    return "allow", "Safe operation permitted by local guard heuristics."

def evaluate(tool_name: str, tool_args: dict, workspace_paths: list, script_content: str) -> tuple[str, str]:
    """Evaluate tool call via Gemini API -> Local Heuristic fallback."""
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

    api_key = get_api_key()
    if api_key:
        try:
            return evaluate_with_gemini_api(system_prompt, prompt, api_key)
        except Exception:
            pass

    return evaluate_local_heuristics(tool_name, tool_args, workspace_paths)

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
