"""Responsibility: Verify Git snapshot isolation, scope comparisons, and failure semantics in documentation-change checks.
Implementation: Temporary Git repositories and static samples cover staging, working trees, and CLI exit codes.
Relationships: Calls check_doc_changes and the original checker test generators; no business databases or real credentials.

Directory:
- ChangeTests: Change-checker regression tests.
- ChangeTests.setUp: Create a temporary repository with a baseline commit.
- ChangeTests.write: Write temporary source.
- ChangeTests.inspect: Inspect the temporary repository snapshot.
- ChangeTests.test_body_signature_and_decorator_changes: Detect implementation, default, and decorator changes.
- ChangeTests.test_format_and_doc_only_changes: Avoid review notices for formatting/documentation-only edits.
- ChangeTests.test_updated_docs_and_nested_scopes: Verify synchronized documentation and parent/child isolation.
- ChangeTests.test_module_and_duplicate_definitions: Detect module configuration and repeated declarations.
- ChangeTests.test_staged_snapshot_is_authoritative: Prevent unstaged corrections from concealing staged errors.
- ChangeTests.test_untracked_and_deleted_files: Verify new/deleted files and scope boundaries.
- ChangeTests.test_bad_baseline_and_encoding: Fail explicitly on read errors.
- ChangeTests.test_unmerged_index: Reject Python files with unmerged stages.
- ChangeTests.test_cli_exit_codes: Verify advisory, strict, and error exit codes.

Variable index:
- None
"""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_doc_changes as changes
from test_check_docs import declaration, module_doc


