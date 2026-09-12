"""职责：静态核对 Python 声明注释、文件目录与变量索引，不导入业务模块。
实现：解析 AST 获取实际符号，比较结构化说明；汇总问题并以非零退出码阻止误报通过。
关联：默认检查 backend/ 和 tools/；格式见 docs/coding-agent-guidelines.md。

目录：
- Inventory：收集声明与赋值名称的 AST 访问器。
- Inventory.__init__：初始化作用域栈、声明表和变量集合。
- Inventory.qualified：按当前词法作用域生成限定名称。
- Inventory.visit_ClassDef：记录类并进入类作用域。
- Inventory.visit_FunctionDef：记录函数并进入函数作用域。
- Inventory.visit_AsyncFunctionDef：按相同规则记录异步函数。
- Inventory.visit_Name：收集模块或类作用域中的赋值名称。
- sections：解析固定标题的结构化说明。
- check_index：校验索引格式、重复、缺失及失效条目。
- declaration_doc：读取声明前注释，或声明自身的 docstring。
- check_file：检查单文件并返回全部可定位的问题。
- main：解析路径、扫描文件、报告问题并返回退出码。

变量索引：
- PROJECT_ROOT：由脚本位置确定的项目目录。
- MODULE_SECTIONS：模块说明必须包含的标题。
- DECLARATION_SECTIONS：声明说明可用的标题，函数要求全部提供。
"""

import argparse
import ast
from pathlib import Path
import re
import sys
import tokenize

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_SECTIONS = ("职责", "实现", "关联", "目录", "变量索引")
DECLARATION_SECTIONS = ("功能", "输入", "输出", "逻辑", "约束")


# 功能：收集实际实现的声明及模块、类作用域赋值名称。
# 逻辑：维护词法作用域栈，声明用点分限定名；函数局部变量不进入文件变量索引。
# 约束：不执行代码、不枚举继承成员、导入别名、属性或字典键；同名条件分支合并索引。
class Inventory(ast.NodeVisitor):
    # 功能：建立一次文件扫描的独立状态。
    # 输入：无外部参数。
    # 输出：无返回值；初始化 scope、declarations、variables 三个实例属性。
    # 逻辑：栈记录作用域类型与名称，声明保留 AST 节点，变量按集合去重。
    # 约束：每个文件创建新实例，避免不同模块的符号互相污染。
    def __init__(self):
        self.scope = []
        self.declarations = []
        self.variables = set()

    # 功能：生成索引使用的限定名称。
    # 输入：`name` 为当前声明或变量的原始名称。
    # 输出：以点连接的作用域路径字符串。
    # 逻辑：依次连接外层类或函数名与当前名称。
    # 约束：不解析运行时别名或继承关系。
    def qualified(self, name):
        return ".".join([entry[1] for entry in self.scope] + [name])

    # 功能：记录类声明并扫描其内部实现。
    # 输入：`node` 为 ClassDef AST 节点。
    # 输出：无返回值；更新声明、变量和暂时的作用域状态。
    # 逻辑：先记录外层限定名，再压入类作用域并访问类体，最后弹栈。
    # 约束：只遍历类体，类装饰器和基类表达式不属于该类的声明目录。
    def visit_ClassDef(self, node):
        self.declarations.append((self.qualified(node.name), node))
        self.scope.append(("class", node.name))
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    # 功能：记录函数或方法，并查找内部嵌套声明。
    # 输入：`node` 为 FunctionDef 或结构兼容的 AsyncFunctionDef 节点。
    # 输出：无返回值；更新声明及暂时的作用域状态。
    # 逻辑：在函数作用域中访问函数体，避免把普通局部变量列入模块索引。
    # 约束：装饰器、默认参数表达式不作为函数体扫描；嵌套类仍独立收集类变量。
    def visit_FunctionDef(self, node):
        self.declarations.append((self.qualified(node.name), node))
        self.scope.append(("function", node.name))
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    # 功能：让异步声明遵循同步声明的检查规则。
    # 输入：`node` 为 AsyncFunctionDef 节点。
    # 输出：无返回值；更新同一份符号清单。
    # 逻辑：委托 visit_FunctionDef 完成作用域管理。
    # 约束：仅分析语法，不启动或等待协程。
    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    # 功能：记录模块或类作用域中的变量绑定。
    # 输入：`node` 为 Name AST 节点。
    # 输出：无返回值；必要时向 variables 加入限定名称。
    # 逻辑：仅收集 Store 上下文，涵盖赋值、解构、注解赋值和循环绑定。
    # 约束：推导式有独立作用域，其绑定由 check_file 的扫描准备步骤排除。
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store) and (not self.scope or self.scope[-1][0] == "class"):
            self.variables.add(self.qualified(node.id))


# 功能：从说明文本提取指定标题的内容。
# 输入：`text` 为说明字符串；`headings` 为允许的标题序列。
# 输出：标题到去除首尾空白的内容映射；重复标题或标题外正文导致 ValueError。
# 逻辑：按行识别全角冒号标题，后续行归属最近标题。
# 约束：不进行自然语言语义校验；标题必须独占行首且不可重复。
def sections(text, headings):
    result = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^(" + "|".join(map(re.escape, headings)) + r")：(.*)$", line)
        if match:
            current = match.group(1)
            if current in result:
                raise ValueError(f"重复标题：{current}")
            result[current] = [match.group(2).strip()]
        elif line:
            if current is None:
                raise ValueError("标题前存在未归类正文")
            result[current].append(line)
    return {key: "\n".join(value).strip() for key, value in result.items()}


