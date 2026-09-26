"""Responsibility: Verify backend foundation HTTP, identity fields, and error boundaries.
Implementation: Use SimpleTestCase to prohibit real database access; explicitly mock connections for readiness branch and use forced authentication for user API.
Relationships: Call real views and middleware through Django routes; results do not represent successful real-login or external-database integration.

Directory:
- FoundationTests: Check foundation HTTP behavior while real database access is prohibited.
- FoundationTests.setUp: Create independent HTTP client for each test.
- FoundationTests.test_liveness_is_available_without_database_or_session: Verify anonymous liveness response and request-ID format.
- FoundationTests.test_request_ids_are_generated_per_request: Verify request IDs do not trust caller and are generated per request.
- FoundationTests.test_http_log_does_not_include_query_or_authorization: Verify logs exclude query parameters and test secrets in authorization headers.
- FoundationTests.test_readiness_checks_database: Verify successful readiness branch probes database.
- FoundationTests.test_readiness_failure_is_503_and_does_not_expose_connection_details: Verify database failure response and log redaction.
- FoundationTests.test_current_user_requires_authentication: Verify anonymous current-user query is rejected with unified error structure.
- FoundationTests.test_current_user_returns_only_public_identity_fields: Verify current-user response contains only allowlisted fields.
- FoundationTests.test_unsupported_method_uses_error_envelope: Verify unsupported HTTP method uses unified error response.

Variable index:
- None
"""

from unittest.mock import patch
from uuid import UUID

from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User


# Function: Check foundation HTTP behavior while real database access is prohibited.
# Logic: APIClient drives real views and middleware; patch database branches and use in-memory User for identity-field tests.
# Constraints: Do not cover real database, session login, or mail business; mocks serve tests only.
class FoundationTests(SimpleTestCase):
    """No database is allowed: test the HTTP boundary independently of provisioning."""

    # Function: Create an independent HTTP client for each test.
    # Inputs: No external parameters; called by unittest lifecycle.
    # Outputs: Returns `None` and initializes `self.client`.
    # Logic: Create fresh APIClient each time to prevent forced-authentication state leaking from the preceding test.
    # Constraints: Do not establish a real database connection or user record.
    def setUp(self):
        self.client = APIClient()

    # Function: Verify anonymous liveness response and request-ID format.
    # Inputs: No external parameters; uses anonymous client created by setup.
    # Outputs: Returns `None`; test fails if status, body, or UUID version differs.
    # Logic: Request health-live and check fixed fields and UUID4 response header.
    # Constraints: SimpleTestCase prohibits real database calls and does not prove external dependencies are ready.
    def test_liveness_is_available_without_database_or_session(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "salesmate-backend"})
        self.assertEqual(UUID(response["X-Request-ID"]).version, 4)

    # Function: Verify request IDs do not trust caller input and are generated per request.
    # Inputs: No external parameters; uses anonymous client.
    # Outputs: Returns `None`; assertion fails if ID is reused or comes from request header.
    # Logic: Send consecutive requests with and without a specified ID and compare response headers.
    # Constraints: Verifies only two samples and is not a mathematical proof of all UUID uniqueness.
    def test_request_ids_are_generated_per_request(self):
        first = self.client.get(reverse("health-live"), HTTP_X_REQUEST_ID="caller-controlled")
        second = self.client.get(reverse("health-live"))
        self.assertNotEqual(first["X-Request-ID"], "caller-controlled")
        self.assertNotEqual(first["X-Request-ID"], second["X-Request-ID"])

    # Function: Verify logs exclude query parameters and test secrets in authorization headers.
    # Inputs: No external parameters; uses explicit fictional token strings.
    # Outputs: Returns `None`; assertion fails for leaked specified strings or missing request ID.
    # Logic: Capture salesmate.http logs, call liveness endpoint, and inspect correlation ID and sensitive values.
    # Constraints: Covers only query and authorization-header input locations and does not prove all paths or logs lack sensitive information.
    def test_http_log_does_not_include_query_or_authorization(self):
        with self.assertLogs("salesmate.http", level="INFO") as logs:
            response = self.client.get(
                reverse("health-live") + "?token=private-query",
                HTTP_AUTHORIZATION="Bearer private-header",
            )
        text = "\n".join(logs.output)
        self.assertIn(response["X-Request-ID"], text)
        self.assertNotIn("private-query", text)
        self.assertNotIn("private-header", text)

    # Function: Verify successful readiness branch performs a database probe.
    # Inputs: `connections` is mocked connection registry injected by patch.
    # Outputs: Returns `None`; fails if status, body, or SELECT 1 call count differs.
    # Logic: Mock cursor to return (1,), call readiness view through HTTP, and check one execution.
    # Constraints: Replaces real connection entry point and does not verify external database service or migrations.
    @patch("common.views.connections")
    def test_readiness_checks_database(self, connections):
        cursor = connections["default"].cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})
        cursor.execute.assert_called_once_with("SELECT 1")

    # Function: Verify database-failure response and log redaction.
    # Inputs: `connections` is mocked connection registry injected by patch.
    # Outputs: Returns `None`; fails if 503, error type, or sensitive-value exclusion differs.
    # Logic: Make cursor raise OperationalError containing a fictional secret, capture health logs, and inspect response.
    # Constraints: Simulates connection failure without connecting to real database and does not enumerate every DatabaseError subtype.
    @patch("common.views.connections")
    def test_readiness_failure_is_503_and_does_not_expose_connection_details(self, connections):
        connections["default"].cursor.side_effect = OperationalError("password=private-database-secret")
        with self.assertLogs("salesmate.health", level="ERROR") as logs:
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable", "database": "unavailable"})
        self.assertNotIn("private-database-secret", str(response.content) + str(logs.output))
        self.assertIn("OperationalError", "\n".join(logs.output))

    # Function: Verify anonymous current-user query is rejected with unified error structure.
    # Inputs: No external parameters; uses unauthenticated client.
    # Outputs: Returns `None`; fails if 403, error code, or request ID differs.
    # Logic: Access accounts:me and compare request ID in response body and header.
    # Constraints: Verifies default permission boundary and does not verify real login credentials or team permissions.
    def test_current_user_requires_authentication(self):
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertEqual(response.json()["request_id"], response["X-Request-ID"])

    # Function: Verify current-user response contains only allowlisted fields.
    # Inputs: No external parameters; creates in-memory User with fictional sensitive fields.
    # Outputs: Returns `None`; assertion fails if response fields or values differ from expectation.
    # Logic: `force_authenticate` bypasses real login and compares complete response with four-field dictionary.
    # Constraints: Do not save user or verify password or session; fresh client isolates authentication state through setup.
    def test_current_user_returns_only_public_identity_fields(self):
        user = User(id=7, username="sales-rep", password="private-hash", email="private@example.com")
        self.client.force_authenticate(user)
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"id": 7, "username": "sales-rep", "first_name": "", "last_name": ""},
        )

    # Function: Verify unsupported HTTP method uses unified error response.
    # Inputs: No external parameters; uses anonymous client.
    # Outputs: Returns `None`; fails if 405 or method_not_allowed error code differs.
    # Logic: Send POST to liveness endpoint implementing only GET and inspect exception-handler output.
    # Constraints: Verifies method rejection for this route only and does not cover 500 pages for unknown exceptions.
    def test_unsupported_method_uses_error_envelope(self):
        response = self.client.post(reverse("health-live"), {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")
