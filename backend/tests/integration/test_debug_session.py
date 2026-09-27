"""Responsibility: Verify session and access boundaries for the local passwordless workspace.
Implementation: Use a real test database and a browser client with CSRF checks enabled while explicitly overriding local configuration.
Relationships: Covers the debug entry of `SessionView`; does not connect to Gmail or the team Agent.
Directory:
- DebugSessionTests: Verify automatic sessions, configuration boundaries, and existing identities.
- DebugSessionTests.setUp: Create an ordinary debug user and browser client.
- DebugSessionTests.test_auto_session_keeps_csrf_and_agent_auth: Verify passwordless access can read business data but writes still require CSRF and Agent still requires credentials.
- DebugSessionTests.test_disabled_or_nonlocal_requires_login: Verify disabled switch, disabled DEBUG, or non-loopback address does not auto-login.
- DebugSessionTests.test_unavailable_user_is_explicit_error: Verify missing, inactive, or administrator accounts do not auto-login.
- DebugSessionTests.test_existing_identity_is_preserved: Verify an existing logged-in identity is not replaced.
- DebugSessionTests.test_login_routes_reuse_existing_forms: Verify both public login URL variants serve the established form.
- DebugSessionTests.test_manual_login_and_logout_suppress_automatic_login: Verify explicit login, session invalidation, and persistent logout under local automatic login.
- DebugSessionTests.test_login_and_logout_require_csrf: Verify rejected writes preserve the existing anonymous or authenticated state.
Variable index:
- None
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


# Function: Verify automatic sessions, configuration boundaries, and existing identities.
# Logic: This class explicitly enables local automatic login and every case uses an independent test transaction.
# Constraints: Test accounts and mailboxes are synthetic and no real credentials from a local demo are used.
@override_settings(DEBUG=True, LOCAL_DEBUG_AUTO_LOGIN=True, LOCAL_DEBUG_USER="debug-user")
class DebugSessionTests(TestCase):
    # Function: Verify both dedicated login routes use the existing interface.
    # Inputs: Anonymous browser client; no external services or template mocks.
    # Outputs: Assertions for HTTP 200 and the existing login/registration forms.
    # Logic: Read slash and no-slash variants through the Django URL resolver.
    # Constraints: Does not assert browser script behavior; browser_auth.cjs covers that separately.
    def test_login_routes_reuse_existing_forms(self):
        for path in ("/login", "/login/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, 'id="login-form"')
                self.assertContains(response, 'id="signup-form"')
                self.assertTemplateUsed(response, "index.html")

    # Function: Verify manual authentication remains usable with local automatic login enabled.
    # Inputs: Synthetic debug account and CSRF-enforcing browser client.
    # Outputs: Assertions for anonymous entry, invalid-password rejection, real login, invalidated old session, persistent logout, and relogin.
    # Logic: Opt out on the entry request, exercise password authentication and DELETE, then replay the old cookie in an independent client.
    # Constraints: Real database/session/CSRF handling; no external services, mocked authentication, or changes to deployment defaults.
    def test_manual_login_and_logout_suppress_automatic_login(self):
        self.user.set_password("test-password-123")
        self.user.save(update_fields=["password"])
        response = self.client.get("/api/v1/session/?auto_login=false")
        self.assertFalse(response.data["authenticated"])
        self.assertFalse(response.data["debug_auto_login"])
        token = response.data["csrf_token"]
        payload = {"username": self.user.username, "password": "incorrect"}
        rejected = self.client.post("/api/v1/session/", payload, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(rejected.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        payload["password"] = "test-password-123"
        response = self.client.post("/api/v1/session/", payload, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        old_cookie = self.client.cookies["sessionid"].value
        token = response.data["csrf_token"]
        self.assertEqual(self.client.delete("/api/v1/session/", HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertFalse(self.client.get("/api/v1/session/").data["authenticated"])
        self.assertFalse(self.client.get("/api/v1/session/").data["authenticated"])
        stale = APIClient()
        stale.cookies["sessionid"] = old_cookie
        self.assertEqual(stale.get("/api/v1/companies/").status_code, 403)
        token = self.client.get("/api/v1/session/").data["csrf_token"]
        response = self.client.post("/api/v1/session/", payload, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("suppress_debug_auto_login", self.client.session)

    # Function: Verify anonymous login and authenticated logout retain CSRF protection.
    # Inputs: Existing synthetic account and a client enforcing Django CSRF middleware.
    # Outputs: Assertions for rejected writes and unchanged session identity.
    # Logic: Submit login without a CSRF token, establish debug login by GET, then submit logout without a token.
    # Constraints: No authentication mocking; a rejected logout must not set the anonymous suppression marker.
    def test_login_and_logout_require_csrf(self):
        self.assertEqual(self.client.post("/api/v1/session/", {"username": "debug-user", "password": "invalid"}, format="json").status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.get("/api/v1/session/")
        self.assertEqual(self.client.delete("/api/v1/session/").status_code, 403)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    # Function: Create an ordinary debug user and browser client.
    # Inputs: Test-framework initialized instance state, with no external parameters.
    # Outputs: Instance state for `user` and `client`.
    # Logic: Create an ordinary user without a usable password and let the automatic session under test establish identity.
    # Constraints: Every write request performs real CSRF validation.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="debug-user")
        self.client = APIClient(enforce_csrf_checks=True)

    # Function: Verify passwordless access can read business data while writes still require CSRF and Agent still requires credentials.
    # Inputs: An anonymous local client and an existing debug user.
    # Outputs: Assertions for session persistence, business reads, and protected writes.
    # Logic: Use the same cookie to read business data and attempt two write types after GET establishes a real session.
    # Constraints: Do not force-authenticate the client or mock authentication services.
    def test_auto_session_keeps_csrf_and_agent_auth(self):
        response = self.client.get("/api/v1/session/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authenticated"])
        self.assertTrue(response.data["debug_auto_login"])
        self.assertEqual(response.data["username"], "debug-user")
        self.assertEqual(self.client.get("/api/v1/companies/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/session/").data["username"], "debug-user")
        payload = {"address": "debug@internal.example"}
        self.assertEqual(self.client.post("/api/v1/mailboxes/", payload, format="json").status_code, 403)
        token = self.client.cookies["csrftoken"].value
        self.assertEqual(self.client.post("/api/v1/mailboxes/", payload, format="json", HTTP_X_CSRFTOKEN=token).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/agent/emails/", [], format="json", HTTP_X_CSRFTOKEN=token).status_code, 401)

    # Function: Verify that disabled switch, disabled DEBUG, or a non-loopback address does not auto-login.
    # Inputs: Three explicit disabling conditions and fresh anonymous clients.
    # Outputs: Assertions for anonymous state and denied business access.
    # Logic: Cover each individual boundary without relying on another boundary accidentally preventing automatic login.
    # Constraints: Do not trust a forgeable loopback address in X-Forwarded-For.
    def test_disabled_or_nonlocal_requires_login(self):
        for config, remote in [({"LOCAL_DEBUG_AUTO_LOGIN": False}, "127.0.0.1"),
                               ({"DEBUG": False}, "127.0.0.1"), ({}, "192.0.2.1")]:
            with self.subTest(config=config, remote=remote), override_settings(**config):
                client = APIClient()
                response = client.get("/api/v1/session/", REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR="127.0.0.1")
                self.assertFalse(response.data["authenticated"])
                self.assertFalse(response.data["debug_auto_login"])
                self.assertEqual(client.get("/api/v1/companies/").status_code, 403)

    # Function: Verify that missing, inactive, or administrator accounts do not auto-login.
    # Inputs: Database states that do not meet debug-user prerequisites.
    # Outputs: Assertions for explicit 409 errors, no new user, and no authenticated session.
    # Logic: Modify user constraints one at a time and verify missing configuration through a separate settings override.
    # Constraints: Do not implicitly create an account or select another identity.
    def test_unavailable_user_is_explicit_error(self):
        for values in [{"is_active": False}, {"is_staff": True}, {"is_superuser": True}]:
            with self.subTest(values=values):
                get_user_model().objects.filter(pk=self.user.pk).update(is_active=True, is_staff=False, is_superuser=False)
                get_user_model().objects.filter(pk=self.user.pk).update(**values)
                self.assertEqual(self.client.get("/api/v1/session/").status_code, 409)
                self.assertNotIn("_auth_user_id", self.client.session)
        with override_settings(LOCAL_DEBUG_USER="missing"):
            self.assertEqual(self.client.get("/api/v1/session/").status_code, 409)
        self.assertEqual(get_user_model().objects.count(), 1)

    # Function: Verify that an existing logged-in identity is not replaced.
    # Inputs: A real Django Session established for another ordinary user.
    # Outputs: Assertions that session username and user ID remain consistent.
    # Logic: `force_login` establishes test preconditions only, then the real `SessionView` executes.
    # Constraints: This case does not verify password authentication; existing CRM tests cover normal login.
    def test_existing_identity_is_preserved(self):
        other = get_user_model().objects.create_user(username="existing-user")
        self.client.force_login(other)
        response = self.client.get("/api/v1/session/")
        self.assertEqual(response.data["username"], "existing-user")
        self.assertEqual(self.client.session["_auth_user_id"], str(other.pk))
