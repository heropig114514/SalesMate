"""Responsibility: Verifies real adapter payloads and authorization-failure boundaries for external tools.
Implementation: Uses isolated database and mocked Google SDK, checking MIME, time, notifications, token encryption, and reconciliation.
Relationships: `apps.sales.actions/calendar/integrations`; mock success does not establish that an external account completed authorization.
Directory:
- ExternalTests: External tools and connection tests.
- ExternalTests.setUp: Creates synthetic identity and connection.
- ExternalTests.test_gmail_adapter_exact_content: Checks MIME and single execution.
- ExternalTests.test_calendar_adapter_explicit_notifications: Checks event ID, time, and notifications.
- ExternalTests.test_calendar_validation_requires_timezone_and_notification: Rejects implicit conferencing parameters.
- ExternalTests.test_credentials_encrypted_and_scopes_verified: Checks encryption and real persisted scope.
- ExternalTests.test_oauth_pkce_reuses_verifier: Checks OAuth round-trip PKCE.
- ExternalTests.test_calendar_reads_are_owner_scoped: Verifies read-only calendar authorization scope.
- ExternalTests.test_verification_cannot_create_or_send: Verify only existing events are queried.
- ExternalTests.test_bad_preflight_never_calls_provider: Verify preflight failures prevent external business calls.
- ExternalTests.test_authenticated_write_requires_csrf: Verify CSRF through actual Sessions.
Variable index:
- None
"""

import base64
from email import message_from_bytes
from unittest.mock import Mock, patch
import uuid

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.crm.access import InvalidState
from apps.sales import actions, calendar, grouping, integrations, models


