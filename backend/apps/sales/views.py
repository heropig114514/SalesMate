"""Responsibility: Provide session-authenticated APIs for sales records, company grouping, attachments, and external actions.
Implementation: Generic CRUD accepts opportunity signals/scores from algorithms. Experiment-mode default authentication supplies a public identity and notification commands allow cross-account maintenance. Events/news retain versioned record APIs with region/time filters. Resource allowlists select strict serializers; authorized transactions perform writes and unified errors hide credentials.
Relationships: catalog provides management-page field contracts; services/grouping/actions/files implement business boundaries.
Directory:
- ResourceDetailView: Single-resource query route.
- ResourceDetailView.get: Read one record.
- SalesView: Shared authentication and safe error boundary.
- SalesView.handle_exception: Convert database and parameter errors.
- ResourceView: Query/write record collections and individual records.
- ResourceView.serializer_type: Resolve allowlisted resources.
- ResourceView.get: Authorize queries and paginate.
- ResourceView.post: Create related records or actions awaiting confirmation.
- ResourceView.patch: Version-update records.
- CommandView: Explicit state, archival, approval, and notification-read operations.
- CommandView.post: Execute authorized commands.
- DirectoryView: Company directory without emails.
- DirectoryView.get: Paginate companies and contacts.
- DirectoryView.post: Create a company.
- ContactView: Manual contact writes.
- ContactView.post: Create or update contact identity.
- GroupingView: Explicit company merging and email moves.
- GroupingView.post: Validate versions before grouping.
- FileView: Attachment upload/download.
- FileView.post: Handle multipart uploads.
- FileView.get: Authenticated attachment downloads.
- OAuthView: Separate Google write-permission authorization flow.
- OAuthView.post: Generate authorization redirect links.
- OAuthView.get: Handle one-time authorization callbacks.
- AuditView: Authorized audit reads.
- AuditView.get: Paginate redacted audits.
- CatalogView: Field/state metadata for management pages.
- CatalogView.get: Generate business form contracts.
- OverviewView: Sales summaries by currency.
- OverviewView.get: Count authorized business data and personal pending items.
- PeopleView: Account lookup for team invitations.
- PeopleView.get: Find active accounts by complete username.
- CalendarView: Protected read-only calendar queries.
- CalendarView.get: Read events or free/busy.
- paged: Apply bounded pagination.
- record_response: Generate records and ETags.
Variable index:
- logger: Redacted API exception logs.
- LABELS: Chinese source labels for resources; translate responses by request language without changing resource keys.
- SalesView.permission_classes: Authentication required.
- FileView.parser_classes: Multipart and form parsers for attachments.
"""

from common.laboratory import owner_scope

import logging
from decimal import Decimal

from django.utils.translation import gettext
from django.contrib.auth import get_user_model
from django.core.exceptions import (
    ObjectDoesNotExist,
    ValidationError as DjangoValidationError,
)
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers as fields
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import Conflict, check_version
from apps.crm.models import Company
from . import actions, calendar, files, grouping, integrations, models, services
from .permissions import scope, visible_company_ids
from .serializers import SERIALIZERS

logger = logging.getLogger("salesmate.api")
LABELS = {
    "opportunity-signals": "商机信号",
    "opportunity-priorities": "商机评分",
    "customers": "客户设置",
    "aliases": "人工归组",
    "contact-profiles": "联系人资料",
    "teams": "团队",
    "memberships": "团队成员",
    "grants": "客户共享",
    "products": "产品",
    "tickets": "工单",
    "opportunities": "商机",
    "quotes": "报价",
    "quote-lines": "报价明细",
    "orders": "订单",
    "order-lines": "订单明细",
    "follow-ups": "跟进",
    "conversations": "助手会话",
    "messages": "会话消息",
    "drafts": "草稿",
    "actions": "外部动作",
    "files": "私有附件",
    "notifications": "提醒",
    "connections": "外部连接",
    "world-events": "全球活动",
    "world-news": "行业资讯",
}


# Function: Apply bounded pagination.
# Inputs: `query`: authorized queryset; `request`: page and page_size.
# Outputs: Current-page records and count/page/page_size.
# Logic: Pages start at 1; default page size is 30, maximum 100.
# Constraints: Reject invalid pages explicitly without silent clamping or full-record traversal.
def paged(query, request):
    page, size = (
        int(request.query_params.get("page", 1)),
        int(request.query_params.get("page_size", 30)),
    )
    if page < 1 or not 1 <= size <= 100:
        raise ValidationError("page 须大于零，page_size 须为 1–100。")
    return list(query[(page - 1) * size : page * size]), {
        "count": query.count(),
        "page": page,
        "page_size": size,
    }


