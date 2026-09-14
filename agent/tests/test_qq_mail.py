"""职责：验证 QQ IMAP 协议解析和只读边界。
实现：模拟 IMAP 传输，验证文件夹发现、UID 去重、代次和 MIME，不访问真实邮箱。
关联：qq_mail 适配器；测试数据均为虚构邮件和授权码。
目录：
- QQMailTests：QQ 传输测试。
- QQMailTests.test_connect_uses_tls_and_hides_provider_error：TLS 与脱敏错误。
- QQMailTests.test_folders_requires_sent_and_supports_flags：发现已发送目录。
- QQMailTests.test_uid_search_filters_reverse_star_range：增量下界过滤。
- QQMailTests.test_message_ids_distinguish_folder_and_generation：消息身份边界。
- QQMailTests.test_read_is_peek_and_preserves_dates：只读 MIME 读取。
- QQMailTests.test_changed_validity_stops_before_fetch：代次改变拒绝读取。
- QQMailTests.test_date_search_and_metadata_are_body_free：日期筛选及纯元数据读取。
- QQMailTests.test_metadata_rejects_missing_or_invalid_responses：元数据缺失、重复或无效时拒绝。
变量索引：
- 无
"""
import imaplib
from datetime import datetime, timezone
import ssl
import unittest
from unittest.mock import Mock, patch

from agent.tools import qq_mail


