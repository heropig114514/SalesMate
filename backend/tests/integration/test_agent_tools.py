"""Responsibility: Verify end-to-end authorization, writes, confirmation, and external-action boundaries for business tools.
Implementation: Email drafts use workspace conversations without a preselected company; the full tool-catalog case explicitly enables QQ; use isolated real PostgreSQL and HTTP requests, mocking providers only at external-send boundaries.
Relationships: Covers the agent_tools package and original crm/sales/chat services; does not verify real mailbox authorization or sending.
Directory:
- AgentToolTests: Tool integration verification.
- AgentToolTests.setUp: Create isolated users and restricted tokens.
- AgentToolTests.call: Request through the Tool entry point.
- AgentToolTests.test_catalog_and_all_list_handlers: Check catalog and every resource query.
- AgentToolTests.test_token_scope_expiry_and_revocation: Check delegated permissions and expiry.
- AgentToolTests.test_session_csrf_and_credential_boundary: Check authorization administration and confirmation permissions.
- AgentToolTests.test_idempotence_conflict_and_revisions: Check logical writes and versions.
- AgentToolTests.test_private_data_and_mass_assignment: Check private-data isolation.
- AgentToolTests.test_proposal_frozen_confirmation_and_conflict: Check frozen proposals and independent confirmation.
- AgentToolTests.test_expired_or_revoked_proposal: Check expired authorization cannot execute.
- AgentToolTests.test_external_actions_prepare_only: Verify all three actions prepare only.
- AgentToolTests.test_quote_precision_and_parent_revision: Verify real quote calculation.
- AgentToolTests.test_knowledge_and_contact_identifiers: Check evidence and integer contact IDs.
- AgentToolTests.test_failed_write_rolls_back_receipt: Verify failure rollback.
- AgentToolTests.test_crm_registration_and_sync_scope: Check CRM fields and QQ synchronization payload.
- ConcurrentToolTests: Concurrent-receipt verification.
- ConcurrentToolTests.invoke_in_thread: Independent connection-send call.
- ConcurrentToolTests.test_two_credentials_share_one_logical_write: Verify the same key under different credentials writes only once.
Variable index:
- BASE: Tool API route.
"""

import hashlib
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from jsonschema import Draft202012Validator
from rest_framework.response import Response
from rest_framework.test import APIClient
from apps.agent_tools.models import ToolCall, ToolCredential, ToolProposal
from apps.agent_tools.registry import build_registry
from apps.chat.models import KnowledgeEntry
from apps.sales import grouping, models

BASE = "/api/v1/agent-tools/"


