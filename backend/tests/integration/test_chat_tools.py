"""Responsibility: Verify the complete backend path for workspace chat read-only interfaces, stable evidence, and original business permissions.
Implementation: Use real PostgreSQL, authenticated HTTP and business handlers; verify complete catalog, checkpointed writes and unchanged generic receipts plus chat-only email ownership annotations; simulate failures and concurrent waits at explicit boundaries.
Relationships: chat.tool_reads/tool_views, the agent_tools registry, and chat.services; no external model or mailbox calls.
Directory:
- ChatToolTests: Integration tests for request-bound tool services.
- ChatToolTests.setUp: Create two employees, general chat, and Agent identities.
- ChatToolTests.read: Send one request-bound tool call.
- ChatToolTests.test_catalog_and_schema: Catalog isolation, schema consistency, and pagination arguments.
- ChatToolTests.test_search_context_answer_round_trip: Search, details for two companies, context compatibility, and citation persistence.
- ChatToolTests.test_search_pagination_and_empty: Pagination completeness and empty-result evidence.
- ChatToolTests.test_shared_search_does_not_grant_detail: Shared directory visibility with private-detail rejection while chat can still complete.
- ChatToolTests.test_registry_and_argument_schema: Reject identity injection, malformed registered tools and unexposed writes.
- ChatToolTests.test_auth_request_and_terminal_boundaries: Credential isolation, request ownership, and terminal-state rejection.
- ChatToolTests.test_read_versions_are_immutable: Repeated reads do not overwrite old evidence.
- ChatToolTests.test_failure_rolls_back_and_hides_internal_error: Exception rollback, safe errors, and retained state.
- ChatToolTests.test_registry_mode_is_rechecked: Retain discovery and enforce the changed execution mode.
- ChatToolConcurrencyTests: Serialization tests for tool reads and final reports.
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read: Allow request completion only after in-flight reads finish.
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read.blocked_execute: Place a synchronization barrier before the real handler.
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read.read: Execute the tool service on an independent database connection.
- ChatToolConcurrencyTests.test_answer_waits_for_inflight_read.answer: Save the final answer on an independent database connection.
Variable index:
- BASE: Agent chat service prefix.
"""

import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from apps.agent_tools import services as tool_services
from apps.agent_tools.dispatch import execute
from apps.agent_tools.registry import build_registry
from apps.chat import services, tool_reads, action_contract
from apps.chat.models import AnswerRequest, ToolRead
from apps.crm.models import Company, Extraction
from apps.sales.models import CompanyGrant, Conversation, Membership, Team
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from tests.integration.test_chat import fixture, result_for

BASE = "/api/v1/agent/chat/"


