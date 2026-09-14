"""职责：离线验证 QQ 测试注入器的写入门槛和结果语义。
实现：模拟 IMAP 与授权码输入，真实构造 MIME；不访问邮箱或项目数据库。
关联：qq_test_injector.py；由工具打包流水线执行。
目录：
- QQInjectorTests：隔离网络的行为测试。
- QQInjectorTests.setUp：构造合成计划和模拟 IMAP。
- QQInjectorTests.test_dry_run_never_reads_credentials_or_connects：预览无外部调用。
- QQInjectorTests.test_explicit_mode_required：缺失写入模式时拒绝运行。
- QQInjectorTests.test_rejects_bad_plan_before_connect：白名单和头部校验。
- QQInjectorTests.test_append_targets_only_own_inbox：账号、文件夹及 MIME 正确。
- QQInjectorTests.test_partial_rejection_keeps_confirmed_count：明确拒绝停止批次。
- QQInjectorTests.test_timeout_stays_uncertain_without_retry：写入中断不重试。
- QQInjectorTests.test_login_failure_does_not_leak_secret：认证异常安全输出。
- QQInjectorTests.test_logout_failure_does_not_change_success：清理异常保留成功。
- QQInjectorTests.test_credentials_require_secure_input：授权格式和无回显约束。
变量索引：
- ORIGINAL_READ_CODE：保留原授权读取函数，独立验证输入保护。
"""
import contextlib
import getpass
import imaplib
import io
import json
import tempfile
import unittest
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

from test_tools import qq_test_injector as tool