# Function: Verify Tool boundaries.
# Logic: The full-catalog assertion with QQ explicitly enabled uses isolated database and real Tool HTTP entry point.
# Constraints: Do not run external sending services or connect to real mailboxes.
@override_settings(QQ_MAIL_ENABLED=True)
class AgentToolTests(TestCase):
    # Function: Establish context.
    # Inputs: No external arguments.
    # Outputs: User, company, token client, and Session client.
    # Logic: The token explicitly lists tools in the test catalog.
    # Constraints: Synthetic token is used only in the test database.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tools-user")
        self.other = get_user_model().objects.create_user(username="tools-other")
        self.company = grouping.create_company(self.user, "工具客户")
        self.foreign = grouping.create_company(self.other, "私人客户")
        self.credential = ToolCredential.objects.create(
            owner=self.user,
            name="tests",
            digest=hashlib.sha256(b"synthetic-tools-token").hexdigest(),
            allowed_tools=list(build_registry()),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool synthetic-tools-token")
        self.human = APIClient()
        self.human.force_login(self.user)

    # Function: Call Tool.
    # Inputs: `name`, `arguments`, optional write UUID `key`, and expected HTTP status `expected`.
    # Outputs: Receipt data.
    # Logic: Call the real authentication path and check status.
    # Constraints: Caller explicitly passes the idempotency key.
    def call(self, name, arguments, key=None, expected=200):
        data = {"name": name, "arguments": arguments}
        if key is not None:
            data["idempotency_key"] = key
        response = self.client.post(BASE + "call/", data, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # Function: Verify every discoverable tool has an executable list adapter.
    # Inputs: Registry and empty business data.
    # Outputs: Every schema is valid and list queries succeed.
    # Logic: Execute every resource type and special read-only entry point individually.
    # Constraints: Calendar requires a connection; separate external tests cover it.
    def test_catalog_and_all_list_handlers(self):
        registry = build_registry()
        self.assertGreaterEqual(len(registry), 120)
        for name, spec in registry.items():
            Draft202012Validator.check_schema(spec["inputSchema"])
            if spec["kind"] == "record_list":
                with self.subTest(tool=name):
                    self.call(name, {})
        for name, args in [
            ("customers.search", {}),
            ("sales.overview", {}),
            ("sales.audit", {}),
            ("people.find", {"username": self.user.username}),
            ("mailboxes.list", {}),
            ("emails.list", {}),
            ("knowledge.search", {}),
        ]:
            self.call(name, args)
        first = self.client.get(BASE + "catalog/", {"page_size": 100}).data
        second = self.client.get(BASE + "catalog/", {"page_size": 100, "page": 2}).data
        self.assertEqual(len(first["tools"]) + len(second["tools"]), len(registry))
        self.assertNotIn("kind", first["tools"][0])
        for forbidden in (
            "actions.approve",
            "sql.execute",
            "credentials.create",
            "actions.execute",
        ):
            self.call(forbidden, {}, expected=404)

    # Function: Verify limited authorization of credentials.
    # Inputs: Restricted, expired, revoked, and inactive authorizations.
    # Outputs: Catalog narrows, while unauthorized and expired access both reject.
    # Logic: Use a real Authorization header.
    # Constraints: Do not replace execution permission checks by hiding catalog entries.
    def test_token_scope_expiry_and_revocation(self):
        self.credential.allowed_tools = ["customers.search"]
        self.credential.save()
        self.assertEqual(self.client.get(BASE + "catalog/").data["count"], 1)
        self.call("products.list", {}, expected=403)
        self.credential.expires_at = timezone.now() - timedelta(seconds=1)
        self.credential.save()
        self.call("customers.search", {}, expected=401)
        self.credential.expires_at = timezone.now() + timedelta(hours=1)
        self.credential.revoked_at = timezone.now()
        self.credential.save()
        self.call("customers.search", {}, expected=401)
        self.credential.revoked_at = None
        self.credential.save()
        self.user.is_active = False
        self.user.save()
        self.call("customers.search", {}, expected=401)
        self.client.credentials(HTTP_AUTHORIZATION="Agent synthetic-tools-token")
        self.assertEqual(self.client.get(BASE + "catalog/").status_code, 401)

    # Function: Verify Session-only access and CSRF.
    # Inputs: Token client and real Session, with requests without and with CSRF.
    # Outputs: Token cannot authorize; Session writes require CSRF; summary does not echo.
    # Logic: Do not use force_authenticate to bypass browser authentication.
    # Constraints: The original token appears only in the creation response.
    def test_session_csrf_and_credential_boundary(self):
        payload = {
            "name": "limited",
            "allowed_tools": ["customers.search"],
            "expires_in_hours": 1,
        }
        self.assertEqual(
            self.client.post(BASE + "credentials/", payload, format="json").status_code,
            403,
        )
        browser = APIClient(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(
            browser.post(BASE + "credentials/", payload, format="json").status_code, 403
        )
        browser.cookies["csrftoken"] = "a" * 32
        created = browser.post(
            BASE + "credentials/", payload, format="json", HTTP_X_CSRFTOKEN="a" * 32
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created["Cache-Control"], "no-store")
        self.assertNotIn(
            created.data["token"], str(browser.get(BASE + "credentials/").data)
        )
        self.assertNotIn("digest", str(browser.get(BASE + "credentials/").data))
        self.assertEqual(
            browser.delete(
                BASE + f"credentials/{created.data['id']}/", HTTP_X_CSRFTOKEN="a" * 32
            ).status_code,
            204,
        )

    # Function: Verify idempotency and optimistic locking.
    # Inputs: Replay with the same key, different content, and stale revision.
    # Outputs: Create only once; conflicts return 409.
    # Logic: Create company and follow-up, then modify version.
    # Constraints: Replay is a historical receipt and does not execute business logic again.
    def test_idempotence_conflict_and_revisions(self):
        key = str(uuid.uuid4())
        first = self.call("customers.create", {"name": "新增"}, key)
        second = self.call("customers.create", {"name": "新增"}, key)
        self.assertTrue(second["replayed"])
        self.assertEqual(first["data"], second["data"])
        self.call("customers.create", {"name": "不同"}, key, 409)
        self.call("customers.create", {"name": "没有键"}, expected=400)
        self.call("customers.search", {}, key, 400)
        item = self.call(
            "follow_ups.create",
            {
                "data": {
                    "company": str(self.company.pk),
                    "title": "跟进",
                    "due_at": "2026-10-01T12:00:00+08:00",
                }
            },
            str(uuid.uuid4()),
        )["data"]
        args = {
            "id": item["id"],
            "revision": item["revision"],
            "data": {"title": "已更新"},
        }
        self.call("follow_ups.update", args, str(uuid.uuid4()))
        self.call("follow_ups.update", args, str(uuid.uuid4()), 409)
        self.assertEqual(models.FollowUp.objects.get(pk=item["id"]).title, "已更新")

    # Function: Verify private-data and field boundaries.
    # Inputs: Cross-user company, knowledge, and extra owner fields.
    # Outputs: Unauthorized queries reject, lists do not leak, and writes reject.
    # Logic: Do not fabricate actor; change only input ID.
    # Constraints: The knowledge-base fixture does not represent real retrieval quality.
    def test_private_data_and_mass_assignment(self):
        self.call(
            "customers.context", {"company_id": str(self.foreign.pk)}, expected=404
        )
        names = [
            row["name"] for row in self.call("customers.search", {})["data"]["results"]
        ]
        self.assertNotIn(self.foreign.name, names)
        self.call(
            "products.create",
            {
                "data": {
                    "sku": "bad",
                    "name": "bad",
                    "currency": "USD",
                    "unit_price": "1.00",
                    "owner": self.other.pk,
                }
            },
            str(uuid.uuid4()),
            400,
        )
        entry = KnowledgeEntry.objects.create(
            owner=self.other,
            source_key="private",
            version="v1",
            title="private",
            content="secret",
        )
        self.call("knowledge.get", {"id": str(entry.pk)}, expected=404)
        self.assertEqual(self.call("knowledge.search", {})["data"]["count"], 0)

    # Function: Verify proposal freezing and one-time confirmation.
    # Inputs: Archived proposal, tampered request, tool identity, and stale version.
    # Outputs: Business data does not change before approval; approval executes once only.
    # Logic: Decide through a real Session; version conflict retains pending status.
    # Constraints: Test-client portions with CSRF disabled verify business behavior only; separate tests cover CSRF.
    def test_proposal_frozen_confirmation_and_conflict(self):
        item = self.call(
            "products.create",
            {
                "data": {
                    "sku": "sku",
                    "name": "产品",
                    "currency": "USD",
                    "unit_price": "2.00",
                }
            },
            str(uuid.uuid4()),
        )["data"]
        args = {"id": item["id"], "revision": item["revision"], "archived": True}
        proposal = self.call("products.archive", args, str(uuid.uuid4()))["proposal"]
        path = proposal["decision_path"]
        self.assertFalse(models.Product.objects.get(pk=item["id"]).archived)
        self.assertEqual(
            self.client.post(path, {"decision": "approve"}, format="json").status_code,
            403,
        )
        self.assertEqual(
            self.human.post(
                path, {"decision": "approve", "arguments": {}}, format="json"
            ).status_code,
            400,
        )
        first = self.human.post(path, {"decision": "approve"}, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(
            self.call("proposals.get", {"id": proposal["id"]})["data"]["status"],
            "approved",
        )
        self.assertTrue(models.Product.objects.get(pk=item["id"]).archived)
        self.assertEqual(
            self.human.post(path, {"decision": "approve"}, format="json").data,
            first.data,
        )
        self.assertEqual(
            self.human.post(path, {"decision": "cancel"}, format="json").status_code,
            409,
        )
        stale = self.call("products.archive", args, str(uuid.uuid4()))["proposal"]
        self.assertEqual(
            self.human.post(
                stale["decision_path"], {"decision": "approve"}, format="json"
            ).status_code,
            409,
        )
        self.assertEqual(ToolProposal.objects.get(pk=stale["id"]).status, "pending")

    # Function: Verify authorization remains valid at confirmation.
    # Inputs: Expired proposal and revoked authorization.
    # Outputs: All approvals fail and business logic does not execute.
    # Logic: Create frozen proposal first, then change state.
    # Constraints: Do not execute original operation; confirmation endpoint still uses a real Session.
    def test_expired_or_revoked_proposal(self):
        proposal = self.call(
            "teams.create", {"data": {"name": "协作"}}, str(uuid.uuid4())
        )["proposal"]
        ToolProposal.objects.filter(pk=proposal["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(
            self.human.post(
                proposal["decision_path"], {"decision": "approve"}, format="json"
            ).status_code,
            409,
        )
        self.credential.revoked_at = timezone.now()
        self.credential.save()
        ToolProposal.objects.filter(pk=proposal["id"]).update(
            expires_at=timezone.now() + timedelta(hours=1)
        )
        self.assertEqual(
            self.human.post(
                proposal["decision_path"], {"decision": "approve"}, format="json"
            ).status_code,
            403,
        )
        self.assertFalse(models.Team.objects.exists())
        self.assertEqual(
            self.human.post(
                proposal["decision_path"], {"decision": "cancel"}, format="json"
            ).status_code,
            200,
        )

    # Function: Verify Gmail, QQ, and calendar all prepare only.
    # Inputs: Synthetic unavailable connection and real draft.
    # Outputs: Freeze action awaiting confirmation; provider does not execute.
    # Logic: Workspace draft uses original action service for explicit company; repeated calls reuse receipt.
    # Constraints: Mocked send function only asserts it was not called and does not prove sending is available.
    def test_external_actions_prepare_only(self):
        conversation = self.call(
            "conversations.create",
            {"data": {"title": "工作空间"}},
            str(uuid.uuid4()),
        )["data"]
        draft = self.call(
            "drafts.create",
            {
                "data": {
                    "conversation": conversation["id"],
                    "kind": "email",
                    "subject": "报价",
                    "content": "请审阅",
                    "recipients": ["buyer@example.com"],
                }
            },
            str(uuid.uuid4()),
        )["data"]
        with patch("apps.sales.actions.execute_provider") as provider:
            for kind in ("gmail", "qq", "calendar"):
                connection = models.Connection.objects.create(
                    owner=self.user,
                    provider=kind,
                    account="seller@example.com",
                    encrypted_credentials="not-real",
                )
                params = {"connection_id": str(connection.pk), "draft_id": draft["id"]}
                if kind == "calendar":
                    params = {
                        "connection_id": str(connection.pk),
                        "calendar_id": "primary",
                        "title": "会议",
                        "description": "",
                        "start": "2026-10-01T10:00:00+08:00",
                        "end": "2026-10-01T11:00:00+08:00",
                        "attendees": ["buyer@example.com"],
                        "send_updates": "all",
                    }
                result = self.call(
                    "actions.prepare_" + kind,
                    {"company": str(self.company.pk), "parameters": params},
                    str(uuid.uuid4()),
                )
                self.assertEqual(result["data"]["status"], "pending_confirmation")
                self.assertEqual(result["status"], "confirmation_required")
            provider.assert_not_called()
        self.assertEqual(models.ToolAction.objects.count(), 3)

    # Function: Verify amount and parent-document version.
    # Inputs: Quote lines: 3 × 12.35 − 0.05.
    # Outputs: Total is 37.00 and parent version increments.
    # Logic: Create through tools and read again.
    # Constraints: Do not approve or send quote.
    def test_quote_precision_and_parent_revision(self):
        quote = self.call(
            "quotes.create",
            {
                "data": {
                    "company": str(self.company.pk),
                    "number": "TOOLS-Q",
                    "currency": "USD",
                }
            },
            str(uuid.uuid4()),
        )["data"]
        self.call(
            "quote_lines.create",
            {
                "data": {
                    "quote": quote["id"],
                    "description": "设备",
                    "quantity": "3",
                    "unit_price": "12.35",
                    "discount": "0.05",
                }
            },
            str(uuid.uuid4()),
        )
        after = self.call("quotes.get", {"id": quote["id"]})["data"]
        self.assertEqual(after["total"], "37.00")
        self.assertGreater(after["revision"], quote["revision"])

    # Function: Verify knowledge citations and contact primary key.
    # Inputs: Synthetic knowledge and contact.
    # Outputs: Retain source identifier and version; integer ID is editable.
    # Logic: Read company version again after creation.
    # Constraints: Knowledge search is keyword matching.
    def test_knowledge_and_contact_identifiers(self):
        entry = KnowledgeEntry.objects.create(
            owner=self.user,
            source_key="manual",
            version="v1",
            title="设备",
            content="检测方案",
        )
        data = self.call("knowledge.search", {"q": "检测"})["data"]["results"][0]
        self.assertEqual(data["source_id"], f"knowledge:{entry.pk}")
        self.assertEqual(data["version"], "v1")
        self.call(
            "contacts.save",
            {
                "company_id": str(self.company.pk),
                "revision": self.company.revision,
                "data": {"email": "buyer@example.com", "name": "联系人"},
            },
            str(uuid.uuid4()),
        )
        self.company.refresh_from_db()
        contact = self.company.contacts.get(email="buyer@example.com")
        self.call(
            "contacts.save",
            {
                "company_id": str(self.company.pk),
                "revision": self.company.revision,
                "data": {"id": contact.pk, "email": contact.email, "name": "新名字"},
            },
            str(uuid.uuid4()),
        )

    # Function: Verify failure leaves no successful receipt.
    # Inputs: Unauthorized write to another user's company.
    # Outputs: Database rejects and rolls back ToolCall.
    # Logic: Real serialization relationship-permission failure.
    # Constraints: Failure does not retry automatically.
    def test_failed_write_rolls_back_receipt(self):
        key = str(uuid.uuid4())
        self.call(
            "tickets.create",
            {"data": {"company": str(self.foreign.pk), "title": "越权"}},
            key,
            400,
        )
        self.assertFalse(ToolCall.objects.filter(key=key).exists())
        self.assertFalse(models.Ticket.objects.exists())

    # Function: Verify CRM and synchronization adapter contract.
    # Inputs: Real CRM registration and scoped synchronization proposal.
    # Outputs: Company profile saves; confirmation passes the complete scope.
    # Logic: Mock mailbox-request boundary only and verify parameters are not swallowed.
    # Constraints: Does not prove mailbox synchronization completed.
    def test_crm_registration_and_sync_scope(self):
        self.call(
            "customers.register",
            {
                "company_id": str(self.company.pk),
                "revision": self.company.revision,
                "data": {
                    "company_name": "登记客户",
                    "industry_from_crm": "unknown",
                    "employee_count": None,
                    "employee_count_source": None,
                },
            },
            str(uuid.uuid4()),
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.crm_status, "registered")
        args = {"mailbox_id": str(uuid.uuid4()), "sync_options": {"max_messages": 5}}
        proposal = self.call("mailboxes.sync", args, str(uuid.uuid4()))["proposal"]
        with patch(
            "apps.agent_tools.dispatch.crm.MailboxViewSet.request_sync",
            return_value=Response({"status": "queued"}, status=202),
        ) as sync:
            result = self.human.post(
                proposal["decision_path"], {"decision": "approve"}, format="json"
            )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(
            sync.call_args.args[0].data, {"sync_options": {"max_messages": 5}}
        )
        self.assertEqual(result.data["result"]["status"], "accepted")


# Function: Verify idempotency across connections.
# Logic: TransactionTestCase permits two threads to commit independently.
# Constraints: Use isolated PostgreSQL; a single-thread mock cannot replace it.
class ConcurrentToolTests(TransactionTestCase):
    # Function: Send concurrent requests.
    # Inputs: `token`, `key`, and `barrier` synchronize the starting point.
    # Outputs: HTTP status and receipt.
    # Logic: Each thread has an independent client and database connection.
    # Constraints: Close that thread's connection on exit to prevent test-database cleanup from blocking.
    def invoke_in_thread(self, token, key, barrier):
        close_old_connections()
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION="Tool " + token)
            barrier.wait(timeout=10)
            response = client.post(
                BASE + "call/",
                {
                    "name": "customers.create",
                    "arguments": {"name": "并发客户"},
                    "idempotency_key": key,
                },
                format="json",
            )
            return response.status_code, response.data
        finally:
            close_old_connections()

    # Function: Verify two delegations do not create duplicate companies or deadlock in reverse order.
    # Inputs: Same user, different credentials, same idempotency key.
    # Outputs: Both requests succeed, one is replayed, and only one receipt exists.
    # Logic: Threads enter real HTTP and database transactions simultaneously.
    # Constraints: The test does not guarantee every concurrent interleaving of arbitrary business logic; it covers this Tool's locking order.
    def test_two_credentials_share_one_logical_write(self):
        user = get_user_model().objects.create_user(username="concurrent-tools")
        for token in ("parallel-one", "parallel-two"):
            ToolCredential.objects.create(
                owner=user,
                name=token,
                digest=hashlib.sha256(token.encode()).hexdigest(),
                allowed_tools=["customers.create"],
                expires_at=timezone.now() + timedelta(hours=1),
            )
        key, barrier = str(uuid.uuid4()), Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.invoke_in_thread, token, key, barrier)
                for token in ("parallel-one", "parallel-two")
            ]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual([status for status, data in results], [200, 200], results)
        self.assertEqual(
            sorted(data["replayed"] for status, data in results), [False, True]
        )
        self.assertEqual(results[0][1]["data"], results[1][1]["data"])
        self.assertEqual(ToolCall.objects.filter(owner=user, key=key).count(), 1)
