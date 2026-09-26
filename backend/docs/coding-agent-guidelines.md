# Code Documentation and Consistency Checks

This document implements the Coding Agent development principles in the repository README. The checker covers every Python file below SalesMate/backend, including tools, tests, migrations, and empty package initializers. It ignores only __pycache__ directories, uses only the Python standard library, does not import application modules or read .env, and does not require a database.

## 1. File-Level Documentation

Every module docstring must contain non-empty Responsibility, Implementation, Relationships, Directory, and Variable index sections. Preserve required shebangs and encoding declarations; the module docstring remains the first Python statement.

    """Responsibility: Provide a pure integer-addition example.
    Implementation: Add the two integers directly and return the computed value.
    Relationships: A standalone example with no business-storage dependency.

    Directory:
    - add: Return the sum of two integers.

    Variable index:
    - None
    """

Directory lists every function, async function, class, method, and nested declaration actually defined in the file. Use dotted qualified names such as CurrentUserSerializer.Meta and ReadinessView.get. Do not list inherited methods that are not overridden, API paths, or imported types as declarations in this file.

Variable index lists names assigned at module or class scope, including ordinary assignments, annotated assignments, unpacking assignments, and bindings in conditional or loop bodies. Qualify class variables. For dictionary configuration, index the top-level variable and explain important configuration purpose, source, default, and boundary.

The checker does not index import aliases, function-local variables, instance attributes, dictionary keys, exception aliases, dynamically created names, or local comprehension iteration variables. If they affect an interface or behavior, explain them in the relevant implementation description or a nearby key-block comment. Outer bindings from assignment expressions are indexed as AST assignment targets.

Index entries use the fixed form - name: purpose, and purpose cannot be empty. Where no symbol exists, write - None; do not omit the heading. Maintain the index whenever a symbol is added, renamed, or removed. Do not generate meaningless purpose text.

## 2. Declaration and Key-Block Documentation

Functions and methods must explain Function, Inputs, Outputs, Logic, and Constraints; classes must at least explain Function, Logic, and Constraints. A declaration comment must be adjacent to its declaration. When decorators exist, place the comment before the first decorator.

    # Function: Compute the sum of two integers.
    # Inputs: left and right are integer operands.
    # Outputs: Return their integer sum.
    # Logic: Use Python integer addition directly and introduce no extra state.
    # Constraints: Callers provide integers. This function performs no runtime type
    # validation and has no external side effects.
    def add(left: int, right: int) -> int:
        return left + right

Inputs identifies actual parameter names, including positional-only, keyword-only, and variadic parameters. Implicit self and cls may be omitted. Do not identify types or environment variables as parameters. A declaration without external parameters must state No external parameters and explain the environment, instance state, or other implicit input it reads.

A structured docstring may serve as declaration documentation. Django REST Framework and schema tooling can use class or method docstrings to generate API descriptions, so backend declarations normally use preceding comments and retain existing docstrings. Before changing a docstring, verify that the API contract does not change. Where consecutive comments precede a declaration, the checker inspects that block first.

Documentation must explain rationale and dependencies, particularly database reads and writes, state and version checks, missing input, authorization preconditions, exception-capture scope, and logging side effects. Add nearby rationale for sensitive fields excluded from logs, test mock boundaries, and configuration precedence. Do not describe unimplemented functionality as available or claim that static-check success proves business behavior.

## 3. Check Commands and Boundaries

From SalesMate/backend in the project Python environment, run:

    python tools/check_docs.py
    python tools/test_check_docs.py

Run the second command whenever the checker changes. An individual file may be checked explicitly, but the default full check remains required before delivery:

    python tools/check_docs.py common/views.py

The default scan directory is determined from the script location and does not depend on the startup directory. Explicit relative paths resolve from the current working directory. A valid check returns 0; problems, missing paths, an empty scan directory, unreadable source, or syntax errors return 1; invalid command-line arguments use argparse exit code 2.

Automated checks cover module and declaration section structure and non-empty content; missing, duplicate, stale, or purposeless directory and variable-index entries; coverage of current function parameters and removal of stale names; and placement around decorators, async declarations, and nested declarations. Tests, migrations, and empty packages have no exemption.

Automation cannot establish that prose is true, defaults are correct, return values and exceptions are fully explained, or all key blocks have adequate reasoning. It cannot prove Git commit atomicity. A change to defaults, types, return behavior, or state logic may pass structural checks when names remain unchanged, so reviewers must still synchronize and assess documentation manually.

