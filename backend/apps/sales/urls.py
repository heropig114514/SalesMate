"""Responsibility: Declare sales business API routes.
Implementation: Register global insights, seller profiles, and opportunity queries; preserve write permissions. Read-only browse merges approved experiment records. Fixed operations take precedence and views validate resource allowlists.
Relationships: config.urls mounts under /api/v1/sales/; views require authenticated employee identity regardless of laboratory settings.
Directory:
- None
Variable index:
- urlpatterns: Path mappings for records, catalogs, files, authorization, and audits.
"""

from django.urls import path
from . import views
from .qq_connection import QQSendConnectionView
from .priority_views import SellerProfileView
from .browse import BrowseOverviewView, BrowseView
from .world import WorldView
from .algorithm_views import SellerContextView, OpportunityContextView, PriorityBoardView

urlpatterns = [
    path("world/", WorldView.as_view()),
    path("seller-context/", SellerContextView.as_view()),
    path("opportunity-context/<uuid:opportunity_id>/", OpportunityContextView.as_view()),
    path("priority-board/", PriorityBoardView.as_view()),
    path("seller-profile/", SellerProfileView.as_view()),
    path("catalog/", views.CatalogView.as_view()),
    path("overview/", views.OverviewView.as_view()),
    path("browse/overview/", BrowseOverviewView.as_view()),
    path("browse/<str:resource>/", BrowseView.as_view()),
    path("directory/", views.DirectoryView.as_view()),
    path("people/", views.PeopleView.as_view()),
    path("directory/<uuid:company_id>/contacts/", views.ContactView.as_view()),
    path("grouping/<str:operation>/", views.GroupingView.as_view()),
    path(
        "records/<str:resource>/",
        views.ResourceView.as_view(http_method_names=["get", "post", "options"]),
    ),
    path(
        "records/<str:resource>/<uuid:record_id>/",
        views.ResourceDetailView.as_view(http_method_names=["get", "patch", "options"]),
    ),
    path(
        "records/<str:resource>/<uuid:record_id>/commands/", views.CommandView.as_view()
    ),
    path("files/", views.FileView.as_view(http_method_names=["post", "options"])),
    path(
        "files/<uuid:record_id>/download/",
        views.FileView.as_view(http_method_names=["get", "options"]),
    ),
    path("oauth/", views.OAuthView.as_view(), name="sales-oauth"),
    path("connections/qq/", QQSendConnectionView.as_view()),
    path("calendar/<str:operation>/", views.CalendarView.as_view()),
    path("audit/", views.AuditView.as_view()),
]
