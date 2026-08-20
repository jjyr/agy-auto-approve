#!/usr/bin/env python3
"""
Registers agy-auto-approve globally:
1. Adds PreToolUse hook into ~/.gemini/config/hooks.json
2. Populates common safe development command prefixes in ~/.gemini/antigravity-cli/settings.json
"""
import json
import os

DEV_COMMAND_PREFIXES = [
    "gh", "npm", "npx", "yarn", "pnpm", "bun", "git", "python", "python3",
    "pytest", "cargo", "go", "node", "make", "docker", "docker-compose",
    "curl", "cat", "echo", "ls", "mkdir", "cp", "touch", "grep", "find",
    "sh", "bash", "zsh", "head", "tail", "mise", "uv"
]

def register_hook():
    hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
    os.makedirs(os.path.dirname(hooks_path), exist_ok=True)
    
    data = {}
    if os.path.exists(hooks_path):
        try:
            with open(hooks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    script_path = os.path.expanduser("~/.gemini/config/plugins/agy-auto-approve/scripts/eval_guard.py")
    if not os.path.exists(script_path):
        local_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "eval_guard.py"))
        if os.path.exists(local_script):
            script_path = local_script

    data["agy-auto-approve"] = {
        "enabled": True,
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {script_path}",
                        "timeout": 30
                    }
                ]
            }
        ]
    }

    with open(hooks_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print("✅ Registered agy-auto-approve in ~/.gemini/config/hooks.json")

def configure_settings_permissions():
    settings_path = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
    if not os.path.exists(settings_path):
        return

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    if "permissions" not in data:
        data["permissions"] = {}
    
    allows = set(data.get("permissions", {}).get("allow", []))
    for p in DEV_COMMAND_PREFIXES:
        allows.add(f"command({p})")
    
    data["permissions"]["allow"] = sorted(list(allows))

    try:
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print("✅ Configured developer command permissions in ~/.gemini/antigravity-cli/settings.json")
    except Exception as e:
        print(f"Note: Could not update settings.json ({e})")

def main():
    register_hook()
    configure_settings_permissions()

if __name__ == "__main__":
    main()