### 3.1 Code-and-Documentation Change Checks

From SalesMate/backend, run:

    python tools/check_doc_changes.py
    python tools/check_doc_changes.py --staged
    python tools/check_doc_changes.py --base origin/main
    python tools/test_check_doc_changes.py

The default comparison is working tree versus HEAD and includes unignored new Python files. --staged reads complete staged blobs and does not substitute unstaged repairs. Scope is backend Python, including tools, tests, and migrations; it excludes agent, JS/CSS/HTML, and .env. Unlike a diff-only check, every current Python file must pass structural rules.

The change checker compares function signatures, defaults, annotations, decorators, bodies, class attributes, and module configuration. Whitespace, line endings, or prose-only changes do not trigger an implementation change. Nested declarations are compared independently, avoiding duplicate requirements for an unchanged class when only a method changes. Conditional declarations with the same qualified name are paired by occurrence; renames, additions, and removals remain constrained by current directory structure rather than inferred cross-file moves or call chains.

- ERROR: A deterministic structural, source-reading, or Git error; exit code 1 and repair are required.
- REVIEW: Implementation changed but corresponding documentation did not; it is reported by default and does not automatically reject a valid refactor.
- --fail-on-review: Explicitly turns review findings into a gate and returns 2 when any exist. No automatic confirmation or exemption record is provided.
- With no structural error and no strict-review option, the command returns 0 while reporting review count and the semantic-verification limitation. argparse argument errors also return 2, distinguished by diagnostic text.

The base must be an existing commit. An invalid ref, shallow clone without history, decoding failure, unresolved staged conflict, or empty scan fails. A repository without HEAD uses check_docs.py for its first commit and then uses the change entry point. Git calls are read-only, each is limited to 30 seconds, failures are not retried, and the checker never modifies source, index, or commits.

A one-word documentation edit does not demonstrate accuracy. The absence of REVIEW does not prove prose truth, complete exception coverage, or all side effects. Synchronize relevant prose during modification; for behavioral commitments, run existing business tests. This tool supplies focused review evidence and does not replace the guideline.

### 3.2 Commit and CI Automation

The repository-root .pre-commit-config.yaml provides the backend-docs hook. Developers can install it in an isolated tool environment without modifying Django or Agent dependencies:

    python -m venv .docs-tools/.venv
    .docs-tools/.venv/Scripts/python.exe -m pip install -r backend/requirements/docs.txt
    .docs-tools/.venv/Scripts/python.exe -m pre_commit install
    .docs-tools/.venv/Scripts/python.exe -m pre_commit run backend-docs --all-files

On Linux/macOS use .docs-tools/.venv/bin/python. The environment is Git-ignored and outside backend, preventing third-party packages from entering the full Python-comment scan. Committing the pre-commit configuration does not install hooks for collaborators; each clone must install them. The hook checks the staged snapshot: structural errors block a commit and review findings appear in output. New files must be staged before hook coverage; use the default working-tree command for unstaged changes.

The backend-docs GitHub workflow runs the full structural check, both checker tests, and change reporting on pull requests, pushes to main/master, and manual dispatch. Pull requests compare their target-branch base to the tested merge result; pushes compare the preceding commit; initial pushes and manual runs self-compare when no change base exists and therefore guarantee only structural validity. It has no path filters, avoiding a required check that waits indefinitely; checker scope remains backend Python.

To enforce it remotely, mark backend-docs as required in GitHub branch rules. Adding the workflow alone does not enable branch protection or prevent privileged bypass. A local hook can be bypassed; CI performs independent verification. The project does not use a model service to upload code for semantic review.

## 4. Synchronized Modification and Delivery

One logical change updates implementation, declaration documentation, the file-level index, and affected interface documentation together. After deletion, search for and remove stale symbol references. Before delivery, verify Function, Inputs, Outputs, implementation rationale, state, and side effects; run documentation checks and business tests appropriate to the change.

A documentation-only cleanup must also verify that API schemas, migrations, configuration, and executable code were not accidentally altered. Changes to the checker must pass violation-sample tests rather than merely accepting current repository samples.

When committing, implementation, comments, and directory entries must be in the same commit; do not deliver implementation and postpone its explanation. The checker does not commit and cannot infer atomicity from the staged snapshot. If work has not been committed, state that fact accurately and do not claim atomic-commit verification.
