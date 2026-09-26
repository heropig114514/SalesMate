"""Responsibility: Verify isolation of Chinese and English HTTP interfaces from business data, permissions, and error contracts.
Implementation: Use real Django middleware, compiled gettext catalogs, and an isolated test database to cover language priority and business round trips.
Relationships: Covers `config.settings.base`, `common.exceptions`, registration/login APIs, and sales metadata; does not call external services.

Directory:
- InterfaceLanguageTests: Language negotiation and business-isolation tests.
- InterfaceLanguageTests.setUp: Create test user and client.
- InterfaceLanguageTests.test_cookie_header_and_default_priority: Verify cookie, request-header, and default language.
- InterfaceLanguageTests.test_login_and_validation_errors_keep_contract: Verify translation and error contracts.
- InterfaceLanguageTests.test_catalog_translates_labels_only: Verify catalog-label changes do not alter field contracts.
- InterfaceLanguageTests.test_business_content_roundtrip_and_account_isolation: Verify Chinese business content is stored unchanged and permissions remain unchanged.
- InterfaceLanguageTests.test_error_translation_preserves_keys_codes_and_unknown_text: Verify error-translation boundaries.

Variable index:
- None
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils.translation import override
from rest_framework.exceptions import ErrorDetail
from rest_framework.test import APIClient

from common.exceptions import translate_detail


# Function: Verify through real HTTP that interface language does not alter business contracts.
# Logic: Disable debug automatic login and isolate accounts, sessions, and translation context.
# Constraints: Use the test database only and do not connect to model, mail, or calendar services.
@override_settings(DEBUG=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class InterfaceLanguageTests(TestCase):
    # Function: Create an ordinary test account and anonymous client.
    # Inputs: No external parameters; uses an isolated test database.
    # Outputs: Instance state for `user` and `client`.
    # Logic: Account has no usable login password; identity-required cases explicitly call `force_login`.
    # Constraints: Do not create administrators or copy real user data.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="language-owner")
        self.client = APIClient()

    # Function: Verify standard Django language-selection priority.
    # Inputs: Client cookie and Accept-Language request header.
    # Outputs: Content-Language and Vary assertions.
    # Logic: Separately verify English, weighted browser preference, manual Chinese override, and Chinese default for unsupported language.
    # Constraints: Session queries identity only and does not enable local automatic login.
    def test_cookie_header_and_default_priority(self):
        for header, expected in [("en-US,en;q=0.9", "en"), ("fr;q=0.9,en;q=0.8", "en"), ("fr", "zh-hans")]:
            with self.subTest(header=header):
                response = self.client.get("/api/v1/session/", HTTP_ACCEPT_LANGUAGE=header)
                self.assertEqual(response["Content-Language"], expected)
                self.assertIn("Accept-Language", response["Vary"])
        self.client.cookies["django_language"] = "zh-hans"
        response = self.client.get("/api/v1/session/", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response["Content-Language"], "zh-hans")
        self.client.cookies["django_language"] = "en"
        response = self.client.get("/api/v1/session/", HTTP_ACCEPT_LANGUAGE="zh-CN")
        self.assertEqual(response["Content-Language"], "en")

    # Function: Verify English errors and stable machine-readable contracts.
    # Inputs: JSON for invalid login and missing registration fields.
    # Outputs: Assertions for status code, error code, request ID, and field prompt.
    # Logic: Authentication failure passes through project gettext; required-field message passes through DRF built-in translation.
    # Constraints: Do not accept invalid credentials, create accounts, or weaken password validation.
    def test_login_and_validation_errors_keep_contract(self):
        response = self.client.post("/api/v1/session/", {"username": "missing-user", "password": "incorrect"}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "authentication_failed")
        self.assertEqual(response.data["error"]["detail"]["detail"], "Incorrect username or password.")
        self.assertEqual(response.data["request_id"], response["X-Request-ID"])
        response = self.client.post("/api/v1/accounts/register/", {}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 400)
        self.assertIn("required", str(response.data["error"]["detail"]["password"]))
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/accounts/register/", {}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "already_authenticated")
        self.assertEqual(response.data["error"]["detail"], "Sign out before creating a new account.")

    # Function: Verify that the business catalog translates human-readable labels only.
    # Inputs: Chinese and English catalog requests for the same authenticated user.
    # Outputs: Assertions that label translations differ while other resource contracts are exactly equal.
    # Logic: Compare resource by resource; field names, choices, permission prompts, and status edges retain original values.
    # Constraints: Metadata requests are read-only and create no business records.
    def test_catalog_translates_labels_only(self):
        self.client.force_login(self.user)
        chinese = self.client.get("/api/v1/sales/catalog/", HTTP_ACCEPT_LANGUAGE="zh-hans").data
        english = self.client.get("/api/v1/sales/catalog/", HTTP_ACCEPT_LANGUAGE="en").data
        self.assertEqual(len(chinese["resources"]), len(english["resources"]))
        for before, after in zip(chinese["resources"], english["resources"]):
            self.assertNotEqual(before["label"], after["label"])
            self.assertEqual({k: v for k, v in before.items() if k != "label"}, {k: v for k, v in after.items() if k != "label"})

    # Function: Verify that English requests do not translate Chinese business data or expand account visibility.
    # Inputs: A synthetic customer whose name exactly matches a UI translation key and another account.
    # Outputs: Assertions for persistence round trip and account isolation.
    # Logic: Save a Chinese company name through the real API and read the original value after refresh; another user still cannot see it.
    # Constraints: Do not use demo data or production accounts or alter the owner permission model.
    def test_business_content_roundtrip_and_account_isolation(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/sales/directory/", {"name": "客户"}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 201)
        rows = self.client.get("/api/v1/sales/directory/", HTTP_ACCEPT_LANGUAGE="en").data["results"]
        self.assertEqual([row["name"] for row in rows], ["客户"])
        other = get_user_model().objects.create_user(username="language-other")
        self.client.force_login(other)
        response = self.client.get("/api/v1/sales/directory/", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.data["results"], [])

    # Function: Verify that error translation retains structure, codes, and unregistered messages.
    # Inputs: Nested error structure and English translation context.
    # Outputs: Assertions for exact translation, unchanged input, and retained code.
    # Logic: Source field name is also a Chinese translation key, ensuring only error values are processed.
    # Constraints: Unregistered diagnostics retain original text and no guesswork or machine translation occurs.
    def test_error_translation_preserves_keys_codes_and_unknown_text(self):
        detail = {"客户": [ErrorDetail("用户名或密码不正确。", code="original_code"), "未登记的服务诊断", 7]}
        with override("en"):
            result = translate_detail(detail)
        self.assertEqual(list(result), ["客户"])
        self.assertEqual(result["客户"][0], "Incorrect username or password.")
        self.assertEqual(result["客户"][0].code, "original_code")
        self.assertEqual(result["客户"][1:], ["未登记的服务诊断", 7])
        self.assertEqual(detail["客户"][0], "用户名或密码不正确。")
