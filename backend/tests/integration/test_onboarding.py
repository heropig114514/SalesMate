"""Responsibility: Verify first-time onboarding, simplified passwords, and private profile-file boundaries.
Implementation: Use real isolated test database and APIClient; do not mock authorization decisions or access external services.
Relationships: Covers `accounts.onboarding`, registration, and the CRM session API.
Directory:
- OnboardingTests: Onboarding-profile integration scenarios.
- OnboardingTests.setUp: Create two synthetic accounts.
- OnboardingTests.test_save_reload_conflict_and_isolation: Verify persistence, versioning, and account isolation.
- OnboardingTests.test_documents_and_product_validation: Verify file reading and product-reference authorization.
- OnboardingTests.test_file_validation_and_anonymous: Verify format, size, anonymous access, and CSRF.
- OnboardingTests.test_simple_password_and_onboarding_lifecycle: Verify numeric-only passwords and onboarding-completion state.
Variable index:
- OnboardingTests.path: Onboarding API address.
- OnboardingTests.files: Private-attachment API address.
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import SalesSetup
from apps.accounts.onboarding import MAX_BYTES


# Function: Verify onboarding and authorization boundaries.
# Logic: Disable debug automatic login and use an isolated database and synthetic files.
# Constraints: Tests do not prove real external services or browser PDF plugins are available.
@override_settings(DEBUG=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class OnboardingTests(TestCase):
    path = "/api/v1/accounts/onboarding/"
    files = "/api/v1/accounts/onboarding/documents/"

    # Function: Prepare independent accounts.
    # Inputs: No external parameters; reads the test database.
    # Outputs: Two users and authenticated-client state.
    # Logic: Use `force_authenticate` to isolate profile tests; CSRF has separate coverage.
    # Constraints: No production data or external request.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="setup-owner")
        self.other = get_user_model().objects.create_user(username="setup-other")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # Function: Verify saving and concurrent conflicts.
    # Inputs: Synthetic identity, current version, and stale version.
    # Outputs: Assertions for persistence, 409, and another user's empty profile.
    # Logic: Read again and switch users after a real PATCH.
    # Constraints: Not a concurrency-scheduling stress test.
    def test_save_reload_conflict_and_isolation(self):
        self.assertEqual(self.client.get(self.path).data["revision"], 0)
        self.assertEqual(SalesSetup.objects.count(), 0)
        personal = {"name": "Alex", "title": "Sales", "email": "alex@example.com", "phone": "", "regions": ["亚太"], "industries": ["光学检测"]}
        response = self.client.patch(self.path, {"personal": personal}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["personal"], personal)
        self.assertEqual(self.client.patch(self.path, {"completed": True}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        self.assertFalse(self.client.get(self.path).data["completed"])
        self.assertEqual(self.client.patch(self.path, {"owner": self.other.pk}, format="json", HTTP_IF_MATCH="1").status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.path).data["personal"], {})

    # Function: Verify private attachments and price ranges.
    # Inputs: UTF-8 text, a product referencing the file, and another account.
    # Outputs: Private 200 read, 400 for unauthorized reference and inverted price, and 404 for unauthorized read.
    # Logic: Associate a product and read JSON again after a real multipart upload.
    # Constraints: Do not execute files or access real customers.
    def test_documents_and_product_validation(self):
        response = self.client.post(self.files, {"file": SimpleUploadedFile("spec.txt", b"Specification")}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        document_id = str(response.data["id"])
        download = self.client.get(self.files + document_id + "/")
        self.assertEqual(download.content, b"Specification")
        self.assertIn("no-store", download["Cache-Control"])
        product = {"name": "Scanner", "category": "Optics", "specifications": ["10 mm"], "price_min": "10.00", "price_max": "20.00", "currency": "SGD", "scenarios": ["inspection"], "document_id": document_id}
        response = self.client.patch(self.path, {"products": [product]}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["products"][0]["price_min"], "10.00")
        product["price_min"] = "30.00"
        self.assertEqual(self.client.patch(self.path, {"products": [product]}, format="json", HTTP_IF_MATCH="1").status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.files + document_id + "/").status_code, 404)
        self.assertEqual(self.client.get(self.path).data["documents"], [])
        self.assertEqual(self.client.patch(self.path, {"solutions": [{"name": "Proposal", "document_id": document_id}]}, format="json", HTTP_IF_MATCH="0").status_code, 400)

    # Function: Verify attachment-input and session boundaries.
    # Inputs: A disguised PDF, invalid encoding, oversized file, and unauthenticated request.
    # Outputs: Assertions for format rejection and 403.
    # Logic: Test failed inputs one at a time and separately use a CSRF-enforcing client to cover session writes.
    # Constraints: Upload sizes are test-constructed values and no real files are used.
    def test_file_validation_and_anonymous(self):
        for name, content in [("bad.pdf", b"not pdf"), ("bad.txt", b"\xff"), ("big.txt", b"a" * (MAX_BYTES + 1))]:
            self.assertEqual(self.client.post(self.files, {"file": SimpleUploadedFile(name, content)}, format="multipart").status_code, 400)
        anonymous = APIClient(enforce_csrf_checks=True)
        self.assertEqual(anonymous.get(self.path).status_code, 403)
        anonymous.force_login(self.owner)
        self.assertEqual(anonymous.patch(self.path, {"completed": True}, format="json", HTTP_IF_MATCH="0").status_code, 403)

    # Function: Verify simplified registration and first-time onboarding.
    # Inputs: New username, eight-digit numeric password, and a real CSRF cookie.
    # Outputs: Assertions for 201 registration, onboarding flag, and cleared flag after completion.
    # Logic: Registration creates an incomplete profile; read session again after explicit completion PATCH.
    # Constraints: Do not weaken password hashing, CSRF, or account-permission requirements.
    def test_simple_password_and_onboarding_lifecycle(self):
        client = APIClient(enforce_csrf_checks=True)
        token = client.get("/api/v1/session/").data["csrf_token"]
        response = client.post("/api/v1/accounts/register/", {"username": "simple-password", "password": "12345678"}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.data)
        session = client.get("/api/v1/session/").data
        self.assertTrue(session["onboarding_required"])
        response = client.patch(self.path, {"completed": True}, format="json", HTTP_IF_MATCH="0", HTTP_X_CSRFTOKEN=session["csrf_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(client.get("/api/v1/session/").data["onboarding_required"])