# Function: Test documentation checks using real temporary Git indexes rather than mocking core Git behavior.
# Logic: Each case creates a separate baseline, then changes working-tree/staged blobs independently.
# Constraints: Modify temporary directories only; supply commit identity per command without changing user Git configuration.
class ChangeTests(unittest.TestCase):
    # Function: Create an independently editable repository and valid initial source.
    # Inputs: No external arguments; called by unittest.
    # Outputs: None; establish repo, source, and path state; clean all repositories after the test class.
    # Logic: Keep separate repositories per case, delaying cleanup to avoid immediate Windows deletion after Git operations.
    # Constraints: Never read the actual repository's .env; Git failures fail tests directly.
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="salesmate-doc-change-")
        self.addClassCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        changes.git(self.repo, "init", "-q")
        changes.git(self.repo, "config", "core.hooksPath", str(self.repo / "empty-hooks"))
        (self.repo / "backend").mkdir()
        self.path = self.repo / "backend" / "sample.py"
        self.source = module_doc("- work: Sample function.") + declaration("`value` is input.") + "def work(value=1):\n    return value\n"
        self.write(self.source)
        changes.git(self.repo, "add", "backend/sample.py")
        changes.git(self.repo, "-c", "user.name=Docs Test", "-c", "user.email=docs@example.invalid", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "commit", "-qm", "baseline")

    # Function: Save temporary sample source.
    # Inputs: `source` is sample text.
    # Outputs: None; overwrite this test's sample.py.
    # Logic: Use UTF-8; individual cases explicitly decide staging.
    # Constraints: Never write production source.
    def write(self, source):
        self.path.write_text(source, encoding="utf-8")

    # Function: Run CLI-equivalent checks on the test repository.
    # Inputs: `staged` selects the index; defaults to the working tree.
    # Outputs: File count, errors, and review lists.
    # Logic: Compare against this case's HEAD.
    # Constraints: No model calls or business-code execution.
    def inspect(self, staged=False):
        return changes.inspect_changes(self.repo, "HEAD", staged)

    # Function: Detect changed defaults, decorators, and bodies even with unchanged parameter names.
    # Inputs: No external arguments.
    # Outputs: None; each edit must produce exactly one work review item.
    # Logic: Change return expressions, defaults, and decorators independently without editing documentation.
    # Constraints: Decorators need not be defined because source is only parsed.
    def test_body_signature_and_decorator_changes(self):
        for source in [self.source.replace("return value", "return value + 1"), self.source.replace("value=1", "value=2"), self.source.replace("def work", "@atomic\ndef work")]:
            with self.subTest(source=source):
                self.write(source)
                _, errors, reviews = self.inspect()
                self.assertEqual(errors, [])
                self.assertEqual(len(reviews), 1)
                self.assertIn("work Implementation or signature changed", reviews[0])

    # Function: Ensure formatting/docstring-only edits are not reported as code changes.
    # Inputs: No external arguments.
    # Outputs: None; expect no structural errors or review items.
    # Logic: Edit whitespace, prose, and standalone strings without changing executable expressions.
    # Constraints: This does not prove revised prose is semantically correct.
    def test_format_and_doc_only_changes(self):
        for source in [self.source.replace("value=1", "value = 1"), self.source.replace("sample declaration", "current sample declaration"), self.source.replace("    return value", '    """sample documentation"""\n    return value')]:
            self.write(source)
            self.assertEqual(self.inspect()[1:], ([], []))

    # Function: Verify independent scope comparisons and synchronized documentation.
    # Inputs: No external arguments.
    # Outputs: None; method changes identify that method only, and revised documentation clears the notice.
    # Logic: Directly compare classes, methods, and inner functions, changing inner/outer implementations separately.
    # Constraints: Tests the difference algorithm; other tests cover structural completeness independently.
    def test_updated_docs_and_nested_scopes(self):
        source = 'class Worker:\n    """class docs"""\n    def run(self):\n        """run docs"""\n        def inner():\n            """inner docs"""\n            return 1\n        return inner()\n'
        after = source.replace("return 1", "return 2")
        reviews = changes.compare_sources(source, after, "sample.py")
        self.assertEqual(len(reviews), 1)
        self.assertIn("Worker.run.inner", reviews[0])
        self.assertEqual(changes.compare_sources(source, after.replace("inner docs", "returns two"), "sample.py"), [])

    # Function: Locate module configuration edits and repeated conditional declarations.
    # Inputs: No external arguments.
    # Outputs: None; the module and both same-named functions produce separate review items.
    # Logic: Change constants and branch return values together to prevent qualified-name dictionaries from hiding duplicates.
    # Constraints: Reordered branches pair by declaration order and may need human refactoring review.
    def test_module_and_duplicate_definitions(self):
        source = '"""module docs"""\nLIMIT = 1\nif FLAG:\n    def work():\n        return 1\nelse:\n    def work():\n        return 1\n'
        reviews = changes.compare_sources(source, source.replace("= 1", "= 2").replace("return 1", "return 2"), "sample.py")
        self.assertEqual(len(reviews), 3)
        self.assertTrue(any("<module>" in item for item in reviews))

    # Function: Ensure working-tree corrections cannot hide undocumented staged source.
    # Inputs: No external arguments.
    # Outputs: None; staged checks fail while working-tree checks pass.
    # Logic: Stage undocumented source, then restore valid source only in the working tree.
    # Constraints: Preserve the staged blob before/after inspection.
    def test_staged_snapshot_is_authoritative(self):
        self.write("def work():\n    return 2\n")
        changes.git(self.repo, "add", "backend/sample.py")
        self.write(self.source)
        before = changes.git(self.repo, "show", ":backend/sample.py")
        self.assertTrue(self.inspect(staged=True)[1])
        self.assertEqual(self.inspect()[1:], ([], []))
        self.assertEqual(changes.git(self.repo, "show", ":backend/sample.py"), before)

    # Function: Verify new-file checks, deleted-file skipping, and agent scope exclusion.
    # Inputs: No external arguments.
    # Outputs: None; undocumented new files fail, agent is excluded, and empty backend scans fail.
    # Logic: Create a path containing spaces and Unicode characters, then delete all backend Python files.
    # Constraints: Use NUL-delimited Git paths without depending on shell escaping.
    def test_untracked_and_deleted_files(self):
        new = self.repo / "backend" / "\u65b0 \u6587\u4ef6.py"
        new.write_text("def undocumented(): pass\n", encoding="utf-8")
        (self.repo / "agent").mkdir()
        (self.repo / "agent" / "ignored.py").write_text("bad syntax", encoding="utf-8")
        count, errors, _ = self.inspect()
        self.assertEqual(count, 2)
        self.assertTrue(any("\u65b0 \u6587\u4ef6.py" in item for item in errors))
        self.assertEqual(self.inspect(staged=True)[1:], ([], []))
        new.unlink()
        self.path.unlink()
        self.assertIn("No Python files to check", "".join(self.inspect()[1]))

    # Function: Ensure invalid baselines, encoding, and damaged history cannot pass.
    # Inputs: No external arguments.
    # Outputs: None; every failure produces diagnostics.
    # Logic: Use nonexistent refs and invalid bytes, then separately verify historical parsing failure.
    # Constraints: Never include source bytes in errors.
    def test_bad_baseline_and_encoding(self):
        self.assertTrue(changes.inspect_changes(self.repo, "missing-ref", False)[1])
        self.path.write_bytes(b"\xff")
        self.assertTrue(self.inspect()[1])
        with self.assertRaises(SyntaxError):
            changes.compare_sources("def broken(:", self.source, "sample.py")

    # Function: Reject unresolved merge conflicts.
    # Inputs: No external arguments.
    # Outputs: None; nonzero index stages must raise errors.
    # Logic: Mock a real nonzero-stage ls-files record, replacing only the listing command's output.
    # Constraints: Never create production merges or weaken staging rules.
    def test_unmerged_index(self):
        with patch.object(changes, "git", return_value=b"100644 abc123 2\tbackend/sample.py\0"):
            with self.assertRaisesRegex(ValueError, "Index conflict"):
                changes.snapshot(self.repo, staged=True)

    # Function: Verify different exit codes for advisory, explicit strict-review, and structural-failure modes.
    # Inputs: No external arguments.
    # Outputs: None; expect 0, 2, and 1 respectively.
    # Logic: Replace the CLI repository path only and read actual temporary Git history.
    # Constraints: Exit-0 output must still state that natural-language semantics are unverified.
    def test_cli_exit_codes(self):
        self.write(self.source.replace("return value", "return value + 1"))
        with patch.object(changes, "REPO_ROOT", self.repo), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(changes.main([]), 0)
            self.assertEqual(changes.main(["--fail-on-review"]), 2)
            self.assertEqual(changes.main(["--base", "missing-ref"]), 1)
        self.assertIn("natural-language semantics are not automatically verified", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
