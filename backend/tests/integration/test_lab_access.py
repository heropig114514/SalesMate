"""Responsibility: Prove experimental settings cannot bypass employee authentication or private ownership.
Implementation: Exercise real Session, Agent, Tool HTTP paths with two employees and real CSRF checks in isolated PostgreSQL.
Relationships: Covers laboratory policy, business CRUD, chat isolation, delegated tools, and both OAuth callbacks.
Directory:
- LaboratoryTests: Account-isolation regression matrix under the former bypass switch.
- LaboratoryTests.setUp: Create two employees and independent private records.
- LaboratoryTests.test_anonymous_and_identity_headers: Reject anonymous and forged identities.
- LaboratoryTests.test_private_crud_and_full_catalog: Preserve owner CRUD and full catalog while hiding foreign records.
- LaboratoryTests.test_csrf_and_versions: Preserve CSRF and optimistic concurrency even in experiments.
- LaboratoryTests.test_agent_credentials: Bind Agent reads to the credential owner.
- LaboratoryTests.test_tool_credentials: Enforce delegation expiry, revocation, scope, and private ownership.
- LaboratoryTests.test_chat_and_knowledge: Reject foreign chat operations, exclude foreign knowledge, and bind explicit retries to the current owner.
- LaboratoryTests.test_team_sharing: Retain explicit shared customer access without sharing private resources.
- LaboratoryTests.test_oauth_owner_binding: Reject account switches before either Google token exchange.
Variable index:
- BASE: Business Tool API prefix.
"""
import hashlib
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIClient

from apps.agent_tools.models import ToolCredential
from apps.agent_tools.registry import build_registry
from apps.agent_tools.services import catalog, invoke
from apps.chat import services as chat
from apps.chat.models import KnowledgeEntry
from apps.crm.access import Conflict, InvalidState, check_version
from apps.crm.gmail_oauth import begin_authorization, finish_authorization
from apps.crm.jobs import require_lease
from apps.crm.models import AgentCredential
from apps.sales import grouping, integrations, models
from apps.sales.permissions import company_access, scope

BASE = "/api/v1/agent-tools/"


