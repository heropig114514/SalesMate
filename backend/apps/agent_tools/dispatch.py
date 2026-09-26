"""Responsibility: Bind authorized tools to existing business handlers.
Implementation: Opportunity context, score list, map, and seller-profile reads share fixed views; tools support fact-upgrade preview and explicit queueing; experiment mode reads knowledge entries across accounts; use an explicit method allowlist and minimal request context; information and files delegate to ``support``, shared batches to ``experiments``, retaining existing serialization, scope, transactions, versions, and state machines.
Relationships: Called after ``services`` completes tool authentication and input validation; ``graph`` reuses the caller's own graph views; this module neither triggers second DRF authentication nor constructs a network loop.
Directory:
- request_context: Build restricted business context.
- execute: Dispatch a fixed tool.
Variable index:
- None
"""

from common.laboratory import owner_scope

from types import SimpleNamespace
from django.db.models import Q
from django.http import QueryDict
from rest_framework.response import Response
from apps.crm import views as crm
from apps.crm import processing_views as processing
from apps.sales import views as sales
from apps.chat.models import KnowledgeEntry
from .support import execute_support
from .experiments import execute_experiment
from .graph import execute_graph_tool


# Function: Construct context required by business handlers.
# Inputs: User ``actor``, query dictionary ``query``, JSON ``data``, and stale version ``revision``.
# Outputs: Minimal request object.
# Logic: Contains only tool-Schema-validated parameters and the real actor.
# Constraints: Does not emulate Session or allow a user-supplied HTTP URL, headers, or authentication state; for the fixed handlers below only.
def request_context(actor, query=None, data=None, revision=None):
    params = QueryDict(mutable=True)
    params.update({key: str(value) for key, value in (query or {}).items()})
    return SimpleNamespace(
        user=actor,
        query_params=params,
        data=data or {},
        headers={} if revision is None else {"If-Match": str(revision)},
    )


# Function: Execute a business adapter.
# Inputs: ``actor``, allowlisted declaration ``spec``, validated arguments ``args``, and optional idempotency UUID ``key``.
# Outputs: Existing ``Response``.
# Logic: ``graph`` dispatches the caller's own graph; ``algorithm_read`` dispatches fixed read-only views, while remaining branches retain original business and experiment-mode boundaries.
# Constraints: ``services`` must authenticate and validate before invocation; does not dispatch arbitrary paths, methods, or action approvals.
def execute(actor, spec, args, key=None):
    kind = spec["kind"]
    if kind == "graph":
        return execute_graph_tool(actor, spec, args)
    request = request_context(actor, args, args.get("data"), args.get("revision"))
    if kind == "algorithm_read":
        from apps.sales.algorithm_views import SellerContextView, OpportunityContextView, PriorityBoardView
        from apps.sales.world import WorldView
        if spec["view"] == "opportunity":
            return OpportunityContextView().get(request, args["opportunity_id"])
        return {"seller": SellerContextView, "board": PriorityBoardView, "world": WorldView}[spec["view"]]().get(request)
    if kind == "experiment":
        return execute_experiment(request, spec, args)
    if kind.startswith("support_"):
        return execute_support(request, spec, args)
    if kind.startswith("record_"):
        resource = spec["resource"]
        if kind == "record_list":
            return sales.ResourceView().get(request, resource)
        if kind == "record_get":
            return sales.ResourceDetailView().get(request, resource, args["id"])
        if kind == "record_create":
            return sales.ResourceView().post(request, resource)
        if kind == "record_update":
            return sales.ResourceView().patch(request, resource, args["id"])
        if kind == "record_command":
            request.data = {"command": spec["command"]}
            if spec["command"] != "read":
                request.data["value"] = args[
                    "archived" if spec["command"] == "archive" else "status"
                ]
            return sales.CommandView().post(request, resource, args["id"])
    if kind == "customers":
        return sales.DirectoryView().get(request)
    if kind == "customer_create":
        request.data = args
        return sales.DirectoryView().post(request)
    if kind == "customer_context":
        return crm.CompanyViewSet().retrieve(request, args["company_id"])
    if kind in {"extraction_status", "upgrade_extractions"}:
        request.method = "GET" if kind == "extraction_status" else "POST"
        return crm.CompanyViewSet().extraction_upgrade(request, args["company_id"])
    if kind == "analyze":
        return crm.CompanyViewSet().analyze(request, args["company_id"])
    if kind == "register":
        return crm.CompanyViewSet().register(request, args["company_id"])
    if kind == "contact":
        return sales.ContactView().post(request, args["company_id"])
    if kind == "overview":
        return sales.OverviewView().get(request)
    if kind == "audit":
        return sales.AuditView().get(request)
    if kind == "people":
        return sales.PeopleView().get(request)
    if kind == "mailboxes":
        return crm.MailboxViewSet().list(request)
    if kind == "sync":
        request.data = (
            {"sync_options": args["sync_options"]} if "sync_options" in args else {}
        )
        return crm.MailboxViewSet().request_sync(request, args["mailbox_id"])
    if kind == "sync_status":
        return processing.SyncRunView().get(request, args["run_id"])
    if kind == "sync_retry":
        return processing.SyncRunView().post(request, args["run_id"])
    if kind == "emails":
        return processing.EmailReviewsView().get(request, args.get("mailbox_id"))
    if kind == "email_review":
        request.data = {"review_status": args["review_status"]}
        return processing.EmailReviewView().patch(request, args["email_id"])
    if kind == "calendar":
        return sales.CalendarView().get(request, spec["operation"])
    if kind == "grouping":
        request.data = args
        return sales.GroupingView().post(request, spec["operation"])
    if kind == "prepare_action":
        request.data = {
            **args,
            "tool": spec["action_tool"],
            "idempotency_key": str(key),
        }
        return sales.ResourceView().post(request, "actions")
    if kind == "file_link":
        response = sales.ResourceDetailView().get(request, "files", args["id"])
        return Response(
            {
                "file": response.data,
                "download_path": f"/api/v1/sales/files/{args['id']}/download/",
                "authentication": "user_session",
                "content_parsed": False,
            }
        )
    if kind == "proposal_get":
        # Resolve projection function lazily after service-module loading to avoid an initialization cycle between dispatch and services.
        from .models import ToolProposal
        from .services import proposal_data

        return Response(
            proposal_data(ToolProposal.objects.get(pk=args["id"], owner=actor))
        )
    if kind in {"knowledge_search", "knowledge_get"}:
        query = KnowledgeEntry.objects.filter(owner_scope(actor), active=True).order_by(
            "-created_at", "id"
        )
        if kind == "knowledge_get":
            rows, pagination = [query.get(pk=args["id"])], {}
        else:
            if args.get("q"):
                query = query.filter(
                    Q(title__icontains=args["q"]) | Q(content__icontains=args["q"])
                )
            rows, pagination = sales.paged(query, request)
        results = [
            {
                "id": str(row.pk),
                "source_id": f"knowledge:{row.pk}",
                "source_type": "internal_knowledge",
                "title": row.title,
                "version": row.version,
                "content": row.content,
            }
            for row in rows
        ]
        return Response(
            results[0]
            if kind == "knowledge_get"
            else {**pagination, "results": results}
        )
    raise ValueError("Unregistered tool handler")