# Function: Generate a versioned record response.
# Inputs: `record`, `serializer` class, `request`, and `status` defaulting to 200.
# Outputs: Response with decimal revision as ETag.
# Logic: Retain strict serialized field allowlists; clients return If-Match on writes.
# Constraints: Do not expose internal model storage keys or credentials.
def record_response(record, serializer, request, status=200):
    return Response(
        serializer(record, context={"request": request}).data,
        status=status,
        headers={"ETag": str(record.revision)},
    )


# Function: Unify sales API authentication and diagnosable errors.
# Logic: DRF session authentication retains CSRF; database conflicts map to 409.
# Constraints: Unknown runtime exceptions remain framework-handled and log their type, never becoming successful responses.
class SalesView(APIView):
    permission_classes = [IsAuthenticated]

    # Function: Convert expected query and argument exceptions.
    # Inputs: `exc`: view execution exception.
    # Outputs: DRF error response or propagation of unknown errors to the framework.
    # Logic: Missing records uniformly return 404, constraint races 409, and invalid types 400; logs contain only exception type and view.
    # Constraints: Never return SQL, tokens, or raw provider exception text.
    def handle_exception(self, exc):
        logger.warning(
            "sales_request_failed view=%s error_type=%s",
            type(self).__name__,
            type(exc).__name__,
        )
        if isinstance(exc, ObjectDoesNotExist):
            exc = NotFound("记录不存在或未授权。")
        elif isinstance(exc, IntegrityError):
            exc = Conflict("记录重复或关系约束冲突，请重新读取后处理。")
        elif isinstance(exc, DjangoValidationError):
            exc = ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            )
        elif isinstance(exc, (ValueError, TypeError, KeyError)):
            exc = ValidationError("参数缺失或格式错误，请检查字段契约。")
        return super().handle_exception(exc)


