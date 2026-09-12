"""职责：验证文档检查器对违规、合法说明及命令行失败的识别能力。
实现：使用临时目录构造独立源码样本，以 unittest 检查诊断与退出码；不执行样本代码。
关联：调用同目录 check_docs，仅依赖 Python 标准库，不接触业务数据库或真实配置。

目录：
- module_doc：为测试样本构造结构化模块头。
- declaration：为测试样本构造声明前注释。
- CheckDocsTests：隔离文件系统的检查器回归测试集合。
- CheckDocsTests.setUp：建立临时测试目录。
- CheckDocsTests.check_source：保存样本并运行单文件检查。
- CheckDocsTests.test_valid_empty_package：验证显式空索引的包说明。
- CheckDocsTests.test_missing_module_and_declaration_docs：拒绝缺少说明的文件与声明。
- CheckDocsTests.test_missing_stale_and_duplicate_indexes：验证缺失、失效、重复和无用途索引。
- CheckDocsTests.test_decorated_async_and_nested_declarations：覆盖装饰器、异步及嵌套声明定位。
- CheckDocsTests.test_class_and_module_bindings：区分类变量、条件赋值、推导式和函数局部变量。
- CheckDocsTests.test_missing_and_removed_parameters：检查签名新增、删除及各类参数的同步说明。
- CheckDocsTests.test_missing_sections_and_duplicate_headings：拒绝空标题与重复标题。
- CheckDocsTests.test_docstring_declarations：验证结构化 docstring 也可用作声明说明。
- CheckDocsTests.test_syntax_encoding_and_missing_file：明确报告语法、编码及读取失败。
- CheckDocsTests.test_source_is_not_executed：确保恶意或有副作用的样本仅被静态解析。
- CheckDocsTests.test_cli_paths_and_exit_codes：验证 CLI 成功、失败、缺失和空路径语义。
- CheckDocsTests.test_cli_default_paths_ignore_working_directory：验证默认扫描位置不依赖启动目录。

变量索引：
- 无
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


# 功能：构造测试源码的模块说明。
# 输入：`symbols` 为目录条目正文；`variables` 为变量索引正文，均默认显式空集合。
# 输出：含完整模块 docstring 和换行的源码字符串。
# 逻辑：固定非空模块叙述，将传入索引按原文放入对应标题。
# 约束：允许调用者故意传入错误条目，以测试检查器拒绝行为；不写文件。
def module_doc(symbols="- 无", variables="- 无"):
    return (
        '"""职责：测试样本。\n实现：仅供静态解析。\n关联：检查器单元测试。\n'
        f'\n目录：\n{symbols}\n\n变量索引：\n{variables}\n"""\n\n'
    )


# 功能：构造可放在函数或类前的结构化注释。
# 输入：`inputs` 为参数说明，默认无；`indent` 为空格缩进；`is_class` 决定是否省略函数输入输出标题。
# 输出：以换行结束的注释字符串。
# 逻辑：函数包含五项说明，类包含功能、逻辑与约束三项。
# 约束：测试文本不宣称真实业务行为，不能直接用作业务说明模板内容。
def declaration(inputs="无。", indent="", is_class=False):
    lines = ["功能：验证样本声明。"]
    if not is_class:
        lines.extend([f"输入：{inputs}", "输出：样本值。"])
    lines.extend(["逻辑：按样本语句执行。", "约束：不执行，仅静态解析。"])
    return "".join(f"{indent}# {line}\n" for line in lines)


