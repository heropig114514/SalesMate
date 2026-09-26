"""Responsibility: Declare independent paths for tools, user authorization, and proposal confirmation.
Implementation: Routes are fixed, permission templates support one-time authorization review, and call names resolve through a JSON allowlist.
Relationships: ``config.urls`` mounts ``/api/v1/agent-tools/``.
Directory:
- None
Variable index:
- urlpatterns: Tool discovery, invocation, and Session-only control endpoints.
"""

from django.urls import path
from . import views
from .presets import PresetView

urlpatterns = [
    path("permission-presets/", PresetView.as_view()),
    path("catalog/", views.CatalogView.as_view()),
    path("call/", views.CallView.as_view()),
    path("credentials/", views.CredentialView.as_view()),
    path("credentials/<uuid:credential_id>/", views.CredentialDetailView.as_view()),
    path("proposals/", views.ProposalView.as_view()),
    path("proposals/<uuid:proposal_id>/", views.ProposalDetailView.as_view()),
    path("proposals/<uuid:proposal_id>/decision/", views.DecisionView.as_view()),
]
