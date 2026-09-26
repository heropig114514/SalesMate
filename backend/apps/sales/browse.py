"""Responsibility: Combine originally authorized business records and approved shared experiment records in existing sales pages.
Implementation: Do not merge experiment batches under personal-workspace isolation; validate exact batch manifests, exclude matching primary keys from ordinary queries before pagination, and project validated shared rows directly.
Relationships: business.js uses this read-only entry point; existing views, permissions, and write endpoints gain no privileges; experiments provides full provenance details.
Directory:
- shared_records: Load and project approved experimental rows for a business resource.
- ordinary_query: Obtain a queryset under original permissions and page filters.
- matches: Apply equivalent page filters to validated experiment projections.
- BrowseView: Read-only merged-list endpoint.
- BrowseView.get: Paginate ordinary and experimental records together with origin markers.
- BrowseOverviewView: Overview combining counts while separating experiment notices.
- BrowseOverviewView.get: Add only previously invisible experiment records to original counts.
Variable index:
- RESOURCES: Fixed business-resource to experiment-model mapping, excluding credentials and external connections.
- logger: Log reader, resource, and counts without body content.
- BrowseView.http_method_names: Accept read methods only.
- BrowseOverviewView.http_method_names: Accept read methods only.
"""

import logging

from django.apps import apps
from django.db import connection, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.crm.models import Company
from . import grouping, models
from common.laboratory import owner_only
from .experiments import APPROVED_BATCHES, TABLES, load_batch, table_rows
from .permissions import scope, visible_company_ids
from .serializers import SERIALIZERS
from .views import OverviewView, SalesView

RESOURCES = {key: serializer.Meta.model._meta.label for key, serializer in SERIALIZERS.items()
             if serializer.Meta.model._meta.label in TABLES} | {"directory": "crm.Company", "audit": "sales.AuditEvent"}
logger = logging.getLogger("salesmate.sales.browse")


# Function: Read synthetic records corresponding to a business page.
# Inputs: `resource`: business resource key in RESOURCES.
# Outputs: List containing id, experiment metadata, and business fields.
# Logic: Personal isolation has no shared rows; otherwise verify each batch and map relation fields to UI names. Include only listed contacts and archival settings.
# Constraints: Do not apply business serializers with reverse relations to shared rows, which could read unlisted details or contacts.
def shared_records(resource):
    if owner_only():
        return []
    label = RESOURCES[resource]
    existing = models.AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id__in=APPROVED_BATCHES).values_list("object_id", flat=True)
    result = []
    for batch in sorted(set(existing)):
        entry = load_batch(batch)
        rows = table_rows(entry, label)
        settings, contacts = {}, {}
        if resource == "directory":
            settings = {str(row["fields"]["company_id"]): row["fields"] for row in table_rows(entry, "sales.CompanySettings")}
            for row in table_rows(entry, "crm.Contact"):
                fields = row["fields"]
                contacts.setdefault(str(fields["company_id"]), []).append({"id": row["pk"], "name": fields["name"], "email": fields["email"]})
        fields = apps.get_model(label)._meta.concrete_fields
        for row in rows:
            data = {field.name: row["fields"][field.attname] for field in fields if field.attname in row["fields"]}
            data["id"] = row["pk"]
            data["experiment"] = {key: row[key] for key in ("batch", "owner", "synthetic", "read_only")}
            data["experiment"]["model"] = label
            if resource == "directory":
                data["contacts"] = contacts.get(row["pk"], [])
                data["archived"] = settings.get(row["pk"], {}).get("archived", False)
            result.append(data)
    return sorted(result, key=lambda row: row["id"])


# Function: Build a list query within the original business scope.
# Inputs: `user`: authenticated user; `resource`: business resource; `params`: restricted page query parameters.
# Outputs: A QuerySet retaining the original business permissions.
# Logic: Use visible_company_ids for companies and scope for other resources; support only company, status, and archival filters used by the page.
# Constraints: This function grants no cross-account access; reject unknown filters instead of accepting arbitrary ORM queries.
def ordinary_query(user, resource, params):
    model = apps.get_model(RESOURCES[resource])
    query = Company.objects.filter(pk__in=visible_company_ids(user)) if resource == "directory" else scope(model, user)
    names = {field.name for field in model._meta.concrete_fields}
    if params.get("company"):
        if resource != "directory" and "company" not in names:
            raise ValidationError("此页面不支持客户筛选。")
        query = query.filter(**{"pk" if resource == "directory" else "company": params["company"]})
    if params.get("status"):
        if "status" not in names:
            raise ValidationError("此页面不支持状态筛选。")
        query = query.filter(status=params["status"])
    archived = params.get("archived", "false")
    if archived not in {"false", "true", "all"}:
        raise ValidationError("archived 应为 false、true 或 all。")
    if archived != "all":
        if resource == "directory":
            query = query.filter(business_settings__archived=True) if archived == "true" else query.exclude(business_settings__archived=True)
        elif "archived" in names:
            query = query.filter(archived=archived == "true")
    return query.order_by("id")