# Function: Verify tool-adapter authorization and payload invariants.
# Logic: Mock SDK networking while retaining an isolated real database; business tasks may queue, but no independent analysis Worker runs.
# Constraints: Never write to real recipients or calendars.
class ExternalTests(TestCase):
    # Function: Create test connections and conversations.
    # Inputs: No external arguments.
    # Outputs: Instance user, company, connection, and client state.
    # Logic: Connections use explicitly invalid ciphertext; tests must mock credential boundaries to execute.
    # Constraints: Do not read existing development credentials.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="external-tester")
        self.company = grouping.create_company(self.user, "外部工具测试客户")
        self.connection = models.Connection.objects.create(
            owner=self.user,
            provider="calendar",
            account="calendar@example.com",
            encrypted_credentials="not-a-real-token",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # Function: Verify the Gmail adapter sends the exact frozen body.
    # Inputs: Synthetic ToolAction and mocked Gmail SDK.
    # Outputs: Decoded MIME recipient, subject, body, and deterministic Message-ID all match.
    # Logic: Inspect payloads built by real execute_provider and num_retries=0.
    # Constraints: No network requests.
    def test_gmail_adapter_exact_content(self):
        action = models.ToolAction(
            id=uuid.uuid4(),
            tool="gmail.send",
            parameters={
                "account": "seller@example.com",
                "to": ["buyer@example.com"],
                "subject": "Test quote",
                "body": "Line one\nLine two",
            },
        )
        api = Mock()
        api.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "sent-id"
        }
        with patch("apps.sales.actions.build", return_value=api):
            result = actions.execute_provider(action, Mock())
        sender = api.users.return_value.messages.return_value.send
        message = message_from_bytes(
            base64.urlsafe_b64decode(sender.call_args.kwargs["body"]["raw"])
        )
        self.assertEqual(message["To"], "buyer@example.com")
        self.assertEqual(message["Subject"], "Test quote")
        self.assertEqual(message["Message-ID"], f"<{action.pk}@salesmate.local>")
        self.assertEqual(
            message.get_payload(decode=True).decode().strip(), "Line one\nLine two"
        )
        sender.return_value.execute.assert_called_once_with(num_retries=0)
        self.assertEqual(result["message_id"], "sent-id")

    # Function: Verify calendar creation does not guess notification behavior.
    # Inputs: An action with explicit timezone, attendees, and send_updates.
    # Outputs: Stable event ID, original time strings, and notification options reach the SDK.
    # Logic: Inspect insert arguments and a single execute call directly.
    # Constraints: Do not create real meetings.
    def test_calendar_adapter_explicit_notifications(self):
        action = models.ToolAction(
            id=uuid.uuid4(),
            tool="calendar.create",
            parameters={
                "calendar_id": "primary",
                "title": "Meeting",
                "description": "Test",
                "start": "2026-09-14T10:00:00+08:00",
                "end": "2026-09-14T11:00:00+08:00",
                "attendees": ["buyer@example.com"],
                "send_updates": "all",
            },
        )
        api = Mock()
        api.events.return_value.insert.return_value.execute.return_value = {
            "id": action.pk.hex
        }
        with patch("apps.sales.actions.build", return_value=api):
            actions.execute_provider(action, Mock())
        arguments = api.events.return_value.insert.call_args.kwargs
        self.assertEqual(arguments["sendUpdates"], "all")
        self.assertEqual(arguments["body"]["id"], action.pk.hex)
        self.assertEqual(
            arguments["body"]["start"]["dateTime"], action.parameters["start"]
        )
        api.events.return_value.insert.return_value.execute.assert_called_once_with(
            num_retries=0
        )

    # Function: Verify missing timezone and notification options both fail explicitly.
    # Inputs: Partially incomplete meeting-preparation requests.
    # Outputs: 400 without action creation.
    # Logic: Correct arguments individually; the complete plan saves but still awaits confirmation.
    # Constraints: Do not call the provider.
    def test_calendar_validation_requires_timezone_and_notification(self):
        parameters = {
            "connection_id": str(self.connection.pk),
            "calendar_id": "primary",
            "title": "Meeting",
            "description": "",
            "start": "2026-09-14T10:00:00",
            "end": "2026-09-14T11:00:00",
            "attendees": [],
            "send_updates": "none",
        }
        data = {
            "company": str(self.company.pk),
            "tool": "calendar.create",
            "parameters": parameters,
            "idempotency_key": str(uuid.uuid4()),
        }
        self.assertEqual(
            self.client.post(
                "/api/v1/sales/records/actions/", data, format="json"
            ).status_code,
            400,
        )
        parameters.update(
            start=parameters["start"] + "+08:00", end=parameters["end"] + "+08:00"
        )
        parameters.pop("send_updates")
        self.assertEqual(
            self.client.post(
                "/api/v1/sales/records/actions/", data, format="json"
            ).status_code,
            400,
        )
        parameters["send_updates"] = "none"
        result = self.client.post("/api/v1/sales/records/actions/", data, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["status"], "pending_confirmation")

    # Function: Verify only ciphertext is stored and requested scopes cannot fabricate authorization.
    # Inputs: Synthetic token JSON and a one-use test Fernet key.
    # Outputs: Decryption produces usable unexpired credentials; insufficient actual scopes are explicitly rejected.
    # Logic: Inspect original JSON scopes, not constructor arguments as authorization evidence.
    # Constraints: Do not refresh or use synthetic tokens externally.
    def test_credentials_encrypted_and_scopes_verified(self):
        document = {
            "token": "fake-access",
            "refresh_token": "fake-refresh",
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "expiry": "2099-01-01T00:00:00Z",
            "scopes": integrations.SCOPES["calendar"],
        }
        with override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode()):
            self.connection.encrypted_credentials = integrations.encrypt_credentials(
                document
            )
            self.assertNotIn("fake-access", self.connection.encrypted_credentials)
            self.assertEqual(
                integrations.credentials_for(self.connection).token, "fake-access"
            )
            document["scopes"] = []
            self.connection.encrypted_credentials = integrations.encrypt_credentials(
                document
            )
            with self.assertRaises(InvalidState):
                integrations.credentials_for(self.connection)

    # Function: Verify OAuth initiation and callback share one PKCE verifier.
    # Inputs: Mock Flow, Google profile, and an ordinary Session dictionary.
    # Outputs: Initiation requests automatic verifier generation; callback reuses it and stores ciphertext.
    # Logic: Run real integrations.begin/finish, replacing only external boundaries.
    # Constraints: No real OAuth exchange.
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="fake-client", GOOGLE_OAUTH_CLIENT_SECRET="fake-secret"
    )
    def test_oauth_pkce_reuses_verifier(self):
        flow = Mock(code_verifier="test-verifier")
        flow.authorization_url.return_value = (
            "https://accounts.google.com/test",
            "test-state",
        )
        flow.credentials.to_json.return_value = '{"token": "fake"}'
        api = Mock()
        api.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "sender@example.com"
        }
        request = Mock(
            session={},
            query_params={"state": "test-state", "code": "test-code"},
            user=self.user,
        )
        with (
            override_settings(SALESMATE_VAULT_KEY=Fernet.generate_key().decode()),
            patch(
                "apps.sales.integrations.Flow.from_client_config", return_value=flow
            ) as factory,
            patch("apps.sales.integrations.build", return_value=api),
        ):
            integrations.begin(
                request, "gmail", "http://testserver/api/v1/sales/oauth/"
            )
            self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
            connection = integrations.finish(request)
            self.assertEqual(factory.call_args.kwargs["code_verifier"], "test-verifier")
            self.assertNotIn("fake", connection.encrypted_credentials)
            self.assertNotIn(integrations.SESSION_KEY, request.session)

    # Function: Verify calendar-read identity and read-only SDK methods.
    # Inputs: An owner's connection, explicit date window, and another user.
    # Outputs: Owner reads an empty event page; another user receives 404.
    # Logic: Mock events.list and forbid creation calls.
    # Constraints: Do not read real schedules.
    def test_calendar_reads_are_owner_scoped(self):
        parameters = {
            "connection_id": str(self.connection.pk),
            "calendar_id": "primary",
            "start": "2026-09-14T00:00:00+08:00",
            "end": "2026-09-15T00:00:00+08:00",
        }
        api = Mock()
        api.events.return_value.list.return_value.execute.return_value = {"items": []}
        with (
            patch("apps.sales.calendar.credentials_for"),
            patch("apps.sales.calendar.build", return_value=api),
        ):
            self.assertEqual(
                calendar.read_calendar(self.user, "events", parameters)["results"], []
            )
        api.events.return_value.insert.assert_not_called()
        self.client.force_authenticate(
            get_user_model().objects.create_user(username="outsider")
        )
        self.assertEqual(
            self.client.get("/api/v1/sales/calendar/events/", parameters).status_code,
            404,
        )

    # Function: Verify uncertain-result reconciliation only queries existing external events.
    # Inputs: An uncertain calendar action and a mocked event with the same ID.
    # Outputs: Mark succeeded without ever calling insert.
    # Logic: Reconciliation increments the revision and prevents later Worker execution.
    # Constraints: This test does not establish that the external event actually exists.
    def test_verification_cannot_create_or_send(self):
        action = models.ToolAction.objects.create(
            owner=self.user,
            company=self.company,
            tool="calendar.create",
            parameters={
                "connection_id": str(self.connection.pk),
                "calendar_id": "primary",
            },
            status="uncertain",
            idempotency_key=uuid.uuid4(),
        )
        api = Mock()
        api.events.return_value.get.return_value.execute.return_value = {
            "id": action.pk.hex,
            "status": "confirmed",
        }
        with (
            patch("apps.sales.actions.credentials_for"),
            patch("apps.sales.actions.build", return_value=api),
        ):
            self.assertEqual(
                actions.verify_action(action, self.user, 0).status, "succeeded"
            )
        api.events.return_value.insert.assert_not_called()
        self.assertEqual(actions.run_action(action.pk), "succeeded")

    # Function: Verify inactive connections prevent business-provider calls.
    # Inputs: An approved action and archived connection.
    # Outputs: failed without calling execute_provider.
    # Logic: Real preflight rejects the connection before external business calls.
    # Constraints: No implicit reauthorization or retry.
    def test_bad_preflight_never_calls_provider(self):
        self.connection.archived = True
        self.connection.save()
        action = models.ToolAction.objects.create(
            owner=self.user,
            company=self.company,
            tool="calendar.create",
            parameters={
                "connection_id": str(self.connection.pk),
                "account": self.connection.account,
            },
            status="approved",
            idempotency_key=uuid.uuid4(),
        )
        with patch("apps.sales.actions.execute_provider") as execute:
            self.assertEqual(actions.run_action(action.pk), "failed")
            execute.assert_not_called()

    # Function: Verify real session writes require CSRF.
    # Inputs: APIClient with a force-logged-in Session, without force_authenticate.
    # Outputs: 403 without a token; creation succeeds with valid cookie/header.
    # Logic: Exercise the authentication and CSRF paths actually used by browsers.
    # Constraints: Do not use authentication bypasses to demonstrate CSRF effectiveness.
    def test_authenticated_write_requires_csrf(self):
        browser = APIClient(enforce_csrf_checks=True)
        browser.force_login(self.user)
        browser.get("/api/v1/session/")
        path = "/api/v1/sales/records/teams/"
        self.assertEqual(
            browser.post(path, {"name": "CSRF team"}, format="json").status_code, 403
        )
        response = browser.post(
            path,
            {"name": "CSRF team"},
            format="json",
            HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 201, response.data)
