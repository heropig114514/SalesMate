"""Responsibility: Verify detection of invalid/valid documentation and command-line failures.
Implementation: Construct independent source samples in temporary directories and assert diagnostics/exit codes with unittest; never execute sample code.
Relationships: Calls sibling check_docs using the standard library only, without business databases or real configuration.

Directory:
- module_doc: Build structured module headers for samples.
- declaration: Build preceding declaration comments for samples.
- CheckDocsTests: Filesystem-isolated checker regression tests.
- CheckDocsTests.setUp: Create temporary test directories.
- CheckDocsTests.check_source: Save a sample and run a single-file check.
- CheckDocsTests.test_valid_empty_package: Verify explicit empty package indexes.
- CheckDocsTests.test_missing_module_and_declaration_docs: Reject missing file/declaration documentation.
- CheckDocsTests.test_missing_stale_and_duplicate_indexes: Test missing, stale, duplicate, and purposeless entries.
- CheckDocsTests.test_decorated_async_and_nested_declarations: Cover decorated, asynchronous, and nested declarations.
- CheckDocsTests.test_class_and_module_bindings: Distinguish class/conditional assignments, comprehensions, and function locals.
- CheckDocsTests.test_missing_and_removed_parameters: Check documentation synchronization for added/removed parameter kinds.
- CheckDocsTests.test_missing_sections_and_duplicate_headings: Reject empty and duplicate headings.
- CheckDocsTests.test_docstring_declarations: Accept structured declaration docstrings.
- CheckDocsTests.test_syntax_encoding_and_missing_file: Report syntax, encoding, and read failures.
- CheckDocsTests.test_source_is_not_executed: Parse malicious/side-effectful samples without executing them.
- CheckDocsTests.test_cli_paths_and_exit_codes: Verify success, failure, missing, and empty CLI paths.
- CheckDocsTests.test_cli_default_paths_ignore_working_directory: Verify location-independent default scanning.

Variable index:
- None
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import check_docs


# Function: Construct module documentation for source samples.
# Inputs: `symbols` supplies declaration entries; `variables` supplies variable entries; both default to explicit empty sets.
# Outputs: Source text containing a complete module docstring and newlines.
# Logic: Use fixed nonempty descriptions and insert indexes verbatim under their headings.
# Constraints: Callers may intentionally supply invalid entries to test rejection; no file writes.
def module_doc(symbols="- None", variables="- None"):
    return (
        '"""Responsibility: Test sample.\nImplementation: Static parsing only.\nRelationships: Checker unit tests.\n'
        f'\nDirectory:\n{symbols}\n\nVariable index:\n{variables}\n"""\n\n'
    )


# Function: Construct structured comments preceding a function/class.
# Inputs: `inputs` describes parameters, defaulting to none; `indent` is space indentation; `is_class` omits function input/output headings.
# Outputs: A newline-terminated comment string.
# Logic: Functions receive five headings; classes receive Function, Logic, and Constraints.
# Constraints: Test prose makes no business-behavior claims and must not be reused as actual business documentation.
def declaration(inputs="None.", indent="", is_class=False):
    lines = ["Function: Verify a sample declaration."]
    if not is_class:
        lines.extend([f"Inputs: {inputs}", "Outputs: Sample value."])
    lines.extend(["Logic: Follow the sample statements.", "Constraints: Static parsing only; no execution."])
    return "".join(f"{indent}# {line}\n" for line in lines)


