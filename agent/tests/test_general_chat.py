"""职责：验证通用聊天的模型调用、显式模式和错误边界。
实现：内存后端与模拟模型覆盖无知识回答、引用约束及失败无重试。
关联：general_chat、chat.process_chat_once；复用 test_chat 的既有合成夹具。
目录：
- GeneralChatTests：通用模式契约测试。
- GeneralChatTests.test_without_customer_or_knowledge：没有资料也允许一般问答。
- GeneralChatTests.test_failures_are_explicit：上下文污染或模型失败不降级。
- GeneralChatTests.test_citations_and_report_failure：引用白名单与回报失败版本。
变量索引：
- 无
"""

import json
import unittest
from unittest.mock import Mock

from agent.tests.test_chat import (
    InMemoryChatBackend,
    answer_context,
    conversation_request,
    context_item,
)
from agent.workflows.chat import CHAT_MAX_TOKENS, process_chat_once


# 功能：验证通用模式与客户模式的明确分流。
# 逻辑：调用真实入口，只模拟后端和模型边界。
# 约束：不访问网络，不证明真实模型输出质量。
class GeneralChatTests(unittest.TestCase):
    # 功能：验证无客户资料的正常交互。
    # 输入：无外部参数；合成历史与空知识。
    # 输出：一次成功回报，模型预算保持原值。
    # 逻辑：显式 null 触发通用提示，历史与当前问题传入模型。
    # 约束：不调用外部检索，不伪造引用。
    def test_without_customer_or_knowledge(self):
        backend = InMemoryChatBackend(
            request=conversation_request(
                company_id=None,
                question="你好",
                recent_history=[{"role": "user", "content": "我想写邮件"}],
            ),
            internal=answer_context(customer_context=[], context_items=[]),
        )
        provider = Mock(
            return_value=json.dumps(
                {"assistant_text": "你好，我们可以一起起草邮件。", "citations": []}
            )
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["chat_prompt_version"], "general-chat-v1")
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs["max_tokens"], CHAT_MAX_TOKENS)
        self.assertIn("我想写邮件", str(provider.call_args.args[0]))
        self.assertEqual(backend.context_calls, [("request-1", "internal")])
        self.assertEqual(backend.report_calls, [result])

    # 功能：验证污染证据和模型异常不能变成成功。
    # 输入：无外部参数；注入客户证据或模型异常。
    # 输出：明确失败且模型最多调用一次。
    # 逻辑：逐一验证发生错误的阶段和安全回报。
    # 约束：异常内容不得写入结果，不自动重试。
    def test_failures_are_explicit(self):
        for contaminated in (True, False):
            with self.subTest(contaminated=contaminated):
                backend = InMemoryChatBackend(
                    request=conversation_request(company_id=None),
                    internal=answer_context(
                        customer_context=[context_item()] if contaminated else []
                    ),
                )
                provider = Mock(side_effect=RuntimeError("synthetic-provider-secret"))
                result = process_chat_once(backend=backend, chat_provider=provider)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(
                    result["error"]["code"],
                    "context_unavailable" if contaminated else "model_unavailable",
                )
                self.assertEqual(provider.call_count, 0 if contaminated else 1)
                self.assertNotIn("synthetic-provider-secret", str(result))

    # 功能：验证引用白名单和失败报告的模式一致性。
    # 输入：无外部参数；未知引用和合成回报异常。
    # 输出：无证据引用被拒绝；回报失败仍保留通用版本。
    # 逻辑：真实解析器拒绝越界来源，报告异常只产生本地失败。
    # 约束：不自动重传，不将回报失败当作已保存。
    def test_citations_and_report_failure(self):
        backend = InMemoryChatBackend(
            request=conversation_request(company_id=None),
            internal=answer_context(customer_context=[]),
        )
        provider = Mock(
            return_value=json.dumps(
                {
                    "assistant_text": "事实。[1]",
                    "citations": [
                        {
                            "source_id": "unknown",
                            "source_type": "internal_knowledge",
                            "title_or_label": "unknown",
                        }
                    ],
                }
            )
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["error"]["code"], "invalid_model_output")
        backend.report_exception = RuntimeError("synthetic")
        provider.return_value = json.dumps({"assistant_text": "你好", "citations": []})
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["error"]["code"], "report_failed")
        self.assertEqual(result["chat_prompt_version"], "general-chat-v1")