# Function: Verify private account boundaries even with synthetic experiments enabled.
# Logic: TestCase rolls back fixtures; override_settings restores all switches after each test.
# Constraints: No production data, provider calls, model calls, or real messages.
@override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class LaboratoryTests(TestCase):
    # Function: Establish independent records and CSRF-aware clients.
    # Inputs: Isolated test database.
    # Outputs: Two users, company, product, connection, conversation, and browser client.
    # Logic: Real Session login is used where authentication behavior matters.
    # Constraints: All records are synthetic and transactionally rolled back.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="isolated-owner")
        self.other = get_user_model().objects.create_user(username="isolated-other")
        self.company = grouping.create_company(self.owner, "Private company")
        self.product = models.Product.objects.create(owner=self.owner, sku="private", name="Original", currency="USD", unit_price="2.00")
        self.connection = models.Connection.objects.create(owner=self.owner, provider="gmail", account="owner@example.com")
        self.conversation = models.Conversation.objects.create(owner=self.owner, title="Private chat")
        self.client = APIClient(enforce_csrf_checks=True)

    # Function: Reject anonymous access and spoofed ownership.
    # Inputs: Public identity header and invalid Agent/Tool tokens.
    # Outputs: Authentication failures, no automatically created laboratory user.
    # Logic: Exercise browser, tool, and Worker endpoints through real authenticators.
    # Constraints: Session status remains publicly readable without authenticating the caller.
    def test_anonymous_and_identity_headers(self):
        self.client.credentials(HTTP_X_LAB_USER=self.owner.username)
        for path in ("/api/v1/sales/directory/", "/api/v1/companies/", "/api/v1/mailboxes/", BASE + "catalog/", "/api/v1/sales/browse/directory/"):
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, (401, 403))
        self.assertFalse(self.client.get("/api/v1/session/").data["authenticated"])
        for header, path in (("Tool invalid", BASE + "catalog/"), ("Agent invalid", "/api/v1/agent/context/")):
            self.client.credentials(HTTP_X_LAB_USER=self.owner.username, HTTP_AUTHORIZATION=header)
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(get_user_model().objects.count(), 2)

    # Function: Preserve owned CRUD and complete capability discovery.
    # Inputs: Authenticated owner and foreign employee sessions.
    # Outputs: Full catalog and successful owner edit; foreign detail/update stay hidden.
    # Logic: Obtain a real CSRF token from Session and use current optimistic revision.
    # Constraints: The catalog lists capabilities without granting cross-account rights.
    def test_private_crud_and_full_catalog(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(BASE + "catalog/").data["count"], len(build_registry()))
        self.client.get("/api/v1/session/")
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.patch(f"/api/v1/sales/records/products/{self.product.pk}/", {"name": "Owned edit"}, format="json", HTTP_X_CSRFTOKEN=csrf, HTTP_IF_MATCH=str(self.product.revision))
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_login(self.other)
        self.client.get("/api/v1/session/")
        csrf = self.client.cookies["csrftoken"].value
        for resource, obj in (("products", self.product), ("connections", self.connection), ("conversations", self.conversation)):
            path = f"/api/v1/sales/records/{resource}/{obj.pk}/"
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.patch(path, {"archived": True}, format="json", HTTP_X_CSRFTOKEN=csrf).status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Owned edit")
        self.assertFalse(self.product.archived)

    # Function: Keep CSRF, version, and claimed-lease requirements active.
    # Inputs: Real authenticated browser without CSRF and missing protocol versions.
    # Outputs: Rejection before any modification.
    # Logic: Check both HTTP middleware and direct business invariants.
    # Constraints: Does not weaken or replace established version semantics.
    def test_csrf_and_versions(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.patch(f"/api/v1/sales/records/products/{self.product.pk}/", {"name": "Denied"}, format="json").status_code, 403)
        with self.assertRaises(ValidationError):
            check_version(None, 0)
        with self.assertRaises(Conflict):
            require_lease(self.company, None, None)
        self.assertEqual(build_registry()["teams.create"]["executionMode"], "confirm")
        self.assertTrue(build_registry()["products.update"]["idempotency_required"])

    # Function: Resolve Agent context using credential ownership only.
    # Inputs: Credential for the second employee with a forged first-employee header.
    # Outputs: Foreign private company context returns 404; inactive owner returns 401.
    # Logic: Use real digest authentication rather than force_authenticate.
    # Constraints: No worker or external model is started.
    def test_agent_credentials(self):
        AgentCredential.objects.create(owner=self.other, name="test", digest=hashlib.sha256(b"test-agent").hexdigest())
        self.client.credentials(HTTP_AUTHORIZATION="Agent test-agent", HTTP_X_LAB_USER=self.owner.username)
        self.assertEqual(self.client.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}).status_code, 404)
        self.other.is_active = False
        self.other.save()
        self.assertEqual(self.client.get("/api/v1/agent/context/").status_code, 401)

    # Function: Validate limited Tool delegation independently of experiment switches.
    # Inputs: Other employee's valid, expired, and revoked credentials.
    # Outputs: Only delegated names are published; foreign data and ungranted writes reject.
    # Logic: Exercise HTTP Tool authentication and actual business dispatch.
    # Constraints: Credentials are synthetic; no raw credential is logged by application code.
    def test_tool_credentials(self):
        credential = ToolCredential.objects.create(owner=self.other, name="test", digest=hashlib.sha256(b"test-tool").hexdigest(), allowed_tools=["products.get"], expires_at=timezone.now()+timedelta(hours=1))
        self.client.credentials(HTTP_AUTHORIZATION="Tool test-tool", HTTP_X_LAB_USER=self.owner.username)
        self.assertEqual([row["name"] for row in self.client.get(BASE+"catalog/").data["tools"]], ["products.get"])
        response = self.client.post(BASE+"call/", {"name": "products.get", "arguments": {"id": str(self.product.pk)}}, format="json")
        self.assertEqual(response.status_code, 404)
        response = self.client.post(BASE+"call/", {"name": "products.list", "arguments": {}}, format="json")
        self.assertEqual(response.status_code, 403)
        for field, value in (("expires_at", timezone.now()-timedelta(seconds=1)), ("revoked_at", timezone.now())):
            credential.expires_at = timezone.now()+timedelta(hours=1)
            setattr(credential, field, value)
            credential.save()
            self.assertEqual(self.client.get(BASE+"catalog/").status_code, 401)

    # Function: Reject foreign chat access without changing ownership or leaking knowledge.
    # Inputs: Private conversation and knowledge belonging to the first employee.
    # Outputs: Foreign submit/read/retry reject; owner retry records the current initiator without rewriting historical audit; knowledge search contains no private text.
    # Logic: Exercise real transactional chat services, including a legacy mismatched initiator, and normal tool dispatch.
    # Constraints: Successful submission remains pending; no model is invoked.
    def test_chat_and_knowledge(self):
        KnowledgeEntry.objects.create(owner=self.owner, title="Secret fact", content="Private evidence", version="1")
        data = {"conversation_id": str(self.conversation.pk), "client_key": str(uuid.uuid4()), "content": "hello"}
        with self.assertRaises(NotFound):
            chat.submit(self.other, data)
        request, _ = chat.submit(self.owner, data)
        self.assertEqual(request.requested_by_id, self.owner.pk)
        with self.assertRaises(NotFound):
            chat.request_for(self.other, request.pk)
        self.assertNotIn("Private evidence", str(invoke(self.other, None, "knowledge.search", {})))
        self.assertEqual(len(catalog(self.other)), len(build_registry()))
        request.status = "failed"
        request.requested_by = self.other
        request.save(update_fields=["status", "requested_by"])
        with self.assertRaises(NotFound):
            chat.retry(self.other, request.pk)
        successor, created = chat.retry(self.owner, request.pk)
        self.assertTrue(created)
        self.assertEqual(successor.requested_by_id, self.owner.pk)
        request.refresh_from_db()
        self.assertEqual(request.requested_by_id, self.other.pk)

    # Function: Preserve team business grants without extending private ownership.
    # Inputs: Active editor membership and explicit customer grant.
    # Outputs: Shared customer remains writable, while conversation and Gmail remain hidden.
    # Logic: Use the existing permission module with real team rows.
    # Constraints: Raw email context and personal files are never authorized by company grants.
    def test_team_sharing(self):
        team = models.Team.objects.create(owner=self.owner, name="Sales team")
        models.Membership.objects.create(owner=self.owner, team=team, user=self.other, role="editor")
        models.CompanyGrant.objects.create(owner=self.owner, company=self.company, team=team, role="editor")
        self.assertEqual(company_access(self.other, self.company, write=True), self.company)
        self.assertFalse(scope(models.Connection, self.other).exists())
        self.assertFalse(scope(models.Conversation, self.other).exists())
        with self.assertRaises(NotFound):
            chat.conversation_for(self.other, self.conversation.pk)

    # Function: Pin both OAuth flows to their initiating employee.
    # Inputs: Mock Google Flow and browser state, with the account switched before callback.
    # Outputs: InvalidState before token exchange; one-time state is consumed.
    # Logic: Exercise real initiation and callback services for sending/calendar and mailbox OAuth.
    # Constraints: Only the Google boundary is mocked; this does not verify a real provider authorization.
    @override_settings(GOOGLE_OAUTH_CLIENT_ID="test-client", GOOGLE_OAUTH_CLIENT_SECRET="test-secret")
    def test_oauth_owner_binding(self):
        with override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode()):
            for module, provider in (("apps.sales.integrations", "gmail"), ("apps.sales.integrations", "calendar"), ("apps.crm.gmail_oauth", "gmail_read")):
                with self.subTest(provider=provider):
                    flow = Mock(code_verifier="test-verifier")
                    flow.authorization_url.return_value = ("https://accounts.google.com/test", "test-state")
                    request = SimpleNamespace(user=self.owner, session={}, query_params={"state": "test-state", "code": "test-code"})
                    with patch(module+".Flow.from_client_config", return_value=flow):
                        if provider == "gmail_read":
                            begin_authorization(request)
                        else:
                            integrations.begin(request, provider, "https://example.com/callback/")
                        request.user = self.other
                        with self.assertRaises(InvalidState):
                            (finish_authorization if provider == "gmail_read" else integrations.finish)(request)
                    flow.fetch_token.assert_not_called()
                    self.assertFalse(request.session)