# Function: Provide queries and strict writes for allowlisted resources.
# Logic: Every request uses current-user scope; transactional services handle creation/updates.
# Constraints: No hard deletion or arbitrary model access; only approved tasks may trigger external execution.
class ResourceView(SalesView):
    # Function: Resolve a resource's serializer.
    # Inputs: `resource`: resource identifier in the URL.
    # Outputs: Concrete ModelSerializer class; unknown resources return 404.
    # Logic: Use a static allowlist without dynamically importing client-selected models.
    # Constraints: Catalog and actual validation use the same mapping.
    def serializer_type(self, resource):
        if resource not in SERIALIZERS:
            raise NotFound("业务资源不存在。")
        return SERIALIZERS[resource]

    # Function: Read one record or a paginated list.
    # Inputs: `request`, `resource`, and optional `record_id`.
    # Outputs: One record or a paginated response with results; unsupported filters produce request-language errors retaining original field names.
    # Logic: Signals/scores filter by opportunity; events/news support region, type, and timezone-aware windows. Other resources support actual relations, status, and archived filters. conversation_scope separates general/company conversations; archived records are excluded by default.
    # Constraints: Relation filters still pass through scope; arbitrary ORM query expressions are unsupported.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="sales_records_read")
    def get(self, request, resource, record_id=None):
        serializer = self.serializer_type(resource)
        query = scope(serializer.Meta.model, request.user)
        if record_id:
            return record_response(query.get(pk=record_id), serializer, request)
        names = {field.name for field in query.model._meta.fields}
        for name in ("company", "opportunity", "conversation", "quote", "order", "team", "status"):
            if name in request.query_params:
                if name not in names:
                    raise ValidationError(gettext("当前资源不支持 %(name)s 筛选。") % {"name": name})
                query = query.filter(**{name: request.query_params[name]})
        if "q" in request.query_params:
            search_fields = names.intersection(
                {"name", "title", "number", "sku", "subject", "description", "content"}
            )
            if not search_fields:
                raise ValidationError("当前资源不支持文本搜索。")
            condition = Q()
            for name in search_fields:
                condition |= Q(**{name + "__icontains": request.query_params["q"]})
            query = query.filter(condition)
        if "conversation_scope" in request.query_params:
            mode = request.query_params["conversation_scope"]
            if resource != "conversations" or mode not in {"general", "customer"}:
                raise ValidationError(
                    "conversation_scope 仅支持会话的 general/customer。"
                )
            query = query.filter(company__isnull=mode == "general")
        archived = request.query_params.get("archived", "false")
        if archived not in ("true", "false", "all"):
            raise ValidationError("archived 应为 true/false/all。")
        if archived != "all":
            query = query.filter(archived=archived == "true")
        query = query.order_by("created_at", "id")
        if resource in {"world-events", "world-news"}:
            from .insights import filter_insights
            query = filter_insights(query, request.query_params)
        rows, pagination = paged(query, request)
        return Response(
            {
                **pagination,
                "results": serializer(
                    rows, many=True, context={"request": request}
                ).data,
            }
        )

    # Function: Create a record or freeze an action awaiting confirmation.
    # Inputs: `request` JSON and `resource`; `record_id` only rejects creation on detail routes.
    # Outputs: 201 and the record; actions remain pending_confirmation.
    # Logic: Validate strict fields before ordinary saves; the dedicated action service validates the complete plan.
    # Constraints: Ordinary creation cannot fabricate connections, files, or reminders.
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses={201: OpenApiTypes.OBJECT},
        operation_id="sales_records_create",
    )
    def post(self, request, resource, record_id=None):
        if record_id:
            raise ValidationError("创建请求必须使用集合地址。")
        serializer_type = self.serializer_type(resource)
        if resource == "actions":
            record = actions.create_action(request.user, request.data)
        else:
            serializer = serializer_type(
                data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)
            record = services.save_record(serializer, request.user)
        return record_response(record, serializer_type, request, 201)

    # Function: Update business records against their old version.
    # Inputs: JSON/If-Match from `request`, plus `resource` and `record_id`.
    # Outputs: New record and ETag.
    # Logic: Locate within visible scope, then perform an authorized transaction and version check.
    # Constraints: Services reject fabricated ownership, states, roles, and frozen-document edits.
    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
        operation_id="sales_records_update",
    )
    def patch(self, request, resource, record_id=None):
        serializer_type = self.serializer_type(resource)
        record = scope(serializer_type.Meta.model, request.user).get(pk=record_id)
        serializer = serializer_type(
            record, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        return record_response(
            services.save_record(
                serializer, request.user, request.headers.get("If-Match")
            ),
            serializer_type,
            request,
        )


# Function: Distinguish the detail-query API contract.
# Logic: Reuse the same authorized query and declare a separate detail operationId.
# Constraints: This route rejects creation requests.
class ResourceDetailView(ResourceView):
    # Function: Read one authorized resource.
    # Inputs: `request`、`resource`、`record_id`.
    # Outputs: One record and ETag.
    # Logic: Delegate scope/type checks to ResourceView.
    # Constraints: Detail responses have no pagination wrapper.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="sales_record_detail")
    def get(self, request, resource, record_id):
        return super().get(request, resource, record_id)


# Function: Separate business state changes from ordinary field editing.
# Logic: A command allowlist explicitly selects services; all writes carry the old version.
# Constraints: Approval only enqueues actions; this endpoint neither sends Gmail nor creates events.
class CommandView(SalesView):
    # Function: Execute state, archival, approval, or notification-read commands.
    # Inputs: `request`: command/value and If-Match; `resource`; `record_id`.
    # Outputs: Record with updated version.
    # Logic: Dispatch by model/command. Notification reads use mode-specific ownership queries and row locks; experiment mode permits cross-account maintenance, then updates version and audit.
    # Constraints: Explicitly reject unknown commands or extra fields.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, resource, record_id):
        serializer = ResourceView().serializer_type(resource)
        record = scope(serializer.Meta.model, request.user).get(pk=record_id)
        if not isinstance(request.data, dict) or set(request.data) - {
            "command",
            "value",
        }:
            raise ValidationError("命令只接受 command 和 value。")
        command, value, expected = (
            request.data.get("command"),
            request.data.get("value"),
            request.headers.get("If-Match"),
        )
        if command == "archive":
            record = services.archive_record(record, request.user, expected, value)
        elif command == "transition":
            record = services.transition_record(record, request.user, expected, value)
        elif command == "decide" and resource == "actions":
            record = actions.decide_action(record, request.user, expected, value)
        elif command == "interrupted" and resource == "actions":
            record = actions.reconcile_action(record, request.user, expected)
        elif command == "verify" and resource == "actions":
            record = actions.verify_action(record, request.user, expected)
        elif command == "read" and resource == "notifications":
            with transaction.atomic():
                record = models.Notification.objects.select_for_update().get(
                    owner_scope(request.user), pk=record.pk
                )
                check_version(expected, record.revision)
                record.read_at, record.revision = timezone.now(), record.revision + 1
                record.save(update_fields=["read_at", "revision", "updated_at"])
                services.audit(request.user, record, "notification_read")
        else:
            raise ValidationError("该资源不支持所请求的命令。")
        return record_response(record, serializer, request)


# Function: Shared company directory and manual record creation.
# Logic: Separate from private email lists; query explicitly authorized companies only.
# Constraints: Do not reuse Agent private-context output.
class DirectoryView(SalesView):
    # Function: Paginate basic company information.
    # Inputs: `request`: optional q, archived, and pagination parameters.
    # Outputs: Company/contact directory.
    # Logic: Search names, domains, and contact emails; hide archived companies by default.
    # Constraints: Always search within current-user visible scope.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        query = Company.objects.filter(
            pk__in=visible_company_ids(request.user)
        ).order_by("id")
        if request.query_params.get("company"):
            query = query.filter(pk=request.query_params["company"])
        if request.query_params.get("archived", "false") != "all":
            query = query.exclude(business_settings__archived=True)
        if request.query_params.get("q"):
            value = request.query_params["q"]
            query = query.filter(
                Q(name__icontains=value)
                | Q(contacts__email__icontains=value)
                | Q(domains__icontains=value)
            ).distinct()
        rows, pagination = paged(query, request)
        return Response(
            {**pagination, "results": [grouping.directory_row(row) for row in rows]}
        )

    # Function: Manually create a company.
    # Inputs: `request`: name only.
    # Outputs: 201 company directory entry.
    # Logic: Create independent manual grouping and lifecycle settings.
    # Constraints: Do not infer domains from names or merge existing companies.
    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        if set(request.data) != {"name"}:
            raise ValidationError("新建客户只接受 name。")
        return Response(
            grouping.directory_row(
                grouping.create_company(request.user, request.data["name"])
            ),
            status=201,
        )


