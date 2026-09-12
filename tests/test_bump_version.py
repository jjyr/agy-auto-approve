"""Exercise release preparation in disposable Git repositories."""

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class BumpVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / "scripts").mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src/main.rs").write_text("fn main() {}\n")
        for filename in ["Cargo.toml", "Cargo.lock", "scripts/bump-version.py"]:
            shutil.copyfile(ROOT / filename, self.repo / filename)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("add", ".")
        self.git("commit", "-m", "Initial")
        self.initial = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def bump(self, *args):
        return subprocess.run([sys.executable, str(self.repo / "scripts/bump-version.py"), *args],
                              cwd=self.temp.name, text=True, capture_output=True)

    def test_bump_commit_and_annotated_tag(self):
        result = self.bump("minor")
        self.assertEqual(result.returncode, 0, result.stderr)
        tag = self.git("describe", "--exact-match", "HEAD")
        self.assertEqual(self.git("cat-file", "-t", tag), "tag")
        self.assertEqual(self.git("rev-parse", "HEAD^"), self.initial)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("diff", "--name-only", "HEAD^", "HEAD").splitlines(),
                         ["Cargo.lock", "Cargo.toml"])
        for filename in ["Cargo.toml", "Cargo.lock"]:
            before = self.git("show", f"HEAD^:{filename}")
            after = (self.repo / filename).read_text().strip()
            old = re.search(r'\nversion = "([^"]+)"', before).group(1)
            self.assertEqual(after, before.replace(f'version = "{old}"',
                                                   f'version = "{tag[1:]}"', 1))
        self.assertIn("git push --atomic", result.stdout)

    def test_explicit_version(self):
        result = self.bump("v99.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("describe", "--exact-match"), "v99.0.0")

    def test_patch_and_major(self):
        self.assertEqual(self.bump("99.1.2").returncode, 0)
        for mode, expected in [("patch", "v99.1.3"), ("major", "v100.0.0")]:
            result = self.bump(mode)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.git("describe", "--exact-match"), expected)

    def test_no_argument_defaults_to_patch(self):
        self.assertEqual(self.bump("99.1.2").returncode, 0)
        result = self.bump()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("describe", "--exact-match"), "v99.1.3")

    def test_invalid_or_lower_versions_leave_tree_unchanged(self):
        for version in ["0.0.0", "01.2.3", "1.2.3-beta", "nonsense"]:
            with self.subTest(version=version):
                self.assertNotEqual(self.bump(version).returncode, 0)
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)

    def test_existing_tag_leaves_tree_unchanged(self):
        self.git("tag", "v99.0.0")
        self.assertNotEqual(self.bump("99.0.0").returncode, 0)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dirty_tree_and_detached_head_are_rejected(self):
        extra = self.repo / "untracked.txt"
        extra.write_text("keep me")
        self.assertNotEqual(self.bump("patch").returncode, 0)
        self.assertEqual(extra.read_text(), "keep me")
        extra.unlink()
        self.git("checkout", "--detach")
        self.assertNotEqual(self.bump("patch").returncode, 0)
        self.assertEqual(self.git("status", "--porcelain"), "")
