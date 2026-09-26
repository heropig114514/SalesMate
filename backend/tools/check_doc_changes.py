"""Responsibility: Check backend Python snapshot documentation and locate changed implementations with unchanged explanations.
Implementation: Read Git baselines, staging, or working trees; reuse check_docs and compare module/class/function ASTs.
Relationships: Used by pre-commit and CI; backend Python only, without source execution or external models.

Directory:
- CodeShape: Extract scope-local code structure without documentation or nested declarations.
- CodeShape.visit_Expr: Remove standalone string expressions.
- CodeShape.visit_FunctionDef: Isolate nested function changes.
- CodeShape.visit_AsyncFunctionDef: Isolate nested asynchronous function changes.
- CodeShape.visit_ClassDef: Isolate nested class changes.
- git: Invoke Git read-only with explicit failure diagnostics.
- decode_source: Decode source according to Python encoding declarations.
- snapshot: Read backend Python from a Git tree, index, or working tree.
- records: Extract declaration structure, documentation, and line numbers.
- compare_sources: Identify changed code with unchanged documentation for review.
- inspect_changes: Run complete structural and baseline-difference checks.
- main: Parse CLI arguments and report errors/review items separately.

Variable index:
- REPO_ROOT: Locate the SalesMate repository from the script path.
"""

import argparse
import ast
import copy
import io
from pathlib import Path
import subprocess
import sys
import tokenize

import check_docs

REPO_ROOT = Path(__file__).resolve().parents[2]


# Function: Extract one scope's AST without documentation or nested declarations.
# Logic: generic_visit handles the root; compare inner declarations separately to avoid reporting method edits as class-body edits.
# Constraints: This detects syntax differences, not behavioral equivalence; never expand imports, dynamic calls, or inheritance.
class CodeShape(ast.NodeTransformer):
    # Function: Exclude docstrings and other standalone string expressions.
    # Inputs: `node` is an expression statement.
    # Outputs: None for string statements; otherwise the recursively processed node.
    # Logic: Prevent documentation-only edits from triggering code-change warnings.
    # Constraints: Strings in assignments, returns, and call arguments remain in comparisons.
    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)

    # Function: Exclude nested functions from parent-scope comparisons.
    # Inputs: `node` is a nested function declaration.
    # Outputs: None, removing the declaration from its parent.
    # Logic: Its own record covers signature, decorators, and implementation.
    # Constraints: Current-snapshot structural checks cover added/deleted declarations; do not infer call relationships.
    def visit_FunctionDef(self, node):
        return None

    # Function: Exclude nested asynchronous functions from parent comparisons.
    # Inputs: `node` is an asynchronous declaration.
    # Outputs: None.
    # Logic: Apply the same scope isolation as synchronous functions.
    # Constraints: Never execute or await coroutines.
    def visit_AsyncFunctionDef(self, node):
        return None

    # Function: Exclude nested classes from parent comparisons.
    # Inputs: `node` is a class declaration.
    # Outputs: None.
    # Logic: The class's own record compares bases, decorators, and class attributes.
    # Constraints: Never collect inherited methods without local implementations.
    def visit_ClassDef(self, node):
        return None


# Function: Execute parameterized read-only Git commands.
# Inputs: `repo` is the repository directory; `args` contains separate Git arguments.
# Outputs: Raw stdout bytes; Git failures, unavailability, or timeouts raise ValueError.
# Logic: Avoid the shell; errors identify subcommands without echoing source or Git stderr.
# Constraints: Call sites use read operations only, with a 30-second limit and no automatic retries.
def git(repo, *args):
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Git {args[0]} could not execute ({type(exc).__name__})") from None
    if result.returncode:
        raise ValueError(f"Git {args[0]} failed (exit {result.returncode}); check the repository, baseline commit, and history completeness")
    return result.stdout


# Function: Decode Python source from files or Git blobs.
# Inputs: `raw` contains source bytes.
# Outputs: A source string; callers report encoding errors.
# Logic: tokenize.detect_encoding supports encoding declarations and UTF-8 BOM.
# Constraints: No code execution or external configuration reads.
def decode_source(raw):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding)


# Function: Read a Python source snapshot restricted to backend.
# Inputs: `repo` is the repository; `revision` is a resolved commit hash or None; `staged` selects the index snapshot.
# Outputs: Relative paths mapped to source strings; callers explicitly report read/unmerged-state failures.
# Logic: Use ls-tree for history, ls-files --stage for the index, and include nonignored new files while skipping deletions in the working tree.
# Constraints: Reject symlinks and unmerged Python files; never read .env, agent, or frontend, or modify the index.
def snapshot(repo, revision=None, staged=False):
    files = {}
    if revision or staged:
        entries = git(repo, "ls-tree", "-r", "-z", revision, "--", "backend/") if revision else git(repo, "ls-files", "--stage", "-z", "--", "backend/")
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            path = raw_path.decode("utf-8")
            if not path.endswith(".py") or "__pycache__" in Path(path).parts:
                continue
            mode, middle, last = metadata.decode("ascii").split()
            if mode not in {"100644", "100755"} or (staged and not revision and last != "0"):
                raise ValueError(f"{path}: Index conflict or unsupported file mode; resolve it before checking")
            blob = last if revision else middle
            files[path] = decode_source(git(repo, "cat-file", "blob", blob))
    else:
        paths = git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "backend/")
        for raw_path in set(paths.split(b"\0")) - {b""}:
            path = raw_path.decode("utf-8")
            if not path.endswith(".py") or "__pycache__" in Path(path).parts:
                continue
            target = repo / path
            if target.is_symlink() or not target.resolve().is_relative_to((repo / "backend").resolve()):
                raise ValueError(f"{path}: Linked paths outside the check scope are not allowed")
            if target.exists():
                files[path] = decode_source(target.read_bytes())
    return files


