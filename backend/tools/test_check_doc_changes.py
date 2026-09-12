"""职责：验证注释变更检查的 Git 快照隔离、作用域比较与失败语义。
实现：临时 Git 仓库和静态源码样本覆盖暂存区、工作区及命令行退出码。
关联：调用 check_doc_changes 和原检查器测试样本生成器，不访问业务数据库或真实凭证。

目录：
- ChangeTests：变更检查回归测试集合。
- ChangeTests.setUp：创建带基准提交的临时仓库。
- ChangeTests.write：写入临时源码。
- ChangeTests.inspect：检查临时仓库快照。
- ChangeTests.test_body_signature_and_decorator_changes：验证实现、默认值与装饰器变化。
- ChangeTests.test_format_and_doc_only_changes：避免纯格式与说明修改触发待复核。
- ChangeTests.test_updated_docs_and_nested_scopes：验证说明同步与父子作用域隔离。
- ChangeTests.test_module_and_duplicate_definitions：验证模块配置和重复声明不会漏报。
- ChangeTests.test_staged_snapshot_is_authoritative：验证未暂存修正不能掩盖提交错误。
- ChangeTests.test_untracked_and_deleted_files：验证新增、删除及检查边界。
- ChangeTests.test_bad_baseline_and_encoding：验证读取异常明确失败。
- ChangeTests.test_unmerged_index：拒绝存在未合并阶段的 Python 文件。
- ChangeTests.test_cli_exit_codes：验证提示、严格模式与错误退出码。

变量索引：
- 无
"""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_doc_changes as changes
from test_check_docs import declaration, module_doc