# Function: Verify detection of realistic documentation-maintenance errors.
# Logic: Independent temporary samples exercise acceptance, omissions, stale entries, and failure branches.
# Constraints: Never modify backend/; the framework cleans temporary files, and CLI subprocesses have a 30-second timeout.
class CheckDocsTests(unittest.TestCase):
    # Function: Provide an isolated file directory for each test.
    # Inputs: No external arguments; called by unittest.
    # Outputs: None; set self.root and register cleanup.
    # Logic: Bind a standard-library TemporaryDirectory to the current test lifetime.
    # Constraints: Never reuse project directories or read real .env files.
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="salesmate-docs-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    # Function: Run a file-level check on in-memory source.
    # Inputs: `source` is a complete Python source string.
    # Outputs: The checker's issue list.
    # Logic: Write temporary sample.py and call check_file.
    # Constraints: Overwrite only this test's sample file; never import it.
    def check_source(self, source):
        path = self.root / "sample.py"
        path.write_text(source, encoding="utf-8")
        return check_docs.check_file(path)

    # Function: Verify valid documentation for files without declarations/variables.
    # Inputs: No external arguments.
    # Outputs: None; fail if a valid empty package is rejected.
    # Logic: Write only a module docstring containing two '- None' indexes.
    # Constraints: Empty packages still require module documentation; empty files are not exempt.
    def test_valid_empty_package(self):
        self.assertEqual(self.check_source(module_doc()), [])
        self.assertTrue(self.check_source(""))

    # Function: Ensure missing module/function documentation cannot pass.
    # Inputs: No external arguments.
    # Outputs: None; fail if key omissions are unreported.
    # Logic: Check a function-only source and look for both module and declaration diagnostics.
    # Constraints: Do not fix diagnostic ordering or entire message strings.
    def test_missing_module_and_declaration_docs(self):
        errors = "\n".join(self.check_source("def work():\n    return 1\n"))
        self.assertIn("Module documentation missing nonempty heading: Responsibility", errors)
        self.assertIn("work Missing nonempty documentation: Function", errors)
        self.assertIn("Directory missing: work", errors)

    # Function: Detect renamed declarations, deleted variables, and manual index mistakes.
    # Inputs: No external arguments.
    # Outputs: None; assert failure if any mistake is missed.
    # Logic: Pair an actual work declaration with old names, duplicate entries, and stale variable indexes.
    # Constraints: Samples exercise structural errors, not natural-language semantics.
    def test_missing_stale_and_duplicate_indexes(self):
        cases = [
            ("- old: Old name.", "- None", "Directory missing: work"),
            ("- old: Old name.", "- None", "Directory stale entry: old"),
            ("- work: Purpose.\n- work: Duplicate.", "- None", "Directory duplicate entry: work"),
            ("- work:", "- None", "Directory invalid entry format"),
            ("- work: Purpose.", "- REMOVED: Deleted variable.", "Variable index stale entry: REMOVED"),
        ]
        for symbols, variables, expected in cases:
            with self.subTest(expected=expected):
                source = module_doc(symbols, variables) + declaration() + "def work():\n    return 1\n"
                self.assertIn(expected, "\n".join(self.check_source(source)))

    # Function: Locate preceding documentation around decorators and nested scopes.
    # Inputs: No external arguments.
    # Outputs: None; fail if a valid sample is rejected.
    # Logic: Construct an asynchronous method with a multiline decorator and inner function, indexed by qualified names.
    # Constraints: Undefined decorators do not affect AST checks, demonstrating independence from execution environments.
    def test_decorated_async_and_nested_declarations(self):
        source = module_doc("- Worker: Sample class.\n- Worker.run: Async method.\n- Worker.run.inner: Nested function.")
        source += declaration(is_class=True) + "class Worker:\n"
        source += declaration("`item` is input.", "    ")
        source += "    @decorate(\n        'value'\n    )\n    async def run(self, item):\n"
        source += declaration(indent="        ") + "        def inner():\n            return 1\n        return item\n"
        self.assertEqual(self.check_source(source), [])

    # Function: Verify module/class binding scope boundaries.
    # Inputs: No external arguments.
    # Outputs: None; fail for omitted real variables or incorrectly collected locals.
    # Logic: Combine conditional assignments, unpacking, annotations, loops, comprehensions, walrus expressions, and class attributes.
    # Constraints: Imports, dictionary keys, and instance attributes are outside the index; this test makes no claims about them.
    def test_class_and_module_bindings(self):
        names = ["FLAG", "LEFT", "RIGHT", "TOTAL", "item", "RESULT", "CAPTURE", "VALUES", "Box.LIMIT"]
        source = module_doc("- Box: Class.\n- Box.run: Method.", "\n".join(f"- {name}: Purpose." for name in names))
        source += "import os as imported\nFLAG: bool = True\nif FLAG:\n    LEFT, RIGHT = 1, 2\nTOTAL = 0\n"
        source += "for item in []:\n    TOTAL += item\nRESULT = [n for n in []]\nVALUES = [(CAPTURE := n) for n in []]\n"
        source += declaration(is_class=True) + "class Box:\n    LIMIT = 2\n"
        source += declaration(indent="    ") + "    def run(self):\n        local = 1\n        self.value = local\n        return local\n"
        self.assertEqual(self.check_source(source), [])
        missing = source.replace("- Box.LIMIT: Purpose.\n", "")
        self.assertIn("Variable index missing: Box.LIMIT", "\n".join(self.check_source(missing)))

    # Function: Verify parameter documentation stays synchronized with signatures.
    # Inputs: No external arguments.
    # Outputs: None; fail when missing new parameters or stale removed ones are not detected.
    # Logic: Cover positional-only, ordinary, variadic, keyword-only, and keyword-dictionary arguments with omissions/stale entries.
    # Constraints: Backtick names in Inputs denote actual parameters only; implicit self/cls may be omitted.
    def test_missing_and_removed_parameters(self):
        body = "def work(first, /, second=1, *items, enabled=True, **options):\n    return first\n"
        valid = "`first`, `second`, `items`, `enabled`, `options` are inputs."
        prefix = module_doc("- work: Function.")
        self.assertEqual(self.check_source(prefix + declaration(valid) + body), [])
        errors = self.check_source(prefix + declaration(valid.replace("`enabled`", "flag")) + body)
        self.assertIn("Inputs missing parameter: enabled", "\n".join(errors))
        errors = self.check_source(prefix + declaration(valid + "`removed` was deleted.") + body)
        self.assertIn("Inputs contain stale parameter: removed", "\n".join(errors))

    # Function: Reject headings without content and duplicate headings.
    # Inputs: No external arguments.
    # Outputs: None; fail if formatting errors are missed.
    # Logic: Empty the Logic content and separately duplicate the module Responsibility heading.
    # Constraints: Nonempty content is not proof of factual accuracy.
    def test_missing_sections_and_duplicate_headings(self):
        source = module_doc("- work: Function.") + declaration().replace("Logic: Follow the sample statements.", "Logic:")
        errors = self.check_source(source + "def work():\n    return 1\n")
        self.assertIn("Missing nonempty documentation: Logic", "\n".join(errors))
        errors = self.check_source(module_doc().replace("Implementation:", "Responsibility: Duplicate.\nImplementation:"))
        self.assertIn("Duplicate heading: Responsibility", "\n".join(errors))

    # Function: Verify structured function docstrings satisfy declaration checks.
    # Inputs: No external arguments.
    # Outputs: None; fail if a valid docstring sample is rejected.
    # Logic: Put documentation in the first string expression of the function body without preceding comments.
    # Constraints: Adding business-view docstrings may affect OpenAPI; this test does not authorize contract changes.
    def test_docstring_declarations(self):
        doc = "\n".join(line[2:] for line in declaration().splitlines())
        source = module_doc("- work: Function.") + 'def work():\n    """' + doc.replace("\n", "\n    ") + '\n    """\n    return 1\n'
        self.assertEqual(self.check_source(source), [])

    # Function: Ensure unreadable/unparseable files fail explicitly.
    # Inputs: No external arguments.
    # Outputs: None; fail if any error is treated as success.
    # Logic: Construct invalid syntax, invalid UTF-8 bytes, and nonexistent paths separately.
    # Constraints: Diagnostics report types/locations only, never source lines that may contain secrets.
    def test_syntax_encoding_and_missing_file(self):
        self.assertIn("SyntaxError", "\n".join(self.check_source("def broken(:\n")))
        path = self.root / "bad.py"
        path.write_bytes(b"\xff\xfe\x00")
        self.assertTrue(check_docs.check_file(path))
        self.assertIn("FileNotFoundError", "\n".join(check_docs.check_file(self.root / "missing.py")))

    # Function: Ensure inspected source is never executed.
    # Inputs: No external arguments.
    # Outputs: None; fail if code executes or valid documentation is rejected.
    # Logic: Follow the module header with unconditional RuntimeError; static checking must still pass.
    # Constraints: Verifies isolation from import side effects, not business-code execution correctness.
    def test_source_is_not_executed(self):
        self.assertEqual(self.check_source(module_doc() + "raise RuntimeError('must not execute')\n"), [])

    # Function: Verify CLI aggregation/exit codes do not conceal failures.
    # Inputs: No external arguments.
    # Outputs: None; fail when actual/expected statuses differ.
    # Logic: Capture diagnostics for valid files, empty directories, missing paths, and undocumented directory contents.
    # Constraints: Operate only in the test directory without changing the calling environment or working directory.
    def test_cli_paths_and_exit_codes(self):
        path = self.root / "valid.py"
        path.write_text(module_doc(), encoding="utf-8")
        empty = self.root / "empty"
        empty.mkdir()
        cases = [(path, 0), (empty, 1), (self.root / "missing", 1)]
        for target, expected in cases:
            with self.subTest(target=target), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(check_docs.main([str(target)]), expected)
                self.assertIn("passed" if expected == 0 else "failed", out.getvalue() + err.getvalue())
        (self.root / "bad.py").write_text("", encoding="utf-8")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(check_docs.main([str(self.root)]), 1)
            self.assertIn("bad.py:1", err.getvalue())

    # Function: Verify default scanning when launched outside the project.
    # Inputs: No external arguments.
    # Outputs: None; fail if the subprocess cannot check the project.
    # Logic: Run the absolute checker path with the current interpreter in a temporary working directory.
    # Constraints: Regresses project-wide documentation only; no Django startup or recursive test execution.
    def test_cli_default_paths_ignore_working_directory(self):
        result = subprocess.run(
            [sys.executable, str(Path(check_docs.__file__).resolve())],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Documentation structure check passed", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
