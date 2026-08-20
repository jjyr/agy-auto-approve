#!/usr/bin/env python3
"""
Registers or updates agy-auto-approve in ~/.gemini/config/hooks.json
"""
import json
import os

def main():
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
        # Fallback to local workspace script if running from workspace
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

    print("Successfully registered agy-auto-approve in ~/.gemini/config/hooks.json")

if __name__ == "__main__":
    main()
