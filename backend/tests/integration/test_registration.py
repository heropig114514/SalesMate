"""Responsibility: Verify simplified registration, login continuation, and data isolation for new accounts.
Implementation: Use real test database and a CSRF-enforcing client to cover account writes, duplicate usernames, and permission boundaries.
Relationships: Covers `accounts.registration`, CRM `SessionView`, and sales customer directory; does not call Gmail or model services.
Directory:
- RegistrationTests: Covers anonymous registration and ordinary session boundaries.
- RegistrationTests.setUp: Prepare a CSRF client and synthetic registration data.
- RegistrationTests.submit: Submit registration payload through real HTTP view.
- RegistrationTests.test_register_login_logout_and_empty_workspace: Verify post-registration login, empty workspace, and relogin.
- RegistrationTests.test_csrf_and_privileged_fields_rejected: Verify CSRF and privilege fields cannot be bypassed.
- RegistrationTests.test_invalid_username_and_password_rejected: Verify username and 8–128-character password-length rules.
- RegistrationTests.test_duplicate_and_normalized_username_rejected: Verify normalized duplicate names do not overwrite existing accounts.
- RegistrationTests.test_database_duplicate_is_validation_error: Mock precheck race then verify real unique-constraint handling.
- RegistrationTests.test_authenticated_registration_keeps_identity: Verify a registration request does not replace existing logged-in identity.
Variable index:
- None
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


# Function: Verify ordinary account registration without external verification services.
# Logic: Disable debug automatic login and make requests with real password hashes, database, and Session.
# Constraints: Use isolated test database and synthetic data only and do not consume model quota or send mail.
@override_settings(DEBUG=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class RegistrationTests(TestCase):
    # Function: Prepare an anonymous browser and valid registration data.
    # Inputs: No external parameters; reads Django test runtime.
    # Outputs: Instance state for `client`, `csrf`, and `payload`.
    # Logic: Access session first to obtain a real CSRF cookie, then construct a request containing only two fields.
    # Constraints: Do not force authentication or skip password validation.
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.csrf = self.client.get("/api/v1/session/").data["csrf_token"]
        self.payload = {"username": "new-sales-user", "password": "Quartz-Bridge-86!"}

    # Function: Submit a registration request with valid CSRF.
    # Inputs: `payload` is registration JSON for this scenario.
    # Outputs: HTTP response object.
    # Logic: Reuse current client cookie and `csrf` instance state.
    # Constraints: Caller must update `csrf` after Session rotation; do not refresh or retry implicitly.
    def submit(self, payload):
        return self.client.post("/api/v1/accounts/register/", payload, format="json", HTTP_X_CSRFTOKEN=self.csrf)

    # Function: Verify the closed loop from registration to business use and relogin.
    # Inputs: Anonymous client, valid registration data, and a customer owned by another user.
    # Outputs: Assertions for 201, ordinary permissions, hashed password, data isolation, and relogin.
    # Logic: Another user first creates a customer; newly registered user can create and read only their own customer, then logs in with the same password after logout.
    # Constraints: Other user's `force_login` establishes isolation preconditions only; registration and login under test use real APIs.
    def test_register_login_logout_and_empty_workspace(self):
        other = get_user_model().objects.create_user(username="existing-owner")
        other_client = APIClient()
        other_client.force_login(other)
        self.assertEqual(other_client.post("/api/v1/sales/directory/", {"name": "Other private customer"}, format="json").status_code, 201)
        response = self.submit(self.payload)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["authenticated"])
        self.assertNotIn(self.payload["password"], str(response.data))
        user = get_user_model().objects.get(username=self.payload["username"])
        self.assertTrue(user.check_password(self.payload["password"]))
        self.assertNotEqual(user.password, self.payload["password"])
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(self.client.get("/api/v1/accounts/me/").data["id"], user.pk)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 0)
        token = response.data["csrf_token"]
        created = self.client.post("/api/v1/sales/directory/", {"name": "My first customer"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 1)
        self.assertEqual(self.client.delete("/api/v1/session/", HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").status_code, 403)
        token = self.client.get("/api/v1/session/").data["csrf_token"]
        logged_in = self.client.post("/api/v1/session/", self.payload, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/sales/directory/").data["count"], 1)

    # Function: Verify registration retains CSRF and rejects client privilege escalation.
    # Inputs: Request missing CSRF and synthetic requests with is_staff or is_superuser.
    # Outputs: Assertions for 403/400 and no new user.
    # Logic: Reject through real middleware and field validation respectively, then check database remains unwritten.
    # Constraints: Do not treat an error response itself as proof of no side effects.
    def test_csrf_and_privileged_fields_rejected(self):
        self.assertEqual(self.client.post("/api/v1/accounts/register/", self.payload, format="json").status_code, 403)
        for field in ["is_staff", "is_superuser"]:
            with self.subTest(field=field):
                self.assertEqual(self.submit({**self.payload, field: True}).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 0)

    # Function: Verify registration input uses established account rules.
    # Inputs: Non-object payload, missing fields, invalid username, noncompliant password lengths, and oversized password case.
    # Outputs: Every case returns 400 with no account write.
    # Logic: Run model username and Django password validation through the full API.
    # Constraints: Do not add email, phone, or verification-code validation or change global password rules.
    def test_invalid_username_and_password_rejected(self):
        for changes in [{"username": ""}, {"username": "bad name"}, {"password": "short"}, {"password": "x" * 129}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.submit({**self.payload, **changes}).status_code, 400)
        self.assertEqual(self.submit({"username": "missing-password"}).status_code, 400)
        self.assertEqual(self.submit([self.payload]).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 0)

    # Function: Verify duplicate registration and Unicode-normalized duplicate-name handling.
    # Inputs: Existing sales user and ordinary/fullwidth username requests.
    # Outputs: Assertions for 400, one user, and unchanged old password.
    # Logic: Both inputs map to the existing username and cannot reset or take over the account.
    # Constraints: Do not change established username case semantics.
    def test_duplicate_and_normalized_username_rejected(self):
        user = get_user_model().objects.create_user(username="sales", password="Original-Secret-82!")
        for username in ["sales", "ｓａｌｅｓ"]:
            self.assertEqual(self.submit({**self.payload, "username": username}).status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Original-Secret-82!"))
        self.assertEqual(get_user_model().objects.count(), 1)

    # Function: Verify an explicit validation error still returns when a duplicate name appears after precheck.
    # Inputs: `validate_username` is a mock precheck that passes; database contains a real same-name user.
    # Outputs: Assertions for 400, no registration session, and unchanged user count.
    # Logic: Mock precheck result only; real database triggers unique constraint and atomic rollback.
    # Constraints: Simulates a race window and does not claim to verify multiprocess concurrency scheduling.
    @patch("apps.accounts.registration.RegistrationSerializer.validate_username")
    def test_database_duplicate_is_validation_error(self, validate_username):
        get_user_model().objects.create_user(username=self.payload["username"])
        validate_username.return_value = self.payload["username"]
        self.assertEqual(self.submit(self.payload).status_code, 400)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    # Function: Verify registration cannot replace an existing logged-in identity.
    # Inputs: Established user Session and another registration username.
    # Outputs: Assertions for 409, original session ID, and one user.
    # Logic: Register through the real API and refresh CSRF first, then submit new username to the same endpoint.
    # Constraints: Do not log out the user or create a second account.
    def test_authenticated_registration_keeps_identity(self):
        response = self.submit(self.payload)
        self.assertEqual(response.status_code, 201)
        self.csrf = response.data["csrf_token"]
        original_id = self.client.session["_auth_user_id"]
        self.assertEqual(self.submit({**self.payload, "username": "replacement"}).status_code, 409)
        self.assertEqual(self.client.session["_auth_user_id"], original_id)
        self.assertEqual(get_user_model().objects.count(), 1)
