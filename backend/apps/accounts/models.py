"""Responsibility: Declare project user identity and account-isolated company-profile models.
Implementation: Inherit ``AbstractUser``; ``CompanyProfile``, ``SalesSetup``, and ``SetupDocument`` store company information, onboarding information, and private files by account without participating in customer scoring.
Relationships: Referenced by ``AUTH_USER_MODEL``, Admin, identity and company-profile endpoints, and accounts migrations; importing ``reset_models`` registers reset-coordination state that preserves identity.

Directory:
- User: Declare the project's custom user type.
- CompanyProfile: Account-isolated company information and editing version.
- SalesSetup: Personal, product, and solution information and onboarding progress.
- SetupDocument: Onboarding attachment that requires login to read.

Variable index:
- CompanyProfile.owner: Sole owning account; clients cannot modify it.
- CompanyProfile.company_name: The company's name.
- CompanyProfile.industry: Company industry.
- CompanyProfile.size_band: Company size; may be blank.
- CompanyProfile.website: Public website address.
- CompanyProfile.email: Business contact mailbox, excluding mailbox authorization.
- CompanyProfile.phone: Business contact phone number.
- CompanyProfile.address: Business address.
- CompanyProfile.description: Company overview.
- CompanyProfile.revision: Optimistic-lock version.
- CompanyProfile.updated_at: Most recent save time.
- SalesSetup.owner: Account that owns the information.
- SalesSetup.personal: Personal identity and responsible region and industry.
- SalesSetup.products: Reference product catalog; does not replace signed quotes.
- SalesSetup.solutions: Solutions and private attachment references.
- SalesSetup.completed: Whether onboarding is completed or skipped.
- SalesSetup.revision: Concurrent-edit version.
- SetupDocument.id: Opaque file identifier.
- SetupDocument.owner: The sole account authorized to read the file.
- SetupDocument.name: Display file name.
- SetupDocument.content_type: Validated PDF or plain-text type.
- SetupDocument.content: File content up to 5 MiB, backed up with the database.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid


# Function: Declare the project's custom user type.
# Logic: Fully inherit ``AbstractUser`` so later identity extensions do not require changing ``AUTH_USER_MODEL``.
# Constraints: Team, mailbox-binding, and Agent-service identity capabilities are not currently available; migrations define the database structure.
class User(AbstractUser):
    """Own the user model from the first migration so identity can evolve safely."""


# Function: Account-isolated company information and editing version.
# Logic: One profile per user, including optional company size; stored separately from CRM customer and sales-target profiles.
# Constraints: Not a cross-account organization or team-permission model; data is saved explicitly only through authenticated endpoints, and unknown information remains blank.
class CompanyProfile(models.Model):
    owner = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)
    company_name = models.CharField(max_length=240)
    industry = models.CharField(max_length=100, blank=True)
    size_band = models.CharField(max_length=30, blank=True)
    website = models.URLField(max_length=500, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=500, blank=True)
    description = models.TextField(max_length=5000, blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


# Function: Persist sales-representative onboarding information.
# Logic: One record per account; the onboarding serializer strictly validates the JSON structure.
# Constraints: Reads do not automatically create a record; only explicit saves increment the version and do not trigger scoring or external calls.
class SalesSetup(models.Model):
    owner = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)
    personal = models.JSONField(default=dict)
    products = models.JSONField(default=list)
    solutions = models.JSONField(default=list)
    completed = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)


# Function: Persist this account's product specifications and sales solutions.
# Logic: Save content and metadata in the same database transaction to avoid a successful file write with a failed record.
# Constraints: The API limits content to 5 MiB of PDF or UTF-8 text; it exposes no public static URL and does not execute file content.
class SetupDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=240)
    content_type = models.CharField(max_length=80)
    content = models.BinaryField()


from .reset_models import AccountReset  # noqa: E402,F401
