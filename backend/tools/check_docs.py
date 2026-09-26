"""Responsibility: Statically check Python declaration comments, declaration directories, and variable indexes without importing business modules.
Implementation: Parse AST symbols and compare structured documentation; aggregate issues and return nonzero to prevent false success.
Relationships: Check all Python files under backend/ by default; see docs/coding-agent-guidelines.md for the format.

Directory:
- Inventory: Visit AST declarations and assignment names.
- Inventory.__init__: Initialize scope, declaration, and variable collections.
- Inventory.qualified: Qualify names using the current lexical scope.
- Inventory.visit_ClassDef: Record a class and enter its scope.
- Inventory.visit_FunctionDef: Record a function and enter its scope.
- Inventory.visit_AsyncFunctionDef: Apply the same rules to asynchronous functions.
- Inventory.visit_Name: Collect assignments in module or class scopes.
- sections: Parse structured documentation with fixed headings.
- check_index: Validate index formatting, duplicates, missing names, and stale entries.
- declaration_doc: Read preceding comments or the declaration's own docstring.
- check_source: Apply identical structural rules to working-tree and Git source snapshots.
- check_file: Check one file and return all located issues.
- main: Parse paths, scan files, report issues, and return an exit code.

Variable index:
- PROJECT_ROOT: Locate the backend software root from this script, independently of the working directory.
- MODULE_SECTIONS: Required module-documentation headings.
- DECLARATION_SECTIONS: Allowed declaration headings; functions require all of them.
"""

import argparse
import ast
from pathlib import Path
import re
import sys
import tokenize

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_SECTIONS = ("Responsibility", "Implementation", "Relationships", "Directory", "Variable index")
DECLARATION_SECTIONS = ("Function", "Inputs", "Outputs", "Logic", "Constraints")


# Function: Collect implemented declarations and module/class assignment names.
# Logic: Maintain lexical scope, using dotted declaration names; exclude function locals from variable indexes.
# Constraints: Never execute code or enumerate inherited members, imports, attributes, or dictionary keys; merge names repeated across conditional branches.
class Inventory(ast.NodeVisitor):
    # Function: Create independent state for one file scan.
    # Inputs: No external arguments.
    # Outputs: None; initialize scope, declarations, and variables instance attributes.
    # Logic: Track scope type/name on a stack, retain declaration AST nodes, and deduplicate variables with a set.
    # Constraints: Create a fresh instance per file to avoid mixing module symbols.
    def __init__(self):
        self.scope = []
        self.declarations = []
        self.variables = set()

    # Function: Generate a qualified index name.
    # Inputs: `name` is the current declaration or variable name.
    # Outputs: A dotted scope-path string.
    # Logic: Join enclosing class/function names and the current name in order.
    # Constraints: Do not resolve runtime aliases or inheritance.
    def qualified(self, name):
        return ".".join([entry[1] for entry in self.scope] + [name])

    # Function: Record a class and scan its implementation.
    # Inputs: `node` is a ClassDef AST node.
    # Outputs: None; update declarations, variables, and temporary scope state.
    # Logic: Record the outer qualified name, push class scope, visit the body, then pop.
    # Constraints: Visit the body only; decorators and base expressions are not this class's declaration directory.
    def visit_ClassDef(self, node):
        self.declarations.append((self.qualified(node.name), node))
        self.scope.append(("class", node.name))
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    # Function: Record a function/method and find nested declarations.
    # Inputs: `node` is a FunctionDef or structurally compatible AsyncFunctionDef.
    # Outputs: None; update declarations and temporary scope state.
    # Logic: Visit the body in function scope so ordinary locals do not enter module indexes.
    # Constraints: Do not scan decorators/default expressions as the body; nested classes still collect their own class variables.
    def visit_FunctionDef(self, node):
        self.declarations.append((self.qualified(node.name), node))
        self.scope.append(("function", node.name))
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    # Function: Apply synchronous declaration rules to asynchronous declarations.
    # Inputs: `node` is an AsyncFunctionDef.
    # Outputs: None; update the same symbol inventory.
    # Logic: Delegate scope management to visit_FunctionDef.
    # Constraints: Syntax analysis only; never start or await coroutines.
    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    # Function: Record module/class variable bindings.
    # Inputs: `node` is a Name AST node.
    # Outputs: None; add qualified names to variables when applicable.
    # Logic: Collect Store contexts, including assignments, unpacking, annotations, and loop bindings.
    # Constraints: Comprehension bindings have separate scopes and are excluded during check_source preparation.
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store) and (not self.scope or self.scope[-1][0] == "class"):
            self.variables.add(self.qualified(node.id))


