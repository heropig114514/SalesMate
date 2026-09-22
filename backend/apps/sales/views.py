"""职责：提供销售关系记录、客户归组、附件及外部动作的会话认证 API。
实现：商机信号/评分通过通用 CRUD 接收算法结果；实验模式默认认证提供公开身份，通知命令允许跨账号维护；活动资讯沿用版本化记录接口，新增地区与时间筛选；资源白名单选择严格序列化器；写入委托授权事务，异常统一输出且不暴露凭证。
关联：catalog 为管理页提供字段契约，services/grouping/actions/files 实现业务边界。
目录：
- ResourceDetailView：单条资源查询路由。
- ResourceDetailView.get：读取单条记录。
- SalesView：统一认证和安全错误边界。
- SalesView.handle_exception：转换数据库及参数错误。
- ResourceView：记录集合和单条查询写入。
- ResourceView.serializer_type：解析白名单资源。
- ResourceView.get：授权查询并分页。
- ResourceView.post：创建关系记录或待确认动作。
- ResourceView.patch：版本化修改记录。
- CommandView：显式状态、归档、审批和通知已读。
- CommandView.post：执行已授权命令。
- DirectoryView：不含邮件的客户目录。
- DirectoryView.get：分页查询客户和联系人。
- DirectoryView.post：新增客户。
- ContactView：人工联系人写入。
- ContactView.post：新增或修改联系人身份。
- GroupingView：显式公司合并和邮件搬移。
- GroupingView.post：校验版本后执行归组。
- FileView：附件上传下载。
- FileView.post：处理 multipart 上传。
- FileView.get：认证下载附件。
- OAuthView：独立 Google 写权限授权流程。
- OAuthView.post：生成授权跳转链接。
- OAuthView.get：处理单次授权回调。
- AuditView：授权审计读取。
- AuditView.get：分页读取脱敏审计。
- CatalogView：管理页面的字段及状态元数据。
- CatalogView.get：生成业务表单契约。
- OverviewView：按币种的销售汇总。
- OverviewView.get：统计授权业务及个人待办。
- PeopleView：团队邀请所需账号查找。
- PeopleView.get：按完整用户名查找有效账号。
- CalendarView：受保护的日历只读查询。
- CalendarView.get：读取事件或忙闲。
- paged：执行受限分页。
- record_response：生成记录及 ETag。
变量索引：
- logger：脱敏接口异常日志。
- LABELS：资源对应中文源文案；响应时按请求语言翻译，不改变资源键。
- SalesView.permission_classes：必须登录。
- FileView.parser_classes：附件 multipart 及表单解析器。
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


# 功能：执行受限分页。
# 输入：`query` 为已授权查询集，`request` 提供 page 和 page_size。
# 输出：本页记录与 count/page/page_size。
# 逻辑：页码从 1 开始，默认每页 30 条，上限 100 条。
# 约束：无效页码显式报错，不静默截断或遍历全部记录。
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


# 功能：生成版本化记录响应。
# 输入：`record`、`serializer` 类、`request`、`status` 默认 200。
# 输出：Response，其 ETag 为十进制 revision。
# 逻辑：沿用严格序列化字段白名单；写入时客户端回传 If-Match。
# 约束：不输出模型内部存储键或凭证。
def record_response(record, serializer, request, status=200):
    return Response(
        serializer(record, context={"request": request}).data,
        status=status,
        headers={"ETag": str(record.revision)},
    )


# 功能：统一销售 API 的认证和可诊断错误。
# 逻辑：DRF 会话认证保留 CSRF，数据库冲突映射为 409。
# 约束：未知运行异常仍按框架处理并记录类型，不以成功响应吞掉异常。
class SalesView(APIView):
    permission_classes = [IsAuthenticated]

    # 功能：转换可预期的查询及参数异常。
    # 输入：`exc` 为视图执行异常。
    # 输出：DRF 错误响应或向框架传播未知错误。
    # 逻辑：不存在统一 404，约束竞争 409，无效类型 400；日志仅含异常类型与视图。
    # 约束：不返回 SQL、令牌或 provider 异常原文。
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


# 功能：提供白名单资源的查询和严格写入。
# 逻辑：所有请求基于当前用户 scope，创建及修改交给事务服务。
# 约束：不提供硬删除或任意模型访问；外部执行只能由已批准任务触发。
class ResourceView(SalesView):
    # 功能：解析资源对应的序列化器。
    # 输入：`resource` 为 URL 中的资源标识。
    # 输出：具体 ModelSerializer 类；未知资源 404。
    # 逻辑：使用静态白名单，不动态导入客户端指定模型。
    # 约束：同一映射用于目录和实际校验。
    def serializer_type(self, resource):
        if resource not in SERIALIZERS:
            raise NotFound("业务资源不存在。")
        return SERIALIZERS[resource]

    # 功能：读取单条或分页列表。
    # 输入：`request`、`resource`、可选 `record_id`。
    # 输出：单条记录或含 results 的分页响应；不支持的字段筛选按请求语言报错，保留原字段名。
    # 逻辑：信号及评分可按 opportunity 筛选；活动资讯支持地区、类型与带时区窗口；其他资源支持实际关系、status、archived 过滤；会话可按 conversation_scope 分离通用和客户记录，默认排除归档。
    # 约束：关联过滤仍经过 scope；不支持 arbitrary ORM 查询表达式。
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

    # 功能：创建记录或冻结待确认动作。
    # 输入：`request` JSON，`resource`；`record_id` 仅用于拒绝向详情创建。
    # 输出：201 及记录；动作仍为 pending_confirmation。
    # 逻辑：普通资源校验严格字段后保存，动作专用服务校验完整计划。
    # 约束：连接、文件和提醒不能通过普通创建伪造。
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

    # 功能：按旧版本修改业务记录。
    # 输入：`request` 的 JSON/If-Match，`resource`、`record_id`。
    # 输出：新记录和新 ETag。
    # 逻辑：先按可见范围定位再执行授权事务及版本检查。
    # 约束：归属、状态、角色伪造和冻结单据修改均被服务拒绝。
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


# 功能：区分详情查询的 API 契约。
# 逻辑：沿用同一授权查询，独立声明详情 operationId。
# 约束：此路由不接受创建请求。
class ResourceDetailView(ResourceView):
    # 功能：读取一个已授权资源。
    # 输入：`request`、`resource`、`record_id`。
    # 输出：单条记录和 ETag。
    # 逻辑：委托 ResourceView 进行范围和类型检查。
    # 约束：详情的响应不包装分页。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="sales_record_detail")
    def get(self, request, resource, record_id):
        return super().get(request, resource, record_id)


# 功能：将业务状态变化与一般字段编辑分开。
# 逻辑：命令白名单显式选择服务，所有写入携带旧版本。
# 约束：批准动作只入队；本接口不会发送 Gmail 或创建事件。
class CommandView(SalesView):
    # 功能：执行状态、归档、审批或通知已读命令。
    # 输入：`request` 含 command/value 和 If-Match，`resource`、`record_id`。
    # 输出：版本更新后的记录。
    # 逻辑：按模型和命令分派；通知已读通过模式对应的归属查询并锁行，实验模式可跨账号，修改版本并写审计。
    # 约束：未知命令或多余字段明确拒绝。
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


# 功能：共享客户目录与人工建档。
# 逻辑：与私人邮件列表分离，仅查询明确授权公司。
# 约束：不复用 Agent 私有上下文输出。
class DirectoryView(SalesView):
    # 功能：分页返回客户基础信息。
    # 输入：`request` 含可选 q、archived 和分页参数。
    # 输出：客户及联系人目录。
    # 逻辑：名称/域名/联系人邮箱搜索，默认隐藏已归档客户。
    # 约束：搜索始终在当前用户可见范围内执行。
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

    # 功能：人工创建客户。
    # 输入：`request` 仅含 name。
    # 输出：201 客户目录项。
    # 逻辑：新建独立人工分组和生命周期设置。
    # 约束：不根据姓名推断域名或合并已有客户。
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


# 功能：提供人工联系人身份修改。
# 逻辑：使用公司版本保护联系人集合。
# 约束：私人历史邮箱身份不被覆盖。
class ContactView(SalesView):
    # 功能：保存客户的联系人。
    # 输入：`request` 含联系人字段及 If-Match，`company_id`。
    # 输出：联系人及公司新版本。
    # 逻辑：委托归组事务验证公司编辑权及历史身份。
    # 约束：补充职位电话另由 contact-profiles 接口维护。
    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, company_id):
        return Response(
            grouping.save_contact(
                request.user, company_id, request.headers.get("If-Match"), request.data
            )
        )


# 功能：接收完整且版本化的人工归组计划。
# 逻辑：源和目标都必须为同一所有者。
# 约束：只有明确选择的邮件被移动，合并保留历史分析。
class GroupingView(SalesView):
    # 功能：执行邮件移动或公司合并。
    # 输入：`request` 包含源目标 ID、两版本，移动时另需 keys；`operation` 为 move/merge。
    # 输出：公司新版本或合并后目录。
    # 逻辑：精确字段白名单后委托事务服务。
    # 约束：失败时整次操作回滚，不进行猜测性合并。
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


# 功能：私有附件上传和强制下载。
# 逻辑：元数据与内容路径分离，任何下载都验证 owner。
# 约束：无公共媒体 URL，无内联执行。
class FileView(SalesView):
    parser_classes = [MultiPartParser, FormParser]

    # 功能：接收 multipart 文件。
    # 输入：`request` 含 company、file，`record_id` 创建时须为空。
    # 输出：201 附件元数据。
    # 逻辑：检查客户可见性后流式保存并审计。
    # 约束：单文件大小上限由 files.MAX_BYTES 控制。
    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, record_id=None):
        if record_id or set(request.data) != {"company", "file"}:
            raise ValidationError("上传只接受 company 和 file。")
        company = Company.objects.filter(pk__in=visible_company_ids(request.user)).get(
            pk=request.data["company"]
        )
        record = files.store_file(request.user, company, request.FILES.get("file"))
        return record_response(record, SERIALIZERS["files"], request, 201)

    # 功能：认证下载指定附件。
    # 输入：`request`、`record_id`。
    # 输出：attachment FileResponse。
    # 逻辑：限定 owner 后打开随机存储文件，并设置 nosniff。
    # 约束：附件下载不暴露内部目录。
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


# 功能：公开显式的外部连接授权入口。
# 逻辑：服务器固定回调，Session 保存单次 state。
# 约束：授权成功不会触发任何待发消息。
class OAuthView(SalesView):
    # 功能：生成 Google 授权地址。
    # 输入：`request` 只接受 provider。
    # 输出：authorization_url。
    # 逻辑：使用当前服务器的命名回调构造完整 URI。
    # 约束：用户必须在 Google 页面完成授权。
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

    # 功能：保存 Google OAuth 回调连接。
    # 输入：`request` 的 code/state 与已登录会话。
    # 输出：重定向至业务管理页。
    # 逻辑：严格验证 state、交换并加密凭证。
    # 约束：失败明确返回错误，不将令牌放入 URL。
    @extend_schema(responses={302: None})
    def get(self, request):
        integrations.finish(request)
        return redirect("/business/#connections")


# 功能：按业务权限读取追加式审计。
# 逻辑：私人会话审计仅对 owner 可见。
# 约束：没有修改或删除入口。
class AuditView(SalesView):
    # 功能：分页输出脱敏审计元数据。
    # 输入：`request` 可选 company 与分页参数。
    # 输出：事件名、对象 ID、操作者及字段名/状态变更。
    # 逻辑：按时间倒序，所有过滤建立在 scope 上。
    # 约束：不输出业务正文、收件人或密钥。
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


# 功能：为管理页提供同源字段契约。
# 逻辑：从实际 DRF 字段生成名称、类型、关系和状态边；仅显示标签使用请求语言。
# 约束：只发布白名单字段，不把凭证或内部存储参数暴露为表单。
class CatalogView(SalesView):
    # 功能：生成资源表单元数据。
    # 输入：`request` 提供当前用户以限制关系字段。
    # 输出：资源、按请求语言翻译的业务名称、字段说明与状态转换；资源键及选项值不变。
    # 逻辑：区分文本、数值、布尔、关系、JSON 和时间；必填和只读与实际校验一致。
    # 约束：关系选项从授权接口单独读取；业务规则继续由事务执行。
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


# 功能：计算当前授权范围的业务概况。
# 逻辑：按币种汇总订单及商机，保持金额语义明确。
# 约束：不跨币种求和，不把草稿订单当收入。
class OverviewView(SalesView):
    # 功能：输出单据和待办汇总。
    # 输入：`request` 的当前用户。
    # 输出：客户数、开放工单、跟进数、未读提醒及币种金额列表。
    # 逻辑：确认/履约订单求净额，未赢单未丢单商机单独汇总。
    # 约束：统计不是会计收入确认，库存不随订单自动扣减。
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


# 功能：支持团队邀请所需的精确账号查找。
# 逻辑：默认返回本人及共同团队成员；邀请时只接受完整用户名，不提供全体用户枚举。
# 约束：实际添加成员仍由团队管理权限检查。
class PeopleView(SalesView):
    # 功能：按用户名定位可邀请账号。
    # 输入：`request` 的 username 查询参数。
    # 输出：授权协作账号或精确匹配的账号 ID、用户名，不含邮箱或管理状态。
    # 逻辑：默认列出本人和共同团队成员；用户名参数采用精确匹配。
    # 约束：不创建账号，不发送邀请消息。
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


# 功能：公开受登录保护的日历读取接口。
# 逻辑：连接 ID 始终限定当前 owner，查询不经过动作执行队列。
# 约束：创建事件只能使用 ToolAction。
class CalendarView(SalesView):
    # 功能：按明确时间范围读取事件或忙闲。
    # 输入：`request` 查询参数和 `operation` 路径选择。
    # 输出：日历只读结果或受控错误。
    # 逻辑：委托 calendar 模块验证时间和连接再执行单次查询。
    # 约束：不发送日历邀请，不自动翻页。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, operation):
        return Response(
            calendar.read_calendar(request.user, operation, request.query_params.dict())
        )
