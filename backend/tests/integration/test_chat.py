"""Responsibility: Validates chat-task transactions, evidence, isolation, and the real HTTP Agent integration.
Implementation: Uses isolated PostgreSQL and the real Django API to cover schema validation and source-metadata persistence; model and Worker environments use deterministic mocks.
Relationships: `apps.chat`, existing sales messages, and `agent.workflows.chat`; does not call a real mailbox or Bailian.
Directory:
- fixture: Creates two employees, an owned customer, and an independent workspace conversation.
- result_for: Builds a valid Agent report.
- ChatTests: Tests API and state invariants.
- ChatTests.setUp: Creates isolated fixtures and clients.
- ChatTests.submit: Asks a question through the browser API.
- ChatTests.test_submit_idempotency_and_active_guard: Covers idempotent submission and active-task constraints.
- ChatTests.test_message_and_request_rollback: Ensures a failed request creation rolls back as a whole.
- ChatTests.test_claim_history_boundary: Covers historical cutoff during claiming and one-time claiming.
- ChatTests.test_agent_auth_and_employee_isolation: Covers service and user permission isolation.
- ChatTests.test_shared_company_does_not_grant_mail_access: Ensures business sharing does not grant email permissions.
- ChatTests.test_context_frozen_budget_and_isolation: Covers frozen knowledge context, capacity, and company isolation.
- ChatTests.test_report_idempotency_and_citations: Covers citation snapshots and terminal-state deduplication.
- ChatTests.test_invalid_report_rolls_back: Ensures schema counterexamples create no messages.
- ChatTests.test_report_accepts_metadata_without_reading_other_snapshots: A source absent from the snapshot may still be saved, but another request's body cannot be read.
- ChatTests.test_report_accepts_arbitrary_error_text: Allows valid error structures without constraining wording to fixed text.
- ChatTests.test_failure_retry_and_late_report: Covers explicit new attempts and rejection of late results.
- ChatTests.test_interrupted_request_recovery: Covers manual recovery confirmation and isolation from old results.
- ChatTests.test_no_context_required_for_zero_citation_answer: Allows zero-citation clarifications to complete.
- ChatTests.test_archived_conversation_is_not_claimed: Fails archived tasks and releases the queue.
- ChatTests.test_browser_cannot_forge_assistant: Ensures the original message write endpoint still forbids assistant impersonation.
- ChatTests.test_browser_csrf_and_bad_input: Ensures conversation writes retain CSRF protection and strict fields.
- ChatTests.test_knowledge_import_versions: Covers non-overridable knowledge versions and atomic rollback.
- ChatTests.test_workspace_does_not_preselect_company: Ensures initial context does not implicitly select customer material.
- ChatTests.test_answer_transaction_rollback: Ensures citation-save failure rolls back the assistant message.
- ChatTests.test_worker_report_failure_stops: Stops the consumer after report failure and does not retry.
- ChatTests.test_processing_access_revoked: After permission revocation following a claim, evidence cannot be read and results cannot be saved.
- ChatTests.test_bad_pagination: Returns 400 for invalid pagination.
- ChatConcurrencyTests: Covers real-database concurrency invariants.
- ChatConcurrencyTests.test_concurrent_claim: Ensures two consumers cannot claim the same request.
- ChatConcurrencyTests.test_concurrent_claim.consume: Claim helper using an independent connection.
- ChatConcurrencyTests.test_concurrent_submit: Ensures concurrent submissions return the same original request.
- ChatConcurrencyTests.test_concurrent_submit.send: Submission helper using an independent connection.
- ChatConcurrencyTests.test_concurrent_report: Ensures duplicate concurrent reports create only one assistant message.
- ChatConcurrencyTests.test_concurrent_report.send: Report helper using an independent connection.
- ChatHTTPTests: Exercises the complete Agent flow across real HTTP.
- ChatHTTPTests.test_agent_real_http_round_trip: Covers real transport, the tool loop, persistence, and citation readback.
- ChatHTTPTests.test_agent_real_http_round_trip.decide: Makes a decision at the model boundary from evidence returned by real tools.
Variable index:
- BROWSER: Browser chat API prefix.
- AGENT: Fixed Agent chat API prefix.
"""

import hashlib
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import (
    TestCase,
    TransactionTestCase,
    LiveServerTestCase,
    override_settings,
)
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.chat import (
    process_chat_once,
    parse_answer_context,
    parse_conversation_request,
)
from apps.chat import services
from apps.chat.models import AnswerRequest, Citation, KnowledgeEntry
from apps.crm.models import (
    AgentCredential,
    Company,
    Mailbox,
    Email,
)
from apps.sales.models import Conversation, Message, Team, Membership, CompanyGrant

BROWSER = "/api/v1/sales/chat/"
AGENT = "/api/v1/agent/chat/"


