"""Responsibility: Verify authenticated password changes, account isolation, and session boundaries.
Implementation: Use the real test database and CSRF-enforcing HTTP clients with synthetic users; exercise hashing, validation, and Django session invalidation without mocks.
Relationships: accounts.password_change, CRM session login, account identity, and the existing Django password validators.
Directory:
- PasswordChangeTests: Password-change integration scenarios.
- PasswordChangeTests.setUp: Create isolated users, authenticated clients, and CSRF state.
- PasswordChangeTests.change: Submit the three-field password contract.
- PasswordChangeTests.test_success_keeps_current_session_and_business_data: Verify password replacement, login continuity, and other-session invalidation.
- PasswordChangeTests.test_invalid_input_keeps_password: Verify current-password, confirmation, length, and account-selection rejection.
- PasswordChangeTests.test_requires_session_and_csrf: Verify authentication and CSRF boundaries.
- PasswordChangeTests.test_length_boundaries_and_whitespace: Verify established length limits and untrimmed passwords.
Variable index:
- URL: Authenticated password-change endpoint.
- PasswordChangeTests.password: Synthetic initial password used only in isolated tests.
"""
from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.crm.models import Mailbox

URL = "/api/v1/accounts/me/password/"


# Function: Exercise real password and SessionAuthentication behavior.
# Logic: Disable debug auto-login and use synthetic accounts in the test database.
# Constraints: No production identities, external providers, or mocked authentication.
@override_settings(LOCAL_DEBUG_AUTO_LOGIN=False)
class PasswordChangeTests(TestCase):
    password = "initial-test-password"

    # Function: Prepare authenticated clients for two users and a second same-user session.
    # Inputs: Django test lifecycle and class-level synthetic password.
    # Outputs: user, other, client, second, other_client, and token instance state.
    # Logic: force_login establishes preconditions only; the endpoint still runs real SessionAuthentication and CSRF verification.
    # Constraints: Password verification, session persistence, and subsequent login checks are not mocked.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="password-owner", password=self.password)
        self.other = get_user_model().objects.create_user(username="password-other", password=self.password)
        self.client = APIClient(enforce_csrf_checks=True)
        self.second = APIClient(enforce_csrf_checks=True)
        self.other_client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.second.force_login(self.user)
        self.other_client.force_login(self.other)
        self.token = self.client.get("/api/v1/session/").data["csrf_token"]

    # Function: Submit a synthetic password change.
    # Inputs: `changes` overrides fields in the valid default payload.
    # Outputs: The endpoint's real HTTP response.
    # Logic: Send all three password fields and the current CSRF token without choosing an owner.
    # Constraints: Does not update test expectations or retry a rejected request.
    def change(self, **changes):
        payload = {"current_password": self.password, "new_password": "replacement-password", "password_confirmation": "replacement-password"}
        payload.update(changes)
        return self.client.post(URL, payload, format="json", HTTP_X_CSRFTOKEN=self.token)

    # Function: Verify replacement changes only the intended password and requesting session.
    # Inputs: Two same-user sessions, another account, and a synthetic mailbox.
    # Outputs: Assertions for 204, current login continuity, old-password rejection, new-password login, session rotation/invalidation, and preserved data.
    # Logic: Read the identity through real HTTP after changing and validate both passwords with Django authentication.
    # Constraints: Does not invoke Gmail or assume that other browser pages navigate immediately when their sessions become invalid.
    def test_success_keeps_current_session_and_business_data(self):
        mailbox = Mailbox.objects.create(owner=self.user, address="same@example.test")
        other_hash = self.other.password
        old_session = self.client.cookies["sessionid"].value
        response = self.change()
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertNotEqual(self.client.cookies["sessionid"].value, old_session)
        self.assertEqual(self.client.get("/api/v1/accounts/me/").data["id"], self.user.pk)
        self.assertEqual(self.second.get("/api/v1/accounts/me/").status_code, 403)
        self.assertEqual(self.other_client.get("/api/v1/accounts/me/").data["id"], self.other.pk)
        self.other.refresh_from_db()
        self.assertEqual(self.other.password, other_hash)
        self.assertTrue(Mailbox.objects.filter(pk=mailbox.pk, owner=self.user).exists())
        self.assertIsNone(authenticate(username=self.user.username, password=self.password))
        self.assertEqual(authenticate(username=self.user.username, password="replacement-password").pk, self.user.pk)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.password, "replacement-password")
        self.assertEqual(self.change().status_code, 400)

    # Function: Reject invalid changes without altering credentials or login state.
    # Inputs: Wrong current password, mismatched confirmation, out-of-range lengths, and an injected owner ID.
    # Outputs: Assertions for 400 and unchanged password/session on every request.
    # Logic: Each subcase uses the still-valid original password and compares stored hashes for both accounts.
    # Constraints: Tests the server contract independently of browser validation.
    def test_invalid_input_keeps_password(self):
        original = self.user.password
        for changes in [
            {"current_password": "wrong-password"},
            {"password_confirmation": "different-password"},
            {"new_password": "short", "password_confirmation": "short"},
            {"new_password": "x" * 129, "password_confirmation": "x" * 129},
            {"owner_id": self.other.pk},
        ]:
            with self.subTest(fields=list(changes)):
                self.assertEqual(self.change(**changes).status_code, 400)
                self.user.refresh_from_db()
                self.assertEqual(self.user.password, original)
                self.assertEqual(self.client.get("/api/v1/accounts/me/").status_code, 200)

    # Function: Reject requests without a valid session or CSRF token.
    # Inputs: Anonymous client and existing authenticated client without write CSRF.
    # Outputs: Assertions for 403 and unchanged password.
    # Logic: Use valid-shaped password input so permission/CSRF checks are the rejecting boundary.
    # Constraints: No authentication bypass or auto-login.
    def test_requires_session_and_csrf(self):
        payload = {"current_password": self.password, "new_password": "replacement-password", "password_confirmation": "replacement-password"}
        self.assertEqual(APIClient(enforce_csrf_checks=True).post(URL, payload, format="json").status_code, 403)
        self.assertEqual(self.client.post(URL, payload, format="json").status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    # Function: Preserve registration password policy and exact whitespace.
    # Inputs: Sequential replacement passwords at the lower/upper limits and with surrounding spaces.
    # Outputs: Assertions for success and exact password matching.
    # Logic: Each successful replacement becomes the next request's current password.
    # Constraints: No additional complexity rules, trimming, or configured-validator changes.
    def test_length_boundaries_and_whitespace(self):
        current = self.password
        for replacement in ("12345678", "x" * 128, " password with spaces "):
            with self.subTest(length=len(replacement)):
                response = self.change(current_password=current, new_password=replacement, password_confirmation=replacement)
                self.assertEqual(response.status_code, 204)
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(replacement))
                current = replacement
