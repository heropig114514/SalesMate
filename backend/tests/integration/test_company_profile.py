"""Responsibility: Verify saving, isolation, and concurrent-version contracts for the user's company profile.
Implementation: Run the real Session API and database in an isolated test database, with synthetic profile input only.
Relationships: Covers `accounts.company_profile` and `CompanyProfile`; does not connect to mailboxes, models, or production services.
Directory:
- CompanyProfileTests: User-company-profile integration tests.
- CompanyProfileTests.setUp: Prepare two independent accounts and a logged-in client.
- CompanyProfileTests.test_save_reload_and_owner_isolation: Save and isolate across accounts.
- CompanyProfileTests.test_validation_versions_and_noop: Field and version errors do not write data.
- CompanyProfileTests.test_anonymous_and_csrf: Authentication and session CSRF cannot be bypassed.
Variable index:
- CompanyProfileTests.path: API path under test.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import CompanyProfile
from apps.crm.models import Company
from apps.sales.models import SellerProfile


# Function: User-company-profile integration tests.
# Logic: Use a real database and authenticated client to validate profile persistence and error side effects.
# Constraints: Normal cases use `force_login`; the CSRF case separately enables real protection; do not mock data saves.
class CompanyProfileTests(TestCase):
    path = "/api/v1/accounts/company-profile/"

    # Function: Prepare two independent accounts and a logged-in client.
    # Inputs: No external parameters; the test framework creates the database.
    # Outputs: Instance state for `owner`, `other`, and `browser`.
    # Logic: Create ordinary users without business profiles and log in `owner`.
    # Constraints: Do not create global organizations or customers or use production credentials.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="profile-owner")
        self.other = get_user_model().objects.create_user(username="profile-other")
        self.browser = APIClient()
        self.browser.force_login(self.owner)

    # Function: Save and isolate across accounts.
    # Inputs: A synthetic company profile and two authenticated users.
    # Outputs: Read-after-write consistency, an empty profile for the other account, and unaffected customer and scoring profiles.
    # Logic: Initial GET creates no record, PATCH creates it, and isolation is verified after switching accounts.
    # Constraints: Do not interpret distinct users as authorized members of the same organization.
    def test_save_reload_and_owner_isolation(self):
        empty = self.browser.get(self.path)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data["revision"], 0)
        self.assertEqual(CompanyProfile.objects.count(), 0)
        data = {"company_name": "Example Seller", "website": "https://seller.example", "email": "sales@seller.example", "industry": "Manufacturing", "phone": "+65 1234 5678", "address": "Singapore", "description": "Our company"}
        saved = self.browser.patch(self.path, data, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data["revision"], 1)
        self.assertEqual(self.browser.get(self.path).data, saved.data)
        self.assertEqual(saved["ETag"], '"1"')
        self.browser.force_login(self.other)
        self.assertEqual(self.browser.get(self.path).data["company_name"], "")
        other = self.browser.patch(self.path, {"company_name": "Other Seller"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(CompanyProfile.objects.get(owner=self.owner).company_name, "Example Seller")
        self.assertEqual(Company.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)

    # Function: Ensure field and version errors do not write data.
    # Inputs: Missing name, unauthorized field, invalid formats, stale version, and identical profile data.
    # Outputs: Errors return 400 or 409, repeated content does not increment the version, and valid clearing of optional fields succeeds.
    # Logic: Check database count and final content after each operation.
    # Constraints: Version checking occurs under the user's row lock; the test verifies the API contract rather than simulating real concurrency scheduling.
    def test_validation_versions_and_noop(self):
        for data in ({}, {"company_name": ""}, {"company_name": "A", "owner": self.other.pk}, {"company_name": "A", "revision": 4}, {"company_name": "A", "email": "invalid"}, {"company_name": "A", "website": "javascript:alert(1)"}, {"company_name": "A", "description": "x" * 5001}):
            self.assertEqual(self.browser.patch(self.path, data, format="json", HTTP_IF_MATCH="0").status_code, 400)
        self.assertEqual(CompanyProfile.objects.count(), 0)
        self.assertEqual(self.browser.patch(self.path, {"company_name": "A"}, format="json").status_code, 400)
        self.browser.patch(self.path, {"company_name": "A", "phone": "123"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(self.browser.patch(self.path, {"company_name": "B"}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        same = self.browser.patch(self.path, {"company_name": "A"}, format="json", HTTP_IF_MATCH="1")
        self.assertEqual(same.data["revision"], 1)
        cleared = self.browser.patch(self.path, {"phone": ""}, format="json", HTTP_IF_MATCH="1")
        self.assertEqual((cleared.data["company_name"], cleared.data["phone"], cleared.data["revision"]), ("A", "", 2))

    # Function: Ensure authentication and session CSRF cannot be bypassed.
    # Inputs: Anonymous access and a logged-in write request without CSRF.
    # Outputs: All requests are rejected and the profile table remains empty.
    # Logic: Enable `enforce_csrf_checks` on the client and log in through a real session.
    # Constraints: Do not use `force_authenticate` to bypass session authentication.
    def test_anonymous_and_csrf(self):
        browser = APIClient(enforce_csrf_checks=True)
        self.assertEqual(browser.get(self.path).status_code, 403)
        browser.force_login(self.owner)
        self.assertEqual(browser.patch(self.path, {"company_name": "A"}, format="json", HTTP_IF_MATCH="0").status_code, 403)
        self.assertEqual(CompanyProfile.objects.count(), 0)
