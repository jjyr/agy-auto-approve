#!/usr/bin/env python3
"""
agy-auto-approve: PreToolUse Security & Intent Guard
Evaluates tool calls using a two-tier defense:
1. Hardcoded regex blacklist for immediate blocking of destructive actions (root/.git deletion).
2. Read-only Antigravity Agent for semantic evaluation of script contents and directory boundaries.
"""
import sys
import json
import os
import re
import asyncio

# 1. Read-only tools whitelist (fast path: instant approval without LLM call)
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
    r"rm\s+-[rfRF]*\s+/\s*$",
    r"rm\s+-[rfRF]*\s+/\*",
    r"rm\s+-[rfRF]*\s+~",
    r"rm\s+-[rfRF]*\s+\$HOME",
    r"rm\s+-[rfRF]*\s+/etc",
    r"rm\s+-[rfRF]*\s+/usr",
    r"rm\s+-[rfRF]*\s+/var",
    r"rm\s+-[rfRF]*\s+/bin",
    r"rm\s+-[rfRF]*\s+/System",
    # Prohibit deletion of .git repository metadata
    r"rm\s+-[rfRF]*\s+.*\.git(\b|/)",
    r"rm\s+-[rfRF]*\s+\.git",
    # Destructive disk / permission / fork bomb patterns
    r"\bmkfs\b",
    r"\bfdisk\b",
    r"\bdd\s+if=",
    r":\(\)\{\s*:\|:&\s*\};:",
    r"chmod\s+-[rwxRWX0-7]*\s+777\s+/",
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

def get_evaluator_prompt() -> str:
    """
    Retrieves the active evaluator prompt.
    Supports overrides in the following priority:
    1. Environment variable: AGY_AUTO_APPROVE_PROMPT
    2. Global custom file: ~/.gemini/config/agy-auto-approve-prompt.txt
    3. Workspace custom file: .agents/agy-auto-approve-prompt.txt
    4. Default prompt: DEFAULT_SYSTEM_PROMPT
    """
    # 1. Check environment variable
    if os.environ.get("AGY_AUTO_APPROVE_PROMPT"):
        return os.environ["AGY_AUTO_APPROVE_PROMPT"]

    # 2. Check workspace custom prompt file
    if os.path.exists(".agents/agy-auto-approve-prompt.txt"):
        try:
            with open(".agents/agy-auto-approve-prompt.txt", "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass

    # 3. Check global custom prompt file
    global_custom_path = os.path.expanduser("~/.gemini/config/agy-auto-approve-prompt.txt")
    if os.path.exists(global_custom_path):
        try:
            with open(global_custom_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass

    # 4. Fallback to default
    return DEFAULT_SYSTEM_PROMPT

def check_hard_blacklist(cmd: str) -> tuple[bool, str]:
    """Check command against hardcoded blacklist regex patterns."""
    for pattern in HARD_BLACKLIST_PATTERNS:
        if re.search(pattern, cmd):
            return True, f"Blocked by agy-auto-approve hard blacklist: matched rule '{pattern}'"
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
                        content = f.read(4000)  # Read first 4000 characters
                        return f"\n[Extracted Content of Script '{candidate}']:\n```\n{content}\n```\n"
                except Exception:
                    pass
    return ""

async def evaluate_with_readonly_agent(tool_name: str, tool_args: dict, workspace_paths: list, script_content: str) -> dict:
    """Call Antigravity Agent in read-only mode to evaluate tool call safety."""
    try:
        from google.antigravity import Agent, LocalAgentConfig

        system_prompt = get_evaluator_prompt()
        config = LocalAgentConfig(
            system_instructions=system_prompt
        )

        ws_str = ", ".join(workspace_paths) if workspace_paths else "Current Workspace"
        prompt = f"""[Environment Context]
Workspace Paths: {ws_str}

[Proposed Tool Call]
Tool Name: {tool_name}
Arguments:
{json.dumps(tool_args, ensure_ascii=False, indent=2)}
{script_content}
Please evaluate this tool call strictly following your instructions and return JSON."""

        async with Agent(config) as agent:
            response = await agent.chat(prompt)
            raw_text = ""
            async for token in response:
                raw_text += token

        # Parse JSON output
        clean_text = raw_text.strip()
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0]
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].split("```")[0]

        result = json.loads(clean_text.strip())
        if result.get("decision") in ["allow", "deny", "ask", "force_ask"]:
            return result

    except Exception as e:
        return {
            "decision": "ask",
            "reason": f"Evaluator agent error ({str(e)}), falling back to manual confirmation."
        }

    return {"decision": "ask", "reason": "Evaluator returned unrecognized response structure."}

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"decision": "ask", "reason": "Failed to parse hook stdin payload"}))
        return

    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    workspace_paths = payload.get("workspacePaths", [])

    # 1. Fast path: Read-only tools are approved immediately
    if tool_name in READ_ONLY_TOOLS:
        print(json.dumps({"decision": "allow", "reason": "Read-only tool automatically approved"}))
        return

    # 2. Hard blacklist check for command execution
    if tool_name == "run_command":
        cmd = tool_args.get("CommandLine", "")
        blocked, reason = check_hard_blacklist(cmd)
        if blocked:
            print(json.dumps({"decision": "deny", "reason": reason}))
            return

    # 3. Extract script content if a local script is being executed
    script_content = ""
    if tool_name == "run_command":
        script_content = extract_script_content_if_any(tool_args.get("CommandLine", ""), workspace_paths)

    # 4. Invoke read-only Evaluator Agent for semantic assessment
    result = asyncio.run(evaluate_with_readonly_agent(tool_name, tool_args, workspace_paths, script_content))
    print(json.dumps(result))

if __name__ == "__main__":
    main()