# Function: Verify read-only tool services integrate with existing business queries.
# Logic: Use real employee service credentials, general requests, and database records; HTTP does not bypass authentication.
# Constraints: Roll back each test; do not connect to models, real customers, or external business services.
class ChatToolTests(TestCase):
    # Function: Create a processing request without a company binding.
    # Inputs: No external arguments; reuse isolated chat fixtures.
    # Outputs: Employees, two owned companies, another employee's company, a request, and an HTTP client.
    # Logic: Populate email payload and the Extraction required by ingestion, then submit and claim through a general conversation without company_id.
    # Constraints: Credentials are restricted to the test database; the other employee's company is initially invisible.
    def setUp(self):
        self.owner, self.other, self.company, _ = fixture()
        email = self.company.emails.get()
        email.payload = {
            **email.payload,
            "dedupe_key": email.pk,
            "direction": email.direction,
            "sent_at": email.sent_at.isoformat(),
            "received_at": email.received_at.isoformat(),
            "source": "manual_test",
        }
        email.save(update_fields=["payload"])
        Extraction.objects.create(
            email=email,
            prompt_version="synthetic-extraction",
            status="failed",
            facts=None,
            error="测试夹具未运行模型抽取",
        )
        self.second = Company.objects.create(
            owner=self.owner, name="第二客户", group_key="manual:second"
        )
        self.foreign = Company.objects.create(
            owner=self.other, name="共享客户", group_key="manual:foreign"
        )
        conversation = Conversation.objects.create(owner=self.owner)
        self.request, _ = services.submit(
            self.owner,
            {
                "conversation_id": str(conversation.pk),
                "content": "比较客户",
                "client_key": str(uuid.uuid4()),
            },
        )
        services.claim(self.owner)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Agent chat-test-token")

    # Function: Send one genuinely authenticated read-only query.
    # Inputs: `name` identifies the tool, `arguments` contains its arguments, and `request_id` may override the default to verify isolation.
    # Outputs: Original HTTP response.
    # Logic: Bind to this test's processing request by default, preserving errors for caller assertions.
    # Constraints: Do not retry automatically or fill company arguments.
    def read(self, name, arguments, request_id=None):
        return self.client.post(
            BASE + "tool-reads/",
            {
                "request_id": str(request_id or self.request.pk),
                "name": name,
                "arguments": arguments,
            },
            format="json",
        )

    # Function: Verify the catalog reflects the complete registry and original argument contracts accurately.
    # Inputs: Processing request, pagination parameters, and invalid query variants.
    # Outputs: All published tools with exact read/write/confirm modes and 400 for invalid queries.
    # Logic: Compare paginated registry schemas, including generic operations and three independent proposal capabilities.
    # Constraints: Customer creation and three experiment writes require checkpoints; confirmation proposals never execute immediately.
    def test_catalog_and_schema(self):
        response = self.client.get(
            BASE + "tools/", {"request_id": str(self.request.pk)}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.data["count"], len(build_registry()) + len(action_contract.ACTION_TOOLS))
        self.assertEqual(
            {row["name"] for row in response.data["tools"]},
            set(sorted(set(build_registry()) | action_contract.ACTION_TOOLS)[:30]),
        )
        registry = {**build_registry(), **action_contract.catalog()}
        for row in response.data["tools"]:
            self.assertEqual(row["inputSchema"], registry[row["name"]]["inputSchema"])
            self.assertEqual(row["executionMode"], registry[row["name"]]["executionMode"])
        page = self.client.get(
            BASE + "tools/",
            {"request_id": str(self.request.pk), "page": 2, "page_size": 1},
        )
        self.assertEqual((page.data["count"], len(page.data["tools"])), (len(build_registry()) + 3, 1))
        for query in (
            {},
            {"page": "x"},
            {"page": 0},
            {"page_size": 101},
            {"owner_id": self.other.pk},
        ):
            params = {"request_id": str(self.request.pk), **query} if query else {}
            self.assertEqual(self.client.get(BASE + "tools/", params).status_code, 400)
        self.assertEqual(
            self.client.get(
                BASE + f"tools/?request_id={self.request.pk}&page=1&page=2"
            ).status_code,
            400,
        )

    # Function: Verify original context, tool sources from two companies, and final browser citations work together.
    # Inputs: Original snapshot, customer search, and two detail reads.
    # Outputs: Business data matches original tools except verified chat email-ownership annotations; sources remain distinct and browsers receive only cited content.
    # Logic: Compare complete search payloads after explicitly adding expected ownership for owned fixtures, then report through HTTP and read persisted status/citations.
    # Constraints: Do not interpret synthetic test answers as real-model tool orchestration.
    def test_search_context_answer_round_trip(self):
        context = services.context_for(self.owner, self.request.pk, "internal")
        search = self.read("customers.search", {"q": "客户"})
        self.assertEqual(search.status_code, 200, search.data)
        expected = tool_services.invoke(self.owner, None, "customers.search", {"q": "客户"})["data"]
        expected["results"] = [{**row, "email_preparation_owned": True} for row in expected["results"]]
        self.assertEqual(search.data["data"], expected)
        items = []
        for company in (self.company, self.second):
            detail = self.read("customers.context", {"company_id": str(company.pk)})
            self.assertEqual(detail.status_code, 200, detail.data)
            item = detail.data["evidence_items"][0]
            self.assertEqual(json.loads(item["content"]), detail.data["data"])
            self.assertIn(str(company.pk), item["source_id"])
            items.append(item)
        self.assertNotEqual(items[0]["source_id"], items[1]["source_id"])
        self.assertEqual(
            services.context_for(self.owner, self.request.pk, "internal"), context
        )
        result = {
            **result_for(self.request),
            "chat_prompt_version": "workspace-chat-v1",
            "citations": [
                {
                    key: item[key]
                    for key in ("source_id", "source_type", "title_or_label")
                }
                for item in items
            ],
        }
        saved = self.client.post(BASE + "answers/", result, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        status = self.client.get(BASE + f"requests/{self.request.pk}/")
        self.assertEqual(status.data["status"], "completed")
        self.assertEqual(
            [row["content"] for row in status.data["citations"]],
            [item["content"] for item in items],
        )
        self.assertNotIn("tool_reads", status.data)
        self.assertNotIn("context_snapshot", status.data)
        self.assertEqual(ToolRead.objects.filter(request=self.request).count(), 3)
        browser = APIClient()
        browser.force_authenticate(self.owner)
        public = browser.get(f"/api/v1/sales/chat/requests/{self.request.pk}/")
        self.assertEqual(public.data["citations"], status.data["citations"])

    # Function: Verify paginated data is not truncated into a false complete dataset and empty searches still provide usable result evidence.
    # Inputs: Two visible customers, one row per page, and a nonexistent keyword.
    # Outputs: Total count is two, page numbers are accurate, and an empty query returns completed/count=0.
    # Logic: Check results and page-level sources individually, ensuring other employees' companies are absent.
    # Constraints: Verify backend pagination only; do not claim the Agent traversed all pages.
    def test_search_pagination_and_empty(self):
        seen = []
        for page in (1, 2):
            response = self.read("customers.search", {"page": page, "page_size": 1})
            self.assertEqual(response.status_code, 200, response.data)
            data = response.data["data"]
            self.assertEqual(
                (data["count"], data["page"], data["page_size"]), (2, page, 1)
            )
            seen.extend(row["id"] for row in data["results"])
        self.assertEqual(set(seen), {str(self.company.pk), str(self.second.pk)})
        empty = self.read("customers.search", {"q": "不存在的客户关键词"})
        self.assertEqual(empty.data["status"], "completed")
        self.assertEqual(empty.data["data"]["results"], [])
        self.assertEqual(
            json.loads(empty.data["evidence_items"][0]["content"])["count"], 0
        )

    # Function: Verify shared search scope does not expand private-detail access.
    # Inputs: Another employee shares a company with the current employee's team.
    # Outputs: Search returns a result, details return 404 with scope=tool, and a normal answer can still be saved.
    # Logic: Use existing team-grant models and real customer handlers.
    # Constraints: Failures register no evidence and do not mark the request failed.
    def test_shared_search_does_not_grant_detail(self):
        team = Team.objects.create(owner=self.other, name="共享团队")
        Membership.objects.create(
            owner=self.other, team=team, user=self.owner, role="viewer"
        )
        CompanyGrant.objects.create(
            owner=self.other, company=self.foreign, team=team, role="viewer"
        )
        search = self.read("customers.search", {"q": "共享客户"})
        self.assertEqual(search.data["data"]["count"], 1)
        detail = self.read("customers.context", {"company_id": str(self.foreign.pk)})
        self.assertEqual(detail.status_code, 404, detail.data)
        self.assertEqual(detail.data["error"]["scope"], "tool")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "processing")
        self.assertFalse(ToolRead.objects.filter(tool="customers.context").exists())
        saved = self.client.post(
            BASE + "answers/",
            {**result_for(self.request), "assistant_text": "详情不可用。"},
            format="json",
        )
        self.assertEqual(saved.status_code, 200)

    # Function: Verify the live registry and argument schema prevent unauthorized access through identity parameters.
    # Inputs: Additional identity/idempotency fields, missing locator arguments, invalid pagination, and unregistered names.
    # Outputs: Structural errors return 400, tool-scope errors return 403, and no read records are created.
    # Logic: Reject unregistered names and malformed arguments for published customer, mailbox and knowledge tools.
    # Constraints: Do not guess customers by removing company_id locator requirements.
    def test_registry_and_argument_schema(self):
        for name in (
            "customers.delete",
            "nonexistent",
        ):
            self.assertEqual(self.read(name, {}).status_code, 403)
        for name, args in (
            ("customers.analyze", {}),
            ("mailboxes.sync", {}),
            ("knowledge.get", {}),
            ("customers.create", {}),
            ("customers.context", {}),
            ("customers.context", {"company_id": "bad"}),
            ("customers.search", {"owner_id": self.other.pk}),
            ("customers.search", {"page_size": 101}),
        ):
            response = self.read(name, args)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(response.data["error"]["scope"], "tool")
        payload = {
            "request_id": str(self.request.pk),
            "name": "customers.search",
            "arguments": {},
        }
        for key, value in (
            ("owner_id", self.other.pk),
            ("idempotency_key", str(uuid.uuid4())),
        ):
            response = self.client.post(
                BASE + "tool-reads/", {**payload, key: value}, format="json"
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["error"]["scope"], "request")
        self.assertFalse(ToolRead.objects.exists())

    # Function: Verify credential, request-ownership, and lifecycle boundaries.
    # Inputs: Missing credentials, Tool credentials, Session, another employee's request, and the current request's terminal state.
    # Outputs: Authentication failures return 401, unauthorized access 404, and terminal-state reads 409; status queries can still read the caller's terminal requests.
    # Logic: Use real authentication and request queries separately, without requiring a company binding for access.
    # Constraints: Do not revive terminal requests or restore revoked conversations.
    def test_auth_request_and_terminal_boundaries(self):
        payload = {
            "request_id": str(self.request.pk),
            "name": "customers.search",
            "arguments": {},
        }
        denied = APIClient()
        self.assertEqual(
            denied.post(BASE + "tool-reads/", payload, format="json").status_code, 401
        )
        denied.force_login(self.owner)
        self.assertEqual(
            denied.post(BASE + "tool-reads/", payload, format="json").status_code, 401
        )
        denied.credentials(HTTP_AUTHORIZATION="Tool chat-test-token")
        self.assertEqual(
            denied.post(BASE + "tool-reads/", payload, format="json").status_code, 401
        )
        conversation = Conversation.objects.create(owner=self.other)
        foreign, _ = services.submit(
            self.other,
            {
                "conversation_id": str(conversation.pk),
                "content": "他人",
                "client_key": str(uuid.uuid4()),
            },
        )
        for request_id in (foreign.pk, uuid.uuid4()):
            response = self.read("customers.search", {}, request_id)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data["error"]["scope"], "request")
            self.assertEqual(
                self.client.get(BASE + f"requests/{request_id}/").status_code, 404
            )
        for state in ("pending", "completed", "failed"):
            AnswerRequest.objects.filter(pk=self.request.pk).update(status=state)
            self.assertEqual(self.read("customers.search", {}).status_code, 409)
            self.assertEqual(
                self.client.get(
                    BASE + "tools/", {"request_id": str(self.request.pk)}
                ).status_code,
                409,
            )
        self.assertEqual(
            self.client.get(BASE + f"requests/{self.request.pk}/").status_code, 200
        )
        self.assertFalse(ToolRead.objects.exists())

    # Function: Verify repeated reads of one company have stable sources without overwriting prior results.
    # Inputs: First detail read, company-profile modification, and second read.
    # Outputs: Source identifiers differ; old records and citations retain the first content.
    # Logic: Report the old source after another query without requiring an earlier chat/context call.
    # Constraints: Test modifications operate on database fixtures; chat tools themselves perform no writes.
    def test_read_versions_are_immutable(self):
        first = self.read(
            "customers.context", {"company_id": str(self.company.pk)}
        ).data
        Company.objects.filter(pk=self.company.pk).update(name="后来修改的名称")
        second = self.read(
            "customers.context", {"company_id": str(self.company.pk)}
        ).data
        self.assertNotEqual(first["read_id"], second["read_id"])
        self.assertEqual(
            ToolRead.objects.get(pk=first["read_id"]).evidence_items,
            first["evidence_items"],
        )
        self.assertNotIn("后来修改", first["evidence_items"][0]["content"])
        item = first["evidence_items"][0]
        services.save_answer(
            self.owner,
            result_for(
                self.request,
                {
                    key: item[key]
                    for key in ("source_id", "source_type", "title_or_label")
                },
            ),
        )
        self.assertEqual(self.request.citations.get().content, item["content"])
        self.request.refresh_from_db()
        self.assertIsNone(self.request.context_snapshot)

    # Function: Verify query/evidence-save exceptions leave no partial success and reveal no internal details.
    # Inputs: Exceptions at tool execution and evidence-write boundaries.
    # Outputs: HTTP 500, no evidence records, request remains processing, and exception text is absent from the response.
    # Logic: Simulate call-boundary and save-transaction failures separately.
    # Constraints: Do not simulate success or interpret exceptions as verification of a real service.
    def test_failure_rolls_back_and_hides_internal_error(self):
        for target in (
            "apps.chat.tool_reads.execute",
            "apps.chat.tool_reads.ToolRead.objects.create",
        ):
            with patch(target, side_effect=RuntimeError("synthetic-private-detail")):
                response = self.read("customers.search", {})
            self.assertEqual(response.status_code, 500, response.data)
            self.assertNotIn("synthetic-private-detail", str(response.data))
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertFalse(ToolRead.objects.exists())
            self.request.refresh_from_db()
            self.assertEqual(self.request.status, "processing")

    # Function: Verify live execution mode controls checkpoint requirements without hiding the tool.
    # Inputs: Replace registry customers.search executionMode only within the test.
    # Outputs: The catalog retains the tool; a missing write checkpoint returns 400 without executing the handler.
    # Logic: Compare the first complete-registry page and assert that switching a read to write cannot silently execute without review.
    # Constraints: Only the registry is mocked; real business code and database are unchanged.
    def test_registry_mode_is_rechecked(self):
        registry = copy.deepcopy(build_registry())
        registry["customers.search"]["executionMode"] = "write"
        with (
            patch("apps.chat.tool_reads.build_registry", return_value=registry),
            patch("apps.agent_tools.services.build_registry", return_value=registry),
            patch("apps.chat.tool_reads.execute") as handler,
        ):
            catalog = self.client.get(
                BASE + "tools/", {"request_id": str(self.request.pk)}
            )
            self.assertEqual(
                [item["name"] for item in catalog.data["tools"]], sorted(set(registry) | action_contract.ACTION_TOOLS)[:30]
            )
            self.assertEqual(self.read("customers.search", {}).status_code, 400)
            handler.assert_not_called()


