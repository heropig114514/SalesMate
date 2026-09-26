"""Responsibility: Verify workspace-upgrade creation boundaries, legacy-task retirement, and history retention.
Implementation: Use real database and API; directly construct legacy records only to simulate pre-upgrade state, then call the retirement service used by real Worker startup.
Relationships: Covers `chat.services`, chat_worker startup cleanup, sales conversation creation service, and email-draft preparation.
Directory:
- WorkspaceChatTests: Upgrade and safety-boundary tests.
- WorkspaceChatTests.setUp: Establish worker and new and legacy conversations.
- WorkspaceChatTests.legacy_request: Construct an existing request from the legacy version.
- WorkspaceChatTests.test_legacy_creation_and_retry_rejected: New creation and retry no longer enter company chat.
- WorkspaceChatTests.test_claim_retires_legacy_and_continues: Legacy pending does not block workspace claiming.
- WorkspaceChatTests.test_retirement_preserves_history_and_is_idempotent: End legacy active tasks while retaining terminal history.
- WorkspaceChatTests.test_workspace_draft_requires_owner_and_explicit_customer: Workspace draft can prepare action but cannot cross worker or legacy customer.
Variable index:
- None
"""

import uuid

from apps.chat import services
from apps.chat.models import AnswerRequest
from apps.crm.access import InvalidState
from apps.crm.models import Company
from apps.sales import actions, models
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from tests.integration.test_chat import fixture


