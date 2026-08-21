#!/usr/bin/env python3
"""
Test Suite for agy-auto-approve (eval_guard.py)

Usage:
  # 1. Fast, isolated tests with dummy AI evaluator (Default: 0 latency, 0 token cost, no side effects)
  python3 tests/test_eval_guard.py

  # 2. Live end-to-end integration tests using real agy CLI
  python3 tests/test_eval_guard.py --real-agy

  # 3. Verbose output
  python3 tests/test_eval_guard.py -v
"""
import unittest
import sys
import os
import json
import tempfile
import shutil
from unittest.mock import patch

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import eval_guard

USE_REAL_AGY = False


class TestEvalGuard(unittest.TestCase):

    def setUp(self):
        """Set up isolated sandbox environment for zero side-effects."""
        self.temp_dir = tempfile.mkdtemp(prefix="agy_test_")
        self.original_env = dict(os.environ)
        os.environ["AGY_AUTO_APPROVE_LOG_DIR"] = self.temp_dir
        os.environ["AGY_AUTO_APPROVE_SILENT"] = "1"

    def tearDown(self):
        """Clean up temporary test files and restore environment."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self.original_env)

    def _run_guard(self, tool_name: str, tool_args: dict, workspace_paths: list = None, override_evaluator = None) -> dict:
        """Helper to invoke eval_guard.main via stdin/stdout capture."""
        if workspace_paths is None:
            workspace_paths = [self.temp_dir]

        payload = {
            "toolCall": {
                "name": tool_name,
                "args": tool_args
            },
            "workspacePaths": workspace_paths
        }

        if override_evaluator:
            with patch.object(eval_guard, "evaluate_with_agy_cli", side_effect=override_evaluator):
                with patch("sys.stdin.read", return_value=json.dumps(payload)):
                    printed = []
                    with patch("builtins.print", side_effect=lambda s: printed.append(s)):
                        eval_guard.main()
                    return json.loads(printed[0]) if printed else {}
        elif not USE_REAL_AGY:
            def dummy_agy_eval(system_prompt: str, prompt: str):
                cmd = tool_args.get("CommandLine", "")
                if "malicious" in cmd or "exfiltrate" in cmd or "/etc/shadow" in cmd:
                    return "deny", "Simulated AI: Prohibited access detected."
                if "suspicious" in cmd:
                    return "ask", "Simulated AI: Review required."
                return "allow", "Simulated AI: Safe development operation approved."

            with patch.object(eval_guard, "evaluate_with_agy_cli", side_effect=dummy_agy_eval):
                with patch("sys.stdin.read", return_value=json.dumps(payload)):
                    printed = []
                    with patch("builtins.print", side_effect=lambda s: printed.append(s)):
                        eval_guard.main()
                    return json.loads(printed[0]) if printed else {}
        else:
            with patch("sys.stdin.read", return_value=json.dumps(payload)):
                printed = []
                with patch("builtins.print", side_effect=lambda s: printed.append(s)):
                    eval_guard.main()
                return json.loads(printed[0]) if printed else {}

    def test_read_only_tools_instant_allow(self):
        read_only_cases = [
            ("view_file", {"AbsolutePath": "/some/path/file.py"}),
            ("grep_search", {"Query": "TODO", "SearchPath": "/some/path"}),
            ("find_by_name", {"Pattern": "*.py", "SearchDirectory": "/some/path"}),
            ("list_dir", {"DirectoryPath": "/some/path"}),
            ("read_url_content", {"Url": "https://example.com"}),
            ("search_web", {"query": "python shlex documentation"}),
        ]
        for tool_name, args in read_only_cases:
            res = self._run_guard(tool_name, args)
            self.assertEqual(res.get("decision"), "allow", f"Failed for {tool_name}")
            self.assertIn("Read-only tool automatically approved", res.get("reason", ""))
            self.assertEqual(res.get("permissionOverrides"), [])

    def test_hard_blacklist_instant_deny(self):
        blacklist_commands = [
            "r" + "m -rf /",
            "r" + "m -rf /*",
            "r" + "m -rf ~",
            "r" + "m -rf ~/.ssh",
            "r" + "m -rf /Users/jjy",
            "r" + "m -rf /etc",
            "r" + "m -rf .git",
            "r" + "m -rf /path/to/.git/",
            "m" + "kfs.ext4 /dev/sda1",
            "d" + "d if=/dev/zero of=/dev/sda",
            ":()" + "{ :" + "|:& " + "};:",
            "ch" + "mod -R 777 /",
            "npm test; r" + "m -rf /",
            "git status && r" + "m -rf ~",
        ]
        for cmd in blacklist_commands:
            res = self._run_guard("run_command", {"CommandLine": cmd})
            self.assertEqual(res.get("decision"), "deny", f"Blacklist failed to block: {cmd}")
            self.assertIn("Blocked by hard blacklist", res.get("reason", ""))
            self.assertNotIn("permissionOverrides", res)

    def test_single_safe_command_exact_override(self):
        cmd = "npm test"
        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(res.get("permissionOverrides"), ["command(npm test)"])

    def test_compound_commands_multi_overrides(self):
        cmd1 = "mise run local:down 2>&1; sleep 2; mise run test:e2e 2>&1"
        res1 = self._run_guard("run_command", {"CommandLine": cmd1})
        self.assertEqual(res1.get("decision"), "allow")
        self.assertEqual(
            res1.get("permissionOverrides"),
            [
                "command(mise run local:down 2>&1)",
                "command(sleep 2)",
                "command(mise run test:e2e 2>&1)"
            ]
        )

        cmd2 = "git add . && git commit -m 'update code' && git push"
        res2 = self._run_guard("run_command", {"CommandLine": cmd2})
        self.assertEqual(res2.get("decision"), "allow")
        self.assertEqual(
            res2.get("permissionOverrides"),
            [
                "command(git add .)",
                "command(git commit -m 'update code')",
                "command(git push)"
            ]
        )

        cmd3 = "cat output.log | grep ERROR | wc -l"
        res3 = self._run_guard("run_command", {"CommandLine": cmd3})
        self.assertEqual(res3.get("decision"), "allow")
        self.assertEqual(
            res3.get("permissionOverrides"),
            [
                "command(cat output.log)",
                "command(grep ERROR)",
                "command(wc -l)"
            ]
        )

    def test_long_command_prefix_truncation(self):
        long_unzip = "unzip -q /tmp/run-32438847930/e2e-failure-32438847930/output/cloudflare-local-e2e/test-results/earnings-share-EARNINGS-SH-39124-removes-the-old-image-and-X/trace.zip -d /tmp/trace-010"
        cmd = f"mkdir -p /tmp/trace-010 && {long_unzip}"
        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                "command(mkdir -p /tmp/trace-010)",
                "command(unzip)"
            ]
        )

    def test_quoted_separators_not_split(self):
        cmd = 'git commit -m "fix: semicolon ; in message && double ampersand"'
        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            ['command(git commit -m "fix: semicolon ; in message && double ampersand")']
        )

    def test_sed_command_recognition(self):
        """Test that sed commands with quotes and regex substitutions are accurately recognized."""
        cmd = "sed -n 's/^  LSP_OPERATOR_TOKEN: //p' tests/bruno/environments/test.bru"
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(extracted, [cmd])
        self.assertTrue(extracted[0].startswith("sed"))

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(res.get("permissionOverrides"), [f"command({cmd})"])

    def test_killall_command_with_or_true(self):
        """Test that killall command chained with || true correctly extracts the primary killall sub-command."""
        cmd = "killall -9 fnn fiber-lsp-sdk-agent ckb ckb-cli || true"
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(
            extracted,
            [
                "killall -9 fnn fiber-lsp-sdk-agent ckb ckb-cli",
                "true"
            ]
        )
        self.assertEqual(extracted[0], "killall -9 fnn fiber-lsp-sdk-agent ckb ckb-cli")

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                "command(killall -9 fnn fiber-lsp-sdk-agent ckb ckb-cli)",
                "command(true)"
            ]
        )

    def test_file_modification_overrides(self):
        target_file = "/path/to/project/src/index.ts"
        res1 = self._run_guard("write_to_file", {"TargetFile": target_file, "CodeContent": "console.log(1)"})
        self.assertEqual(res1.get("decision"), "allow")
        self.assertEqual(res1.get("permissionOverrides"), [f"file({target_file})"])

        res2 = self._run_guard("replace_file_content", {"TargetFile": target_file, "ReplacementContent": "foo"})
        self.assertEqual(res2.get("decision"), "allow")
        self.assertEqual(res2.get("permissionOverrides"), [f"file({target_file})"])


    def test_script_content_extraction(self):
        script_name = "build_helper.sh"
        script_path = os.path.join(self.temp_dir, script_name)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\necho ok\n")

        extracted = eval_guard.extract_script_content_if_any(f"REMOVE_OLD_STATE=y bash {script_name}", [self.temp_dir])
        self.assertIn("echo ok", extracted)
        self.assertIn(f"Extracted Content of Script '{script_name}'", extracted)

    def test_compound_commands_with_parentheses_and_cd(self):
        cmd = "cargo build -p fiber-lsp-sdk-agent && (cd tests/deploy/udt-init && cargo build) && (cd tests/funding-tx-builder && cargo build)"
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(
            extracted,
            [
                "cargo build -p fiber-lsp-sdk-agent",
                "cd tests/deploy/udt-init",
                "cargo build",
                "cd tests/funding-tx-builder",
                "cargo build"
            ]
        )
        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                "command(cargo build -p fiber-lsp-sdk-agent)",
                "command(cd tests/deploy/udt-init)",
                "command(cargo build)",
                "command(cd tests/funding-tx-builder)",
                "command(cargo build)"
            ]
        )

    def test_command_with_leading_environment_variables(self):
        cmd = "REMOVE_OLD_STATE=y ./tests/nodes/start.sh e2e/lsp"
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(extracted, ["./tests/nodes/start.sh e2e/lsp"])

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(res.get("permissionOverrides"), ["command(./tests/nodes/start.sh e2e/lsp)"])

    def test_model_and_prompt_customization(self):
        model, effort = eval_guard.get_evaluator_model()
        self.assertEqual(model, "gemini-3.7-flash")
        self.assertEqual(effort, "low")

        os.environ["AGY_AUTO_APPROVE_MODEL"] = "gemini-2.5-pro"
        os.environ["AGY_AUTO_APPROVE_EFFORT"] = "high"
        model, effort = eval_guard.get_evaluator_model()
        self.assertEqual(model, "gemini-2.5-pro")
        self.assertEqual(effort, "high")

        custom_prompt = "Custom strict security evaluator rules."
        os.environ["AGY_AUTO_APPROVE_PROMPT"] = custom_prompt
        self.assertEqual(eval_guard.get_evaluator_prompt(), custom_prompt)

    def test_ai_evaluation_fallback_on_error(self):
        if not USE_REAL_AGY:
            def failing_eval(system_prompt: str, prompt: str):
                return "ask", "AI evaluation via agy was unavailable or timed out."
            res = self._run_guard("run_command", {"CommandLine": "unknown_tool --do-stuff"}, override_evaluator=failing_eval)
            self.assertEqual(res.get("decision"), "ask")
            self.assertIn("[agy-auto-approve: REVIEW REQUIRED]", res.get("reason", ""))

    def test_audit_log_written_to_isolated_directory(self):
        self._run_guard("run_command", {"CommandLine": "echo 'Testing isolation'"})
        log_file = os.path.join(self.temp_dir, "auto-approve.log")
        self.assertTrue(os.path.exists(log_file), "Audit log should be written to isolated temp dir")
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("tool=run_command", content)
            self.assertIn("reason=", content)
            self.assertNotIn("overrides=", content)


if __name__ == "__main__":
    if "--real-agy" in sys.argv:
        USE_REAL_AGY = True
        sys.argv.remove("--real-agy")
        print("🚀 Running tests with REAL agy CLI integration...")
    else:
        print("⚡ Running tests with DUMMY AI evaluator (Fast & Isolated, use --real-agy for live test)...")

    unittest.main()