# Function: Provide manual contact identity editing.
# Logic: Protect the contact collection using the company version.
# Constraints: Never overwrite private historical email identity.
class ContactView(SalesView):
    # Function: Save a company contact.
    # Inputs: `request`: contact fields and If-Match; `company_id`.
    # Outputs: Contact and new company version.
    # Logic: Delegate company edit-permission and historical-identity checks to grouping transactions.
    # Constraints: contact-profiles separately maintains supplemental titles/phone numbers.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, company_id):
        return Response(
            grouping.save_contact(
                request.user, company_id, request.headers.get("If-Match"), request.data
            )
        )


# Function: Accept complete versioned manual grouping plans.
# Logic: Source and target must have the same owner.
# Constraints: Move explicitly selected emails only; merges preserve historical analyses.
class GroupingView(SalesView):
    # Function: Move emails or merge companies.
    # Inputs: `request`: source/target IDs and both versions, plus keys for moves; `operation`: move/merge.
    # Outputs: New company versions or merged directory entry.
    # Logic: Apply the exact field allowlist, then delegate to transactional services.
    # Constraints: Failures roll back the entire operation; no speculative merges.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, operation):
        required = {"source_id", "target_id", "source_revision", "target_revision"}
        if operation == "move":
            required.add("keys")
        if operation not in ("move", "merge") or set(request.data) != required:
            raise ValidationError("归组操作或参数不完整。")
        function = (
            grouping.move_emails if operation == "move" else grouping.merge_companies
        )
        return Response(function(request.user, **request.data))