# 功能：检查人工维护的索引是否与 AST 符号集合一致。
# 输入：`text` 为索引正文；`expected` 为实际名称集合；`label` 为诊断中的索引类型。
# 输出：问题字符串列表，空列表表示结构一致。
# 逻辑：解析每行的名称与用途，比较集合并单独报告重复和格式错误。
# 约束：空索引必须写“- 无”；用途是否准确仍由人工核验。
def check_index(text, expected, label):
    names = set()
    errors = []
    if text.strip() == "- 无":
        lines = []
    else:
        lines = text.splitlines()
    for line in lines:
        match = re.fullmatch(r"- ([\w.]+)：\s*(\S.*)", line)
        if not match:
            errors.append(f"{label}条目格式错误，应为 '- 名称：用途'：{line}")
            continue
        name = match.group(1)
        if name in names:
            errors.append(f"{label}重复条目：{name}")
        names.add(name)
    for name in sorted(expected - names):
        errors.append(f"{label}缺少：{name}")
    for name in sorted(names - expected):
        errors.append(f"{label}失效条目：{name}")
    return errors


# 功能：取得函数或类的结构化实现说明。
# 输入：`node` 为声明节点；`lines` 为文件源码行列表。
# 输出：声明前连续注释的正文，或不存在该注释时的 docstring 字符串。
# 逻辑：从最早装饰器或声明行向前读取连续注释，剥离 # 前缀。
# 约束：声明说明与装饰器或声明之间不能有空行；已有 docstring 不修改。
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


# 功能：验证一个 Python 文件的说明结构及符号同步情况。
# 输入：`path` 为待检查文件的 Path。
# 输出：包含文件路径和行号的问题列表；无问题时为空。
# 逻辑：只读解析源码，核对模块标题、两个索引、声明标题及输入参数名称。
# 约束：读取、编码或语法错误均明确报错；不导入模块，不检查自然语言真实性或 Git 原子性。
def check_file(path):
    try:
        with tokenize.open(path) as source_file:
            source = source_file.read()
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError, LookupError) as exc:
        return [f"{path}:{getattr(exc, 'lineno', None) or 1}: 无法读取或解析源码（{type(exc).__name__}）；检查编码、权限和语法"]

    inventory = Inventory()
    # 推导式的迭代变量不会泄露到模块或类命名空间；扫描其余 AST 保留原始行号。
    # 推导式中的海象赋值可能绑定外部变量，因此保留 NamedExpr 节点继续扫描。
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
        errors.append(f"{path}:1: 模块说明格式错误：{exc}")
        module = {}
    for heading in MODULE_SECTIONS:
        if not module.get(heading):
            errors.append(f"{path}:1: 模块说明缺少非空标题：{heading}")
    for label, expected in (("目录", {name for name, _ in inventory.declarations}), ("变量索引", inventory.variables)):
        errors.extend(f"{path}:1: {error}" for error in check_index(module.get(label, ""), expected, label))

    for name, node in inventory.declarations:
        location = f"{path}:{node.lineno}: {name}"
        try:
            doc = sections(declaration_doc(node, source.splitlines()), DECLARATION_SECTIONS)
        except ValueError as exc:
            errors.append(f"{location} 声明说明格式错误：{exc}")
            doc = {}
        required = ("功能", "逻辑", "约束") if isinstance(node, ast.ClassDef) else DECLARATION_SECTIONS
        for heading in required:
            if not doc.get(heading):
                errors.append(f"{location} 缺少非空说明：{heading}")
        if not isinstance(node, ast.ClassDef):
            args = node.args
            parameters = args.posonlyargs + args.args + args.kwonlyargs
            parameters += [arg for arg in (args.vararg, args.kwarg) if arg is not None]
            documented = set(re.findall(r"`([A-Za-z_]\w*)`", doc.get("输入", "")))
            actual = {parameter.arg for parameter in parameters}
            for parameter in parameters:
                if parameter.arg not in {"self", "cls"} and f"`{parameter.arg}`" not in doc.get("输入", ""):
                    errors.append(f"{location} 输入说明缺少参数：{parameter.arg}（须用反引号标识）")
            for parameter in sorted(documented - actual):
                errors.append(f"{location} 输入说明含失效参数：{parameter}")
    return errors


# 功能：执行只读文档规范检查并返回可用于开发流程的状态码。
# 输入：`argv` 为可选命令行参数序列；None 表示读取进程参数。
# 输出：通过返回 0，发现问题返回 1；argparse 参数错误按标准行为退出 2。
# 逻辑：默认检查项目 backend/ 与 tools/，显式路径可为 Python 文件或目录；输出文件数及全部问题。
# 约束：缺失路径、空目录或非 Python 文件均失败；不会自动生成说明或修改源码。
def main(argv=None):
    parser = argparse.ArgumentParser(description="检查 Python 声明说明、目录和变量索引；语义一致性仍需人工审核。")
    parser.add_argument("paths", nargs="*", type=Path, help="可选 Python 文件或目录，默认 backend/ 和 tools/")
    args = parser.parse_args(argv)
    paths = args.paths or [PROJECT_ROOT / "backend", PROJECT_ROOT / "tools"]
    files = set()
    errors = []
    for path in paths:
        if path.is_dir():
            found = {p.resolve() for p in path.rglob("*.py") if "__pycache__" not in p.parts}
            if not found:
                errors.append(f"{path}:1: 目录没有 Python 文件，请检查扫描路径")
            files.update(found)
        elif path.is_file() and path.suffix == ".py":
            files.add(path.resolve())
        else:
            errors.append(f"{path}:1: 路径不存在或不是 Python 文件/目录")
    for path in sorted(files):
        errors.extend(check_file(path))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"文档检查失败：检查 {len(files)} 个文件，发现 {len(errors)} 项问题。", file=sys.stderr)
        return 1
    print(f"文档结构检查通过：{len(files)} 个文件；仍须人工核对说明语义及代码与注释的同步交付。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