# 功能：验证离线预览和显式 IMAP 写入的边界。
# 逻辑：标准库 Mock 接管网络和授权；其余代码真实执行。
# 约束：测试成功不证明 QQ 服务允许实际 APPEND，不读取真实授权码。
class QQInjectorTests(unittest.TestCase):
    # 功能：生成双邮件场景并替换传输和授权读取。
    # 输入：无外部参数；仅固定合成地址和文本。
    # 输出：address、messages、factory、client、code 实例状态。
    # 逻辑：所有补丁注册清理，默认模拟服务明确接受命令。
    # 约束：即便误走写入路径也不会连接真实服务。
    def setUp(self):
        self.address = "tester@qq.com"
        self.messages = tool.build_test_messages(self.address, [{"from": "测试客户 <buyer@example.com>", "subject": "采购咨询", "body": "第一行\n第二行"}] * 2, "test-run")
        network = patch.object(tool.imaplib, "IMAP4_SSL")
        self.factory = network.start()
        self.addCleanup(network.stop)
        self.client = self.factory.return_value
        self.client.login.return_value = self.client.select.return_value = self.client.append.return_value = ("OK", [b"ok"])
        secret = patch.object(tool, "read_authorization_code", return_value="abcdefghijklmnop")
        self.code = secret.start()
        self.addCleanup(secret.stop)

    # 功能：确保默认 QQ 示例可以完全离线预览。
    # 输入：--dry-run 与随包模板。
    # 输出：成功 JSON，既无授权读取也无网络。
    # 逻辑：捕获 stdout 检查明确模式和非空数量。
    # 约束：不将预览成功当作服务器连通证据。
    def test_dry_run_never_reads_credentials_or_connects(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(["--dry-run"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "dry_run")
        self.assertGreater(result["message_count"], 0)
        self.factory.assert_not_called()
        self.code.assert_not_called()

    # 功能：避免漏填参数时隐式写入邮箱。
    # 输入：空参数和互斥的两个模式。
    # 输出：argparse 退出 2，未读取凭证。
    # 逻辑：验证模式组 required 和互斥契约。
    # 约束：不改变 Gmail 原工具的行为。
    def test_explicit_mode_required(self):
        for args in ([], ["--dry-run", "--apply"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                tool.main(args)
            self.assertEqual(caught.exception.code, 2)
        self.factory.assert_not_called()
        self.code.assert_not_called()

    # 功能：非法地址、头部注入及额外字段必须先于连接失败。
    # 输入：临时 JSON 的多种非法计划。
    # 输出：--apply 返回失败，无网络与授权读取。
    # 逻辑：通过真实 CLI 入口验证整个计划先校验。
    # 约束：临时文件仅含合成文本且自动清理。
    def test_rejects_bad_plan_before_connect(self):
        message = {"from": "buyer@example.com", "subject": "Test", "body": "Body"}
        base = {"mailbox_address": self.address, "messages": [message]}
        cases = [{**base, "mailbox_address": "x@gmail.com"}, {**base, "authorization_code": "hidden"}, {**base, "messages": [{**message, "subject": "x\r\nBcc: x@example.com"}]}, {**base, "messages": [{**message, "to": "other@qq.com"}]}, {**base, "messages": []}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            for document in cases:
                path.write_text(json.dumps(document), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(tool.main(["--apply", "--messages-file", str(path)]), 1)
        self.factory.assert_not_called()
        self.code.assert_not_called()

    # 功能：验证唯一账号、固定 INBOX 及精确中文 MIME。
    # 输入：两封合成邮件与模拟 APPEND OK。
    # 输出：两次确认、正确主题正文、过去 Date 和不带已读标记的写入。
    # 逻辑：检查真实提交字节、证书校验和账号绑定。
    # 约束：不发送 SMTP，不删除或 EXPUNGE 任何邮件。
    def test_append_targets_only_own_inbox(self):
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["inserted_count"], 2)
        self.assertEqual(self.factory.call_args.args, ("imap.qq.com", 993))
        self.assertTrue(self.factory.call_args.kwargs["ssl_context"].check_hostname)
        self.client.login.assert_called_once_with(self.address, "abcdefghijklmnop")
        self.client.select.assert_called_once_with("INBOX", readonly=True)
        for call in self.client.append.call_args_list:
            folder, flags, date, raw = call.args
            self.assertEqual(folder, "INBOX")
            self.assertIsNone(flags)
            self.assertTrue(date.startswith('"'))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            message = message_from_bytes(raw, policy=policy.default)
            self.assertEqual(message["To"], self.address)
            self.assertIn("[SalesMate测试:QQ:test-run]", str(message["Subject"]))
            self.assertEqual(message.get_content().replace("\r\n", "\n").strip(), "第一行\n第二行")
            self.assertLess(parsedate_to_datetime(message["Date"]), datetime.now().astimezone())
        self.client.expunge.assert_not_called()
        self.client.close.assert_not_called()

    # 功能：明确拒绝时保留先前成功邮件的精确数量。
    # 输入：第一次 APPEND OK，第二次 NO。
    # 输出：failed、已确认一封，调用总数为二。
    # 逻辑：停止当前批次，不删除前一封或重试后一封。
    # 约束：模拟协议响应，不证明真实 QQ 配额行为。
    def test_partial_rejection_keeps_confirmed_count(self):
        self.client.append.side_effect = [("OK", [b"ok"]), ("NO", [b"private-service-detail"])]
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual((result["status"], result["inserted_count"]), ("failed", 1))
        self.assertEqual(self.client.append.call_count, 2)
        self.assertNotIn("private-service-detail", str(result))

    # 功能：提交中断必须标明可能已经写入。
    # 输入：首封成功后第二封 APPEND 超时。
    # 输出：uncertain 和第二封 Message-ID，不自动重试。
    # 逻辑：已确认数仅包含服务端明确接受的邮件。
    # 约束：未知结果不等同于未写入。
    def test_timeout_stays_uncertain_without_retry(self):
        self.client.append.side_effect = [("OK", [b"ok"]), TimeoutError("private")]
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual((result["status"], result["inserted_count"]), ("uncertain", 1))
        self.assertEqual(result["uncertain_message_id"], self.messages[1]["Message-ID"])
        self.assertEqual(self.client.append.call_count, 2)

    # 功能：保证认证失败不暴露授权码或服务端异常正文。
    # 输入：模拟登录错误携带测试秘密。
    # 输出：failed，日志及结果中均无测试秘密，APPEND 未调用。
    # 逻辑：仅记录受控阶段和异常类型。
    # 约束：不使用真实账号或密钥。
    def test_login_failure_does_not_leak_secret(self):
        self.client.login.side_effect = imaplib.IMAP4.error("abcdefghijklmnop private")
        with self.assertLogs(tool.logger, level="ERROR") as logs:
            result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("abcdefghijklmnop", str(result) + str(logs.output))
        self.client.append.assert_not_called()

    # 功能：确认写入后清理中断不能制造可重试失败。
    # 输入：所有 APPEND OK，LOGOUT 中断。
    # 输出：completed，shutdown 仍执行。
    # 逻辑：传输清理与写入证据独立。
    # 约束：不自动重连。
    def test_logout_failure_does_not_change_success(self):
        self.client.logout.side_effect = imaplib.IMAP4.abort("disconnected")
        result = tool.inject_messages(self.address, self.messages, "test-run")
        self.assertEqual(result["status"], "completed")
        self.client.shutdown.assert_called_once()

    # 功能：校验环境授权码并拒绝终端回显降级。
    # 输入：无效环境值、有效合成值及 getpass 警告。
    # 输出：无效输入报错，有效值返回；无安全终端时显式停止。
    # 逻辑：独立调用原函数，不经过 setUp 的授权 Mock。
    # 约束：所有环境值仅在补丁作用域内存在。
    def test_credentials_require_secure_input(self):
        with patch.dict(tool.os.environ, {"QQ_TEST_AUTHORIZATION_CODE": "bad"}):
            with self.assertRaises(ValueError):
                ORIGINAL_READ_CODE()
        with patch.dict(tool.os.environ, {"QQ_TEST_AUTHORIZATION_CODE": "abcdefghijklmnop"}):
            self.assertEqual(ORIGINAL_READ_CODE(), "abcdefghijklmnop")
        with patch.dict(tool.os.environ, {}, clear=True), patch.object(tool.getpass, "getpass", side_effect=getpass.GetPassWarning("no tty")):
            with self.assertRaises(getpass.GetPassWarning):
                ORIGINAL_READ_CODE()


ORIGINAL_READ_CODE = tool.read_authorization_code

if __name__ == "__main__":
    unittest.main()
