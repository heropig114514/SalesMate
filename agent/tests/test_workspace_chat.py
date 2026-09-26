"""Responsibility: Offline read-only query and evidence-closure tests for workspace chat.
Implementation: Exercise real local functions against fixed in-memory data and mocked service boundaries; assertions check outputs, errors, and interactions.
Relationships: Uses agent workflows and clients without proving live mailbox, model, or backend availability.

Directory:
- conversation_request: Build a workspace request fixture.
- context_item: Build a four-field source fixture.
- answer_context: Build a frozen context response fixture.
- citation: Build citation metadata for a fixture source.
- InMemoryChatBackend: Group offline assertions and fixture behavior for InMemoryChatBackend.
- InMemoryChatBackend.__init__: Initialize isolated fixture state and configured simulated responses.
- InMemoryChatBackend.claim_answer_request: Claim one workspace chat answer request.
- InMemoryChatBackend.get_answer_context: Read customer and knowledge context bound to a request.
- InMemoryChatBackend.report_answer: Report a chat result with its prompt version.
- detail_read_id: Build a deterministic detail-read identifier.
- detail_evidence: Build detail evidence with source identity.
- ToolBackend: Group offline assertions and fixture behavior for ToolBackend.
- ToolBackend.__init__: Initialize isolated fixture state and configured simulated responses.
- ToolBackend.get_chat_tools: Discover this request's read and experiment maintenance tools.
- ToolBackend.read_chat_tool: Execute a customer or shared experiment query and validate the response.
- QueueProvider: Group offline assertions and fixture behavior for QueueProvider.
- QueueProvider.__init__: Initialize isolated fixture state and configured simulated responses.
- QueueProvider.__call__: Record a mocked provider call and return the configured fixture output.
- search_result: Build a mocked company search receipt.
- detail_result: Build a mocked company detail receipt.
- WorkspaceChatTests: Group offline assertions and fixture behavior for WorkspaceChatTests.
- WorkspaceChatTests.test_company_bound_request_does_not_enter_legacy_chat: Verify company bound request does not enter legacy chat.
- WorkspaceChatTests.test_general_question_needs_no_customer_query: Verify general question needs no customer query.
- WorkspaceChatTests.test_direct_write_request_is_not_executed: Verify direct write request is not executed.
- WorkspaceChatTests.test_unused_citation_is_removed_and_markers_are_reordered: Verify unused citation is removed and markers are reordered.
- WorkspaceChatTests.test_search_then_context_then_evidence_based_answer: Verify search then context then evidence based answer.
- WorkspaceChatTests.test_two_companies_keep_sources_separate: Verify two companies keep sources separate.
- WorkspaceChatTests.test_context_not_found_is_answerable_without_fabrication: Verify context not found is answerable without fabrication.
- WorkspaceChatTests.test_unauthorized_source_or_write_tool_fails_closed: Verify unauthorized source or write tool fails closed.
- WorkspaceChatTests.test_tool_result_must_match_request: Verify tool result must match request.
- WorkspaceChatTests.test_request_level_not_found_is_not_treated_as_missing_customer: Verify request level not found is not treated as missing customer.
- WorkspaceChatTests.test_tool_argument_error_can_be_corrected_in_same_request: Verify tool argument error can be corrected in same request.
- WorkspaceChatTests.test_long_customer_context_is_excerpted_around_question: Verify long customer context is excerpted around question.
- WorkspaceChatTests.test_search_page_evidence_survives_many_results: Verify search page evidence survives many results.
- WorkspaceChatTests.test_full_agent_http_mapping_matches_backend_contract: Verify full agent http mapping matches backend contract.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state: Verify lost report response is confirmed from authoritative state.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state.LostResponseBackend: Group offline assertions and fixture behavior for LostResponseBackend.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state.LostResponseBackend.report_answer: Report a chat result with its prompt version.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state.LostResponseBackend.get_chat_request_status: Read the current employee's request status.

Variable index:
- COMPANY_ID: Fixed company UUID used by fixtures.
- SEARCH_READ_ID: Fixed tool-read UUID for search receipts.
- SECOND_ID: Second fixed company UUID used to distinguish sources.
"""