# Function: Creates independent chat-test entities.
# Inputs: No external parameters; uses the current test database.
# Outputs: Employee, other employee, company, and conversation.
# Logic: Uses only synthetic accounts and mail; the workspace conversation is not bound to a company, and credentials are fixed test values.
# Constraints: Does not read runtime credentials or trigger mailbox or model calls.
def fixture():
    owner = get_user_model().objects.create_user(username="chat-owner")
    other = get_user_model().objects.create_user(username="chat-other")
    company = Company.objects.create(
        owner=owner, name="测试客户", group_key="domain:chat.example"
    )
    conversation = Conversation.objects.create(owner=owner)
    AgentCredential.objects.create(
        owner=owner,
        name="chat-test",
        digest=hashlib.sha256(b"chat-test-token").hexdigest(),
    )
    mailbox = Mailbox.objects.create(owner=owner, address="seller@chat.example")
    Email.objects.create(
        dedupe_key="seller@chat.example:one",
        mailbox=mailbox,
        company=company,
        sent_at=timezone.now(),
        received_at=timezone.now(),
        direction="inbound",
        payload={"subject": "采购需求", "body_text": "客户需要设备。"},
    )
    return owner, other, company, conversation


# Function: Builds a valid final result.
# Inputs: `request` answers the task; `citation` is an optional strict citation triple.
# Outputs: A completed chat-v2 report.
# Logic: Returns an evidence-supported definitive statement when citations exist; otherwise returns insufficient-information guidance.
# Constraints: Uses test fixtures only and does not represent real model capability.
def result_for(request, citation=None):
    return {
        "request_id": str(request.pk),
        "chat_prompt_version": "chat-v2",
        "status": "completed",
        "error": None,
        "assistant_text": (
            "客户需要设备。[1]" if citation else "现有资料不足，无法回答该问题。"
        ),
        "citations": [citation] if citation else [],
    }


