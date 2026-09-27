"""Responsibility: Declare browser and fixed Agent routes for chat adaptation.
Implementation: Mount request tools, state queries, independent business proposals, and checkpointed experiment approvals relative to /api/v1/ without changing the business Router.
Relationships: views/tool_views expose browser and Agent interfaces; approvals accepts only a logged-in browser Session for decisions.
Directory:
- None
Variable index:
- urlpatterns: Browser submission, status, retry, and approval; Agent claim, context, tools, and reports.
"""

from django.urls import path
from . import tool_views, views
from .approvals import ApprovalDecisionView
from .action_views import ProposalListView, ProposalDetailView, ProposalDecisionView

urlpatterns = [
    path("sales/chat/action-proposals/", ProposalListView.as_view()),
    path("sales/chat/action-proposals/<uuid:proposal_id>/", ProposalDetailView.as_view()),
    path("sales/chat/action-proposals/<uuid:proposal_id>/decision/", ProposalDecisionView.as_view()),
    path("sales/chat/messages/", views.SubmitView.as_view()),
    path("sales/chat/requests/", views.RequestListView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/", views.RequestView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/retry/", views.RetryView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/approvals/<uuid:approval_id>/decision/", ApprovalDecisionView.as_view()),
    path("agent/chat/requests/claim/", views.ClaimView.as_view()),
    path("agent/chat/context/", views.ContextView.as_view()),
    path("agent/chat/answers/", views.AnswerView.as_view()),
    path("agent/chat/tools/", tool_views.ToolCatalogView.as_view()),
    path("agent/chat/tool-reads/", tool_views.ToolReadView.as_view()),
    path(
        "agent/chat/requests/<uuid:request_id>/", tool_views.AgentRequestView.as_view()
    ),
]