# 功能：验证检查器对真实维护失误的识别能力。
# 逻辑：临时样本独立于项目实现，既检查通过样本也检查漏项、失效和失败分支。
# 约束：不修改 backend/；临时文件由测试框架清理，CLI 子进程最多等待 30 秒。
class CheckDocsTests(unittest.TestCase):
    # 功能：为每项测试提供独立文件目录。
    # 输入：无外部参数，由 unittest 调用。
    # 输出：返回 None，设置 self.root 并注册自动清理。
    # 逻辑：使用标准库 TemporaryDirectory，生命周期绑定当前测试。
    # 约束：不复用项目目录，不读取真实 .env。
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="salesmate-docs-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    # 功能：对内存源码执行一次文件级检查。
    # 输入：`source` 为完整 Python 源码字符串。
    # 输出：检查器返回的问题列表。
    # 逻辑：写入临时 sample.py 后调用 check_file。
    # 约束：仅覆盖当前测试自己的样本文件，样本不会被导入。
    def check_source(self, source):
        path = self.root / "sample.py"
        path.write_text(source, encoding="utf-8")
        return check_docs.check_file(path)

    # 功能：验证无声明无变量文件的合法说明。
    # 输入：无外部参数。
    # 输出：无返回值；合法空包被拒绝时测试失败。
    # 逻辑：仅写带两个“- 无”索引的模块 docstring。
    # 约束：空包仍必须有模块说明，不能靠文件为空豁免。
    def test_valid_empty_package(self):
        self.assertEqual(self.check_source(module_doc()), [])
        self.assertTrue(self.check_source(""))

    # 功能：确认缺少模块和函数说明不会误报通过。
    # 输入：无外部参数。
    # 输出：无返回值；未报告关键漏项时失败。
    # 逻辑：检查只有函数实现的样本，并查找模块和声明两种错误。
    # 约束：不要求诊断顺序或完整字符串固定。
    def test_missing_module_and_declaration_docs(self):
        errors = "\n".join(self.check_source("def work():\n    return 1\n"))
        self.assertIn("模块说明缺少非空标题：职责", errors)
        self.assertIn("work 缺少非空说明：功能", errors)
        self.assertIn("目录缺少：work", errors)

    # 功能：检测声明重命名、变量删除及手工索引错误。
    # 输入：无外部参数。
    # 输出：无返回值；任一错误未被检出时断言失败。
    # 逻辑：用真实 work 声明配旧名称、重复目录和失效变量索引逐项检查。
    # 约束：各子样本只用于触发结构错误，不测试说明语义。
    def test_missing_stale_and_duplicate_indexes(self):
        cases = [
            ("- old：旧名。", "- 无", "目录缺少：work"),
            ("- old：旧名。", "- 无", "目录失效条目：old"),
            ("- work：用途。\n- work：重复。", "- 无", "目录重复条目：work"),
            ("- work：", "- 无", "目录条目格式错误"),
            ("- work：用途。", "- REMOVED：已删除变量。", "变量索引失效条目：REMOVED"),
        ]
        for symbols, variables, expected in cases:
            with self.subTest(expected=expected):
                source = module_doc(symbols, variables) + declaration() + "def work():\n    return 1\n"
                self.assertIn(expected, "\n".join(self.check_source(source)))

    # 功能：检查声明前说明在装饰器及嵌套作用域中的定位。
    # 输入：无外部参数。
    # 输出：无返回值；合法样本不能通过时失败。
    # 逻辑：构造带多行装饰器的异步方法及方法内函数，使用限定名称目录。
    # 约束：装饰器未定义也不影响 AST 检查，证明检查器不要求执行环境。
    def test_decorated_async_and_nested_declarations(self):
        source = module_doc("- Worker：样本类。\n- Worker.run：异步方法。\n- Worker.run.inner：嵌套函数。")
        source += declaration(is_class=True) + "class Worker:\n"
        source += declaration("`item` 为输入。", "    ")
        source += "    @decorate(\n        'value'\n    )\n    async def run(self, item):\n"
        source += declaration(indent="        ") + "        def inner():\n            return 1\n        return item\n"
        self.assertEqual(self.check_source(source), [])

    # 功能：验证模块和类绑定索引的作用域边界。
    # 输入：无外部参数。
    # 输出：无返回值；遗漏真实变量或误收集局部变量时失败。
    # 逻辑：混合条件赋值、解构、注解赋值、循环、推导式、海象赋值和类属性。
    # 约束：导入别名、字典键及实例属性不属于当前索引；不对这些名称作通过承诺。
    def test_class_and_module_bindings(self):
        names = ["FLAG", "LEFT", "RIGHT", "TOTAL", "item", "RESULT", "CAPTURE", "VALUES", "Box.LIMIT"]
        source = module_doc("- Box：类。\n- Box.run：方法。", "\n".join(f"- {name}：用途。" for name in names))
        source += "import os as imported\nFLAG: bool = True\nif FLAG:\n    LEFT, RIGHT = 1, 2\nTOTAL = 0\n"
        source += "for item in []:\n    TOTAL += item\nRESULT = [n for n in []]\nVALUES = [(CAPTURE := n) for n in []]\n"
        source += declaration(is_class=True) + "class Box:\n    LIMIT = 2\n"
        source += declaration(indent="    ") + "    def run(self):\n        local = 1\n        self.value = local\n        return local\n"
        self.assertEqual(self.check_source(source), [])
        missing = source.replace("- Box.LIMIT：用途。\n", "")
        self.assertIn("变量索引缺少：Box.LIMIT", "\n".join(self.check_source(missing)))

    # 功能：验证各类参数的说明与签名同步。
    # 输入：无外部参数。
    # 输出：无返回值；新增漏注释或删除残留参数未被检出时失败。
    # 逻辑：覆盖位置限定、普通、变长、关键字限定和关键字字典参数，并分别制造遗漏和残留。
    # 约束：输入说明中反引号名称专用于真实参数；隐式 self/cls 可省略。
    def test_missing_and_removed_parameters(self):
        body = "def work(first, /, second=1, *items, enabled=True, **options):\n    return first\n"
        valid = "`first`、`second`、`items`、`enabled`、`options` 为输入。"
        prefix = module_doc("- work：函数。")
        self.assertEqual(self.check_source(prefix + declaration(valid) + body), [])
        errors = self.check_source(prefix + declaration(valid.replace("`enabled`", "开关")) + body)
        self.assertIn("输入说明缺少参数：enabled", "\n".join(errors))
        errors = self.check_source(prefix + declaration(valid + "`removed` 已删除。") + body)
        self.assertIn("输入说明含失效参数：removed", "\n".join(errors))

    # 功能：拒绝只有标题没有内容以及重复标题的说明。
    # 输入：无外部参数。
    # 输出：无返回值；格式错误未报告时失败。
    # 逻辑：分别清空逻辑说明和重复模块职责标题。
    # 约束：不把非空内容当作事实准确性的证明。
    def test_missing_sections_and_duplicate_headings(self):
        source = module_doc("- work：函数。") + declaration().replace("逻辑：按样本语句执行。", "逻辑：")
        errors = self.check_source(source + "def work():\n    return 1\n")
        self.assertIn("缺少非空说明：逻辑", "\n".join(errors))
        errors = self.check_source(module_doc().replace("实现：", "职责：重复。\n实现："))
        self.assertIn("重复标题：职责", "\n".join(errors))

    # 功能：验证函数内的结构化 docstring 也可满足声明检查。
    # 输入：无外部参数。
    # 输出：无返回值；合法 docstring 样本被拒绝时失败。
    # 逻辑：将函数说明放在函数体首个字符串表达式内，不添加声明前注释。
    # 约束：业务视图新增 docstring 可能影响 OpenAPI，测试不授权修改业务契约。
    def test_docstring_declarations(self):
        doc = "\n".join(line[2:] for line in declaration().splitlines())
        source = module_doc("- work：函数。") + 'def work():\n    """' + doc.replace("\n", "\n    ") + '\n    """\n    return 1\n'
        self.assertEqual(self.check_source(source), [])

    # 功能：确认无法解析和读取的文件显式失败。
    # 输入：无外部参数。
    # 输出：无返回值；任何错误被误报通过时失败。
    # 逻辑：分别构造语法错误、无效 UTF-8 字节和不存在的文件路径。
    # 约束：诊断只报告类型和位置，不输出可能含秘密的源码行。
    def test_syntax_encoding_and_missing_file(self):
        self.assertIn("SyntaxError", "\n".join(self.check_source("def broken(:\n")))
        path = self.root / "bad.py"
        path.write_bytes(b"\xff\xfe\x00")
        self.assertTrue(check_docs.check_file(path))
        self.assertIn("FileNotFoundError", "\n".join(check_docs.check_file(self.root / "missing.py")))

    # 功能：确认检查器不会执行被检查源码。
    # 输入：无外部参数。
    # 输出：无返回值；样本被执行或合法说明被拒绝时失败。
    # 逻辑：在模块头后放置无条件 RuntimeError；静态检查仍应通过。
    # 约束：该测试验证导入副作用隔离，不验证业务代码能正常运行。
    def test_source_is_not_executed(self):
        self.assertEqual(self.check_source(module_doc() + "raise RuntimeError('must not execute')\n"), [])

    # 功能：验证命令行聚合与退出码不会掩盖检查失败。
    # 输入：无外部参数。
    # 输出：无返回值；实际状态与预期不符时失败。
    # 逻辑：依次检查合法文件、空目录、缺失路径及目录内未注释文件，并捕获诊断。
    # 约束：只操作测试目录；不修改调用进程的环境或工作目录。
    def test_cli_paths_and_exit_codes(self):
        path = self.root / "valid.py"
        path.write_text(module_doc(), encoding="utf-8")
        empty = self.root / "empty"
        empty.mkdir()
        cases = [(path, 0), (empty, 1), (self.root / "missing", 1)]
        for target, expected in cases:
            with self.subTest(target=target), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(check_docs.main([str(target)]), expected)
                self.assertIn("通过" if expected == 0 else "失败", out.getvalue() + err.getvalue())
        (self.root / "bad.py").write_text("", encoding="utf-8")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(check_docs.main([str(self.root)]), 1)
            self.assertIn("bad.py:1", err.getvalue())

    # 功能：验证从项目外启动时仍扫描默认项目路径。
    # 输入：无外部参数。
    # 输出：无返回值；脚本未成功检查项目时断言失败。
    # 逻辑：使用当前解释器和检查器绝对路径，在临时工作目录中启动独立进程。
    # 约束：这是对当前项目整体说明的回归检查；不会启动 Django 或执行检查器测试自身。
    def test_cli_default_paths_ignore_working_directory(self):
        result = subprocess.run(
            [sys.executable, str(Path(check_docs.__file__).resolve())],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("文档结构检查通过", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