import copy
import json
import unittest
import uuid
from typing import Any

from agent.clients.backend_api import BackendRequestError, DjangoBackendClient
from agent.tests.test_http_backend import _Response, _Session
from agent.workflows.chat import WORKSPACE_CHAT_PROMPT_VERSION, process_chat_once


COMPANY_ID = "11111111-1111-4111-8111-111111111111"
SECOND_ID = "22222222-2222-4222-8222-222222222222"
SEARCH_READ_ID = "33333333-3333-4333-8333-333333333333"


def conversation_request(**updates: Any) -> dict[str, Any]:
    request = {
        "request_id": "request-1", "conversation_id": "conversation-1",
        "user_message_id": "message-1",
        "question": "盛微预算是多少？", "recent_history": [],
    }
    request.update(updates)
    return request


def context_item(
    *, source_id: str = "knowledge:1", source_type: str = "internal_knowledge",
    title_or_label: str = "内部知识", content: str = "已知事实。",
) -> dict[str, str]:
    return {
        "source_id": source_id, "source_type": source_type,
        "title_or_label": title_or_label, "content": content,
    }


def answer_context(*, customer_context=None, context_items=None):
    return {
        "request_id": "request-1", "scope": "internal",
        "customer_context": customer_context or [], "context_items": context_items or [],
        "customer_context_status": "completed", "knowledge_status": "completed",
        "retrieval_gaps": [], "external_available": False,
    }


def citation(item):
    return {key: item[key] for key in ("source_id", "source_type", "title_or_label")}


class InMemoryChatBackend:
    def __init__(self, *, request=None, internal=None):
        self.request = copy.deepcopy(request)
        self.internal = copy.deepcopy(internal or answer_context())
        self.claim_calls = 0
        self.context_calls = []
        self.report_calls = []

    def claim_answer_request(self):
        self.claim_calls += 1
        return copy.deepcopy(self.request)

    def get_answer_context(self, request_id, scope):
        self.context_calls.append((request_id, scope))
        return copy.deepcopy(self.internal)

    def report_answer(self, result):
        self.report_calls.append(copy.deepcopy(result))
        return {
            "request_id": result["request_id"], "saved": True, "duplicate": False,
            "assistant_message_id": "assistant-1" if result["status"] == "completed" else None,
        }


def detail_read_id(company_id):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, company_id))


def detail_evidence(company_id, name, content):
    return context_item(
        source_id=f"chat-tool:{detail_read_id(company_id)}:company:{company_id}",
        source_type="customer_context", title_or_label=f"{name} · 客户资料",
        content=content,
    )


