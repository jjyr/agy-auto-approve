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

    def test_long_command_exact_override(self):
        long_unzip = "unzip -q /tmp/run-32438847930/e2e-failure-32438847930/output/cloudflare-local-e2e/test-results/earnings-share-EARNINGS-SH-39124-removes-the-old-image-and-X/trace.zip -d /tmp/trace-010"
        cmd = f"mkdir -p /tmp/trace-010 && {long_unzip}"
        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                "command(mkdir -p /tmp/trace-010)",
                f"command({long_unzip[:80]})"
            ]
        )

    def test_long_sed_command(self):
        cmd = "gh run view 32507479625 --job 96852760226 --log | sed -n '250,450p'"
        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                "command(gh run view)",
                "command(sed -n '250,450p')"
            ]
        )

    def test_curl_piped_to_wc(self):
        """Test that long curl command with headers piped to wc extracts both sub-commands."""
        cmd = 'curl -s -H "User-Agent: Whale Moat research ops@whalemoat.com" -H "Accept: application/json" "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json" | wc -c'
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(
            extracted,
            [
                'curl -s -H "User-Agent: Whale Moat research ops@whalemoat.com" -H "Accept: application/json" "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"',
                "wc -c"
            ]
        )
        self.assertTrue(extracted[0].startswith("curl"))
        self.assertEqual(extracted[1], "wc -c")

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                f"command({extracted[0][:80]})",
                "command(wc -c)"
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

    def test_start_script_with_multiple_env_vars(self):
        """Test start.sh command with multiple environment variables across lines or space-separated."""
        cmd_multiline = 'REMOVE_OLD_STATE=y\n  PATH="/Users/jjy/.cargo/bin:$PATH"\n  ./tests/nodes/start.sh e2e/lsp'
        extracted = eval_guard.extract_shell_commands(cmd_multiline)
        self.assertEqual(extracted, ["./tests/nodes/start.sh e2e/lsp"])
        self.assertTrue(extracted[0].startswith("./tests/nodes/start.sh"))

        res = self._run_guard("run_command", {"CommandLine": cmd_multiline})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(res.get("permissionOverrides"), ["command(./tests/nodes/start.sh e2e/lsp)"])

        # Also verify with backslash line continuation and single-line format
        cmd_backslash = 'REMOVE_OLD_STATE=y \\\n  PATH="/Users/jjy/.cargo/bin:$PATH" \\\n  ./tests/nodes/start.sh e2e/lsp'
        self.assertEqual(eval_guard.extract_shell_commands(cmd_backslash), ["./tests/nodes/start.sh e2e/lsp"])
        cmd_single = 'REMOVE_OLD_STATE=y PATH="/Users/jjy/.cargo/bin:$PATH" ./tests/nodes/start.sh e2e/lsp'
        self.assertEqual(eval_guard.extract_shell_commands(cmd_single), ["./tests/nodes/start.sh e2e/lsp"])

    def test_wait_script_with_path_env_var(self):
        """Test wait.sh command with PATH environment variable prefix."""
        cmd_multiline = 'PATH="/Users/jjy/.cargo/bin:$PATH"\n  ./tests/nodes/wait.sh'
        extracted = eval_guard.extract_shell_commands(cmd_multiline)
        self.assertEqual(extracted, ["./tests/nodes/wait.sh"])

        res = self._run_guard("run_command", {"CommandLine": cmd_multiline})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(res.get("permissionOverrides"), ["command(./tests/nodes/wait.sh)"])

        # Also verify with single-line format
        cmd_single = 'PATH="/Users/jjy/.cargo/bin:$PATH" ./tests/nodes/wait.sh'
        self.assertEqual(eval_guard.extract_shell_commands(cmd_single), ["./tests/nodes/wait.sh"])

    def test_heredoc_cat_multiline_command(self):
        """Test multiline heredoc with cat writes to file without splitting lines into bogus sub-commands."""
        cmd = """cat << 'EOF' > workers/api/src/home-catalog.ts
import {
  HomeCatalogResponseSchema,
  type HomeCatalogResponse,
  type HomeFocusStockSummary,
}
EOF"""
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(len(extracted), 1)
        self.assertTrue(extracted[0].startswith("cat"))
        self.assertIn("HomeCatalogResponseSchema", extracted[0])

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        # Long heredoc command is truncated to an exact 80-char prefix.
        self.assertEqual(res.get("permissionOverrides"), [f"command({cmd[:80]})"])

    def test_user_example_mkdir_and_echo_command(self):
        """Test command 1: mkdir chained with echo to agent config script path."""
        cmd = "mkdir -p /Users/jjy/.gemini/config/plugins/agy-auto-approve/scripts && echo 'import sys;sys.exit(0)' > /Users/jjy/.gemini/config/plugins/agy-auto-approve/scripts/eval_guard.py"
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(
            extracted,
            [
                "mkdir -p /Users/jjy/.gemini/config/plugins/agy-auto-approve/scripts",
                "echo 'import sys;sys.exit(0)' > /Users/jjy/.gemini/config/plugins/agy-auto-approve/scripts/eval_guard.py"
            ]
        )

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                "command(mkdir -p /Users/jjy/.gemini/config/plugins/agy-auto-approve/scripts)",
                f"command({extracted[1][:80]})"
            ]
        )

    def test_user_example_cat_heredoc_css_command(self):
        """Test command 2: cat heredoc appending multi-line CSS to workspace stylesheet."""
        cmd = """cat << 'EOF' >> apps/public-web/src/styles/public-home.css

/* ==========================================================================
   Whale Moat Focus Stock & Whale Teasers, Starter Packs & Pro Badge
   ========================================================================== */

.stock-teaser-canvas,
.whale-teaser-canvas {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  background: var(--radar-surface, var(--terminal-bg, #0b0f14));
  color: var(--radar-text, #c9d1d9);
  border-radius: 0 !important;
}

.stock-teaser-header,
.whale-teaser-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 1px solid var(--radar-border, #21262d);
  padding-bottom: 12px;
}

.stock-teaser-symbol,
.whale-teaser-manager {
  font-family: var(--font-mono, monospace);
  font-size: 18px;
  font-weight: 700;
  color: var(--radar-text-bright, #f0f6fc);
}

.stock-teaser-name,
.whale-teaser-display {
  font-size: 14px;
  color: var(--radar-text-muted, #8b949e);
  margin-left: 6px;
}

.stock-teaser-badge {
  display: inline-block;
  margin-left: 8px;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  padding: 1px 6px;
  background: rgba(56, 189, 248, 0.15);
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 0 !important;
}

.stock-teaser-metrics,
.whale-teaser-metrics {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  font-family: var(--font-mono, monospace);
  font-size: 13px;
}

.stock-teaser-price {
  font-weight: 700;
  color: var(--radar-text-bright, #f0f6fc);
}

.stock-teaser-qualifier,
.whale-teaser-value {
  color: var(--radar-text-muted, #8b949e);
}

.stock-teaser-section,
.whale-teaser-section {
  border: 1px solid var(--radar-border, #21262d);
  padding: 12px;
  background: var(--radar-surface-subtle, rgba(255, 255, 255, 0.02));
  border-radius: 0 !important;
}

.stock-teaser-section-title,
.whale-teaser-section-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--radar-text-muted, #8b949e);
  margin-bottom: 8px;
}

.stock-teaser-highlights,
.whale-teaser-holdings-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.stock-teaser-highlight-item,
.whale-teaser-holding-item {
  font-size: 13px;
  line-height: 1.4;
  color: var(--radar-text, #c9d1d9);
  display: flex;
  gap: 6px;
}

.stock-teaser-bullet {
  color: #38bdf8;
  font-weight: bold;
}

.whale-holding-symbol {
  font-family: var(--font-mono, monospace);
  font-weight: 700;
  color: var(--radar-text-bright, #f0f6fc);
  min-width: 60px;
}

.whale-holding-weight {
  font-family: var(--font-mono, monospace);
  color: var(--radar-text-muted, #8b949e);
  min-width: 60px;
}

.whale-holding-action.is-increased {
  color: #3fb950;
}
.whale-holding-action.is-decreased {
  color: #f85149;
}
.whale-holding-action.is-new {
  color: #38bdf8;
}
.whale-holding-action.is-unchanged {
  color: #8b949e;
}

.stock-teaser-footer,
.whale-teaser-footer {
  margin-top: 8px;
}

.stock-teaser-cta-button,
.whale-teaser-cta-button {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 10px 16px;
  background: #238636;
  color: #ffffff;
  font-weight: 600;
  font-size: 13px;
  text-decoration: none;
  border-radius: 0 !important;
  border: 1px solid rgba(255, 255, 255, 0.1);
  transition: background 0.15s ease;
}

.stock-teaser-cta-button:hover,
.whale-teaser-cta-button:hover {
  background: #2ea043;
}

.market-starter-packs {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--radar-border, #21262d);
}

.market-starter-pack-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--radar-text-muted, #8b949e);
}

.market-starter-pack-btn {
  background: transparent;
  border: 1px dashed var(--radar-border, #30363d);
  padding: 6px 10px;
  color: var(--radar-text, #c9d1d9);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  border-radius: 0 !important;
}

.market-starter-pack-btn:hover {
  border-color: #38bdf8;
  color: #38bdf8;
}

.radar-user-pro-badge {
  display: inline-block;
  margin-left: 6px;
  font-family: var(--font-mono, monospace);
  font-size: 10px;
  font-weight: 700;
  padding: 0 4px;
  background: #d29922;
  color: #0b0f14;
  border-radius: 0 !important;
}

.market-tape-badge {
  display: inline-block;
  font-size: 10px;
  font-family: var(--font-mono, monospace);
  padding: 1px 4px;
  margin-left: 4px;
  background: rgba(56, 189, 248, 0.1);
  color: #38bdf8;
  border-radius: 0 !important;
}
EOF"""
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0], cmd)

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        # Long heredoc command is truncated to an exact 80-char prefix.
        self.assertEqual(res.get("permissionOverrides"), [f"command({cmd[:80]})"])

    def test_user_example_cat_heredoc_conversion_spec_command(self):
        """Test command: cat heredoc writing e2e conversion spec file."""
        cmd = """cat << 'EOF' > e2e/cloudflare-local/radar-home-conversion.spec.ts
import { expect, test } from "../support/test.js";
import { waitForAppReady } from "../support/terminal.js";

test.describe("Calendar & Web Push Conversion Hooks", () => {
  test("CONVERSION-001: Calendar sync popover button is accessible in Right Pane", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await waitForAppReady(page);

    const rightPane = page.locator(".radar-agenda");
    await expect(rightPane).toBeVisible();

    const calendarBtn = rightPane.locator(".calendar-subscription-trigger, button[aria-
label*='calendar' i]");
    await expect(calendarBtn).toBeVisible();
  });
});
EOF"""
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0], cmd)

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        # Long heredoc command is truncated to an exact 80-char prefix.
        self.assertEqual(res.get("permissionOverrides"), [f"command({cmd[:80]})"])

    def test_heredoc_cat_chained_with_subsequent_command(self):
        """Test multiline heredoc followed by another command."""
        cmd = """cat << 'EOF' > workers/api/src/home-catalog.ts
export const version = "1.0.0";
EOF
npm run build"""
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(len(extracted), 2)
        self.assertTrue(extracted[0].startswith("cat"))
        self.assertEqual(extracted[1], "npm run build")

        res = self._run_guard("run_command", {"CommandLine": cmd})
        self.assertEqual(res.get("decision"), "allow")
        self.assertEqual(
            res.get("permissionOverrides"),
            [
                f"command({extracted[0][:80]})",
                "command(npm run build)"
            ]
        )

    def test_heredoc_syntax_variations(self):
        """Test different heredoc delimiter styles: unquoted, double-quoted, tab-stripped (<<-)."""
        # Unquoted delimiter
        cmd_unquoted = "cat << EOF > file1.txt\nline 1\nEOF"
        ext1 = eval_guard.extract_shell_commands(cmd_unquoted)
        self.assertEqual(len(ext1), 1)
        self.assertTrue(ext1[0].startswith("cat"))

        # Double quoted delimiter
        cmd_double = 'cat << "MY_DELIM" > file2.txt\nline 2\nMY_DELIM'
        ext2 = eval_guard.extract_shell_commands(cmd_double)
        self.assertEqual(len(ext2), 1)
        self.assertTrue(ext2[0].startswith("cat"))

        # Tab-stripped <<- delimiter
        cmd_tab = "cat <<- 'EOF' > file3.txt\n\tline 3\n\tEOF"
        ext3 = eval_guard.extract_shell_commands(cmd_tab)
        self.assertEqual(len(ext3), 1)
        self.assertTrue(ext3[0].startswith("cat"))

    def test_heredoc_chained_with_subsequent_commands(self):
        """Test multiline heredoc followed by subsequent commands on next lines."""
        cmd = """cat << 'EOF' > config.json
{"key": "value"}
EOF
echo "saved" """
        extracted = eval_guard.extract_shell_commands(cmd)
        self.assertEqual(len(extracted), 2)
        self.assertTrue(extracted[0].startswith("cat"))
        self.assertEqual(extracted[1], 'echo "saved"')

    def test_model_and_prompt_customization(self):
        model, effort = eval_guard.get_evaluator_model()
        self.assertEqual(model, "gemini-3.7-flash")
        self.assertEqual(effort, "medium")

        os.environ["AGY_AUTO_APPROVE_MODEL"] = "gemini-2.5-pro"
        os.environ["AGY_AUTO_APPROVE_EFFORT"] = "high"
        model, effort = eval_guard.get_evaluator_model()
        self.assertEqual(model, "gemini-2.5-pro")
        self.assertEqual(effort, "high")

        # Test workspace effort config file
        del os.environ["AGY_AUTO_APPROVE_EFFORT"]
        os.makedirs(".agents", exist_ok=True)
        try:
            with open(".agents/agy-auto-approve-effort.txt", "w", encoding="utf-8") as f:
                f.write("low")
            self.assertEqual(eval_guard.get_evaluator_effort(), "low")
        finally:
            if os.path.exists(".agents/agy-auto-approve-effort.txt"):
                os.remove(".agents/agy-auto-approve-effort.txt")

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
