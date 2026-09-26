"""Responsibility: Declare the accounts API namespace and relative routes.
Implementation: ``onboarding/`` maintains personal product plans and private files; ``company-profile/`` maintains company information; ``me/`` reads the current identity; ``me/reset/`` clears internal data while retaining login; ``register/`` provides CSRF-protected anonymous standard-account registration.
Relationships: Mounted at ``api/v1/accounts/`` by ``config.urls`` and delegated to ``views``, ``company_profile``, and ``registration``.

Directory:
- None

Variable index:
- app_name: ``accounts`` namespace used for reverse resolution.
- urlpatterns: Module-local routes for the current user, internal-data clearing, company information, onboarding, and account registration.
"""

from django.urls import path

from .onboarding import SetupView, DocumentView
from .views import CurrentUserView
from .company_profile import CompanyProfileView
from .registration import RegistrationView
from .reset import ResetView

app_name = "accounts"
urlpatterns = [
    path("me/reset/", ResetView.as_view(), name="reset"),
    path("onboarding/", SetupView.as_view(), name="onboarding"),
    path("onboarding/documents/", DocumentView.as_view(http_method_names=["post", "options"]), name="setup-document-upload"),
    path("onboarding/documents/<uuid:document_id>/", DocumentView.as_view(http_method_names=["get", "head", "options"]), name="setup-document"),
    path("company-profile/", CompanyProfileView.as_view(), name="company-profile"),
    path("me/", CurrentUserView.as_view(), name="me"),
    path("register/", RegistrationView.as_view(), name="register"),
]