class ToolBackend(InMemoryChatBackend):

    def __init__(self, *, request, replies, internal=None):
        super().__init__(
            request=request,
            internal=internal or answer_context(customer_context=[]),
        )
        self.replies = list(replies)
        self.tool_calls = []
        self.catalog_calls = []

    def get_chat_tools(self, request_id):
        self.catalog_calls.append(request_id)
        return {
            "contract_version": "chat-tools-v1", "request_id": request_id,
            "count": 2, "page": 1, "page_size": 30,
            "tools": [
                {
                    "name": name, "description": name, "executionMode": "read",
                    "inputSchema": {
                        "type": "object", "properties": properties,
                        "required": required, "additionalProperties": False,
                    },
                }
                for name, properties, required in (
                    ("customers.search", {"q": {"type": "string"}, "page": {"type": "integer"}, "page_size": {"type": "integer"}}, []),
                    ("customers.context", {"company_id": {"type": "string"}}, ["company_id"]),
                )
            ],
        }

    def read_chat_tool(self, request_id, name, arguments):
        self.tool_calls.append((request_id, name, copy.deepcopy(arguments)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


class QueueProvider:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, messages, *, max_tokens):
        self.calls.append((copy.deepcopy(messages), max_tokens))
        return json.dumps(self.outputs.pop(0), ensure_ascii=False)


def search_result(*rows):
    results = [{"id": company_id, "name": name} for company_id, name in rows]
    return {
        "request_id": "request-1", "tool": "customers.search", "status": "completed",
        "read_id": SEARCH_READ_ID, "http_status": 200,
        "data": {
            "count": len(rows), "page": 1, "page_size": 20,
            "results": results,
        },
        "evidence_items": [
            context_item(
                source_id=f"chat-tool:{SEARCH_READ_ID}:company:{row['id']}",
                source_type="customer_search", title_or_label=f"{row['name']} · 客户目录",
                content=json.dumps(row, ensure_ascii=False),
            ) for row in results
        ] + [context_item(
            source_id=f"chat-tool:{SEARCH_READ_ID}:page:1",
            source_type="customer_search_page", title_or_label="客户搜索 · 第 1 页",
            content=json.dumps({"count": len(rows), "page": 1}, ensure_ascii=False),
        )],
    }


def detail_result(company_id, name, evidence):
    return {
        "request_id": "request-1", "tool": "customers.context", "status": "completed",
        "read_id": detail_read_id(company_id), "http_status": 200,
        "data": {"company_id": company_id, "company_name": name},
        "evidence_items": [evidence],
    }


class WorkspaceChatTests(unittest.TestCase):
    def test_company_bound_request_does_not_enter_legacy_chat(self):
        backend = ToolBackend(
            request=conversation_request(company_id=COMPANY_ID), replies=[]
        )
        provider = QueueProvider()
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(provider.calls, [])
        self.assertEqual(backend.context_calls, [])
        self.assertEqual(backend.report_calls, [result])

    def test_general_question_needs_no_customer_query(self):
        backend = ToolBackend(
            request=conversation_request(question="你好"), replies=[]
        )
        provider = QueueProvider({
            "action": "answer", "assistant_text": "你好！需要我帮什么？", "citations": []
        })
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["chat_prompt_version"], WORKSPACE_CHAT_PROMPT_VERSION)
        self.assertEqual(backend.tool_calls, [])
        self.assertEqual(backend.catalog_calls, [])
        self.assertEqual(len(provider.calls), 1)

    def test_direct_write_request_is_not_executed(self):
        backend = ToolBackend(
            request=conversation_request(question="请帮我发送邮件给客户"), replies=[]
        )
        provider = QueueProvider()
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn("No action was taken", result["assistant_text"])
        self.assertEqual(provider.calls, [])
        self.assertEqual(backend.tool_calls, [])

    def test_english_direct_write_request_is_not_executed(self):
        backend = ToolBackend(
            request=conversation_request(question="Please send an email to the customer"), replies=[]
        )
        provider = QueueProvider()
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn("No action was taken", result["assistant_text"])
        self.assertEqual(provider.calls, [])
        self.assertEqual(backend.tool_calls, [])

    def test_unused_citation_is_removed_and_markers_are_reordered(self):
        first = detail_evidence(COMPANY_ID, "盛微", "预算 32 万。")
        second = detail_evidence(SECOND_ID, "启明", "预算 28 万。")
        backend = ToolBackend(
            request=conversation_request(question="比较盛微和启明的预算"),
            replies=[
                detail_result(COMPANY_ID, "盛微", first),
                detail_result(SECOND_ID, "启明", second),
            ],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": COMPANY_ID}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": SECOND_ID}},
            {
                "action": "answer", "assistant_text": "启明预算 28 万 [3]，盛微预算 32 万 [1]。",
                "citations": [citation(first), citation(first), citation(second)],
            },
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["assistant_text"], "启明预算 28 万 [1]，盛微预算 32 万 [2]。")
        self.assertEqual(result["citations"], [citation(second), citation(first)])

    def test_search_then_context_then_evidence_based_answer(self):
        evidence = detail_evidence(COMPANY_ID, "盛微", "客户预算 32 万元。")
        backend = ToolBackend(
            request=conversation_request(question="盛微预算是多少？"),
            replies=[search_result((COMPANY_ID, "盛微")), detail_result(COMPANY_ID, "盛微", evidence)],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": COMPANY_ID}},
            {"action": "answer", "assistant_text": "盛微预算为 32 万元 [1]。", "citations": [citation(evidence)]},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(backend.tool_calls), 2)
        self.assertEqual(backend.tool_calls[0][1], "customers.search")
        self.assertEqual(backend.tool_calls[1][1], "customers.context")
        self.assertIn("盛微 · 客户资料", provider.calls[-1][0][-1]["content"])
        self.assertEqual(backend.catalog_calls, ["request-1"])
        self.assertEqual(result["citations"], [citation(evidence)])
        self.assertEqual(backend.report_calls, [result])

    def test_two_companies_keep_sources_separate(self):
        first = detail_evidence(COMPANY_ID, "盛微", "预算 32 万。")
        second = detail_evidence(SECOND_ID, "启明", "预算 28 万。")
        backend = ToolBackend(
            request=conversation_request(question="比较盛微和启明的预算"),
            replies=[
                search_result((COMPANY_ID, "盛微"), (SECOND_ID, "启明")),
                detail_result(COMPANY_ID, "盛微", first),
                detail_result(SECOND_ID, "启明", second),
            ],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微 启明"}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": COMPANY_ID}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": SECOND_ID}},
            {"action": "answer", "assistant_text": "盛微为 32 万 [1]，启明为 28 万 [2]。", "citations": [citation(first), citation(second)]},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["citations"]), 2)

    def test_context_not_found_is_answerable_without_fabrication(self):
        backend = ToolBackend(
            request=conversation_request(question="这家公司画像是什么？"),
            replies=[
                search_result((COMPANY_ID, "盛微")),
                BackendRequestError(404, "not_found", "不可访问", scope="tool"),
            ],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": COMPANY_ID}},
            {"action": "answer", "assistant_text": "找到了客户，但当前无法读取其画像。", "citations": []},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn('"status": "unavailable"', provider.calls[-1][0][-1]["content"])

    def test_unauthorized_source_or_write_tool_fails_closed(self):
        for output in (
            {"action": "tool", "name": "customers.create", "arguments": {"name": "假客户"}},
            {"action": "answer", "assistant_text": "预算 32 万 [1]。", "citations": [{
                "source_id": "invented", "source_type": "customer_analysis", "title_or_label": "假证据",
            }]},
        ):
            with self.subTest(output=output["action"]):
                backend = ToolBackend(request=conversation_request(), replies=[])
                result = process_chat_once(
                    backend=backend, chat_provider=QueueProvider(output)
                )
                self.assertEqual(result["error"]["code"], "invalid_model_output")
                self.assertEqual(backend.tool_calls, [])

    def test_tool_result_must_match_request(self):
        wrong = search_result((COMPANY_ID, "盛微"))
        wrong["request_id"] = "other-request"
        backend = ToolBackend(
            request=conversation_request(), replies=[wrong]
        )
        provider = QueueProvider({
            "action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}
        })
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["error"]["code"], "context_unavailable")

    def test_request_level_not_found_is_not_treated_as_missing_customer(self):
        backend = ToolBackend(
            request=conversation_request(),
            replies=[BackendRequestError(404, "not_found", "请求不存在", scope="request")],
        )
        provider = QueueProvider({
            "action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}
        })
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["error"]["code"], "context_unavailable")
        self.assertEqual(len(provider.calls), 1)

    def test_tool_argument_error_can_be_corrected_in_same_request(self):
        backend = ToolBackend(
            request=conversation_request(),
            replies=[
                BackendRequestError(400, "invalid", "参数错误", scope="tool"),
                search_result((COMPANY_ID, "盛微")),
            ],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}},
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微公司"}},
            {"action": "answer", "assistant_text": "找到盛微 [1]。", "citations": [
                citation(search_result((COMPANY_ID, "盛微"))["evidence_items"][0])
            ]},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn('"invalid_arguments"', provider.calls[1][0][-1]["content"])

    def test_long_customer_context_is_excerpted_around_question(self):
        evidence = detail_evidence(
            COMPANY_ID, "盛微", "其他字段" * 5000 + "预算 32 万元" + "其他字段" * 5000
        )
        backend = ToolBackend(
            request=conversation_request(question="盛微预算是多少？"),
            replies=[search_result((COMPANY_ID, "盛微")), detail_result(COMPANY_ID, "盛微", evidence)],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": COMPANY_ID}},
            {"action": "answer", "assistant_text": "预算为 32 万元 [1]。", "citations": [citation(evidence)]},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        prompt = provider.calls[-1][0][-1]["content"]
        self.assertEqual(result["status"], "completed")
        self.assertIn("预算 32 万元", prompt)
        self.assertIn("[Excerpt; full source not provided]", prompt)
        self.assertLess(len(prompt), 8000)

    def test_search_page_evidence_survives_many_results(self):
        rows = [
            (str(uuid.uuid5(uuid.NAMESPACE_DNS, str(index))), f"公司{index}")
            for index in range(20)
        ]
        backend = ToolBackend(
            request=conversation_request(question="有多少客户？"),
            replies=[search_result(*rows)],
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {}},
            {"action": "answer", "assistant_text": "本页搜索共返回 20 家 [1]。", "citations": [
                citation(search_result(*rows)["evidence_items"][-1])
            ]},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        prompt = json.loads(provider.calls[-1][0][-1]["content"])
        self.assertEqual(prompt["evidence_items_shown"], 12)
        self.assertEqual(prompt["authorized_evidence"][0]["source_type"], "customer_search_page")
        self.assertEqual(prompt["evidence_items_available"], 21)

    def test_full_agent_http_mapping_matches_backend_contract(self):
        request = conversation_request(question="盛微预算是多少？")
        evidence = detail_evidence(COMPANY_ID, "盛微", "预算 32 万元。")
        catalog = ToolBackend(request=request, replies=[]).get_chat_tools("request-1")
        session = _Session(
            _Response({"request": {**request, "company_id": None}}),
            _Response(answer_context(customer_context=[])),
            _Response(catalog),
            _Response(search_result((COMPANY_ID, "盛微"))),
            _Response(detail_result(COMPANY_ID, "盛微", evidence)),
            _Response({
                "request_id": "request-1", "saved": True, "duplicate": False,
                "assistant_message_id": "assistant-1",
            }),
        )
        backend = DjangoBackendClient(
            "http://backend.test/api/v1/agent/", "employee-token",
            session=session,
        )
        provider = QueueProvider(
            {"action": "tool", "name": "customers.search", "arguments": {"q": "盛微"}},
            {"action": "tool", "name": "customers.context", "arguments": {"company_id": COMPANY_ID}},
            {"action": "answer", "assistant_text": "预算 32 万元 [1]。", "citations": [citation(evidence)]},
        )
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call[0] for call in session.calls], [
            "POST", "POST", "GET", "POST", "POST", "POST",
        ])
        self.assertTrue(all(
            call[2]["headers"]["Authorization"] == "Agent employee-token"
            for call in session.calls
        ))

    def test_lost_report_response_is_confirmed_from_authoritative_state(self):
        class LostResponseBackend(ToolBackend):
            def report_answer(self, result):
                self.report_calls.append(copy.deepcopy(result))
                self.saved = result
                raise BackendRequestError(0, "network_error", "response_lost")

            def get_chat_request_status(self, request_id):
                return {
                    "request_id": request_id, "status": self.saved["status"],
                    "chat_prompt_version": self.saved["chat_prompt_version"],
                    "assistant_message_id": "assistant-1", "citations": [],
                }

        backend = LostResponseBackend(
            request=conversation_request(question="你好"), replies=[]
        )
        provider = QueueProvider({
            "action": "answer", "assistant_text": "你好！", "citations": []
        })
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(backend.report_calls), 1)