# 功能：在真实临时 Git 索引上测试注释检查，不模拟关键 Git 行为。
# 逻辑：每个用例独立建立基准提交，再分别修改工作区及暂存 blob。
# 约束：只修改临时目录；临时提交身份通过单条命令提供，不修改用户 Git 配置。
class ChangeTests(unittest.TestCase):
    # 功能：创建可独立修改的临时仓库及合法初始源码。
    # 输入：无外部参数，由 unittest 调用。
    # 输出：无；建立 repo、source、path 实例状态，所有仓库在测试类结束时统一清理。
    # 逻辑：每项仍使用独立仓库；延后清理避免刚结束的 Git 文件操作与 Windows 目录删除紧邻。
    # 约束：不读取当前业务仓库的 .env；Git 命令失败直接使测试失败。
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="salesmate-doc-change-")
        self.addClassCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        changes.git(self.repo, "init", "-q")
        changes.git(self.repo, "config", "core.hooksPath", str(self.repo / "empty-hooks"))
        (self.repo / "backend").mkdir()
        self.path = self.repo / "backend" / "sample.py"
        self.source = module_doc("- work：样本函数。") + declaration("`value` 为输入。") + "def work(value=1):\n    return value\n"
        self.write(self.source)
        changes.git(self.repo, "add", "backend/sample.py")
        changes.git(self.repo, "-c", "user.name=Docs Test", "-c", "user.email=docs@example.invalid", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "commit", "-qm", "baseline")

    # 功能：保存临时样本源码。
    # 输入：`source` 为样本文本。
    # 输出：无；覆盖当前测试的 sample.py。
    # 逻辑：固定 UTF-8 编码，Git 是否暂存由各用例显式决定。
    # 约束：不写入生产源码。
    def write(self, source):
        self.path.write_text(source, encoding="utf-8")

    # 功能：对测试仓库运行与 CLI 相同的检查逻辑。
    # 输入：`staged` 指定是否读取暂存区，默认为工作区。
    # 输出：文件数、错误和复核列表。
    # 逻辑：以本用例的 HEAD 为基准。
    # 约束：不调用模型或执行业务代码。
    def inspect(self, staged=False):
        return changes.inspect_changes(self.repo, "HEAD", staged)

    # 功能：验证参数名称未变时，默认值、装饰器及函数体变化仍被发现。
    # 输入：无外部参数。
    # 输出：无；每种修改必须产生且只产生一个 work 复核项。
    # 逻辑：独立改变返回表达式、默认值和装饰器，不改变说明。
    # 约束：装饰器无需定义，因为源码仅被解析。
    def test_body_signature_and_decorator_changes(self):
        for source in [self.source.replace("return value", "return value + 1"), self.source.replace("value=1", "value=2"), self.source.replace("def work", "@atomic\ndef work")]:
            with self.subTest(source=source):
                self.write(source)
                _, errors, reviews = self.inspect()
                self.assertEqual(errors, [])
                self.assertEqual(len(reviews), 1)
                self.assertIn("work 实现或签名变化", reviews[0])

    # 功能：验证格式及 docstring 单独变化不会冒充代码变化。
    # 输入：无外部参数。
    # 输出：无；预期没有结构错误或待复核项目。
    # 逻辑：修改空白、说明及独立字符串，不修改执行表达式。
    # 约束：测试不证明改后的自然语言语义正确。
    def test_format_and_doc_only_changes(self):
        for source in [self.source.replace("value=1", "value = 1"), self.source.replace("样本声明", "当前样本声明"), self.source.replace("    return value", '    """sample documentation"""\n    return value')]:
            self.write(source)
            self.assertEqual(self.inspect()[1:], ([], []))

    # 功能：验证作用域独立比较及对应说明更新。
    # 输入：无外部参数。
    # 输出：无；方法变化只定位该方法，更新其说明后不再提示未同步。
    # 逻辑：直接比较含类、方法和嵌套函数的源码，分别修改内外实现。
    # 约束：此处验证差异算法，结构完整性由其他测试独立覆盖。
    def test_updated_docs_and_nested_scopes(self):
        source = 'class Worker:\n    """class docs"""\n    def run(self):\n        """run docs"""\n        def inner():\n            """inner docs"""\n            return 1\n        return inner()\n'
        after = source.replace("return 1", "return 2")
        reviews = changes.compare_sources(source, after, "sample.py")
        self.assertEqual(len(reviews), 1)
        self.assertIn("Worker.run.inner", reviews[0])
        self.assertEqual(changes.compare_sources(source, after.replace("inner docs", "returns two"), "sample.py"), [])

    # 功能：验证模块配置变化及条件分支内同名声明均可定位。
    # 输入：无外部参数。
    # 输出：无；模块及两个同名函数各产生独立复核项。
    # 逻辑：同时修改常量与条件分支返回值，防止限定名称字典覆盖同名节点。
    # 约束：分支重排按声明顺序配对，可能需要人工辨别重构。
    def test_module_and_duplicate_definitions(self):
        source = '"""module docs"""\nLIMIT = 1\nif FLAG:\n    def work():\n        return 1\nelse:\n    def work():\n        return 1\n'
        reviews = changes.compare_sources(source, source.replace("= 1", "= 2").replace("return 1", "return 2"), "sample.py")
        self.assertEqual(len(reviews), 3)
        self.assertTrue(any("<module>" in item for item in reviews))

    # 功能：确保工作区修正不会掩盖暂存区的缺失注释。
    # 输入：无外部参数。
    # 输出：无；暂存检查必须失败，工作区检查应通过。
    # 逻辑：先暂存缺少说明的源码，再只在工作区恢复合法源码。
    # 约束：检查前后暂存 blob 必须保持一致。
    def test_staged_snapshot_is_authoritative(self):
        self.write("def work():\n    return 2\n")
        changes.git(self.repo, "add", "backend/sample.py")
        self.write(self.source)
        before = changes.git(self.repo, "show", ":backend/sample.py")
        self.assertTrue(self.inspect(staged=True)[1])
        self.assertEqual(self.inspect()[1:], ([], []))
        self.assertEqual(changes.git(self.repo, "show", ":backend/sample.py"), before)

    # 功能：验证工作区新文件受检、删除文件跳过及 agent 范围隔离。
    # 输入：无外部参数。
    # 输出：无；未注释新文件报错，agent 不进入检查；空 backend 失败。
    # 逻辑：构造带空格中文路径的新文件，随后删除所有 backend Python。
    # 约束：使用 NUL 分隔 Git 路径，测试不依赖 shell 转义。
    def test_untracked_and_deleted_files(self):
        new = self.repo / "backend" / "新 文件.py"
        new.write_text("def undocumented(): pass\n", encoding="utf-8")
        (self.repo / "agent").mkdir()
        (self.repo / "agent" / "ignored.py").write_text("bad syntax", encoding="utf-8")
        count, errors, _ = self.inspect()
        self.assertEqual(count, 2)
        self.assertTrue(any("新 文件.py" in item for item in errors))
        self.assertEqual(self.inspect(staged=True)[1:], ([], []))
        new.unlink()
        self.path.unlink()
        self.assertIn("没有可检查", "".join(self.inspect()[1]))

    # 功能：验证错误基准、无效编码和损坏历史不能被当作通过。
    # 输入：无外部参数。
    # 输出：无；每种失败均产生诊断。
    # 逻辑：使用不存在 ref 和无效字节，并单独验证历史解析异常。
    # 约束：不在错误中输出源码字节。
    def test_bad_baseline_and_encoding(self):
        self.assertTrue(changes.inspect_changes(self.repo, "missing-ref", False)[1])
        self.path.write_bytes(b"\xff")
        self.assertTrue(self.inspect()[1])
        with self.assertRaises(SyntaxError):
            changes.compare_sources("def broken(:", self.source, "sample.py")

    # 功能：验证未解决的合并冲突不能检查为成功。
    # 输入：无外部参数。
    # 输出：无；暂存区含非零 stage 时必须报错。
    # 逻辑：模拟 ls-files 的真实非零阶段记录，只替换清单命令的返回值。
    # 约束：不创建真实业务合并，不放宽暂存区规则。
    def test_unmerged_index(self):
        with patch.object(changes, "git", return_value=b"100644 abc123 2\tbackend/sample.py\0"):
            with self.assertRaisesRegex(ValueError, "暂存冲突"):
                changes.snapshot(self.repo, staged=True)

    # 功能：验证默认提示、显式严格复核和结构失败的不同退出码。
    # 输入：无外部参数。
    # 输出：无；预期依次返回 0、2、1。
    # 逻辑：仅替换 CLI 的仓库路径，实际读取临时 Git 历史。
    # 约束：退出 0 的输出必须仍明确说明自然语言语义未验证。
    def test_cli_exit_codes(self):
        self.write(self.source.replace("return value", "return value + 1"))
        with patch.object(changes, "REPO_ROOT", self.repo), redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(changes.main([]), 0)
            self.assertEqual(changes.main(["--fail-on-review"]), 2)
            self.assertEqual(changes.main(["--base", "missing-ref"]), 1)
        self.assertIn("自然语言语义未自动验证", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