# Function: Build comparable records for modules and implemented declarations.
# Inputs: `source` is Python text.
# Outputs: A dictionary keyed by qualified name/occurrence, containing AST text, normalized documentation, and line number.
# Logic: Retain signatures, decorators, defaults, and scope-local implementations; omit positions, formatting, and nested declarations.
# Constraints: Pair repeated conditional names by occurrence; syntax errors propagate. Do not infer cross-file function moves.
def records(source):
    tree = ast.parse(source)
    inventory = check_docs.Inventory()
    inventory.visit(tree)
    entries = [("<module>", tree), *inventory.declarations]
    result = {}
    counts = {}
    for name, node in entries:
        ordinal = counts.get(name, 0)
        counts[name] = ordinal + 1
        doc = ast.get_docstring(node) or "" if isinstance(node, ast.Module) else check_docs.declaration_doc(node, source.splitlines())
        shape = CodeShape().generic_visit(copy.deepcopy(node))
        result[(name, ordinal)] = (ast.dump(shape, include_attributes=False), " ".join(doc.split()), getattr(node, "lineno", 1))
    return result


# Function: Locate changed implementations with unchanged corresponding documentation.
# Inputs: `before` and `after` are baseline/current source; `path` labels diagnostics.
# Outputs: Review messages, without claiming definite violations.
# Logic: Compare shared declarations only; AST changes with identical documentation request review of defaults, effects, and constraints.
# Constraints: Editing prose does not prove accuracy; structural checks cover additions/deletions without cross-file impact analysis.
def compare_sources(before, after, path):
    previous, current = records(before), records(after)
    reviews = []
    for key in sorted(previous.keys() & current.keys()):
        old_code, old_doc, _ = previous[key]
        code, doc, line = current[key]
        if old_code != code and old_doc == doc:
            reviews.append(f"{path}:{line}: {key[0]} Implementation or signature changed but documentation did not; review inputs, outputs, defaults, side effects, and constraints")
    return reviews


# Function: Check a complete current snapshot against an explicit baseline.
# Inputs: `repo` is the repository; `base` is a baseline ref; `staged` selects the index instead of the working tree.
# Outputs: File count, structural/read errors, and review items.
# Logic: Resolve the baseline commit, read both snapshots, and apply identical structural standards to all current files.
# Constraints: Missing baselines, unreadable files, and empty scans fail explicitly; failed reads never mean no changes.
def inspect_changes(repo, base, staged):
    errors, reviews = [], []
    try:
        revision = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode("ascii").strip()
        previous = snapshot(repo, revision=revision)
        current = snapshot(repo, staged=staged)
        if not current:
            return 0, ["backend/: No Python files to check"], []
        for path, source in sorted(current.items()):
            issues = check_docs.check_source(source, path)
            errors.extend(issues)
            if not issues and path in previous:
                try:
                    reviews.extend(compare_sources(previous[path], source, path))
                except (SyntaxError, ValueError):
                    errors.append(f"{path}: Baseline source cannot be parsed; change comparison is unavailable")
        return len(current), errors, reviews
    except (ValueError, OSError, UnicodeError, SyntaxError, LookupError) as exc:
        # Show safe read diagnostics only; encoding/syntax messages may contain source, so expose only their exception types.
        message = str(exc) if type(exc) is ValueError else f"Snapshot read failed ({type(exc).__name__})"
        return 0, [message], []


# Function: Provide one entry point for working-tree, staging, and CI checks.
# Inputs: `argv` is an argument sequence or None; defaults to comparing the working tree with HEAD.
# Outputs: Return 1 for structural errors, 2 for review items in explicit strict mode, otherwise 0 with review counts.
# Logic: Separate deterministic errors from semantic-review notices; --fail-on-review may enforce the latter.
# Constraints: Default notices do not block valid refactoring; exit 0 indicates structural success, not reviewed semantics.
def main(argv=None):
    parser = argparse.ArgumentParser(description="Check backend documentation structure and changes without executing business code.")
    parser.add_argument("--base", default="HEAD", help="Explicit existing baseline commit; defaults to HEAD")
    parser.add_argument("--staged", action="store_true", help="Check actual staged blobs without working-tree corrections")
    parser.add_argument("--fail-on-review", action="store_true", help="Return nonzero for review items; this does not automate semantic judgment")
    args = parser.parse_args(argv)
    count, errors, reviews = inspect_changes(REPO_ROOT, args.base, args.staged)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    for review in reviews:
        print(f"REVIEW {review}")
    print(f"Backend documentation check: {count} files, {len(errors)} errors, {len(reviews)} review items; natural-language semantics are not automatically verified.")
    return 1 if errors else 2 if reviews and args.fail_on_review else 0


if __name__ == "__main__":
    raise SystemExit(main())
