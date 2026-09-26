"""Responsibility: Verify persistence, scope isolation, and answer protocol for general conversations.
Implementation: Use real database and APIClient to cover customerless creation, private access, drafts, and reports with any valid version.
Relationships: Covers sales-record API, chat transactions, and general Agent mode; does not call an external model.
Directory:
- GeneralChatTests: Integration tests for chats without a customer.
- GeneralChatTests.setUp: Create customerless workers and an authenticated client.
- GeneralChatTests.test_general_round_trip_and_isolation: Full flow for conversation, draft, question, context, and answer.
- GeneralChatTests.test_scope_and_immutable_binding: General/customer filtering and prohibited rebinding.
Variable index:
- RECORDS: Sales-record entry point.
"""

import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from apps.chat import services
from apps.chat.models import KnowledgeEntry
from apps.crm.models import Company
from apps.sales.models import Conversation

RECORDS = "/api/v1/sales/records/"


# Function: Verify that general chat requires no customer data.
# Logic: Create relationship records from an empty account and reject cross-worker reads.
# Constraints: Use test-database transactions and do not access production data or models.
class GeneralChatTests(TestCase):
    # Function: Create two users without customers.
    # Inputs: No external parameters; uses the test database.
    # Outputs: `owner`, `other`, and browser client.
    # Logic: Establish identity through the user model; API uses DRF authentication.
    # Constraints: Do not bypass business permissions or serialization validation.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="general-owner")
        self.other = get_user_model().objects.create_user(username="general-other")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # Function: Verify complete persistence and permission boundaries for a zero-customer account.
    # Inputs: No external parameters; knowledge fixtures for the owner and another worker.
    # Outputs: Successful answer and readable draft; unauthorized users cannot read or ask.
    # Logic: Omit company when creating a conversation and saving a draft; claiming freezes only owner knowledge, claim response contains no company field, and report version checks only type and length.
    # Constraints: Model output is an explicit synthetic receipt and does not validate external services.
    def test_general_round_trip_and_isolation(self):
        created = self.client.post(
            RECORDS + "conversations/",
            {"title": "通用会话"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        conversation = created.data["id"]
        draft = self.client.post(
            RECORDS + "drafts/",
            {"conversation": conversation, "kind": "chat", "content": "邮件草稿"},
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        KnowledgeEntry.objects.create(
            owner=self.owner,
            source_key="mine",
            version="1",
            title="本人知识",
            content="可用内容",
        )
        KnowledgeEntry.objects.create(
            owner=self.other,
            source_key="other",
            version="1",
            title="他人知识",
            content="不可见内容",
        )
        request, _ = services.submit(
            self.owner,
            {
                "conversation_id": conversation,
                "content": "你好",
                "client_key": str(uuid.uuid4()),
            },
        )
        claimed = services.claim(self.owner)
        self.assertNotIn("company_id", claimed)
        context = services.context_for(self.owner, request.pk, "internal")
        self.assertEqual(context["customer_context"], [])
        self.assertEqual(len(context["context_items"]), 1)
        self.assertNotIn("不可见", str(context))
        result = {
            "request_id": str(request.pk),
            "chat_prompt_version": "workspace-chat-v1",
            "status": "completed",
            "error": None,
            "assistant_text": "你好，可以一起起草邮件。",
            "citations": [],
        }
        for version in (None, [], {}, "", "v" * 101):
            with self.assertRaises(ValidationError):
                services.save_answer(
                    self.owner, {**result, "chat_prompt_version": version}
                )
        self.assertTrue(services.save_answer(self.owner, result)["saved"])
        self.assertTrue(services.save_answer(self.owner, result)["duplicate"])
        self.assertEqual(
            len(
                self.client.get(
                    RECORDS + f"messages/?conversation={conversation}"
                ).data["results"]
            ),
            2,
        )
        self.assertEqual(
            self.client.get(RECORDS + f"drafts/?conversation={conversation}").data[
                "results"
            ][0]["content"],
            "邮件草稿",
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(
            self.client.get(RECORDS + "conversations/?conversation_scope=general").data[
                "count"
            ],
            0,
        )
        self.assertEqual(
            self.client.get(RECORDS + f"conversations/{conversation}/").status_code, 404
        )
        denied = self.client.post(
            "/api/v1/sales/chat/messages/",
            {
                "conversation_id": conversation,
                "content": "越权",
                "client_key": str(uuid.uuid4()),
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 404)

    # Function: Verify listings and immutable binding of the two conversation kinds.
    # Inputs: No external parameters; one conversation of each kind for the same user.
    # Outputs: Filters are mutually exclusive and invalid filters and rebinding are rejected.
    # Logic: General and customer queries both apply owner scope; attempt to change a general conversation into a customer conversation.
    # Constraints: Do not bypass history or permission isolation through rebinding.
    def test_scope_and_immutable_binding(self):
        company = Company.objects.create(
            owner=self.owner, name="客户", group_key="domain:general.example"
        )
        general = Conversation.objects.create(owner=self.owner, company=None)
        specific = Conversation.objects.create(owner=self.owner, company=company)
        for mode, expected in (("general", general), ("customer", specific)):
            response = self.client.get(
                RECORDS + f"conversations/?conversation_scope={mode}"
            )
            self.assertEqual(
                [row["id"] for row in response.data["results"]], [str(expected.pk)]
            )
        self.assertEqual(
            self.client.get(
                RECORDS + "conversations/?conversation_scope=invalid"
            ).status_code,
            400,
        )
        changed = self.client.patch(
            RECORDS + f"conversations/{general.pk}/",
            {"company": str(company.pk)},
            format="json",
            HTTP_IF_MATCH=str(general.revision),
        )
        self.assertEqual(changed.status_code, 400, changed.data)
