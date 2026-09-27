"""Responsibility: Offline read-only query and evidence-closure tests for workspace chat.
Implementation: Customer creation is now approved separately; the forbidden-write case checks unexposed customer deletion. Exercise real local functions against fixed in-memory data and mocked service boundaries; assertions check outputs, errors, and interactions.
Relationships: Uses agent workflows and clients without proving live mailbox, model, or backend availability.

Directory:
- conversation_request: Construct the canonical five-field request and apply explicit overrides for negative cases.
- context_item: Return exactly the four evidence identity/content fields with supplied defaults.
- answer_context: Build a completed internal context with supplied source arrays and external access disabled.
- citation: Project only source identity metadata, excluding content from model citations.
- InMemoryChatBackend: Record in-memory transport interactions and deep-copy fixtures to isolate each assertion.
- InMemoryChatBackend.__init__: Store deep-copied claim/context and initialize call counters; no transport opens.
- InMemoryChatBackend.claim_answer_request: Increment the claim count and return a deep copy of the configured request.
- InMemoryChatBackend.get_answer_context: Record request/scope and return a deep copy of the configured context.
- InMemoryChatBackend.report_answer: Record a deep copy and return a saved receipt with an assistant ID only on completion.
- detail_read_id: Derive a reproducible UUID5 from the company identifier for source binding.
- detail_evidence: Combine that read UUID, customer label and supplied text into one detail source.
- ToolBackend: Extend the fake backend with a two-read catalog and an ordered tool-response queue.
- ToolBackend.__init__: Initialize inherited state, a copied reply queue and separate tool/catalog logs.
- ToolBackend.get_chat_tools: Record discovery and publish closed read-only search/context schemas bound to the request.
- ToolBackend.read_chat_tool: Record copied arguments; consume one reply, propagating configured exceptions or copying response data.
- QueueProvider: Replace model selection with ordered JSON decisions and recorded prompt/token arguments.
- QueueProvider.__init__: Copy the output queue and initialize prompt-call capture.
- QueueProvider.__call__: Record a deep copy of messages and max_tokens; pop and encode exactly one decision.
- search_result: Create bounded search data plus per-company and page evidence using a fixed read UUID.
- detail_result: Bind supplied customer identity and evidence to a completed customer-context receipt.
- WorkspaceChatTests: Run real workspace parsing/planning orchestration against deterministic model and transport fixtures.
- WorkspaceChatTests.test_company_bound_request_does_not_enter_legacy_chat: Reject a preselected company before context/model calls and report invalid_request.
- WorkspaceChatTests.test_general_question_needs_no_customer_query: Verify a greeting completes in one model call without tool discovery or query.
- WorkspaceChatTests.test_direct_write_request_is_not_executed: Given a simulated clarification answer to a Chinese send request, assert no tool call and no claimed action.
- WorkspaceChatTests.test_english_direct_write_request_is_not_executed: Repeat the clarification-only behavior for an English send request with no business tool execution.
- WorkspaceChatTests.test_unused_citation_is_removed_and_markers_are_reordered: Read two sources; assert duplicate/unused entries are removed and markers follow first use.
- WorkspaceChatTests.test_search_then_context_then_evidence_based_answer: Check discovery once, two ordered reads, visible detail evidence and saved matching citations.
- WorkspaceChatTests.test_two_companies_keep_sources_separate: Read two distinct customer contexts and verify both evidence identities survive the final answer.
- WorkspaceChatTests.test_context_not_found_is_answerable_without_fabrication: Simulate a tool-scoped 404; expose unavailability to the model and allow an honest final answer.
- WorkspaceChatTests.test_unauthorized_source_or_write_tool_fails_closed: Reject unexposed customers.delete and fabricated source citations before any tool call; customers.create is tested separately as a resumable approval.
- WorkspaceChatTests.test_tool_result_must_match_request: Return another request ID and require context_unavailable instead of consuming foreign evidence.
- WorkspaceChatTests.test_request_level_not_found_is_not_treated_as_missing_customer: A request-scoped 404 terminates processing after one model call instead of becoming missing customer evidence.
- WorkspaceChatTests.test_tool_argument_error_can_be_corrected_in_same_request: Expose a tool-scoped 400 and accept a distinct corrected query; no identical automatic retry occurs.
- WorkspaceChatTests.test_long_customer_context_is_excerpted_around_question: Keep the relevant budget span from a long source and an excerpt marker inside the existing prompt bound.
- WorkspaceChatTests.test_customer_excerpt_retains_later_revision_within_budget: Check the excerpt retains both early pending and later approved budget updates within 1200 characters.
- WorkspaceChatTests.test_plain_email_draft_can_drop_unused_authorized_citations: Remove unused authorized citations from plain drafts but reject invalid markers or unauthorized sources.
- WorkspaceChatTests.test_search_page_evidence_survives_many_results: For twenty rows, verify page evidence remains visible under the twelve-source display limit.
- WorkspaceChatTests.test_full_agent_http_mapping_matches_backend_contract: Run the actual HTTP client against a fake session; assert ordered methods and Agent headers for the read/answer cycle.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state: Lose the report response after simulated persistence and confirm success by one authoritative status read without reporting twice.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state.LostResponseBackend: Simulate a lost report response while retaining its authoritative saved state.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state.LostResponseBackend.report_answer: Capture the result then raise a network error, modelling lost acknowledgement after persistence.
- WorkspaceChatTests.test_lost_report_response_is_confirmed_from_authoritative_state.LostResponseBackend.get_chat_request_status: Project the saved result with matching request/prompt/status and empty citations for reconciliation.

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


# Function: Construct the canonical five-field request and apply explicit overrides for negative cases.
# Inputs: `updates` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Construct the canonical five-field request and apply explicit overrides for negative cases.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def conversation_request(**updates: Any) -> dict[str, Any]:
    request = {
        "request_id": "request-1", "conversation_id": "conversation-1",
        "user_message_id": "message-1",
        "question": "盛微预算是多少？", "recent_history": [],
    }
    request.update(updates)
    return request


# Function: Return exactly the four evidence identity/content fields with supplied defaults.
# Inputs: `source_id`, `source_type`, `title_or_label`, `content` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Return exactly the four evidence identity/content fields with supplied defaults.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def context_item(
    *, source_id: str = "knowledge:1", source_type: str = "internal_knowledge",
    title_or_label: str = "内部知识", content: str = "已知事实。",
) -> dict[str, str]:
    return {
        "source_id": source_id, "source_type": source_type,
        "title_or_label": title_or_label, "content": content,
    }


# Function: Build a completed internal context with supplied source arrays and external access disabled.
# Inputs: `customer_context`, `context_items` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Build a completed internal context with supplied source arrays and external access disabled.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def answer_context(*, customer_context=None, context_items=None):
    return {
        "request_id": "request-1", "scope": "internal",
        "customer_context": customer_context or [], "context_items": context_items or [],
        "customer_context_status": "completed", "knowledge_status": "completed",
        "retrieval_gaps": [], "external_available": False,
    }


# Function: Project only source identity metadata, excluding content from model citations.
# Inputs: `item` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Project only source identity metadata, excluding content from model citations.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def citation(item):
    return {key: item[key] for key in ("source_id", "source_type", "title_or_label")}


# Function: Record in-memory transport interactions and deep-copy fixtures to isolate each assertion.
# Logic: Record in-memory transport interactions and deep-copy fixtures to isolate each assertion.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
class InMemoryChatBackend:
    # Function: Store deep-copied claim/context and initialize call counters; no transport opens.
    # Inputs: `request`, `internal` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Store deep-copied claim/context and initialize call counters; no transport opens.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def __init__(self, *, request=None, internal=None):
        self.request = copy.deepcopy(request)
        self.internal = copy.deepcopy(internal or answer_context())
        self.claim_calls = 0
        self.context_calls = []
        self.report_calls = []

    # Function: Increment the claim count and return a deep copy of the configured request.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Increment the claim count and return a deep copy of the configured request.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def claim_answer_request(self):
        self.claim_calls += 1
        return copy.deepcopy(self.request)

    # Function: Record request/scope and return a deep copy of the configured context.
    # Inputs: `request_id`, `scope` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Record request/scope and return a deep copy of the configured context.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def get_answer_context(self, request_id, scope):
        self.context_calls.append((request_id, scope))
        return copy.deepcopy(self.internal)

    # Function: Record a deep copy and return a saved receipt with an assistant ID only on completion.
    # Inputs: `result` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Record a deep copy and return a saved receipt with an assistant ID only on completion.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def report_answer(self, result):
        self.report_calls.append(copy.deepcopy(result))
        return {
            "request_id": result["request_id"], "saved": True, "duplicate": False,
            "assistant_message_id": "assistant-1" if result["status"] == "completed" else None,
        }


# Function: Derive a reproducible UUID5 from the company identifier for source binding.
# Inputs: `company_id` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Derive a reproducible UUID5 from the company identifier for source binding.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def detail_read_id(company_id):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, company_id))


# Function: Combine that read UUID, customer label and supplied text into one detail source.
# Inputs: `company_id`, `name`, `content` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Combine that read UUID, customer label and supplied text into one detail source.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def detail_evidence(company_id, name, content):
    return context_item(
        source_id=f"chat-tool:{detail_read_id(company_id)}:company:{company_id}",
        source_type="customer_context", title_or_label=f"{name} · 客户资料",
        content=content,
    )


# Function: Extend the fake backend with a two-read catalog and an ordered tool-response queue.
# Logic: Extend the fake backend with a two-read catalog and an ordered tool-response queue.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
class ToolBackend(InMemoryChatBackend):

    # Function: Initialize inherited state, a copied reply queue and separate tool/catalog logs.
    # Inputs: `request`, `replies`, `internal` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Initialize inherited state, a copied reply queue and separate tool/catalog logs.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def __init__(self, *, request, replies, internal=None):
        super().__init__(
            request=request,
            internal=internal or answer_context(customer_context=[]),
        )
        self.replies = list(replies)
        self.tool_calls = []
        self.catalog_calls = []

    # Function: Record discovery and publish closed read-only search/context schemas bound to the request.
    # Inputs: `request_id` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Record discovery and publish closed read-only search/context schemas bound to the request.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Record copied arguments; consume one reply, propagating configured exceptions or copying response data.
    # Inputs: `request_id`, `name`, `arguments` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Record copied arguments; consume one reply, propagating configured exceptions or copying response data.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def read_chat_tool(self, request_id, name, arguments):
        self.tool_calls.append((request_id, name, copy.deepcopy(arguments)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


# Function: Replace model selection with ordered JSON decisions and recorded prompt/token arguments.
# Logic: Replace model selection with ordered JSON decisions and recorded prompt/token arguments.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
class QueueProvider:
    # Function: Copy the output queue and initialize prompt-call capture.
    # Inputs: `outputs` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Copy the output queue and initialize prompt-call capture.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    # Function: Record a deep copy of messages and max_tokens; pop and encode exactly one decision.
    # Inputs: `messages`, `max_tokens` as explicit fixture values; instance state stores configured fake responses and recorded calls.
    # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
    # Logic: Record a deep copy of messages and max_tokens; pop and encode exactly one decision.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def __call__(self, messages, *, max_tokens):
        self.calls.append((copy.deepcopy(messages), max_tokens))
        return json.dumps(self.outputs.pop(0), ensure_ascii=False)


# Function: Create bounded search data plus per-company and page evidence using a fixed read UUID.
# Inputs: `rows` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Create bounded search data plus per-company and page evidence using a fixed read UUID.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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


# Function: Bind supplied customer identity and evidence to a completed customer-context receipt.
# Inputs: `company_id`, `name`, `evidence` as explicit fixture values; instance state stores configured fake responses and recorded calls.
# Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
# Logic: Bind supplied customer identity and evidence to a completed customer-context receipt.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
def detail_result(company_id, name, evidence):
    return {
        "request_id": "request-1", "tool": "customers.context", "status": "completed",
        "read_id": detail_read_id(company_id), "http_status": 200,
        "data": {"company_id": company_id, "company_name": name},
        "evidence_items": [evidence],
    }


# Function: Run real workspace parsing/planning orchestration against deterministic model and transport fixtures.
# Logic: Run real workspace parsing/planning orchestration against deterministic model and transport fixtures.
# Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
class WorkspaceChatTests(unittest.TestCase):
    # Function: Reject a preselected company before context/model calls and report invalid_request.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Reject a preselected company before context/model calls and report invalid_request.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Verify a greeting completes in one model call without tool discovery or query.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Verify a greeting completes in one model call without tool discovery or query.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Given a simulated clarification answer to a Chinese send request, assert no tool call and no claimed action.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Given a simulated clarification answer to a Chinese send request, assert no tool call and no claimed action.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def test_direct_write_request_is_not_executed(self):
        backend = ToolBackend(
            request=conversation_request(question="请帮我发送邮件给客户"), replies=[]
        )
        provider = QueueProvider({"action": "answer", "assistant_text": "Please specify the recipient and email content. No action was taken.", "citations": []})
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn("No action was taken", result["assistant_text"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(backend.tool_calls, [])

    # Function: Repeat the clarification-only behavior for an English send request with no business tool execution.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Repeat the clarification-only behavior for an English send request with no business tool execution.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def test_english_direct_write_request_is_not_executed(self):
        backend = ToolBackend(
            request=conversation_request(question="Please send an email to the customer"), replies=[]
        )
        provider = QueueProvider({"action": "answer", "assistant_text": "Please specify the recipient and email content. No action was taken.", "citations": []})
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed")
        self.assertIn("No action was taken", result["assistant_text"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(backend.tool_calls, [])

    # Function: Read two sources; assert duplicate/unused entries are removed and markers follow first use.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Read two sources; assert duplicate/unused entries are removed and markers follow first use.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Check discovery once, two ordered reads, visible detail evidence and saved matching citations.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Check discovery once, two ordered reads, visible detail evidence and saved matching citations.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Read two distinct customer contexts and verify both evidence identities survive the final answer.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Read two distinct customer contexts and verify both evidence identities survive the final answer.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Simulate a tool-scoped 404; expose unavailability to the model and allow an honest final answer.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Simulate a tool-scoped 404; expose unavailability to the model and allow an honest final answer.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Reject unexposed customers.delete and fabricated source citations before any tool call; customers.create is tested separately as a resumable approval.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Reject unexposed customers.delete and fabricated source citations before any tool call; customers.create is tested separately as a resumable approval.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def test_unauthorized_source_or_write_tool_fails_closed(self):
        for output in (
            {"action": "tool", "name": "customers.delete", "arguments": {"company_id": COMPANY_ID}},
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

    # Function: Return another request ID and require context_unavailable instead of consuming foreign evidence.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Return another request ID and require context_unavailable instead of consuming foreign evidence.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: A request-scoped 404 terminates processing after one model call instead of becoming missing customer evidence.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: A request-scoped 404 terminates processing after one model call instead of becoming missing customer evidence.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Expose a tool-scoped 400 and accept a distinct corrected query; no identical automatic retry occurs.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Expose a tool-scoped 400 and accept a distinct corrected query; no identical automatic retry occurs.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Keep the relevant budget span from a long source and an excerpt marker inside the existing prompt bound.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Keep the relevant budget span from a long source and an excerpt marker inside the existing prompt bound.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Check the excerpt retains both early pending and later approved budget updates within 1200 characters.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Check the excerpt retains both early pending and later approved budget updates within 1200 characters.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def test_customer_excerpt_retains_later_revision_within_budget(self):
        from agent.workflows.chat import _workspace_excerpt
        content = ("2026-09-17: Budget approval pending. " + "Earlier technical notes. " * 200
                   + "2026-09-25: Budget approved at SGD 75,000; quantity remains 500.")
        excerpt = _workspace_excerpt(content, "Summarize requirements and budget", 1200)
        self.assertIn("Budget approval pending", excerpt)
        self.assertIn("Budget approved at SGD 75,000", excerpt)
        self.assertIn("[Excerpt; full source not provided]", excerpt)
        self.assertLessEqual(len(excerpt), 1200)

    # Function: Remove unused authorized citations from plain drafts but reject invalid markers or unauthorized sources.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Remove unused authorized citations from plain drafts but reject invalid markers or unauthorized sources.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def test_plain_email_draft_can_drop_unused_authorized_citations(self):
        from agent.workflows.chat import parse_model_candidate, ChatValidationError
        evidence = detail_evidence(COMPANY_ID, "BioTech", "Requested 500 sensors.")
        draft = "Dear Daniel,\nThank you for your inquiry.\nBest regards"
        result = parse_model_candidate(
            {"assistant_text": draft, "citations": [citation(evidence)]},
            allowed_context_items=[evidence],
        )
        self.assertEqual(result, {"assistant_text": draft, "citations": []})
        with self.assertRaises(ChatValidationError):
            parse_model_candidate({"assistant_text": draft + " [2]", "citations": [citation(evidence)]},
                                  allowed_context_items=[evidence])
        with self.assertRaises(ChatValidationError):
            parse_model_candidate({"assistant_text": draft, "citations": [citation(evidence)]},
                                  allowed_context_items=[])

    # Function: For twenty rows, verify page evidence remains visible under the twelve-source display limit.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: For twenty rows, verify page evidence remains visible under the twelve-source display limit.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Run the actual HTTP client against a fake session; assert ordered methods and Agent headers for the read/answer cycle.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Run the actual HTTP client against a fake session; assert ordered methods and Agent headers for the read/answer cycle.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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

    # Function: Lose the report response after simulated persistence and confirm success by one authoritative status read without reporting twice.
    # Inputs: Isolated in-memory fixtures and unittest instance; model/HTTP boundaries are simulated.
    # Outputs: Assertion success or unittest failure; no persistent business mutation.
    # Logic: Lose the report response after simulated persistence and confirm success by one authoritative status read without reporting twice.
    # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
    def test_lost_report_response_is_confirmed_from_authoritative_state(self):
        # Function: Simulate a lost report response while retaining its authoritative saved state.
        # Logic: Simulate a lost report response while retaining its authoritative saved state.
        # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
        class LostResponseBackend(ToolBackend):
            # Function: Capture the result then raise a network error, modelling lost acknowledgement after persistence.
            # Inputs: `result` as explicit fixture values; instance state stores configured fake responses and recorded calls.
            # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
            # Logic: Capture the result then raise a network error, modelling lost acknowledgement after persistence.
            # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
            def report_answer(self, result):
                self.report_calls.append(copy.deepcopy(result))
                self.saved = result
                raise BackendRequestError(0, "network_error", "response_lost")

            # Function: Project the saved result with matching request/prompt/status and empty citations for reconciliation.
            # Inputs: `request_id` as explicit fixture values; instance state stores configured fake responses and recorded calls.
            # Outputs: Configured fixture data or updated call-capture state; explicitly simulated exceptions propagate.
            # Logic: Project the saved result with matching request/prompt/status and empty citations for reconciliation.
            # Constraints: Offline deterministic test boundary; no actual model, HTTP server, mailbox or production availability is established.
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