# Function: Verify tool reads and answer saves cannot interleave to violate terminal-state boundaries in the real database.
# Logic: Use independent connections, thread barriers, and original business queries without mocking database row locks.
# Constraints: Requires PostgreSQL select_for_update support; does not establish concurrency behavior for other databases.
class ChatToolConcurrencyTests(TransactionTestCase):
    # Function: Verify final reporting waits while a read holds its lock, and new reads are rejected after completion.
    # Inputs: No external arguments; a real processing request and controlled in-flight read.
    # Outputs: The read registers first, the answer completes afterward, and new terminal-state reads are rejected.
    # Logic: Each thread uses its own connection; events only delay the business handler and do not replace transaction locks.
    # Constraints: All waits are bounded and released in finally, leaving no threads or database connections after failure.
    def test_answer_waits_for_inflight_read(self):
        owner, _, _, conversation = fixture()
        request, _ = services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "content": "查询",
                "client_key": str(uuid.uuid4()),
            },
        )
        services.claim(owner)
        entered, release, answer_started = Event(), Event(), Event()

        # Function: Block the read after acquiring the request lock to verify another transaction actually waits.
        # Inputs: `args`/`kwargs` are the original execute arguments.
        # Outputs: Original business response.
        # Logic: Notify the main thread, wait for release, then invoke the real handler.
        # Constraints: Wait at most 10 seconds without mocking query results.
        def blocked_execute(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("测试未释放在途读取")
            return execute(*args, **kwargs)

        # Function: Read a tool through an independent connection.
        # Inputs: The closure's owner and request.
        # Outputs: Successful tool receipt.
        # Logic: Clean connections before and after the thread and call the real transactional service.
        # Constraints: Do not reuse the main test thread's connection.
        def read():
            close_old_connections()
            try:
                return tool_reads.read_tool(
                    owner,
                    {
                        "request_id": str(request.pk),
                        "name": "customers.search",
                        "arguments": {},
                    },
                )
            finally:
                close_old_connections()

        # Function: Report the final answer through an independent connection.
        # Inputs: The closure's owner, request, and answer_started event.
        # Outputs: Save receipt.
        # Logic: Signal the start, call the real save service, and clean connections on completion.
        # Constraints: Do not change request locks or tool state.
        def answer():
            close_old_connections()
            try:
                answer_started.set()
                return services.save_answer(owner, result_for(request))
            finally:
                close_old_connections()

        with (
            patch("apps.chat.tool_reads.execute", side_effect=blocked_execute),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            reading = pool.submit(read)
            try:
                self.assertTrue(entered.wait(5))
                answering = pool.submit(answer)
                self.assertTrue(answer_started.wait(5))
                self.assertFalse(answering.done())
            finally:
                release.set()
            self.assertEqual(reading.result(timeout=10)["status"], "completed")
            self.assertTrue(answering.result(timeout=10)["saved"])
        request.refresh_from_db()
        self.assertEqual(request.status, "completed")
        self.assertEqual(request.tool_reads.count(), 1)
        from apps.crm.access import InvalidState

        with self.assertRaises(InvalidState):
            tool_reads.read_tool(
                owner,
                {
                    "request_id": str(request.pk),
                    "name": "customers.search",
                    "arguments": {},
                },
            )