# Function: Covers chat backend state and permissions.
# Logic: Uses real ORM/HTTP with mocked failures to verify transaction boundaries.
# Constraints: `TestCase` rolls back each case; external services do not participate.
class ChatTests(TestCase):
    # Function: Prepares an employee and two authentication clients.
    # Inputs: No external parameters; reads the test-framework database.
    # Outputs: Instance fixtures.
    # Logic: The browser uses `force_authenticate`; the Agent authenticates with real service credentials.
    # Constraints: Does not bypass `AgentAuthentication`.
    def setUp(self):
        self.owner, self.other, self.company, self.conversation = fixture()
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent chat-test-token")

    # Function: Submits a question through the public chat write endpoint.
    # Inputs: `key` is an optional idempotency UUID; `content` is the question body.
    # Outputs: Successful HTTP response.
    # Logic: Returns after state assertions so boundary tests can reuse it.
    # Constraints: Does not create `AnswerRequest` directly.
    def submit(self, key=None, content="客户需要什么？"):
        response = self.browser.post(
            BROWSER + "messages/",
            {
                "conversation_id": str(self.conversation.pk),
                "content": content,
                "client_key": str(key or uuid.uuid4()),
            },
            format="json",
        )
        self.assertIn(response.status_code, (200, 201), response.data)
        return response

    # Function: Verifies that duplicate submissions neither charge twice nor create duplicate tasks.
    # Inputs: No external parameters; uses the same conversation and idempotency key.
    # Outputs: Asserts request identity, conflicts, and database counts.
    # Logic: Retransmission with the same content succeeds; different content and a second active question conflict.
    # Constraints: Verifies backend idempotency and does not simulate browser double-clicking.
    def test_submit_idempotency_and_active_guard(self):
        key = uuid.uuid4()
        first = self.submit(key)
        second = self.submit(key)
        self.assertEqual(first.data["request_id"], second.data["request_id"])
        for changed_key, content in ((key, "不同问题"), (uuid.uuid4(), "另一个问题")):
            response = self.browser.post(
                BROWSER + "messages/",
                {
                    "conversation_id": str(self.conversation.pk),
                    "content": content,
                    "client_key": str(changed_key),
                },
                format="json",
            )
            self.assertEqual(response.status_code, 409)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(AnswerRequest.objects.count(), 1)

    # Function: Verifies atomic creation of messages and tasks.
    # Inputs: No external parameters; simulates a database write failure while creating the task.
    # Outputs: Both message and request counts are zero.
    # Logic: The internal exception propagates, and the transaction rolls back the newly written user message.
    # Constraints: Mocks only the specified write boundary and does not replace database transactions.
    def test_message_and_request_rollback(self):
        with patch(
            "apps.chat.services.AnswerRequest.objects.create",
            side_effect=RuntimeError("synthetic"),
        ):
            with self.assertRaises(RuntimeError):
                services.submit(
                    self.owner,
                    {
                        "conversation_id": str(self.conversation.pk),
                        "content": "问题",
                        "client_key": str(uuid.uuid4()),
                    },
                )
        self.assertFalse(Message.objects.exists())
        self.assertFalse(AnswerRequest.objects.exists())

    # Function: Verifies that claiming does not include future questions in history.
    # Inputs: No external parameters; creates past, current, and later messages.
    # Outputs: The strict request can be parsed by the Agent, and history contains only past messages.
    # Logic: The five-field workspace request is validated by the real new parser; the second claim is empty and history remains frozen.
    # Constraints: Concurrent claiming is separately verified with `TransactionTestCase`.
    def test_claim_history_boundary(self):
        Message.objects.create(
            owner=self.owner,
            conversation=self.conversation,
            client_key=uuid.uuid4(),
            role="user",
            content="之前的问题",
        )
        self.submit()
        Message.objects.create(
            owner=self.owner,
            conversation=self.conversation,
            client_key=uuid.uuid4(),
            role="user",
            content="之后的问题",
        )
        response = self.agent.post(AGENT + "requests/claim/", {}, format="json")
        self.assertEqual(
            set(response.data["request"]),
            {
                "request_id",
                "conversation_id",
                "user_message_id",
                "question",
                "recent_history",
            },
        )
        parsed = parse_conversation_request(response.data["request"])
        self.assertEqual(
            parsed["recent_history"], [{"role": "user", "content": "之前的问题"}]
        )
        self.assertIsNone(
            self.agent.post(AGENT + "requests/claim/", {}, format="json").data[
                "request"
            ]
        )

    # Function: Verifies that every endpoint rechecks employee identity.
    # Inputs: No external parameters; another employee uses a real independent token.
    # Outputs: Unauthorized query, context, and reports return 404; invalid authentication returns 401.
    # Logic: The server does not trust arbitrary `request_id` values or browser sessions as substitutes for service credentials.
    # Constraints: Does not reveal whether a task belongs to another employee.
    def test_agent_auth_and_employee_isolation(self):
        request_id = self.submit().data["request_id"]
        AgentCredential.objects.create(
            owner=self.other,
            name="other",
            digest=hashlib.sha256(b"other-token").hexdigest(),
        )
        self.agent.credentials(HTTP_AUTHORIZATION="Agent other-token")
        self.assertIsNone(
            self.agent.post(AGENT + "requests/claim/", {}, format="json").data[
                "request"
            ]
        )
        self.assertEqual(
            self.agent.post(
                AGENT + "context/",
                {"request_id": request_id, "scope": "internal"},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/",
                result_for(AnswerRequest.objects.get(pk=request_id)),
                format="json",
            ).status_code,
            404,
        )
        self.browser.force_authenticate(self.other)
        self.assertEqual(
            self.browser.get(BROWSER + f"requests/{request_id}/").status_code, 404
        )
        anonymous = APIClient()
        self.assertEqual(
            anonymous.post(AGENT + "requests/claim/", {}, format="json").status_code,
            401,
        )

    # Function: Verifies that business sharing cannot read another person's email-derived context.
    # Inputs: No external parameters; creates team and customer-sharing authorization.
    # Outputs: A shared employee is rejected when creating a chat task.
    # Logic: Even if the conversation belongs to the shared employee, complete customer chat still requires a company owned by that employee.
    # Constraints: Does not change existing business-sharing rules.
    def test_shared_company_does_not_grant_mail_access(self):
        team = Team.objects.create(owner=self.owner, name="团队")
        Membership.objects.create(
            owner=self.owner, team=team, user=self.other, role="viewer"
        )
        CompanyGrant.objects.create(
            owner=self.owner, company=self.company, team=team, role="viewer"
        )
        conversation = Conversation.objects.create(
            owner=self.other, company=self.company
        )
        self.browser.force_authenticate(self.other)
        response = self.browser.post(
            BROWSER + "messages/",
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "给我邮件",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    # Function: Verifies workspace knowledge snapshots, budgets, and company isolation.
    # Inputs: No external parameters; creates own knowledge and company mail.
    # Outputs: Initial context excludes customer mail, and later knowledge changes do not alter the existing snapshot.
    # Logic: Freezes own knowledge after a real API claim and checks the response without external context enabled.
    # Constraints: Customer queries are tested separately through `ToolRead`; no model is called.
    def test_context_frozen_budget_and_isolation(self):
        KnowledgeEntry.objects.create(
            owner=self.owner,
            source_key="manual",
            version="1",
            title="测试知识",
            content="员工明确提供的测试资料",
        )
        email = Email.objects.first()
        Email.objects.create(
            dedupe_key="hidden",
            mailbox=email.mailbox,
            company=self.company,
            business_classification="non_business",
            payload={"body_text": "隐藏正文"},
            direction="inbound",
            sent_at=timezone.now(),
            received_at=timezone.now(),
        )
        request_id = self.submit().data["request_id"]
        services.claim(self.owner)
        first = services.context_for(self.owner, uuid.UUID(request_id), "internal")
        parse_answer_context(first)
        self.assertEqual(first["customer_context"], [])
        self.assertNotIn("隐藏正文", json.dumps(first, ensure_ascii=False))
        self.assertEqual(len(first["context_items"]), 1)
        self.assertLessEqual(
            len(first["customer_context"]) + len(first["context_items"]), 12
        )
        KnowledgeEntry.objects.filter(owner=self.owner).update(content="更新后的知识")
        self.assertEqual(
            first, services.context_for(self.owner, uuid.UUID(request_id), "internal")
        )
        self.assertFalse(first["external_available"])
        response = self.agent.post(
            AGENT + "context/",
            {"request_id": request_id, "scope": "external"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    # Function: Verifies exact idempotency of completed results and citation-body provenance.
    # Inputs: No external parameters; uses real frozen knowledge citations.
    # Outputs: Ensures one assistant message, one citation, and conflict protection.
    # Logic: Repeated reports with the same knowledge citation save idempotently; different bodies are rejected, and the browser can read the evidence.
    # Constraints: The Agent cannot self-report citation bodies.
    def test_report_idempotency_and_citations(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        KnowledgeEntry.objects.create(
            owner=self.owner,
            source_key="citation-test",
            version="1",
            title="测试知识",
            content="客户需要设备。",
        )
        context = services.context_for(self.owner, request.pk, "internal")
        citation = {
            key: context["context_items"][0][key]
            for key in ("source_id", "source_type", "title_or_label")
        }
        payload = result_for(request, citation)
        first = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        second = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(
            first.data["assistant_message_id"], second.data["assistant_message_id"]
        )
        self.assertEqual(Message.objects.filter(role="assistant").count(), 1)
        self.assertEqual(
            Citation.objects.get().content, context["context_items"][0]["content"]
        )
        payload["assistant_text"] = "其他内容。[1]"
        self.assertEqual(
            self.agent.post(AGENT + "answers/", payload, format="json").status_code, 409
        )
        state = self.browser.get(BROWSER + f"requests/{request.pk}/")
        self.assertEqual(state.data["status"], "completed")
        self.assertEqual(state.data["citations"][0]["position"], 1)

    # Function: Verifies that invalid reports create no partial success records.
    # Inputs: No external parameters; constructs missing-version, invalid-field, citation-type-or-length errors, and a failed body.
    # Outputs: All return 400 and the request remains `processing`.
    # Logic: Verifies each counterexample through the real HTTP interface.
    # Constraints: Verifies schema counterexamples only and does not mix content restrictions into structural tests.
    def test_invalid_report_rolls_back(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        missing = result_for(request)
        del missing["chat_prompt_version"]
        cases = [
            missing,
            {**result_for(request), "extra": True},
            result_for(
                request,
                {
                    "source_id": [],
                    "source_type": "customer_email",
                    "title_or_label": "其他员工",
                },
            ),
            {
                **result_for(request),
                "status": "failed",
                "error": {"code": "model_unavailable", "message": "unsafe"},
            },
        ]
        cases.extend(
            [
                {**result_for(request), "assistant_text": 123},
                {**result_for(request), "citations": {}},
                {**result_for(request), "status": "pending"},
                result_for(
                    request,
                    {
                        "source_id": "source",
                        "source_type": "x" * 81,
                        "title_or_label": "过长来源类型",
                    },
                ),
            ]
        )
        for payload in cases:
            self.assertEqual(
                self.agent.post(AGENT + "answers/", payload, format="json").status_code,
                400,
            )
        self.assertFalse(Message.objects.filter(role="assistant").exists())
        self.assertFalse(Citation.objects.exists())
        request.refresh_from_db()
        self.assertEqual(request.status, "processing")

    # Function: Verifies that relaxed content validation still cannot read evidence bodies across requests or employees.
    # Inputs: No external parameters; this request, another request by the same employee, and another employee's request each have independent snapshots.
    # Outputs: Duplicate and unregistered sources are saved in original order; only sources matching this request receive bodies.
    # Logic: Reports through the real authenticated API using a new version and mismatched body identifiers, then verifies browser projection and idempotency.
    # Constraints: Snapshots are explicit test data and do not establish material truthfulness or citation quality from the model.
    def test_report_accepts_metadata_without_reading_other_snapshots(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        known = {
            "source_id": "known",
            "source_type": "customer_email",
            "title_or_label": "本次来源",
            "content": "本次已提供正文",
        }
        request.context_snapshot = {"customer_context": [known], "context_items": []}
        request.save(update_fields=["context_snapshot"])
        citations = [
            {key: known[key] for key in ("source_id", "source_type", "title_or_label")}
        ]
        for index, owner in enumerate((self.owner, self.other)):
            conversation = Conversation.objects.create(owner=owner)
            other_request, _ = services.submit(
                owner,
                {
                    "conversation_id": str(conversation.pk),
                    "content": "其他问题",
                    "client_key": str(uuid.uuid4()),
                },
            )
            item = {
                "source_id": f"other-request:{index}",
                "source_type": "internal_knowledge",
                "title_or_label": "其他请求来源",
                "content": "不得从其他请求读取的正文",
            }
            other_request.context_snapshot = {"context_items": [item]}
            other_request.save(update_fields=["context_snapshot"])
            citations.append(
                {
                    key: item[key]
                    for key in ("source_id", "source_type", "title_or_label")
                }
            )
        citations.append(citations[0].copy())
        payload = {
            **result_for(request),
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "回答可使用普通方括号 [99]，后端不校验编号或语义。",
            "citations": citations,
        }
        response = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            self.agent.post(AGENT + "answers/", payload, format="json").data[
                "duplicate"
            ]
        )
        state = self.browser.get(BROWSER + f"requests/{request.pk}/")
        self.assertEqual(
            [row["content"] for row in state.data["citations"]],
            [known["content"], "", "", known["content"]],
        )
        self.assertEqual(
            [row["position"] for row in state.data["citations"]], [1, 2, 3, 4]
        )
        request.refresh_from_db()
        self.assertEqual(request.result, payload)
        self.assertEqual(request.chat_prompt_version, "workspace-chat-v1")

    # Function: Verifies that error codes and wording are checked structurally only, and valid new errors can persist.
    # Inputs: No external parameters; a claimed request and a test-only error object.
    # Outputs: A failure report succeeds and reads the error back unchanged without creating an assistant message.
    # Logic: Submits an unknown error code and custom wording through the Agent-authenticated endpoint, then checks request state.
    # Constraints: The producer is responsible for redacting error wording; tests do not send real service exceptions.
    def test_report_accepts_arbitrary_error_text(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        payload = {
            **result_for(request),
            "status": "failed",
            "assistant_text": "",
            "error": {"code": "tool_unavailable", "message": "客户工具暂时不可用。"},
        }
        response = self.agent.post(AGENT + "answers/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        request.refresh_from_db()
        self.assertEqual(request.error, payload["error"])
        self.assertEqual(request.status, "failed")
        self.assertIsNone(request.assistant_message_id)

    # Function: Verifies failed attempts are retained and retries receive a new identity.
    # Inputs: No external parameters; a standard model-failure report.
    # Outputs: Repeated failures are idempotent, explicit retry is unique, and an old completion report conflicts.
    # Logic: The same user message may be associated with multiple requests but cannot overwrite the original failed record.
    # Constraints: Does not start a model or implicitly requeue work.
    def test_failure_retry_and_late_report(self):
        self.submit()
        services.claim(self.owner)
        old = AnswerRequest.objects.get()
        failure = {
            **result_for(old),
            "status": "failed",
            "assistant_text": "",
            "error": {
                "code": "model_unavailable",
                "message": "回答模型暂时不可用，请稍后重试。",
            },
        }
        self.assertIsNone(
            services.save_answer(self.owner, failure)["assistant_message_id"]
        )
        self.assertTrue(services.save_answer(self.owner, failure)["duplicate"])
        first = self.browser.post(
            BROWSER + f"requests/{old.pk}/retry/", {}, format="json"
        )
        second = self.browser.post(
            BROWSER + f"requests/{old.pk}/retry/", {}, format="json"
        )
        self.assertEqual(first.data["request_id"], second.data["request_id"])
        self.assertNotEqual(first.data["request_id"], str(old.pk))
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/", result_for(old), format="json"
            ).status_code,
            409,
        )

    # Function: Verifies that interruption recovery cannot let an old result overwrite a new attempt.
    # Inputs: No external parameters; simulates an operator-confirmed interruption after claiming.
    # Outputs: Without confirmation the operation is rejected; with confirmation it terminates, and old reports conflict.
    # Logic: The command does not use automatic timeouts or revive requests.
    # Constraints: Verifies the command contract only and does not simulate operating-system process termination.
    def test_interrupted_request_recovery(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        with self.assertRaises(CommandError):
            call_command(
                "chat_interrupt", owner=self.owner.username, request_id=str(request.pk)
            )
        call_command(
            "chat_interrupt",
            owner=self.owner.username,
            request_id=str(request.pk),
            confirm_interrupted=True,
            stdout=StringIO(),
        )
        request.refresh_from_db()
        self.assertEqual(request.error["code"], "worker_interrupted")
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/", result_for(request), format="json"
            ).status_code,
            409,
        )

    # Function: Verifies that side-effect-free rejection or clarification does not require evidence reads.
    # Inputs: No external parameters; a valid zero-citation answer.
    # Outputs: Creates one successful assistant message and leaves the snapshot empty.
    # Logic: The report service requires context only for allowlisted citations.
    # Constraints: Does not require every natural-language answer to contain citations.
    def test_no_context_required_for_zero_citation_answer(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        self.assertTrue(services.save_answer(self.owner, result_for(request))["saved"])
        request.refresh_from_db()
        self.assertIsNone(request.context_snapshot)

    # Function: Verifies that a request whose permission expires before claiming is not consumed.
    # Inputs: No external parameters; archives the conversation after submission.
    # Outputs: Returns no task and explicitly fails the original pending request.
    # Logic: Archival is not a reason to permanently block the queue or permit unauthorized access.
    # Constraints: Does not cancel sent external operations; this module contains read-only answers only.
    def test_archived_conversation_is_not_claimed(self):
        self.submit()
        self.conversation.archived = True
        self.conversation.save(update_fields=["archived"])
        self.assertIsNone(services.claim(self.owner))
        self.assertEqual(AnswerRequest.objects.get().error["code"], "access_revoked")

    # Function: Verifies that the integration does not loosen the browser assistant-role restriction.
    # Inputs: No external parameters; calls the existing generic message endpoint to forge an assistant.
    # Outputs: Returns 400 and creates no message.
    # Logic: Protected chat reports remain separate from ordinary message writes.
    # Constraints: Does not change read-only field rules in the original sales serializer.
    def test_browser_cannot_forge_assistant(self):
        response = self.browser.post(
            "/api/v1/sales/records/messages/",
            {
                "conversation": str(self.conversation.pk),
                "role": "assistant",
                "content": "伪造回答",
                "client_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    # Function: Verifies browser-write authentication and input constraints.
    # Inputs: No external parameters; uses a real conversation login but omits CSRF.
    # Outputs: Returns 403; additional `owner` and non-string questions return 400.
    # Logic: Uses `enforce_csrf_checks` and does not substitute `force_authenticate` for CSRF testing.
    # Constraints: Does not modify default security configuration.
    def test_browser_csrf_and_bad_input(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.owner)
        data = {
            "conversation_id": str(self.conversation.pk),
            "content": "问题",
            "client_key": str(uuid.uuid4()),
        }
        self.assertEqual(
            client.post(BROWSER + "messages/", data, format="json").status_code, 403
        )
        self.assertEqual(
            self.browser.post(
                BROWSER + "messages/", {**data, "owner": self.other.pk}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.browser.post(
                BROWSER + "messages/", {**data, "content": 123}, format="json"
            ).status_code,
            400,
        )

    # Function: Verifies explicit replacement and non-overridability of new knowledge versions.
    # Inputs: No external parameters; a temporary UTF-8 JSON file.
    # Outputs: Same-version different content fails; a new version deactivates the old version.
    # Logic: Uses the real management command and database transaction.
    # Constraints: Test materials are marked synthetic and do not import production knowledge.
    def test_knowledge_import_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "knowledge.json"
            row = {
                "source_key": "test",
                "version": "1",
                "title": "合成知识",
                "content": "合成内容",
                "active": True,
            }
            path.write_text(json.dumps([row]), encoding="utf-8")
            call_command(
                "chat_knowledge",
                owner=self.owner.username,
                file=str(path),
                stdout=StringIO(),
            )
            row["content"] = "不同内容"
            path.write_text(json.dumps([row]), encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command(
                    "chat_knowledge",
                    owner=self.owner.username,
                    file=str(path),
                    stdout=StringIO(),
                )
            row["version"] = "2"
            path.write_text(json.dumps([row]), encoding="utf-8")
            call_command(
                "chat_knowledge",
                owner=self.owner.username,
                file=str(path),
                stdout=StringIO(),
            )
            self.assertEqual(
                list(
                    KnowledgeEntry.objects.filter(active=True).values_list(
                        "version", flat=True
                    )
                ),
                ["2"],
            )

    # Function: Verifies that workspace initial context does not implicitly select any customer.
    # Inputs: No external parameters; the employee already has customers, mail, and business projections.
    # Outputs: Both `customer_context` and `retrieval_gaps` are empty.
    # Logic: Even with only one customer, the backend returns knowledge only; customer material is queried through Agent tools.
    # Constraints: Does not change customer business data or model-generation parameters.
    def test_workspace_does_not_preselect_company(self):
        self.company.tickets = [{"ticket_id": "ticket-one", "status": "open"}]
        self.company.save(update_fields=["tickets"])
        self.submit()
        claimed = services.claim(self.owner)
        self.assertNotIn("company_id", claimed)
        context = services.context_for(self.owner, claimed["request_id"], "internal")
        self.assertEqual(context["customer_context"], [])
        self.assertEqual(context["retrieval_gaps"], [])
        self.assertNotIn("客户需要设备", json.dumps(context, ensure_ascii=False))

    # Function: Verifies assistant messages, citations, and terminal state are in one transaction.
    # Inputs: No external parameters; simulates a citation write failure.
    # Outputs: No assistant message is created, and the request remains `processing`.
    # Logic: Uses an own-knowledge citation; the exception propagates and the database rolls back the previously created message.
    # Constraints: The mock is limited to write failure and does not replace the transaction mechanism.
    def test_answer_transaction_rollback(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        KnowledgeEntry.objects.create(
            owner=self.owner,
            source_key="citation-test",
            version="1",
            title="测试知识",
            content="客户需要设备。",
        )
        context = services.context_for(self.owner, request.pk, "internal")
        citation = {
            key: context["context_items"][0][key]
            for key in ("source_id", "source_type", "title_or_label")
        }
        with patch(
            "apps.chat.services.Citation.objects.bulk_create",
            side_effect=RuntimeError("synthetic"),
        ):
            with self.assertRaises(RuntimeError):
                services.save_answer(self.owner, result_for(request, citation))
        self.assertFalse(Message.objects.filter(role="assistant").exists())
        request.refresh_from_db()
        self.assertEqual(request.status, "processing")

    # Function: Verifies that an unknown report result stops consumption and is not redispatched.
    # Inputs: No external parameters; simulates process_chat_once returning report_failed.
    # Outputs: Raises `CommandError` and executes the workflow only once.
    # Logic: After shared scheduling selects an employee, persistent mode also stops immediately when report status is uncertain; an operator must inspect the request's real state.
    # Constraints: Mocks the Agent execution boundary and does not claim that real process or network failures were reproduced.
    def test_worker_report_failure_stops(self):
        failure = {
            "request_id": str(uuid.uuid4()),
            "status": "failed",
            "error": {"code": "report_failed"},
        }
        # Does not let Worker connection cleanup close the outer transaction of `TestCase`; production `finally` behavior remains unchanged.
        with (
            patch(
                "apps.chat.management.commands.chat_worker.next_owner",
                return_value=self.owner,
            ),
            patch("apps.chat.management.commands.chat_worker.scoped_backend"),
            patch("apps.chat.management.commands.chat_worker.connections.close_all"),
            patch(
                "apps.chat.management.commands.chat_worker.process_chat_once",
                return_value=failure,
            ) as process,
        ):
            with self.assertRaises(CommandError):
                call_command("chat_worker")
            process.assert_called_once()

    # Function: Verifies permission revocation after claiming.
    # Inputs: No external parameters; archives the conversation after claiming.
    # Outputs: Both context and report return 404, and no assistant message is written.
    # Logic: Every service call rechecks binding and accessibility.
    # Constraints: The task remains for explicit manual termination and cannot save results by bypassing permission checks.
    def test_processing_access_revoked(self):
        self.submit()
        services.claim(self.owner)
        request = AnswerRequest.objects.get()
        self.conversation.archived = True
        self.conversation.save(update_fields=["archived"])
        self.assertEqual(
            self.agent.post(
                AGENT + "context/",
                {"request_id": str(request.pk), "scope": "internal"},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.agent.post(
                AGENT + "answers/", result_for(request), format="json"
            ).status_code,
            404,
        )
        self.assertFalse(Message.objects.filter(role="assistant").exists())

    # Function: Verifies pagination errors do not become server exceptions.
    # Inputs: No external parameters; a valid conversation and an invalid page.
    # Outputs: 400.
    # Logic: The view maps numeric-parsing exceptions to protocol errors.
    # Constraints: Does not change shared pagination defaults.
    def test_bad_pagination(self):
        response = self.browser.get(
            BROWSER + f"requests/?conversation={self.conversation.pk}&page=not-a-number"
        )
        self.assertEqual(response.status_code, 400)


# Function: Verifies concurrent behavior using independent PostgreSQL connections.
# Logic: Uses a synchronization barrier to trigger two transactions simultaneously and queries the real persisted result.
# Constraints: Must run on a PostgreSQL test database supporting row locks; does not substitute mocked locks.
class ChatConcurrencyTests(TransactionTestCase):
    # Function: Verifies uniqueness of claims by multiple consumers.
    # Inputs: No external parameters; two independent connections.
    # Outputs: Exactly one request and one empty result.
    # Logic: Concurrent transactions contend for the employee lock; the later lock holder observes the already-processed state.
    # Constraints: Closes connections when threads finish to avoid leaking test resources.
    def test_concurrent_claim(self):
        owner, _, _, conversation = fixture()
        services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "问题",
            },
        )
        barrier = Barrier(2)

        # Function: Claims a task in an independent connection.
        # Inputs: `index` is the concurrent slot number.
        # Outputs: Claim result.
        # Logic: Calls the real transactional service after barrier synchronization.
        # Constraints: Closes this thread's connection in `finally`.
        def consume(index):
            try:
                barrier.wait(timeout=10)
                return services.claim(owner)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(consume, range(2)))
        self.assertEqual(sum(row is not None for row in results), 1)

    # Function: Verifies concurrent retransmission creates only one message and request.
    # Inputs: No external parameters; two connections submit the same idempotency key.
    # Outputs: The same `request_id` and unique counts.
    # Logic: User row locks protect message checks and creation.
    # Constraints: Does not make the test pass by changing database isolation level.
    def test_concurrent_submit(self):
        owner, _, _, conversation = fixture()
        data = {
            "conversation_id": str(conversation.pk),
            "client_key": str(uuid.uuid4()),
            "content": "问题",
        }
        barrier = Barrier(2)

        # Function: Submits the same question in independent transactions.
        # Inputs: `index` is the concurrent slot number.
        # Outputs: Creates or reuses a request ID.
        # Logic: Calls the real submission service after the barrier.
        # Constraints: Closes the connection on exit.
        def send(index):
            try:
                barrier.wait(timeout=10)
                return services.submit(owner, data)[0].pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(AnswerRequest.objects.count(), 1)

    # Function: Verifies concurrent final reports do not create two assistant messages.
    # Inputs: No external parameters; two independent connections send exactly the same result.
    # Outputs: One assistant message, with one response each for the initial and repeated report.
    # Logic: Result validation and writes are serialized by the employee lock.
    # Constraints: Does not mock database locks or isolation levels.
    def test_concurrent_report(self):
        owner, _, _, conversation = fixture()
        request, _ = services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "问题",
            },
        )
        services.claim(owner)
        barrier = Barrier(2)
        payload = result_for(request)

        # Function: Submits the same terminal state in an independent connection.
        # Inputs: `index` is the concurrent slot number.
        # Outputs: Persists the real response.
        # Logic: Competes for the same employee lock after synchronized start.
        # Constraints: Cleans up this thread's database connection in `finally`.
        def send(index):
            try:
                barrier.wait(timeout=10)
                return services.save_answer(owner, payload)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(sorted(row["duplicate"] for row in results), [False, True])
        self.assertEqual(Message.objects.filter(role="assistant").count(), 1)


# Function: Verifies compatibility between real backend HTTP and the original Agent implementation.
# Logic: A temporary Django server, `requests` client, and PostgreSQL all run for real.
# Constraints: Model output is mocked and does not establish Bailian availability or model-answer quality.
class ChatHTTPTests(LiveServerTestCase):
    # Function: Verifies the closed loop of claiming, context, answering, and browser readback.
    # Inputs: No external parameters; a temporary service and fixed synthetic evidence.
    # Outputs: Completed state, a real assistant message, and citations; repeated reports are idempotent.
    # Logic: The original client claims through HTTP, queries the directory, searches customers, and saves the answer; the model first returns a tool call and then an answer.
    # Constraints: Does not connect to external mail, knowledge, or model services.
    @override_settings(
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
        LOCAL_DEBUG_AUTO_LOGIN=False,
    )
    def test_agent_real_http_round_trip(self):
        owner, _, _, conversation = fixture()
        request, _ = services.submit(
            owner,
            {
                "conversation_id": str(conversation.pk),
                "client_key": str(uuid.uuid4()),
                "content": "客户需要什么？",
            },
        )
        backend = DjangoBackendClient(
            self.live_server_url + "/api/v1/agent/", "chat-test-token"
        )

        # Function: Generates a synthetic model decision from evidence returned by real backend tools.
        # Inputs: `messages` is the workspace prompt and `max_tokens` is the established model budget.
        # Outputs: First emits a search-tool decision, then outputs a final answer with `action` after receiving evidence.
        # Logic: Citations come from authorized sources in the current prompt and never fabricate `source_id`.
        # Constraints: Replaces only the model boundary; directory access, read-only queries, and saving all go through real HTTP.
        def decide(messages, *, max_tokens):
            payload = json.loads(messages[-1]["content"])
            if not payload["tool_results"]:
                return json.dumps(
                    {
                        "action": "tool",
                        "name": "customers.search",
                        "arguments": {"q": "测试客户"},
                    }
                )
            evidence = next(
                row
                for row in payload["authorized_evidence"]
                if row["source_type"] == "customer_search"
            )
            citation = {
                key: evidence[key]
                for key in ("source_id", "source_type", "title_or_label")
            }
            return json.dumps(
                {
                    "action": "answer",
                    "assistant_text": "找到测试客户。[1]",
                    "citations": [citation],
                },
                ensure_ascii=False,
            )

        provider = Mock(side_effect=decide)
        result = process_chat_once(backend=backend, chat_provider=provider)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(request.tool_reads.count(), 1)
        citation = result["citations"][0]
        request.refresh_from_db()
        self.assertEqual(request.assistant_message.content, "找到测试客户。[1]")
        self.assertTrue(backend.report_answer(result)["duplicate"])
        browser = APIClient()
        browser.force_authenticate(owner)
        state = browser.get(BROWSER + f"requests/{request.pk}/").data
        self.assertEqual(state["citations"][0]["source_id"], citation["source_id"])
        self.assertIsNone(process_chat_once(backend=backend, chat_provider=provider))
