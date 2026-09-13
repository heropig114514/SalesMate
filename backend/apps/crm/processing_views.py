"""职责：提供员工同步进度、明确重试及邮件人工复核接口。
实现：Session 身份限定邮箱 owner，复核使用 If-Match 避免覆盖并发判断。
关联：urls 注册显式路径，processing 和 classification 承担数据库事务。
目录：
- ReviewRequestSerializer：声明人工确认载荷。
- SyncRunView：查询或明确重试一个同步批次。
- SyncRunView.get：返回批次整体进度。
- SyncRunView.post：明确重试失败邮件。
- EmailReviewsView：查询待复核和已隐藏邮件。
- EmailReviewsView.get：按员工和可选邮箱分页返回原文证据。
- EmailReviewView：保存一封邮件的人工决定。
- EmailReviewView.patch：验证版本并持久化人工确认。
变量索引：
- ReviewRequestSerializer.review_status：两个允许的人工决定。
- OBJECT：OpenAPI 通用对象表示。
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers

from .access import mailbox_for
from .classification import review_data, review_email
from .models import Email, MailboxSyncRun
from .processing import retry_run, run_data
from .serializers import StrictSerializer

OBJECT = OpenApiTypes.OBJECT


# 功能：声明明确的人工分类决定。
# 逻辑：只允许确认业务或非业务，不接受自报身份和任意模型字段。
# 约束：邮箱权限及版本由事务服务校验。
class ReviewRequestSerializer(StrictSerializer):
    review_status = serializers.ChoiceField(choices=["confirmed_business", "confirmed_non_business"])


# 功能：提供独立于公司分页的批次状态。
# 逻辑：仅查询当前员工邮箱的批次，POST 显式重试。
# 约束：不返回授权或租约凭证。
class SyncRunView(APIView):
    # 功能：查询同步和画像整体进度。
    # 输入：`request` 为员工会话，`run_id` 为批次 UUID。
    # 输出：批次计数和逐封安全错误。
    # 逻辑：owner 限定后调用派生统计。
    # 约束：不存在与越权均返回 404，无写入副作用。
    @extend_schema(responses=OBJECT, tags=["processing"])
    def get(self, request, run_id):
        run = MailboxSyncRun.objects.filter(pk=run_id, mailbox__owner=request.user).first()
        if run is None:
            raise NotFound("批次不存在。")
        return Response(run_data(run))

    # 功能：明确重试失败邮件。
    # 输入：`request` 为员工会话，`run_id` 为失败批次 UUID。
    # 输出：HTTP 202 和新的排队批次。
    # 逻辑：复用原消息 ID，保留旧失败记录。
    # 约束：非失败批次或存在活动同步时拒绝，不启动 Web 线程。
    @extend_schema(request=None, responses={202: OBJECT}, tags=["processing"])
    def post(self, request, run_id):
        return Response(run_data(retry_run(request.user, run_id)), status=202)


# 功能：提供人工复核分页列表。
# 逻辑：默认 needs_review，可查询规则隐藏邮件以便纠错。
# 约束：员工仅访问自己邮箱的原文和证据。
class EmailReviewsView(APIView):
    # 功能：按邮箱和状态列出可复核邮件。
    # 输入：`request` 可带 status/page，`mailbox_id` 可限定一个邮箱。
    # 输出：最多 20 项、总数和待复核数量。
    # 逻辑：先授权后筛选，all 包含机器隐藏及已有人工判断。
    # 约束：不解析正文为 HTML；非法状态或分页返回 400。
    @extend_schema(responses=OBJECT, tags=["processing"], parameters=[OpenApiParameter("status", str), OpenApiParameter("page", int)])
    def get(self, request, mailbox_id=None):
        query = Email.objects.filter(mailbox__owner=request.user)
        if mailbox_id:
            query = query.filter(mailbox=mailbox_for(request.user, mailbox_id))
        pending_count = query.filter(business_classification="needs_review").count()
        status = request.query_params.get("status", "pending")
        if status == "pending":
            query = query.filter(business_classification="needs_review")
        elif status == "non_business":
            query = query.filter(business_classification="non_business")
        elif status == "all":
            query = query.exclude(business_classification="business", review_status="")
        else:
            raise ValidationError("status 必须是 pending、non_business 或 all。")
        try:
            page = int(request.query_params.get("page", 1))
        except (ValueError, TypeError):
            raise ValidationError("page 必须为正整数。") from None
        if page < 1:
            raise ValidationError("page 必须为正整数。")
        count = query.count()
        items = query.order_by("-received_at", "dedupe_key")[(page - 1) * 20:page * 20]
        return Response({"results": [review_data(item) for item in items], "count": count, "pending_count": pending_count, "page": page, "page_size": 20})


# 功能：处理版本化的人工决定。
# 逻辑：序列化器白名单与事务层权限双重校验。
# 约束：遵循 Session/CSRF，不调用 Gmail 或 LLM。
class EmailReviewView(APIView):
    # 功能：确认业务或非业务邮件。
    # 输入：`request` 含 review_status 和 If-Match，`email_id` 为完整去重键。
    # 输出：新复核版本及决定。
    # 逻辑：保存人工优先结果，按需求触发或禁止画像。
    # 约束：并发冲突返回 409，未知邮件或越权返回 404。
    @extend_schema(request=ReviewRequestSerializer, responses=OBJECT, tags=["processing"], parameters=[OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True)])
    def patch(self, request, email_id):
        data = ReviewRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(review_email(request.user, email_id, data.validated_data["review_status"], request.headers.get("If-Match")))