# Function: Cover legacy-contract retirement and workspace-draft permissions.
# Logic: Execute real ORM and services, explicitly constructing legacy data rather than using the disabled creation path.
# Constraints: Do not access production database or send mail; TestCase rolls back test records.
class WorkspaceChatTests(TestCase):
    # Function: Create worker, workspace, and company-bound historical conversation.
    # Inputs: No parameters; uses isolated test database.
    # Outputs: Instance fixtures and worker-authenticated client.
    # Logic: Reuse public test fixture and directly construct legacy conversation to simulate pre-upgrade data.
    # Constraints: Do not rewrite system users or actual external credentials.
    def setUp(self):
        self.owner, self.other, self.company, self.workspace = fixture()
        self.legacy = models.Conversation.objects.create(
            owner=self.owner, company=self.company
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # Function: Construct a pre-upgrade company-bound request.
    # Inputs: `status` is the state under test and defaults to pending.
    # Outputs: Persisted AnswerRequest.
    # Logic: Retain original company, conversation, and user-message relationships; terminal `result` compares before and after retirement.
    # Constraints: Use only as historical-data fixture and do not bypass production entry point to create new tasks.
    def legacy_request(self, status="pending"):
        message = models.Message.objects.create(
            owner=self.owner,
            conversation=self.legacy,
            role="user",
            content="这个客户怎么样？",
            client_key=uuid.uuid4(),
        )
        return AnswerRequest.objects.create(
            owner=self.owner,
            conversation=self.legacy,
            company=self.company,
            user_message=message,
            status=status,
            result={"historical": True} if status == "completed" else None,
        )

    # Function: Verify that every new chat can be created only from a workspace conversation.
    # Inputs: No parameters; browser creation payload and legacy failed task.
    # Outputs: Company-conversation creation returns 400, legacy submission and retry reject, and history remains readable.
    # Logic: Check rejected operations create neither user messages nor successor tasks.
    # Constraints: Do not delete or rebind legacy conversation; workspace explicit null remains creatable.
    def test_legacy_creation_and_retry_rejected(self):
        created = self.client.post(
            "/api/v1/sales/records/conversations/",
            {"company": str(self.company.pk)},
            format="json",
        )
        self.assertEqual(created.status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/v1/sales/records/conversations/", {"company": None}, format="json"
            ).status_code,
            201,
        )
        old = self.legacy_request("failed")
        with self.assertRaises(InvalidState):
            services.submit(
                self.owner,
                {
                    "conversation_id": str(self.legacy.pk),
                    "content": "继续",
                    "client_key": str(uuid.uuid4()),
                },
            )
        with self.assertRaises(InvalidState):
            services.retry(self.owner, old.pk)
        self.assertEqual(models.Message.objects.count(), 1)
        self.assertEqual(services.request_for(self.owner, old.pk).pk, old.pk)

    # Function: Verify that legacy runtime tasks do not block the claim queue.
    # Inputs: No parameters; legacy company pending task and new workspace request.
    # Outputs: Returns only a five-field workspace request; legacy request explicitly fails without assistant message.
    # Logic: Skip legacy task under the same worker lock and continue searching for executable task.
    # Constraints: Do not implicitly transfer question or automatically reinvoke the model.
    def test_claim_retires_legacy_and_continues(self):
        old = self.legacy_request()
        current, _ = services.submit(
            self.owner,
            {
                "conversation_id": str(self.workspace.pk),
                "content": "查找客户",
                "client_key": str(uuid.uuid4()),
            },
        )
        claimed = services.claim(self.owner)
        self.assertEqual(claimed["request_id"], str(current.pk))
        self.assertNotIn("company_id", claimed)
        old.refresh_from_db()
        self.assertEqual(old.status, "failed")
        self.assertEqual(old.error["code"], "workspace_chat_required")
        self.assertIsNotNone(old.finished_at)
        self.assertIsNone(old.assistant_message_id)

    # Function: Verify that Worker-startup cleanup ends only legacy active tasks.
    # Inputs: No parameters; pending, processing, completed history, and new workspace task.
    # Outputs: Legacy active tasks end while workspace and historical results remain unchanged; second run makes no additional change.
    # Logic: Call Worker retirement service twice and check terminal-state idempotency and unaffected workspace.
    # Constraints: Do not change database structure, call model, or turn legacy tasks into new pending tasks.
    def test_retirement_preserves_history_and_is_idempotent(self):
        pending = self.legacy_request()
        self.legacy = models.Conversation.objects.create(
            owner=self.owner, company=self.company
        )
        processing = self.legacy_request("processing")
        completed = self.legacy_request("completed")
        current, _ = services.submit(
            self.owner,
            {
                "conversation_id": str(self.workspace.pk),
                "content": "你好",
                "client_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(services.retire_legacy_requests(), 2)
        pending.refresh_from_db()
        finished = pending.finished_at
        self.assertEqual(services.retire_legacy_requests(), 0)
        for old in (pending, processing):
            old.refresh_from_db()
            self.assertEqual(old.status, "failed")
        self.assertEqual(pending.finished_at, finished)
        completed.refresh_from_db()
        current.refresh_from_db()
        self.assertEqual(completed.result, {"historical": True})
        self.assertEqual(completed.status, "completed")
        self.assertEqual(current.status, "pending")
        self.assertEqual(models.Message.objects.count(), 4)

    # Function: Verify that workspace drafts serve actions for an explicit customer while ownership restrictions remain effective.
    # Inputs: No parameters; owner connection and email draft, another worker conversation, and another-customer legacy conversation.
    # Outputs: Owner workspace draft freezes successfully; drafts of another worker or wrong customer reject.
    # Logic: Call real parameter-preparation service and change only the draft conversation as counterexample.
    # Constraints: Do not execute provider, approve action, or use real connection credentials.
    def test_workspace_draft_requires_owner_and_explicit_customer(self):
        connector = models.Connection.objects.create(
            owner=self.owner,
            provider="gmail",
            account="synthetic@example.com",
            encrypted_credentials="not-a-token",
        )
        draft = models.Draft.objects.create(
            owner=self.owner,
            conversation=self.workspace,
            kind="email",
            subject="测试",
            content="合成正文",
            recipients=["buyer@example.com"],
        )
        parameters = {"connection_id": str(connector.pk), "draft_id": str(draft.pk)}
        self.assertEqual(
            actions.validate_parameters(
                self.owner, self.company, "gmail.send", parameters
            )["body"],
            draft.content,
        )
        other_company = Company.objects.create(
            owner=self.owner, name="其他客户", group_key="domain:other.example"
        )
        for conversation in (
            models.Conversation.objects.create(owner=self.other),
            models.Conversation.objects.create(owner=self.owner, company=other_company),
        ):
            draft.conversation = conversation
            draft.save(update_fields=["conversation"])
            with self.assertRaises(ValidationError):
                actions.validate_parameters(
                    self.owner, self.company, "gmail.send", parameters
                )