# Function: Apply the same page filters to shared rows as to business queries.
# Inputs: `row`: verified business projection; `resource`: resource key; `params`: page parameters.
# Outputs: Whether the row belongs in the current list.
# Logic: Compare exact company IDs, status, and archival state without expanding relations or fuzzy name matching.
# Constraints: ordinary_query validates supported fields first; archival state defaults to false.
def matches(row, resource, params):
    if params.get("company") and str(row["id"] if resource == "directory" else row.get("company")) != params["company"]:
        return False
    if params.get("status") and row.get("status") != params["status"]:
        return False
    archived = params.get("archived", "false")
    return archived == "all" or bool(row.get("archived", False)) == (archived == "true")


# Function: Provide merged reads for existing business pages.
# Logic: Serialize ordinary data with existing handlers, use only verified shared projections, and deduplicate both by primary key.
# Constraints: No write methods; ordinary detail and write API permissions remain unchanged.
class BrowseView(SalesView):
    http_method_names = ["get", "head", "options"]

    # Function: Paginate ordinary business records and synthetic experiment records.
    # Inputs: The authenticated user and page filters from `request`; `resource`: fixed resource name.
    # Outputs: count, page, page_size, shared_count, and results; shared records include an experiment marker.
    # Logic: Open a read-only consistent snapshot in the outermost transaction; place ordinary rows first and deduplicate shared rows by primary key. Never replace errors with empty shared data.
    # Constraints: At most 100 rows per page; drift in any shared table fails the entire request. No model calls or business writes.
    @extend_schema(operation_id="sales_browse", responses=OpenApiTypes.OBJECT,
                   parameters=[OpenApiParameter(name, str) for name in ("company", "status", "archived", "page", "page_size")])
    def get(self, request, resource):
        if resource not in RESOURCES:
            raise NotFound("此资源没有共享业务浏览入口。")
        params = request.query_params
        if set(params) - {"company", "status", "archived", "page", "page_size"} or any(len(params.getlist(key)) != 1 for key in params):
            raise ValidationError("只接受唯一的页面筛选参数。")
        page, size = int(params.get("page", 1)), int(params.get("page_size", 20))
        if page < 1 or not 1 <= size <= 100:
            raise ValidationError("page 须大于零，page_size 须为 1–100。")
        with transaction.atomic():
            if len(connection.atomic_blocks) == 1:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            shared = shared_records(resource)
            query = ordinary_query(request.user, resource, params).exclude(pk__in=[row["id"] for row in shared])
            shared = [row for row in shared if matches(row, resource, params)]
            count = query.count()
            start, end = (page - 1) * size, page * size
            ordinary = query[start:min(end, count)] if start < count else []
            if resource == "directory":
                rows = [grouping.directory_row(record) for record in ordinary]
            elif resource == "audit":
                rows = list(ordinary.values("id", "actor_id", "company_id", "event", "object_type", "object_id", "changes", "created_at")) if ordinary else []
            else:
                rows = list(SERIALIZERS[resource](ordinary, many=True, context={"request": request}).data)
            rows.extend(shared[max(0, start - count):max(0, end - count)])
        logger.info("business_browse actor_id=%s resource=%s ordinary=%s shared=%s page=%s", request.user.pk, resource, count, len(shared), page)
        response = Response({"count": count + len(shared), "shared_count": len(shared), "page": page, "page_size": size, "results": rows})
        response["Cache-Control"] = "private, no-store"
        return response


# Function: Provide counts corresponding to merged lists.
# Logic: Add only experiment companies and pending items outside the original permission scope; retain and explicitly label the original monetary scope.
# Constraints: Do not add shared synthetic transaction amounts to ordinary business totals.
class BrowseOverviewView(SalesView):
    http_method_names = ["get", "head", "options"]

    # Function: Build an overview consistent with merged lists.
    # Inputs: `request`: the current authenticated user request.
    # Outputs: The original overview and shared_counts; company, ticket, and follow-up counts include deduplicated shared rows.
    # Logic: Apply the original status conditions to three verified record types and add only records outside the original scope.
    # Constraints: No business writes; shared-table drift fails explicitly instead of displaying zero values.
    @extend_schema(operation_id="sales_browse_overview", responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def get(self, request):
        data = dict(OverviewView().get(request).data)
        shared_counts = {}
        for resource, key in (("directory", "customers"), ("tickets", "open_tickets"), ("follow-ups", "open_follow_ups")):
            rows = [row for row in shared_records(resource) if not row.get("archived", False)]
            if resource == "tickets":
                rows = [row for row in rows if row["status"] not in {"resolved", "closed"}]
            elif resource == "follow-ups":
                rows = [row for row in rows if row["status"] == "open"]
            visible = set(str(pk) for pk in ordinary_query(request.user, resource, {"archived": "all"}).filter(pk__in=[row["id"] for row in rows]).values_list("pk", flat=True))
            data[key] += sum(row["id"] not in visible for row in rows)
            shared_counts[key] = len(rows)
        data["shared_counts"] = shared_counts
        data["money_scope"] = "original_business_permissions"
        response = Response(data)
        response["Cache-Control"] = "private, no-store"
        return response
