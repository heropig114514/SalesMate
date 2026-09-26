"""Responsibility: Verifies sales relation models, permissions, business state, and external-action boundaries.
Implementation: Email drafts use a workspace conversation with no preselected company; an isolated test database drives real HTTP and transactions, while Google calls are mocked only at designated boundaries.
Relationships: Covers `apps.sales` and verifies CRM original-mail projection compatibility; does not establish real external authorization.
Directory:
- SalesTests: Sales business integration tests.
- SalesTests.setUp: Creates isolated users, companies, and clients.
- SalesTests.create: Creates records through the real API.
- SalesTests.command: Sends versioned business commands.
- SalesTests.quote: Builds a quote draft with discounted lines.
- SalesTests.action: Builds a pending-confirmation sending action.
- SalesTests.test_catalog_and_unknown_fields: Field contracts and read-only protection.
- SalesTests.test_revision_and_ownership: Version and cross-user isolation.
- SalesTests.test_team_sharing_and_private_records: Shared business and private-data separation.
- SalesTests.test_manager_cannot_escalate: Management-role escalation prevention.
- SalesTests.test_quote_money_freeze_and_projection: Amount freezing and real outbound evidence.
- SalesTests.test_order_only_confirmed_is_history: Order state and historical projection.
- SalesTests.test_line_validation_and_parent_revision: Line constraints and parent-document version.
- SalesTests.test_cross_company_relations_rejected: Cross-company reference rejection.
- SalesTests.test_messages_idempotent_and_immutable: Conversation idempotency and immutable messages.
- SalesTests.test_action_confirmation_success_and_idempotence: Explicit approval and one execution.
- SalesTests.test_action_unknown_result_not_retried: Unknown network results are not retried.
- SalesTests.test_quote_reserved_by_approved_action: Approved actions freeze quote versions.
- SalesTests.test_action_cancel_and_tampering: Cancellation and non-editable snapshots.
- SalesTests.test_files_private_and_not_inline: Attachment isolation and forced download.
- SalesTests.test_due_notifications_are_deduplicated: Expiration-reminder deduplication.
- SalesTests.test_manual_alias_controls_ingestion: Manual mapping controls future grouping.
- SalesTests.test_manual_primary_contact: Primary contact overrides automatic selection.
- SalesTests.test_merge_preserves_records_and_profiles: Merge preserves relationships and supplemental material.
- SalesTests.test_connection_credentials_never_exposed: Connection ciphertext does not enter API responses.
- SalesTests.test_missing_vault_fails_explicitly: Missing keys fail explicitly.
Variable index:
- BASE: Sales API prefix.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm import ingestion, rules, selectors
from apps.crm.models import Contact, Mailbox
from apps.sales import actions, grouping, models, services

BASE = "/api/v1/sales/"


# Function: Verifies sales-business persistence and boundaries.
# Logic: Each test uses a real isolated database; business updates create analysis tasks through the existing provider; tests do not start an independent Worker.
# Constraints: External providers are mocked only in action tests; no real messages are sent.
class SalesTests(TestCase):
    # Function: Creates the minimum business context.
    # Inputs: No external parameters; created by the test framework.
    # Outputs: User, company, and authenticated `APIClient` instance state.
    # Logic: Two users and two companies for permission counterexamples.
    # Constraints: Passwords and email addresses are test samples.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="seller")
        self.other = get_user_model().objects.create_user(username="colleague")
        self.company = grouping.create_company(self.user, "测试客户")
        self.second = grouping.create_company(self.user, "第二客户")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # Function: Creates a business record through HTTP.
    # Inputs: `resource`, `data`, and `expected`; expected status defaults to 201.
    # Outputs: Response data.
    # Logic: Failures expose the real API error for diagnosis.
    # Constraints: Does not bypass business services.
    def create(self, resource, data, expected=201):
        response = self.client.post(BASE + f"records/{resource}/", data, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # Function: Sends a versioned command.
    # Inputs: `resource`, `record`, `command`, `value`, and `expected`; expected defaults to 200.
    # Outputs: Response data.
    # Logic: Builds If-Match from the read `revision`.
    # Constraints: Conflict tests may supply an old record version.
    def command(self, resource, record, command, value=None, expected=200):
        response = self.client.post(
            BASE + f"records/{resource}/{record['id']}/commands/",
            {"command": command, "value": value},
            format="json",
            HTTP_IF_MATCH=str(record["revision"]),
        )
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    # Function: Builds a quote with verifiable amounts.
    # Inputs: No external parameters; reads the test company.
    # Outputs: One-line quote response.
    # Logic: 3 × 12.35 − 0.05 = 37.00.
    # Constraints: Does not approve or send.
    def quote(self):
        quote = self.create(
            "quotes",
            {"company": str(self.company.pk), "number": "Q-001", "currency": "USD"},
        )
        self.create(
            "quote-lines",
            {
                "quote": quote["id"],
                "description": "设备",
                "quantity": "3",
                "unit_price": "12.35",
                "discount": "0.05",
            },
        )
        return self.client.get(BASE + f"records/quotes/{quote['id']}/").data

    # Function: Builds a pending-confirmation action with a complete email snapshot.
    # Inputs: `quote` is an optional approved quote response.
    # Outputs: Action response.
    # Logic: Drafts use a workspace conversation; connection credentials are unusable placeholders, and execution tests must patch the credential boundary.
    # Constraints: Creation does not mean approval or sending.
    def action(self, quote=None):
        connection = models.Connection.objects.create(
            owner=self.user,
            provider="gmail",
            account="seller@example.com",
            encrypted_credentials="test-not-a-token",
        )
        conversation = self.create("conversations", {"title": "工作空间"})
        draft = self.create(
            "drafts",
            {
                "conversation": conversation["id"],
                "kind": "email",
                "subject": "测试报价",
                "content": "请审阅",
                "recipients": ["buyer@example.com"],
            },
        )
        parameters = {"connection_id": str(connection.pk), "draft_id": draft["id"]}
        if quote:
            parameters["quote_id"] = quote["id"]
        return self.create(
            "actions",
            {
                "company": str(self.company.pk),
                "tool": "gmail.send",
                "parameters": parameters,
                "idempotency_key": str(uuid.uuid4()),
            },
        )

    # Function: Verifies field contracts and rejects client-supplied permission fields.
    # Inputs: Test identity and catalog request.
    # Outputs: All resources exist and forged `owner`/`status` return 400.
    # Logic: Verifies the actual API rather than model fields alone.
    # Constraints: Does not cover every field combination.
    def test_catalog_and_unknown_fields(self):
        response = self.client.get(BASE + "catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["resources"]), 20)
        self.create(
            "tickets",
            {"company": str(self.company.pk), "title": "测试", "status": "closed"},
            400,
        )
        self.create(
            "products",
            {
                "sku": "P",
                "name": "设备",
                "currency": "USD",
                "unit_price": "10",
                "owner": self.other.pk,
            },
            400,
        )

    # Function: Verifies revision conflict and non-owner isolation.
    # Inputs: After real creation, uses an outdated version and a second user.
    # Outputs: Outdated is 409; unauthorized is 404.
    # Logic: Makes one successful update, then reuses the old revision.
    # Constraints: Does not substitute list hiding for detail permission.
    def test_revision_and_ownership(self):
        ticket = self.create(
            "tickets", {"company": str(self.company.pk), "title": "原始"}
        )
        path = BASE + f"records/tickets/{ticket['id']}/"
        self.assertEqual(
            self.client.patch(
                path, {"title": "新的"}, format="json", HTTP_IF_MATCH="0"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.patch(
                path, {"title": "覆盖"}, format="json", HTTP_IF_MATCH="0"
            ).status_code,
            409,
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(path).status_code, 404)

    # Function: Verifies shared-customer editing and private-conversation isolation.
    # Inputs: An editor team, editor company authorization, and private conversation.
    # Outputs: A colleague may edit the ticket but cannot see the private conversation or CRM email context.
    # Logic: Uses a no-company conversation and calls business API and the original private CRM API separately.
    # Constraints: Sharing does not represent mailbox authorization.
    def test_team_sharing_and_private_records(self):
        team = self.create("teams", {"name": "销售团队"})
        self.create(
            "memberships", {"team": team["id"], "user": self.other.pk, "role": "editor"}
        )
        self.create(
            "grants",
            {"company": str(self.company.pk), "team": team["id"], "role": "editor"},
        )
        self.create("conversations", {"title": "工作空间"})
        self.client.force_authenticate(self.other)
        self.create("tickets", {"company": str(self.company.pk), "title": "协作工单"})
        self.assertEqual(
            self.client.get(BASE + "records/conversations/").data["count"], 0
        )
        self.assertEqual(
            self.client.get(f"/api/v1/companies/{self.company.pk}/").status_code, 404
        )
        self.assertNotIn("content", str(self.client.get(BASE + "audit/").data))

    # Function: Verifies a manager cannot create or modify another manager.
    # Inputs: Manager identity granted by the team owner.
    # Outputs: Forged management authority is 403.
    # Logic: Attempts to create a manager and demote itself.
    # Constraints: Ordinary editor-member management remains allowed.
    def test_manager_cannot_escalate(self):
        team = self.create("teams", {"name": "团队"})
        member = self.create(
            "memberships",
            {"team": team["id"], "user": self.other.pk, "role": "manager"},
        )
        third = get_user_model().objects.create_user(username="third")
        self.client.force_authenticate(self.other)
        self.create(
            "memberships",
            {"team": team["id"], "user": third.pk, "role": "manager"},
            403,
        )
        response = self.client.patch(
            BASE + f"records/memberships/{member['id']}/",
            {"role": "editor"},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 403)

    # Function: Verifies quote net amount, freezing, and actual sending evidence.
    # Inputs: Verifiable quote and approval operation.
    # Outputs: Net amount is 37.00; editing after approval and forged sent status are forbidden.
    # Logic: Verifies persisted `company.quotes` has no draft or approved-only record.
    # Constraints: Real sending is covered by separate action tests.
    def test_quote_money_freeze_and_projection(self):
        quote = self.quote()
        self.assertEqual(quote["total"], "37.00")
        quote = self.command("quotes", quote, "transition", "approved")
        self.company.refresh_from_db()
        self.assertEqual(self.company.quotes, [])
        self.command("quotes", quote, "transition", "sent", 409)
        response = self.client.patch(
            BASE + f"records/quotes/{quote['id']}/",
            {"notes": "changed"},
            format="json",
            HTTP_IF_MATCH=str(quote["revision"]),
        )
        self.assertEqual(response.status_code, 409)

    # Function: Verifies the effect of order confirmation and cancellation on historical projection.
    # Inputs: One draft order with lines.
    # Outputs: Draft is absent from history, confirmation enters it, and cancellation leaves effective history.
    # Logic: Reads real Company JSON each time.
    # Constraints: Does not infer inventory or accounting revenue.
    def test_order_only_confirmed_is_history(self):
        order = self.create(
            "orders",
            {"company": str(self.company.pk), "number": "O1", "currency": "USD"},
        )
        self.create(
            "order-lines",
            {
                "order": order["id"],
                "description": "设备",
                "quantity": "2",
                "unit_price": "10",
            },
        )
        order = self.client.get(BASE + f"records/orders/{order['id']}/").data
        self.company.refresh_from_db()
        self.assertEqual(self.company.orders, [])
        order = self.command("orders", order, "transition", "confirmed")
        self.company.refresh_from_db()
        self.assertEqual(self.company.orders[0]["amount"], "20.00")
        self.command("orders", order, "transition", "cancelled")
        self.company.refresh_from_db()
        self.assertEqual(self.company.orders, [])

    # Function: Verifies quantity-discount boundary and parent-version increments.
    # Inputs: Quote and invalid line.
    # Outputs: Invalid discount is 400; parent revision reflects the existing valid line.
    # Logic: Rejects discounts exceeding quantity times unit price.
    # Constraints: Amounts failing validation cannot affect the parent document.
    def test_line_validation_and_parent_revision(self):
        quote = self.quote()
        self.assertEqual(quote["revision"], 1)
        self.create(
            "quote-lines",
            {
                "quote": quote["id"],
                "description": "非法",
                "quantity": "1",
                "unit_price": "1",
                "discount": "2",
            },
            400,
        )
        self.assertEqual(models.Quote.objects.get(pk=quote["id"]).revision, 1)

    # Function: Verifies quote source and primary contact reject cross-customer references.
    # Inputs: A second customer and the first customer's quote/contact.
    # Outputs: Both writes return 400.
    # Logic: Requires company consistency even for the same owner.
    # Constraints: Does not rely on UUID unguessability for security.
    def test_cross_company_relations_rejected(self):
        quote = self.quote()
        self.create(
            "orders",
            {
                "company": str(self.second.pk),
                "number": "O2",
                "currency": "USD",
                "quote": quote["id"],
            },
            400,
        )
        contact = Contact.objects.create(
            company=self.second, email="second@example.com"
        )
        setting = models.CompanySettings.objects.get(company=self.company)
        response = self.client.patch(
            BASE + f"records/customers/{setting.pk}/",
            {"primary_contact": contact.pk},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 400)

    # Function: Verifies idempotent message submission, unforgeable roles, and immutability.
    # Inputs: Conversation and messages with the same client_key.
    # Outputs: Same content returns the same ID; different content conflicts; editing and forged roles fail.
    # Logic: Workspace conversations use both transactional uniqueness and payload consistency.
    # Constraints: Does not mock or generate an assistant response.
    def test_messages_idempotent_and_immutable(self):
        conversation = self.create("conversations", {"title": "工作空间"})
        data = {
            "conversation": conversation["id"],
            "content": "请整理需求",
            "client_key": str(uuid.uuid4()),
        }
        message = self.create("messages", data)
        self.assertEqual(self.create("messages", data)["id"], message["id"])
        self.create("messages", {**data, "content": "other"}, 409)
        self.create("messages", {**data, "role": "assistant"}, 400)
        response = self.client.patch(
            BASE + f"records/messages/{message['id']}/",
            {"content": "改写历史"},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 409)
        self.create(
            "drafts",
            {"conversation": conversation["id"], "kind": "chat", "content": "未完成"},
        )

    # Function: Verifies execution occurs once only after explicit confirmation.
    # Inputs: Test draft and mocked credentials/provider.
    # Outputs: Provider is not called before approval, succeeds after approval, and repeated execution does not duplicate it.
    # Logic: Repeated idempotency key requests reuse the original action.
    # Constraints: This test does not connect to real Gmail.
    def test_action_confirmation_success_and_idempotence(self):
        action = self.action()
        with (
            patch("apps.sales.actions.credentials_for"),
            patch(
                "apps.sales.actions.execute_provider",
                return_value={"message_id": "mock-id"},
            ) as execute,
        ):
            self.assertEqual(actions.run_action(action["id"]), "pending_confirmation")
            execute.assert_not_called()
            action = self.command("actions", action, "decide", "approved")
            self.assertEqual(actions.run_action(action["id"]), "succeeded")
            self.assertEqual(actions.run_action(action["id"]), "succeeded")
            execute.assert_called_once()
        duplicate = self.create(
            "actions",
            {
                "company": action["company"],
                "tool": action["tool"],
                "parameters": action["parameters"]["request"],
                "idempotency_key": action["idempotency_key"],
            },
        )
        self.assertEqual(duplicate["id"], action["id"])

    # Function: Verifies unknown external result persists separately and is not retried.
    # Inputs: Approved action with provider simulating connection interruption.
    # Outputs: `uncertain`; later Worker calls do not send again.
    # Logic: An exception after network invocation starts cannot be classified as unsent.
    # Constraints: Does not claim a mocked exception covers all real Google errors.
    def test_action_unknown_result_not_retried(self):
        action = self.command("actions", self.action(), "decide", "approved")
        with (
            patch("apps.sales.actions.credentials_for"),
            patch(
                "apps.sales.actions.execute_provider",
                side_effect=ConnectionError("mock"),
            ) as execute,
        ):
            self.assertEqual(actions.run_action(action["id"]), "uncertain")
            self.assertEqual(actions.run_action(action["id"]), "uncertain")
            execute.assert_called_once()

    # Function: Verifies quote version is retained for approved outbound actions.
    # Inputs: Approved quote and action.
    # Outputs: Approval revocation fails; only mocked send success creates `actual_outbound`.
    # Logic: Detects the risk of state changes during the network window.
    # Constraints: Provider is mocked; no email is sent.
    def test_quote_reserved_by_approved_action(self):
        quote = self.command("quotes", self.quote(), "transition", "approved")
        action = self.command("actions", self.action(quote), "decide", "approved")
        self.command("quotes", quote, "transition", "draft", 409)
        with (
            patch("apps.sales.actions.credentials_for"),
            patch(
                "apps.sales.actions.execute_provider",
                return_value={"message_id": "mock-sent"},
            ),
        ):
            self.assertEqual(actions.run_action(action["id"]), "succeeded")
        self.company.refresh_from_db()
        self.assertEqual(self.company.quotes[0]["evidence_type"], "actual_outbound")

    # Function: Verifies action cancellation and non-editable snapshots.
    # Inputs: Pending-confirmation action and modification-parameter request.
    # Outputs: Editing is 409; after cancellation it cannot be approved.
    # Logic: All action writes use the dedicated entry point.
    # Constraints: An unexecuted action has no side effect of retracting an external service.
    def test_action_cancel_and_tampering(self):
        action = self.action()
        response = self.client.patch(
            BASE + f"records/actions/{action['id']}/",
            {"parameters": {}},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 409)
        action = self.command("actions", action, "decide", "cancelled")
        self.command("actions", action, "decide", "approved", 409)

    # Function: Verifies upload storage, download permission, and attachment response.
    # Inputs: Temporary directory and synthetic HTML file.
    # Outputs: Owner can download; another user receives 404; response is not inline HTML.
    # Logic: Checks storage bytes and response disposition; exhausting the stream lets the test client close the response in its own lifecycle.
    # Constraints: Cleans only the temporary directory after the test.
    def test_files_private_and_not_inline(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(BASE_DIR=Path(directory)),
        ):
            response = self.client.post(
                BASE + "files/",
                {
                    "company": str(self.company.pk),
                    "file": SimpleUploadedFile(
                        "proof.html", b"<h1>test</h1>", content_type="text/html"
                    ),
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertNotIn("storage_key", response.data)
            path = BASE + f"files/{response.data['id']}/download/"
            download = self.client.get(path)
            self.assertIn("attachment", download["Content-Disposition"])
            self.assertEqual(b"".join(download.streaming_content), b"<h1>test</h1>")
            self.client.force_authenticate(self.other)
            self.assertEqual(self.client.get(path).status_code, 404)

    # Function: Verifies repeated polling creates no duplicate reminder.
    # Inputs: An expired open follow-up.
    # Outputs: First call adds 1, second adds 0, and read state persists.
    # Logic: Owner, task, and revision form the joint deduplication key.
    # Constraints: Does not send external reminders.
    def test_due_notifications_are_deduplicated(self):
        self.create(
            "follow-ups",
            {
                "company": str(self.company.pk),
                "title": "回访",
                "due_at": timezone.now().isoformat(),
            },
        )
        self.assertEqual(services.notify_due(), 1)
        self.assertEqual(services.notify_due(), 0)
        notification = self.client.get(BASE + "records/notifications/").data["results"][
            0
        ]
        self.assertIsNotNone(
            self.command("notifications", notification, "read")["read_at"]
        )

    # Function: Verifies exact contact mapping takes precedence over domain rules.
    # Inputs: Manual alias and rule-generated synthetic email.
    # Outputs: New email is grouped into the designated manual company.
    # Logic: Calls actual ingestion without changing the email body.
    # Constraints: Does not access external Gmail.
    def test_manual_alias_controls_ingestion(self):
        self.create(
            "aliases",
            {
                "company": str(self.company.pk),
                "group_key": "contact:buyer@sample.example",
            },
        )
        mailbox = Mailbox.objects.create(owner=self.user, address="seller@example.com")
        payload = rules.extract_email(
            mailbox, "buyer@sample.example", "询价", "需求：采购", "alias-test"
        )
        self.assertEqual(
            ingestion.submit_emails(self.user, [payload])[0]["company_id"],
            str(self.company.pk),
        )

    # Function: Verifies manual primary contact changes grouping while preserving protocol.
    # Inputs: Two contacts with no exchanges and an explicit primary-contact setting.
    # Outputs: The second contact is marked primary.
    # Logic: Saves through the customer-settings API, then reads the real selector.
    # Constraints: Does not add L1-L4 protocol fields.
    def test_manual_primary_contact(self):
        Contact.objects.create(company=self.company, email="a@example.com")
        primary = Contact.objects.create(company=self.company, email="z@example.com")
        setting = models.CompanySettings.objects.get(company=self.company)
        response = self.client.patch(
            BASE + f"records/customers/{setting.pk}/",
            {"primary_contact": primary.pk},
            format="json",
            HTTP_IF_MATCH="0",
        )
        self.assertEqual(response.status_code, 200, response.data)
        grouped, _ = selectors.context_pair(self.company)
        self.assertEqual(
            [c["contact_email"] for c in grouped["contacts"] if c["is_primary"]],
            ["z@example.com"],
        )

    # Function: Verifies company merge preserves business relations and contact material.
    # Inputs: Source ticket, contact, and phone.
    # Outputs: Ticket and material move to the target; source is archived.
    # Logic: Checks relation models after real transactional merge.
    # Constraints: Does not overwrite conflicting fields.
    def test_merge_preserves_records_and_profiles(self):
        ticket = self.create(
            "tickets", {"company": str(self.company.pk), "title": "来源工单"}
        )
        contact = Contact.objects.create(
            company=self.company, email="buyer@example.com"
        )
        profile = models.ContactProfile.objects.create(
            owner=self.user, contact=contact, phone="123456"
        )
        self.company.refresh_from_db()
        result = grouping.merge_companies(
            self.user,
            self.company.pk,
            self.second.pk,
            self.company.revision,
            self.second.revision,
        )
        self.assertEqual(result["id"], str(self.second.pk))
        self.assertEqual(
            models.Ticket.objects.get(pk=ticket["id"]).company_id, self.second.pk
        )
        profile.refresh_from_db()
        self.assertEqual(profile.contact.company_id, self.second.pk)
        self.assertTrue(
            models.CompanySettings.objects.get(company=self.company).archived
        )

    # Function: Verifies connection ciphertext does not enter directory responses.
    # Inputs: A test ciphertext connection.
    # Outputs: The list includes the account but no credential field or ciphertext.
    # Logic: Uses the real connection serializer.
    # Constraints: Does not decrypt or verify external identity.
    def test_connection_credentials_never_exposed(self):
        models.Connection.objects.create(
            owner=self.user,
            provider="gmail",
            account="test@example.com",
            encrypted_credentials="hidden-test-secret",
        )
        response = self.client.get(BASE + "records/connections/")
        self.assertNotIn("hidden-test-secret", str(response.data))
        self.assertNotIn("encrypted_credentials", str(response.data))

    # Function: Verifies the authorization entry point explicitly rejects a missing encryption key.
    # Inputs: Empty SALESMATE_VAULT_KEY.
    # Outputs: 409 and no connection write.
    # Logic: Service configuration is checked when requesting the OAuth URL.
    # Constraints: Does not automatically generate a key or fall back to plaintext storage.
    @override_settings(SALESMATE_VAULT_KEY="")
    def test_missing_vault_fails_explicitly(self):
        response = self.client.post(
            BASE + "oauth/", {"provider": "gmail"}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(models.Connection.objects.count(), 0)