# Function: Private attachment upload and forced download.
# Logic: Separate metadata from content paths; validate owner on every download.
# Constraints: No public media URLs or inline execution.
class FileView(SalesView):
    parser_classes = [MultiPartParser, FormParser]

    # Function: Receive multipart files.
    # Inputs: `request`: company and file; `record_id` must be absent on creation.
    # Outputs: 201 attachment metadata.
    # Logic: Check company visibility, then stream-save and audit.
    # Constraints: files.MAX_BYTES controls the per-file size limit.
    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, record_id=None):
        if record_id or set(request.data) != {"company", "file"}:
            raise ValidationError("上传只接受 company 和 file。")
        company = Company.objects.filter(pk__in=visible_company_ids(request.user)).get(
            pk=request.data["company"]
        )
        record = files.store_file(request.user, company, request.FILES.get("file"))
        return record_response(record, SERIALIZERS["files"], request, 201)

    # Function: Authenticated download of a selected attachment.
    # Inputs: `request`、`record_id`.
    # Outputs: attachment FileResponse.
    # Logic: Restrict by owner, open the randomly stored file, and set nosniff.
    # Constraints: Attachment downloads never expose internal directories.
    @extend_schema(responses=OpenApiTypes.BINARY)
    def get(self, request, record_id=None):
        record = scope(models.Attachment, request.user).get(pk=record_id)
        response = FileResponse(
            files.open_file(request.user, record),
            as_attachment=True,
            filename=record.name,
            content_type="application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


# Function: Expose explicit external-connection authorization entry points.
# Logic: The server fixes callback URLs; Session stores one-time state.
# Constraints: Successful authorization triggers no pending messages.
class OAuthView(SalesView):
    # Function: Generate a Google authorization URL.
    # Inputs: `request`: provider only.
    # Outputs: authorization_url.
    # Logic: Build the complete URI from the current server's named callback.
    # Constraints: Users must complete authorization on Google's page.
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        if set(request.data) != {"provider"}:
            raise ValidationError("仅接受 provider。")
        return Response(
            {
                "authorization_url": integrations.begin(
                    request,
                    request.data["provider"],
                    request.build_absolute_uri(reverse("sales-oauth")),
                )
            }
        )

    # Function: Save Google OAuth callback connections.
    # Inputs: code/state from `request` and the authenticated session.
    # Outputs: Redirect to the business management page.
    # Logic: Strictly validate state, exchange credentials, and encrypt them.
    # Constraints: Return explicit errors on failure; never put tokens in URLs.
    @extend_schema(responses={302: None})
    def get(self, request):
        integrations.finish(request)
        return redirect("/business/#connections")


# Function: Read append-only audits according to business permissions.
# Logic: Private-conversation audits are owner-visible only.
# Constraints: No modification or deletion endpoints.
class AuditView(SalesView):
    # Function: Paginate redacted audit metadata.
    # Inputs: `request`: optional company and pagination parameters.
    # Outputs: Event names, object IDs, actors, and field-name/state changes.
    # Logic: Reverse chronological order; every filter builds on scope.
    # Constraints: Never expose business bodies, recipients, or keys.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        query = scope(models.AuditEvent, request.user).order_by("-created_at", "-id")
        if request.query_params.get("company"):
            query = query.filter(company_id=request.query_params["company"])
        rows, pagination = paged(
            query.values(
                "id",
                "actor_id",
                "company_id",
                "event",
                "object_type",
                "object_id",
                "changes",
                "created_at",
            ),
            request,
        )
        return Response({**pagination, "results": rows})


# Function: Provide management pages with field contracts from the same source.
# Logic: Derive names, types, relations, and state edges from actual DRF fields; only display labels use request language.
# Constraints: Publish allowlisted fields only; never expose credentials/internal storage parameters as form fields.
class CatalogView(SalesView):
    # Function: Generate resource form metadata.
    # Inputs: `request`: current user for relation-field restrictions.
    # Outputs: Resources, request-language business names, field descriptions, and transitions; resource keys/choice values remain unchanged.
    # Logic: Distinguish text, numbers, booleans, relations, JSON, and time; required/read-only flags match actual validation.
    # Constraints: Fetch relation options separately through authorized APIs; transactions continue enforcing business rules.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        resources = []
        for key, serializer in SERIALIZERS.items():
            definitions = []
            for name, field in serializer(context={"request": request}).fields.items():
                kind = "text"
                relation = None
                if isinstance(field, fields.PrimaryKeyRelatedField):
                    kind = "relation"
                    relation = (
                        field.queryset.model._meta.model_name
                        if field.queryset is not None
                        else None
                    )
                elif isinstance(field, fields.ChoiceField):
                    kind = "choice"
                elif isinstance(
                    field, (fields.DecimalField, fields.IntegerField, fields.FloatField)
                ):
                    kind = "number"
                elif isinstance(field, fields.BooleanField):
                    kind = "boolean"
                elif isinstance(field, fields.DateTimeField):
                    kind = "datetime"
                elif isinstance(field, fields.DateField):
                    kind = "date"
                elif isinstance(field, fields.JSONField):
                    kind = "json"
                definitions.append(
                    {
                        "name": name,
                        "type": kind,
                        "relation": relation,
                        "required": field.required,
                        "readonly": field.read_only,
                        "nullable": field.allow_null,
                        "choices": (
                            list(field.choices) if hasattr(field, "choices") else []
                        ),
                    }
                )
            resources.append(
                {
                    "key": key,
                    "label": gettext(LABELS[key]),
                    "model": serializer.Meta.model._meta.model_name,
                    "fields": definitions,
                    "transitions": services.TRANSITIONS.get(serializer.Meta.model, {}),
                    "creatable": key
                    not in ("actions", "connections", "files", "notifications"),
                }
            )
        return Response({"resources": resources})


# Function: Compute the business overview within current authorization scope.
# Logic: Aggregate orders/opportunities by currency with explicit monetary semantics.
# Constraints: Never sum across currencies or treat draft orders as revenue.
class OverviewView(SalesView):
    # Function: Output document and pending-item summaries.
    # Inputs: Current user from `request`.
    # Outputs: Company, open-ticket, follow-up, and unread-reminder counts, plus currency amount lists.
    # Logic: Sum confirmed/fulfilled order net amounts; aggregate opportunities neither won nor lost separately.
    # Constraints: Statistics are not accounting revenue recognition; orders do not automatically reduce inventory.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        totals, pipeline = {}, {}
        for order in scope(models.SalesOrder, request.user).filter(
            archived=False, status__in=["confirmed", "fulfilled"]
        ):
            totals[order.currency] = totals.get(
                order.currency, Decimal("0.00")
            ) + Decimal(SERIALIZERS["orders"](order).data["total"])
        for item in (
            scope(models.Opportunity, request.user)
            .filter(archived=False)
            .exclude(status__in=["won", "lost"])
            .exclude(amount=None)
        ):
            pipeline[item.currency] = (
                pipeline.get(item.currency, Decimal("0.00")) + item.amount
            )
        return Response(
            {
                "customers": Company.objects.filter(
                    pk__in=visible_company_ids(request.user)
                )
                .exclude(business_settings__archived=True)
                .count(),
                "open_tickets": scope(models.Ticket, request.user)
                .filter(archived=False)
                .exclude(status__in=["resolved", "closed"])
                .count(),
                "open_follow_ups": scope(models.FollowUp, request.user)
                .filter(archived=False, status="open")
                .count(),
                "unread_notifications": models.Notification.objects.filter(
                    owner=request.user, read_at=None
                ).count(),
                "confirmed_order_net": {
                    key: str(value) for key, value in totals.items()
                },
                "open_opportunity_amount": {
                    key: str(value) for key, value in pipeline.items()
                },
            }
        )


# Function: Support exact account lookup for team invitations.
# Logic: Default to the current user/common team members; invitations require complete usernames, without enumerating all users.
# Constraints: Adding members still requires team-management permission checks.
class PeopleView(SalesView):
    # Function: Locate invitable accounts by username.
    # Inputs: username query parameter from `request`.
    # Outputs: IDs/usernames of authorized collaborators or exact matches, excluding email addresses and administrative status.
    # Logic: Default to the current user/common team members; supplied usernames match exactly.
    # Constraints: Do not create accounts or send invitations.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        query = get_user_model().objects.filter(is_active=True)
        if "username" in request.query_params:
            query = query.filter(username=request.query_params["username"])
        else:
            teams = scope(models.Team, request.user).filter(archived=False)
            query = query.filter(
                Q(pk=request.user.pk)
                | Q(
                    sales_memberships__team__in=teams, sales_memberships__archived=False
                )
                | Q(pk__in=teams.values("owner_id"))
            ).distinct()
        return Response({"results": list(query.values("id", "username"))})


# Function: Expose authenticated calendar read APIs.
# Logic: Always restrict connection IDs to the current owner; queries bypass the action execution queue.
# Constraints: Event creation must use ToolAction.
class CalendarView(SalesView):
    # Function: Read events or free/busy within explicit time ranges.
    # Inputs: Query parameters from `request` and the `operation` path selection.
    # Outputs: Read-only calendar results or controlled errors.
    # Logic: Delegate time/connection validation and a single query to the calendar module.
    # Constraints: No calendar invitations or automatic pagination.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, operation):
        return Response(
            calendar.read_calendar(request.user, operation, request.query_params.dict())
        )
