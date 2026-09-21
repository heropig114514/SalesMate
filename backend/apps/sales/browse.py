"""职责：在现有销售页面合并展示原授权业务与获准共享的实验记录。
实现：个人空间隔离时不合并实验批次；先校验精确批次清单，普通查询排除同批主键后分页合并；共享行直接投影校验结果。
关联：business.js 使用本只读入口；原 views、permissions 及写接口不扩权；experiments 提供完整来源详情。
目录：
- shared_records：加载并投影指定业务资源的获准实验行。
- ordinary_query：取得原权限下且符合页面筛选的查询集。
- matches：对校验后的实验投影应用相同页面筛选。
- BrowseView：合并列表只读接口。
- BrowseView.get：分页合并普通记录与实验记录并标记来源。
- BrowseOverviewView：合并计数且分离实验提示的概览入口。
- BrowseOverviewView.get：在原计数上仅增加此前不可见的实验记录。
变量索引：
- RESOURCES：业务资源到实验模型的固定映射，不含凭据或外部连接。
- logger：记录读取者、资源及数量，不记录正文。
- BrowseView.http_method_names：只接受读取方法。
- BrowseOverviewView.http_method_names：只接受读取方法。
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


# 功能：读取业务页面对应的合成记录。
# 输入：`resource` 为 RESOURCES 中的业务资源键。
# 输出：带 id、experiment 元数据及业务字段的列表。
# 逻辑：个人隔离时没有共享行；其余逐批校验原行，关系字段转换为界面字段名；客户联系人和归档设置也仅取同清单记录。
# 约束：不调用含反向关系的业务序列化器处理共享行，避免顺带读取清单外明细或联系人。
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


# 功能：生成原业务范围内的列表查询。
# 输入：`user` 已认证用户、`resource` 业务资源、`params` 已限定的页面查询参数。
# 输出：保留原业务权限的 QuerySet。
# 逻辑：客户沿用 visible_company_ids，其余沿用 scope；只支持页面实际使用的公司、状态与归档筛选。
# 约束：此函数不增加跨账号权限；筛选未知字段明确报错，不接受任意 ORM 查询。
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


# 功能：对共享行使用与业务查询相同的页面筛选。
# 输入：`row` 已核验业务投影、`resource` 资源键、`params` 页面参数。
# 输出：是否应在当前列表出现的布尔值。
# 逻辑：比较精确客户 ID、状态及归档；不展开关联或做名称模糊匹配。
# 约束：字段支持情况由 ordinary_query 先验证，缺省归档状态为 false。
def matches(row, resource, params):
    if params.get("company") and str(row["id"] if resource == "directory" else row.get("company")) != params["company"]:
        return False
    if params.get("status") and row.get("status") != params["status"]:
        return False
    archived = params.get("archived", "false")
    return archived == "all" or bool(row.get("archived", False)) == (archived == "true")


# 功能：提供现有业务页面的合并读取。
# 逻辑：普通数据保留原处理器序列化，共享数据只来自已核验投影，两者主键去重。
# 约束：没有写方法；普通详情和写 API 权限保持原状。
class BrowseView(SalesView):
    http_method_names = ["get", "head", "options"]

    # 功能：分页返回原业务及合成实验记录。
    # 输入：`request` 的认证用户及页面筛选，`resource` 固定资源名。
    # 输出：count、page、page_size、shared_count、results；共享记录带 experiment 标记。
    # 逻辑：最外层开启只读一致性快照；先普通后共享，并按主键排除重复；错误不降级成空共享数据。
    # 约束：最多每页 100 条；任何已共享表漂移则整次失败，不调用模型或写业务记录。
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


# 功能：为合并列表提供对应计数。
# 逻辑：只补充原权限下不可见的实验客户与待办计数，金额保持原业务范围并明确标记。
# 约束：不把共享模拟交易金额加入普通业务金额。
class BrowseOverviewView(SalesView):
    http_method_names = ["get", "head", "options"]

    # 功能：生成与合并列表一致的概览。
    # 输入：`request` 当前认证用户。
    # 输出：原概览和 shared_counts；客户、工单、跟进计数已包含共享行且去重。
    # 逻辑：对三类已核验记录应用原状态条件，与原 scope 比较后仅补新增数量。
    # 约束：无业务写入；共享表漂移时明确失败，不继续展示零值。
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
