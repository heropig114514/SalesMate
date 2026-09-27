"""Responsibility: Verify safe classification of Gmail refresh failures.
Implementation: Mock Google credential refresh and SDK construction, exercising the real authorization adapter.
Relationships: GmailReauthorizationRequired informs durable Worker guidance; other errors retain legacy failure semantics.
Directory:
- GmailAuthorizationTests: Offline authorization classification regressions.
- GmailAuthorizationTests.test_invalid_grant_requires_consent: Preserve structured grant rejection without provider bodies.
- GmailAuthorizationTests.test_other_refresh_failures_are_not_revocation: Keep transport/configuration failures distinct.
- GmailAuthorizationTests.test_success_returns_refreshed_credentials: Preserve normal refresh and SDK return contract.
Variable index:
- None
"""
import unittest
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError
from agent.tools.gmail import GmailReauthorizationRequired, create_service_from_authorization


# Function: Exercise authorization handling without real credentials or Google requests.
# Logic: Inject SDK credentials at the external boundary and assert classification, call counts and safe errors.
# Constraints: These tests do not verify real Google authorization or mailbox access.
class GmailAuthorizationTests(unittest.TestCase):
    # Function: Classify an invalid grant as requiring fresh employee consent.
    # Inputs: Mock expired credential with structured Google invalid_grant and synthetic sensitive detail.
    # Outputs: Safe typed error; no Gmail service and exactly one refresh call.
    # Logic: Exercise the real adapter and verify no raw response or token reaches the error string.
    # Constraints: No retries, provider requests, or credential persistence.
    def test_invalid_grant_requires_consent(self):
        credentials = Mock(valid=False, expired=True, refresh_token="synthetic-secret")
        credentials.refresh.side_effect = RefreshError("private-provider-body", {"error": "invalid_grant", "error_description": "synthetic-secret"})
        with patch("agent.tools.gmail.Credentials.from_authorized_user_info", return_value=credentials), patch("agent.tools.gmail.build") as build:
            with self.assertRaises(GmailReauthorizationRequired) as caught:
                create_service_from_authorization({"mock": True})
        self.assertNotIn("private-provider-body", str(caught.exception))
        self.assertNotIn("synthetic-secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        credentials.refresh.assert_called_once()
        build.assert_not_called()

    # Function: Avoid interpreting every token-refresh error as employee revocation.
    # Inputs: Mock invalid_client, temporary failure, or unstructured grant-like text.
    # Outputs: Ordinary RuntimeError without raw details; no SDK construction.
    # Logic: Require the actual structured invalid_grant field, not message substring matching.
    # Constraints: No automatic retry or conversion of transport/configuration failures into success.
    def test_other_refresh_failures_are_not_revocation(self):
        for error in (RefreshError("private", {"error": "invalid_client"}), RefreshError("private", {"error": "temporarily_unavailable"}), RefreshError("invalid_grant private")):
            credentials = Mock(valid=False, expired=True, refresh_token="synthetic-secret")
            credentials.refresh.side_effect = error
            with patch("agent.tools.gmail.Credentials.from_authorized_user_info", return_value=credentials), patch("agent.tools.gmail.build") as build:
                with self.assertRaises(RuntimeError) as caught:
                    create_service_from_authorization({"mock": True})
            self.assertNotIsInstance(caught.exception, GmailReauthorizationRequired)
            self.assertNotIn("private", str(caught.exception))
            credentials.refresh.assert_called_once()
            build.assert_not_called()

    # Function: Retain successful refresh and serialization behavior.
    # Inputs: Mock expired refreshable credential and synthetic serialized result.
    # Outputs: Original service object and refreshed credential dictionary for backend-only storage.
    # Logic: Refresh once, build once, and preserve the caller's credential object unchanged.
    # Constraints: SDK and OAuth calls are mocked; no real token is used.
    def test_success_returns_refreshed_credentials(self):
        credentials = Mock(valid=False, expired=True, refresh_token="synthetic-secret")
        credentials.to_json.return_value = '{"token":"synthetic-refreshed"}'
        with patch("agent.tools.gmail.Credentials.from_authorized_user_info", return_value=credentials), patch("agent.tools.gmail.build") as build:
            service, refreshed = create_service_from_authorization({"mock": True})
        self.assertIs(service, build.return_value)
        self.assertEqual(refreshed, {"token": "synthetic-refreshed"})
        credentials.refresh.assert_called_once()
        build.assert_called_once()
