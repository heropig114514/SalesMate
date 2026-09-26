"""Responsibility: Declare browser and fixed Agent routes for chat adaptation.
Implementation: Mount relative to ``/api/v1/``, adding request-bound tool catalog, reads, and status checks without changing the existing Router.
Relationships: ``chat.views`` and ``tool_views`` implement two independent authentication schemes.
Directory:
- None
Variable index:
- urlpatterns: Browser actions; Agent claim, context, and reports; tool catalog, reads, and status mappings.
"""

from django.urls import path
from . import tool_views, views

urlpatterns = [
    path("sales/chat/messages/", views.SubmitView.as_view()),
    path("sales/chat/requests/", views.RequestListView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/", views.RequestView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/retry/", views.RetryView.as_view()),
    path("agent/chat/requests/claim/", views.ClaimView.as_view()),
    path("agent/chat/context/", views.ContextView.as_view()),
    path("agent/chat/answers/", views.AnswerView.as_view()),
    path("agent/chat/tools/", tool_views.ToolCatalogView.as_view()),
    path("agent/chat/tool-reads/", tool_views.ToolReadView.as_view()),
    path(
        "agent/chat/requests/<uuid:request_id>/", tool_views.AgentRequestView.as_view()
    ),
]
