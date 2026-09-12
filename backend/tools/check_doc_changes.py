"""职责：检查 backend Python 快照的注释结构，并定位实现变化后说明未更新的声明。
实现：只读 Git 基准、暂存区或工作区；复用 check_docs，按 AST 比较模块、类及函数。
关联：供 pre-commit 与 CI 调用；只检查 backend/ 下 Python，不执行源码或调用外部模型。

目录：
- CodeShape：去除说明和嵌套声明，提取当前作用域的代码结构。
- CodeShape.visit_Expr：去除独立字符串表达式。
- CodeShape.visit_FunctionDef：隔离嵌套函数变化。
- CodeShape.visit_AsyncFunctionDef：隔离嵌套异步函数变化。
- CodeShape.visit_ClassDef：隔离嵌套类变化。
- git：只读调用 Git 并把失败转为明确诊断。
- decode_source：按 Python 编码声明解码源码。
- snapshot：读取指定 Git 树、暂存区或工作区的 backend Python 文件。
- records：提取每个声明的代码结构、说明与行号。
- compare_sources：发现代码结构变化但说明未变的复核项。
- inspect_changes：执行全量结构检查及基准差异检查。
- main：解析命令行并分别输出错误与待复核项。

变量索引：
- REPO_ROOT：由脚本路径定位 SalesMate 仓库。
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


# 功能：为单个作用域提取不包含注释和嵌套声明的 AST。
# 逻辑：根节点由 generic_visit 处理；内部声明独立比较，不把方法修改误报为类体修改。
# 约束：这是语法差异而非行为等价判定；不展开导入、动态调用或继承。
class CodeShape(ast.NodeTransformer):
    # 功能：排除 docstring 和其他独立字符串表达式。
    # 输入：`node` 为表达式语句。
    # 输出：字符串语句返回 None，其余返回递归处理后的节点。
    # 逻辑：避免仅修改说明触发代码变更告警。
    # 约束：普通赋值、返回值和调用参数中的字符串仍参与比较。
    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)

    # 功能：从父作用域比较中排除嵌套函数。
    # 输入：`node` 为嵌套函数声明。
    # 输出：None，使该声明从父节点中移除。
    # 逻辑：函数的签名、装饰器和实现由它自己的记录覆盖。
    # 约束：新增、删除声明由当前快照结构检查约束目录，不推断调用关系。
    def visit_FunctionDef(self, node):
        return None

    # 功能：从父作用域比较中排除嵌套异步函数。
    # 输入：`node` 为异步函数声明。
    # 输出：None。
    # 逻辑：与同步函数保持相同作用域隔离规则。
    # 约束：不会执行或等待协程。
    def visit_AsyncFunctionDef(self, node):
        return None

    # 功能：从父作用域比较中排除嵌套类。
    # 输入：`node` 为类声明。
    # 输出：None。
    # 逻辑：类基类、装饰器及类属性由该类记录单独比较。
    # 约束：不收集继承但未实现的方法。
    def visit_ClassDef(self, node):
        return None


# 功能：执行参数化的只读 Git 命令。
# 输入：`repo` 为仓库目录；`args` 为独立 Git 参数。
# 输出：原始 stdout 字节；Git 错误、不可用或超时抛 ValueError。
# 逻辑：不用 shell，失败时只报告子命令，不回显源码或 Git stderr。
# 约束：调用点仅使用读取命令；单次最多 30 秒，不自动重试。
def git(repo, *args):
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Git {args[0]} 无法执行（{type(exc).__name__}）") from None
    if result.returncode:
        raise ValueError(f"Git {args[0]} 失败（退出码 {result.returncode}）；检查仓库、基准提交及 Git 历史是否完整")
    return result.stdout


# 功能：解码文件或 Git blob 中的 Python 源码。
# 输入：`raw` 为原始字节。
# 输出：源码字符串；编码错误由调用方报告。
# 逻辑：使用 tokenize.detect_encoding 支持编码声明及 UTF-8 BOM。
# 约束：不执行源码、不读取外部配置。
def decode_source(raw):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding)


# 功能：读取限定在 backend 的 Python 源码快照。
# 输入：`repo` 为仓库；`revision` 为已解析提交哈希或 None；`staged` 指定当前快照来自暂存区。
# 输出：相对路径到源码字符串的字典；读取或未合并状态异常由上层明确报告。
# 逻辑：历史用 ls-tree，暂存区用 ls-files --stage，工作区包含未忽略的新文件并跳过已删除文件。
# 约束：拒绝符号链接和未合并 Python 文件；不读取 .env、agent 或前端，不修改暂存区。
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
                raise ValueError(f"{path}: 暂存冲突或不支持的文件模式；请先解决再检查")
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
                raise ValueError(f"{path}: 不允许通过链接读取检查范围外文件")
            if target.exists():
                files[path] = decode_source(target.read_bytes())
    return files


# 功能：为源码中的模块及实际声明建立可比较记录。
# 输入：`source` 为 Python 文本。
# 输出：以限定名称和同名声明序号为键，值为 AST 文本、规范化说明及行号的字典。
# 逻辑：保留签名、装饰器、默认值和当前作用域实现；排除行号、格式及嵌套声明。
# 约束：同名条件声明按出现顺序配对；语法错误抛出，不推断函数跨文件移动。
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


# 功能：定位实现发生变化但对应说明未变的作用域。
# 输入：`before`、`after` 为基准与当前源码；`path` 为诊断路径。
# 输出：待复核消息列表；不宣称这些项目必然违反规范。
# 逻辑：仅比较两边都存在的声明，AST 变化且说明相同时提示检查默认值、副作用与约束。
# 约束：改动注释文本不证明内容正确；新增与删除声明由结构检查处理，不分析跨文件影响。
def compare_sources(before, after, path):
    previous, current = records(before), records(after)
    reviews = []
    for key in sorted(previous.keys() & current.keys()):
        old_code, old_doc, _ = previous[key]
        code, doc, line = current[key]
        if old_code != code and old_doc == doc:
            reviews.append(f"{path}:{line}: {key[0]} 实现或签名变化，但说明未变；请复核输入输出、默认值、副作用和约束")
    return reviews


# 功能：检查当前完整快照并与明确基准比较。
# 输入：`repo` 为仓库；`base` 为基准 ref；`staged` 选择暂存区而非工作区。
# 输出：文件数量、结构或读取错误列表、待复核列表。
# 逻辑：先解析基准提交再读取两份快照，对当前全部文件应用相同结构标准。
# 约束：基准不存在、文件不可读或扫描为空明确失败；不把读取失败当成无变更。
def inspect_changes(repo, base, staged):
    errors, reviews = [], []
    try:
        revision = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode("ascii").strip()
        previous = snapshot(repo, revision=revision)
        current = snapshot(repo, staged=staged)
        if not current:
            return 0, ["backend/: 没有可检查的 Python 文件"], []
        for path, source in sorted(current.items()):
            issues = check_docs.check_source(source, path)
            errors.extend(issues)
            if not issues and path in previous:
                try:
                    reviews.extend(compare_sources(previous[path], source, path))
                except (SyntaxError, ValueError):
                    errors.append(f"{path}: 基准源码无法解析，不能执行变更比较")
        return len(current), errors, reviews
    except (ValueError, OSError, UnicodeError, SyntaxError, LookupError) as exc:
        # 只展示安全的读取诊断；编码/语法异常消息可能包含源码，故仅显示异常类型。
        message = str(exc) if type(exc) is ValueError else f"快照读取失败（{type(exc).__name__}）"
        return 0, [message], []


# 功能：提供工作区、暂存区和 CI 共用的检查入口。
# 输入：`argv` 为参数序列或 None；默认以 HEAD 对比工作区。
# 输出：结构错误返回 1；显式严格模式存在待复核项返回 2；其余返回 0 并展示复核数量。
# 逻辑：分开展示确定性错误与语义复核提示，--fail-on-review 可将后者作为门槛。
# 约束：默认提示不阻断合法重构；退出 0 只表示结构检查成功，不表示语义已审核。
def main(argv=None):
    parser = argparse.ArgumentParser(description="backend 注释结构与变更检查；不执行业务代码。")
    parser.add_argument("--base", default="HEAD", help="明确的基准提交，默认 HEAD；必须存在")
    parser.add_argument("--staged", action="store_true", help="检查实际暂存 blob，不读取工作区修正")
    parser.add_argument("--fail-on-review", action="store_true", help="待复核项目也返回非零；不代表自动语义判断")
    args = parser.parse_args(argv)
    count, errors, reviews = inspect_changes(REPO_ROOT, args.base, args.staged)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    for review in reviews:
        print(f"REVIEW {review}")
    print(f"backend 注释检查：{count} 个文件，{len(errors)} 项错误，{len(reviews)} 项待复核；自然语言语义未自动验证。")
    return 1 if errors else 2 if reviews and args.fail_on_review else 0


if __name__ == "__main__":
    raise SystemExit(main())
