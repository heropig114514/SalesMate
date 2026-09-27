"""Responsibility: Declare the accounts API namespace and relative routes.
Implementation: ``onboarding/`` maintains personal product plans and private files; ``company-profile/`` maintains company information; ``me/`` reads identity; ``me/password/`` changes the password; ``me/reset/`` clears internal data while retaining login; ``register/`` provides CSRF-protected registration.
Relationships: Mounted at ``api/v1/accounts/`` by ``config.urls``; password_change verifies the current password at ``me/password/`` while preserving the requesting session.

Directory:
- None

Variable index:
- app_name: ``accounts`` namespace used for reverse resolution.
- urlpatterns: Module-local routes for identity, password changes, internal-data clearing, company information, onboarding, and registration.
"""

from django.urls import path

from .onboarding import SetupView, DocumentView
from .views import CurrentUserView
from .company_profile import CompanyProfileView
from .registration import RegistrationView
from .reset import ResetView
from .password_change import PasswordChangeView

app_name = "accounts"
urlpatterns = [
    path("me/password/", PasswordChangeView.as_view(), name="password-change"),
    path("me/reset/", ResetView.as_view(), name="reset"),
    path("onboarding/", SetupView.as_view(), name="onboarding"),
    path("onboarding/documents/", DocumentView.as_view(http_method_names=["post", "options"]), name="setup-document-upload"),
    path("onboarding/documents/<uuid:document_id>/", DocumentView.as_view(http_method_names=["get", "head", "options"]), name="setup-document"),
    path("company-profile/", CompanyProfileView.as_view(), name="company-profile"),
    path("me/", CurrentUserView.as_view(), name="me"),
    path("register/", RegistrationView.as_view(), name="register"),
]
