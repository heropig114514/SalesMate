"""职责：验证聊天回报仅执行 Schema 校验，不依赖数据库或模型服务。
实现：以合法回报为基准覆盖开放版本、引用元数据、错误文本及非法类型和存储长度。
关联：直接调用 apps.chat.contracts.report；权限、快照和事务由 integration/test_chat.py 验证。
目录：
- ChatReportSchemaTests：无数据库的聊天回报契约测试。
- ChatReportSchemaTests.setUp：准备合法回报与引用。
- ChatReportSchemaTests.test_content_is_not_validated：接受新版本、任意编号和重复来源。
- ChatReportSchemaTests.test_custom_error_and_terminal_shapes：接受自定义错误，拒绝互斥终态结构错误。
- ChatReportSchemaTests.test_invalid_types_and_fields：拒绝非法字段、UUID 和类型。
- ChatReportSchemaTests.test_storage_lengths：验证数据库字符串字段长度边界。
变量索引：
- 无
"""

from apps.chat.contracts import report
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError


# 功能：验证回报结构校验与回答内容判断的边界。
# 逻辑：使用普通字典直接测试契约函数，不模拟数据库行为。
# 约束：通过本测试不能证明权限、事务或真实模型质量正确。
class ChatReportSchemaTests(SimpleTestCase):
    # 功能：构造满足当前回报 Schema 的基准输入。
    # 输入：无外部参数；使用固定测试 UUID 和普通文本。
    # 输出：实例状态 payload 与 citation。
    # 逻辑：基准不包含 company_id，也不要求存在上下文快照。
    # 约束：所有标识为测试常量，不访问真实记录。
    def setUp(self):
        self.citation = {
            "source_id": "unregistered-source",
            "source_type": "customer_context",
            "title_or_label": "Agent 声明的来源",
        }
        self.payload = {
            "request_id": "00000000-0000-0000-0000-000000000001",
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "文本中的 [99] 不作为后端拒绝依据。",
            "citations": [],
            "status": "completed",
            "error": None,
        }

    # 功能：验证后端不扫描正文、绑定版本或限制来源内容。
    # 输入：实例基准回报、非白名单来源及重复引用。
    # 输出：每个回报原值被接受。
    # 逻辑：覆盖无引用、重复引用、无编号正文及非既定版本。
    # 约束：仅检查结构接受行为，不声明来源真实或语义正确。
    def test_content_is_not_validated(self):
        for version in ("chat-v2", "general-chat-v1", "workspace-chat-v1", "future-v9"):
            for citations in ([], [self.citation, self.citation.copy()]):
                for body in (self.payload["assistant_text"], "没有引用标记的回答"):
                    payload = {
                        **self.payload,
                        "chat_prompt_version": version,
                        "assistant_text": body,
                        "citations": citations,
                    }
                    with self.subTest(
                        version=version, citations=len(citations), body=body
                    ):
                        self.assertEqual(report(payload), payload)

    # 功能：验证开放错误文本与终态 Schema 的分界。
    # 输入：基准回报及自定义错误对象。
    # 输出：合法失败原样接受，成功携带错误或失败携带回答时拒绝。
    # 逻辑：分别覆盖 completed 和 failed 的条件字段约束。
    # 约束：错误脱敏属于 Agent 责任，不在本测试判断文本含义。
    def test_custom_error_and_terminal_shapes(self):
        failure = {
            **self.payload,
            "status": "failed",
            "assistant_text": "",
            "error": {"code": "custom_error", "message": "自定义失败说明"},
        }
        self.assertEqual(report(failure), failure)
        for payload in (
            {**self.payload, "error": failure["error"]},
            {**failure, "assistant_text": "回答"},
            {**failure, "citations": [self.citation]},
            {**failure, "error": None},
            {**failure, "error": {"code": 1, "message": "说明"}},
            {**failure, "error": {"code": "code", "message": []}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                report(payload)

    # 功能：验证放宽内容限制后仍拒绝不合法 JSON 对象结构。
    # 输入：基准回报的缺失字段、未知字段及类型变体。
    # 输出：每个反例均抛 ValidationError。
    # 逻辑：覆盖顶层、必需 UUID、字符串、数组、枚举及引用对象。
    # 约束：不通过 ORM 或 HTTP 测试类型转换。
    def test_invalid_types_and_fields(self):
        cases = [None, [], {**self.payload, "owner_id": 1}]
        cases.extend(
            {key: value for key, value in self.payload.items() if key != missing}
            for missing in self.payload
        )
        for key, values in (
            ("request_id", [None, 1, "not-a-uuid"]),
            ("chat_prompt_version", [None, {}, " "]),
            ("assistant_text", [None, 1, " "]),
            (
                "citations",
                [
                    None,
                    {},
                    [None],
                    [{**self.citation, "content": "自报正文"}],
                    [{**self.citation, "source_id": []}],
                ],
            ),
            ("status", [None, [], "processing"]),
        ):
            cases.extend({**self.payload, key: value} for value in values)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                report(payload)

    # 功能：验证版本和来源类型与现有数据库字段容量一致。
    # 输入：长度恰为上限及超出一字符的文本。
    # 输出：边界合法，超长拒绝。
    # 逻辑：版本使用 100 字符、source_type 使用 80 字符的既有存储上限。
    # 约束：不修改模型、迁移或既定存储长度。
    def test_storage_lengths(self):
        payload = {
            **self.payload,
            "chat_prompt_version": "v" * 100,
            "citations": [{**self.citation, "source_type": "s" * 80}],
        }
        self.assertEqual(report(payload), payload)
        for invalid in (
            {**payload, "chat_prompt_version": "v" * 101},
            {**payload, "citations": [{**self.citation, "source_type": "s" * 81}]},
        ):
            with self.subTest(payload=invalid), self.assertRaises(ValidationError):
                report(invalid)
