"""职责：将已授权工具绑定到现有业务处理器。
实现：使用显式方法白名单和最小请求上下文；资料及文件委托 support，共享批次委托 experiments，保留既有序列化、scope、事务、版本和状态机。
关联：services 完成工具认证与输入验证后调用；这里不触发 DRF 二次认证、不构造网络回环。
目录：
- request_context：建立限定业务上下文。
- execute：分派固定工具。
变量索引：
- 无
"""

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


# 功能：构造业务处理器所需上下文。
# 输入：`actor` 用户、`query` 查询字典、`data` JSON、`revision` 旧版本。
# 输出：最小请求对象。
# 逻辑：只包含经工具 Schema 校验的参数及真实 actor。
# 约束：不模拟 Session、不允许用户传入 HTTP URL、请求头或认证状态；仅供下列固定处理器。
def request_context(actor, query=None, data=None, revision=None):
    params = QueryDict(mutable=True)
    params.update({key: str(value) for key, value in (query or {}).items()})
    return SimpleNamespace(
        user=actor,
        query_params=params,
        data=data or {},
        headers={} if revision is None else {"If-Match": str(revision)},
    )


# 功能：执行业务适配。
# 输入：`actor`、`spec` 白名单声明、`args` 校验后参数、`key` 可选幂等 UUID。
# 输出：既有 Response。
# 逻辑：experiment 委托精确批次读取，support_* 委托资料与文件工具，记录读写复用原处理器，证据查询限定 owner。
# 约束：调用前必须由 services 认证与校验；不分派任意路径、任意方法或动作批准。
def execute(actor, spec, args, key=None):
    kind = spec["kind"]
    request = request_context(actor, args, args.get("data"), args.get("revision"))
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
        # 服务模块加载完毕后才按需取得投影函数，避免 dispatch 与 services 的模块初始化环。
        from .models import ToolProposal
        from .services import proposal_data

        return Response(
            proposal_data(ToolProposal.objects.get(pk=args["id"], owner=actor))
        )
    if kind in {"knowledge_search", "knowledge_get"}:
        query = KnowledgeEntry.objects.filter(owner=actor, active=True).order_by(
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