# Function: Extract content under specified documentation headings.
# Inputs: `text` is documentation; `headings` is the allowed heading sequence.
# Outputs: Headings mapped to stripped content; duplicate headings or unclassified introductory text raise ValueError.
# Logic: Recognize English headings followed by ASCII colons; subsequent lines belong to the latest heading.
# Constraints: No natural-language semantic validation; headings must start the line and cannot repeat.
def sections(text, headings):
    result = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^(" + "|".join(map(re.escape, headings)) + r"):(.*)$", line)
        if match:
            current = match.group(1)
            if current in result:
                raise ValueError(f"Duplicate heading: {current}")
            result[current] = [match.group(2).strip()]
        elif line:
            if current is None:
                raise ValueError("Unclassified text before the first heading")
            result[current].append(line)
    return {key: "\n".join(value).strip() for key, value in result.items()}


# Function: Compare manually maintained indexes with AST symbol sets.
# Inputs: `text` is index content; `expected` is the actual name set; `label` identifies the index in diagnostics.
# Outputs: Issue strings; an empty list means structural agreement.
# Logic: Parse names/purposes by line, compare sets, and report duplicates/format errors separately.
# Constraints: Empty indexes must say '- None'; purpose accuracy still requires human review.
def check_index(text, expected, label):
    names = set()
    errors = []
    if text.strip() == "- None":
        lines = []
    else:
        lines = text.splitlines()
    for line in lines:
        match = re.fullmatch(r"- ([\w.]+):\s*(\S.*)", line)
        if not match:
            errors.append(f"{label} invalid entry format; expected '- name: purpose': {line}")
            continue
        name = match.group(1)
        if name in names:
            errors.append(f"{label} duplicate entry: {name}")
        names.add(name)
    for name in sorted(expected - names):
        errors.append(f"{label} missing: {name}")
    for name in sorted(names - expected):
        errors.append(f"{label} stale entry: {name}")
    return errors


# Function: Retrieve structured implementation documentation for a function/class.
# Inputs: `node` is a declaration; `lines` contains the source lines.
# Outputs: Consecutive preceding comment content, or the declaration docstring if absent.
# Logic: Scan backward from the earliest decorator/declaration and remove comment prefixes.
# Constraints: No blank line may separate comments from decorators/declarations; existing docstrings remain untouched.
def declaration_doc(node, lines):
    start = min([node.lineno] + [item.lineno for item in node.decorator_list]) - 1
    comments = []
    for line in reversed(lines[:start]):
        if not line.lstrip().startswith("#"):
            break
        comments.append(line.lstrip()[1:].lstrip())
    if comments:
        return "\n".join(reversed(comments))
    return ast.get_docstring(node) or ""


# Function: Check documentation structure and symbol synchronization for one Python file.
# Inputs: `path` is the file Path.
# Outputs: Located issues with file/line information, or an empty list.
# Logic: Read using the source encoding and delegate to check_source, keeping Git snapshots under identical rules.
# Constraints: Report read/encoding/syntax failures explicitly; never import modules or claim semantic truth or Git atomicity.
def check_file(path):
    try:
        with tokenize.open(path) as source_file:
            source = source_file.read()
    except (OSError, UnicodeError, SyntaxError, LookupError) as exc:
        return [f"{path}:{getattr(exc, 'lineno', None) or 1}: Cannot read or parse source ({type(exc).__name__}); check encoding, permissions, and syntax"]
    return check_source(source, path)