# 功能：验证 QQ 协议适配器的确定性行为。
# 逻辑：模拟网络返回真实形状的 IMAP 响应。
# 约束：不证明 QQ 真实账号授权或外网可达。
class QQMailTests(unittest.TestCase):
    # 功能：核验连接固定主机和证书验证。
    # 输入：无外部参数；模拟 SSL 客户端与认证失败。
    # 输出：使用 TLS 校验，错误不含模拟授权码。
    # 逻辑：检查构造参数与失败清理。
    # 约束：不连接 QQ，不记录凭证。
    def test_connect_uses_tls_and_hides_provider_error(self):
        with patch.object(qq_mail.imaplib, "IMAP4_SSL") as factory:
            client = factory.return_value
            client.login.return_value = ("OK", [])
            self.assertIs(qq_mail.connect("demo@qq.com", "abcdefghijklmnop"), client)
            self.assertEqual(factory.call_args.args, ("imap.qq.com", 993))
            self.assertEqual(factory.call_args.kwargs["ssl_context"].verify_mode, ssl.CERT_REQUIRED)
            client.login.side_effect = imaplib.IMAP4.error("raw-secret-provider-error")
            with self.assertRaises(qq_mail.QQMailError) as caught:
                qq_mail.connect("demo@qq.com", "abcdefghijklmnop")
            self.assertNotIn("raw-secret", str(caught.exception))
            client.logout.assert_called_once()

    # 功能：验证发送目录识别与不完整范围失败。
    # 输入：无外部参数；三种 LIST 响应。
    # 输出：标记/QQ 名称被识别，缺少发送目录时报错。
    # 逻辑：测试特殊用途标志和固定服务名称。
    # 约束：不把缺少文件夹作为只同步收件箱的理由。
    def test_folders_requires_sent_and_supports_flags(self):
        client = Mock()
        client.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent Messages"'])
        self.assertEqual(qq_mail.folders(client), ["INBOX", "Sent Messages"])
        client.list.return_value = ("OK", [b'() "/" "&XfJT0ZAB-"'])
        self.assertEqual(qq_mail.folders(client)[1], "&XfJT0ZAB-")
        client.list.return_value = ("OK", [b'() "/" "INBOX"'])
        with self.assertRaises(qq_mail.QQMailError):
            qq_mail.folders(client)

    # 功能：验证 IMAP 星号反向范围不会重复导入旧 UID。
    # 输入：无外部参数；服务器返回下界之前和之后的 UID。
    # 输出：仅保留严格大于已保存游标的值。
    # 逻辑：显式排序和过滤。
    # 约束：无自动回扫旧邮件。
    def test_uid_search_filters_reverse_star_range(self):
        client = Mock()
        client.uid.return_value = ("OK", [b"5 7 6 7"])
        self.assertEqual(qq_mail.list_uids(client, 5), [6, 7])
        client.uid.return_value = ("OK", [b"5"])
        self.assertEqual(qq_mail.list_uids(client, 5), [])

    # 功能：验证持久消息标识唯一性与安全解析。
    # 输入：无外部参数；相同 UID 位于不同文件夹或代次。
    # 输出：互不相同且可逆的标识，非法编码拒绝。
    # 逻辑：往返编解码和注入输入测试。
    # 约束：不使用可重复的 RFC Message-ID 代替 IMAP 身份。
    def test_message_ids_distinguish_folder_and_generation(self):
        values = {qq_mail.message_id("INBOX", 10, 1), qq_mail.message_id("Sent Messages", 10, 1), qq_mail.message_id("INBOX", 11, 1)}
        self.assertEqual(len(values), 3)
        self.assertEqual(qq_mail.split_message_id(qq_mail.message_id("Sent Messages", 10, 1)), ("Sent Messages", 10, 1))
        with self.assertRaises(qq_mail.QQMailError):
            qq_mail.split_message_id("qq:SU5CT1g:10:1\r\nLOGOUT")

    # 功能：验证 MIME 解析与读取无副作用。
    # 输入：无外部参数；模拟 INTERNALDATE 和一封虚构邮件。
    # 输出：保留来源 ID、接收时间和正文。
    # 逻辑：确认 readonly 与 BODY.PEEK[] 命令。
    # 约束：不以运行当前时间替代实际接收时间。
    def test_read_is_peek_and_preserves_dates(self):
        client = Mock()
        client.select.return_value = ("OK", [b"1"])
        client.response.return_value = ("UIDVALIDITY", [b"20"])
        raw = b"From: buyer@example.com\r\nTo: demo@qq.com\r\nDate: Mon, 14 Sep 2026 09:00:00 +0800\r\nSubject: Inquiry\r\n\r\nNeed 2 units."
        client.uid.return_value = ("OK", [(b'1 (UID 3 INTERNALDATE "14-Sep-2026 10:00:00 +0800" BODY[] {100}', raw), b')'])
        value = qq_mail.message_id("INBOX", 20, 3)
        result = qq_mail.read_email(client, value)
        self.assertEqual(result["gmail_message_id"], value)
        self.assertEqual(result["received_at"], "2026-09-14T02:00:00+00:00")
        self.assertIsNone(result["thread_id"])
        self.assertEqual(result["body_text"], "Need 2 units.")
        client.select.assert_called_once_with('"INBOX"', readonly=True)
        client.uid.assert_called_once_with("fetch", "3", "(UID INTERNALDATE BODY.PEEK[])")

    # 功能：阻止文件夹代次变化后的误读。
    # 输入：无外部参数；持久代次与服务器代次不同。
    # 输出：失败且没有 FETCH。
    # 逻辑：身份检查先于原文读取。
    # 约束：不隐式重置游标。
    def test_changed_validity_stops_before_fetch(self):
        client = Mock()
        client.select.return_value = ("OK", [b"1"])
        client.response.return_value = ("UIDVALIDITY", [b"21"])
        with self.assertRaises(qq_mail.QQMailError):
            qq_mail.read_email(client, qq_mail.message_id("INBOX", 20, 3))
        client.uid.assert_not_called()

    # 功能：验证日期粗筛覆盖时区边界且不请求正文。
    # 输入：无外部参数；UTC 日期下界及两个有时区的 IMAP 日期响应。
    # 输出：SINCE 提前一天，内部日期转换为 UTC，FETCH 仅请求元数据。
    # 逻辑：检查实际命令参数与输出日期值。
    # 约束：不联网，不以 MIME Date 头替代 INTERNALDATE。
    def test_date_search_and_metadata_are_body_free(self):
        client = Mock()
        client.uid.return_value = ("OK", [b"1 2"])
        qq_mail.list_uids(client, 0, since=datetime(2026, 9, 7, 3, tzinfo=timezone.utc))
        client.uid.assert_called_once_with("search", None, "UID", "1:*", "SINCE", "06-Sep-2026")
        client.uid.reset_mock()
        client.uid.return_value = ("OK", [b'1 (UID 1 INTERNALDATE "06-Sep-2026 23:00:00 -0800")', b'2 (INTERNALDATE "07-Sep-2026 15:00:00 +0800" UID 2)'])
        dates = qq_mail.message_dates(client, [1, 2])
        self.assertEqual(dates[1], datetime(2026, 9, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(dates[1], dates[2])
        client.uid.assert_called_once_with("fetch", "1,2", "(UID INTERNALDATE)")

    # 功能：验证日期查询不能把不完整范围当作成功。
    # 输入：无外部参数；缺失、重复、额外 UID 及非法日期的模拟响应。
    # 输出：全部抛出 QQMailError。
    # 逻辑：比较请求集合、解析结果和服务状态。
    # 约束：不自动重试，也不读取正文补足元数据。
    def test_metadata_rejects_missing_or_invalid_responses(self):
        row = b'1 (UID 1 INTERNALDATE "14-Sep-2026 10:00:00 +0800")'
        for response in [("NO", []), ("OK", []), ("OK", [row, row]), ("OK", [row.replace(b'UID 1', b'UID 2')]), ("OK", [b'1 (UID 1 INTERNALDATE "invalid")'])]:
            client = Mock()
            client.uid.return_value = response
            with self.subTest(response=response), self.assertRaises(qq_mail.QQMailError):
                qq_mail.message_dates(client, [1])