# Function: Compare in-memory documentation structure with actual declarations.
# Inputs: `source` is decoded Python source; `path` is a diagnostic label only.
# Outputs: Located issues; an empty list means the structural check passed.
# Logic: Statically parse the AST and check module indexes, declaration headings, and input parameters without reading the labeled path.
# Constraints: Never execute source; syntax errors return diagnostics. Documentation accuracy and Git atomicity are outside this check.
def check_source(source, path):
    try:
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        return [f"{path}:{getattr(exc, 'lineno', None) or 1}: Cannot parse source ({type(exc).__name__})"]

    inventory = Inventory()
    # Comprehension iteration variables do not leak into module/class namespaces; preserve original positions while scanning other nodes.
    # Walrus expressions may bind outer variables, so retain NamedExpr nodes for scanning.
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            for name in ast.walk(node.target):
                if isinstance(name, ast.Name):
                    name.ctx = ast.Load()
    inventory.visit(tree)
    errors = []
    try:
        module = sections(ast.get_docstring(tree) or "", MODULE_SECTIONS)
    except ValueError as exc:
        errors.append(f"{path}:1: Invalid module documentation format: {exc}")
        module = {}
    for heading in MODULE_SECTIONS:
        if not module.get(heading):
            errors.append(f"{path}:1: Module documentation missing nonempty heading: {heading}")
    for label, expected in (("Directory", {name for name, _ in inventory.declarations}), ("Variable index", inventory.variables)):
        errors.extend(f"{path}:1: {error}" for error in check_index(module.get(label, ""), expected, label))

    for name, node in inventory.declarations:
        location = f"{path}:{node.lineno}: {name}"
        try:
            doc = sections(declaration_doc(node, source.splitlines()), DECLARATION_SECTIONS)
        except ValueError as exc:
            errors.append(f"{location} Invalid declaration documentation format: {exc}")
            doc = {}
        required = ("Function", "Logic", "Constraints") if isinstance(node, ast.ClassDef) else DECLARATION_SECTIONS
        for heading in required:
            if not doc.get(heading):
                errors.append(f"{location} Missing nonempty documentation: {heading}")
        if not isinstance(node, ast.ClassDef):
            args = node.args
            parameters = args.posonlyargs + args.args + args.kwonlyargs
            parameters += [arg for arg in (args.vararg, args.kwarg) if arg is not None]
            documented = set(re.findall(r"`([A-Za-z_]\w*)`", doc.get("Inputs", "")))
            actual = {parameter.arg for parameter in parameters}
            for parameter in parameters:
                if parameter.arg not in {"self", "cls"} and f"`{parameter.arg}`" not in doc.get("Inputs", ""):
                    errors.append(f"{location} Inputs missing parameter: {parameter.arg} (use backticks)")
            for parameter in sorted(documented - actual):
                errors.append(f"{location} Inputs contain stale parameter: {parameter}")
    return errors


# Function: Run read-only documentation checks with development-workflow exit codes.
# Inputs: `argv` is an optional argument sequence; None reads process arguments.
# Outputs: Return 0 on success or 1 for issues; argparse retains exit 2 for argument errors.
# Logic: Scan backend including tools by default; explicit paths may select Python files or directories.
# Constraints: Missing paths, empty directories, and non-Python files fail; never generate documentation or modify source automatically.
def main(argv=None):
    parser = argparse.ArgumentParser(description="Check Python declaration documentation, directories, and variable indexes; semantics still require human review.")
    parser.add_argument("paths", nargs="*", type=Path, help="Optional Python files/directories; defaults to backend/ including tools/")
    args = parser.parse_args(argv)
    paths = args.paths or [PROJECT_ROOT]
    files = set()
    errors = []
    for path in paths:
        if path.is_dir():
            found = {p.resolve() for p in path.rglob("*.py") if "__pycache__" not in p.parts}
            if not found:
                errors.append(f"{path}:1: No Python files in directory; check the scan path")
            files.update(found)
        elif path.is_file() and path.suffix == ".py":
            files.add(path.resolve())
        else:
            errors.append(f"{path}:1: Path is missing or is not a Python file/directory")
    for path in sorted(files):
        errors.extend(check_file(path))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"Documentation check failed: {len(files)} files checked, {len(errors)} issues.", file=sys.stderr)
        return 1
    print(f"Documentation structure check passed: {len(files)} files; review semantics and synchronized code/documentation delivery manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
