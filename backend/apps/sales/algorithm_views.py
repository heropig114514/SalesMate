"""职责：提供算法的资料上下文及商机评分展示查询。
实现：复用原资料序列化和权限，原样提供商机、信号和评分；没有调用模型或更改评分规则。
关联：算法可通过 MCP 的固定工具调用；前端商机优先级页读取分页结果。
目录：
- seller_context：汇总本人基础资料及版本。
- SellerContextView：基础资料读取。
- SellerContextView.get：返回当前身份资料。
- OpportunityContextView：读取单条商机及关联业务资料。
- OpportunityContextView.get：返回商机输入、信号和结果。
- PriorityBoardView：商机级优先级列表。
- PriorityBoardView.get：按最新结果排序并分页。
变量索引：
- logger：上下文与结果查询诊断日志。
"""

import logging
from django.db.models import OuterRef, Subquery, F
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from apps.accounts.onboarding import snapshot
from apps.accounts.models import CompanyProfile
from apps.accounts.company_profile import CompanyProfileSerializer
from apps.crm.selectors import context_pair
from common.laboratory import enabled
from .models import Opportunity, OpportunitySignal, OpportunityPriority, SellerProfile, Product
from .serializers import OpportunitySerializer, OpportunitySignalSerializer, OpportunityPrioritySerializer, ProductSerializer
from .permissions import scope
from .priority import ACTIVE_STAGES, historical_orders
from .views import SalesView, paged

logger = logging.getLogger("salesmate.algorithm_support")


# 功能：汇总可供算法使用的销售方资料。
# 输入：`user` 为所选业务身份，`request` 提供序列化上下文。
# 输出：公司、个人、参考产品、方案、交易产品及卖方目标资料。
# 逻辑：读原始资料，保留各自 revision；不存在时使用未保存对象。
# 约束：不把参考目录隐式同步到交易目录，不读取其他身份的个人资料。
def seller_context(user, request):
    company = CompanyProfile.objects.filter(owner=user).first() or CompanyProfile(owner=user)
    seller = SellerProfile.objects.filter(owner=user).first()
    return {"company_profile": CompanyProfileSerializer(company).data, "sales_setup": snapshot(user), "seller_profile": {"profile": seller.profile if seller else {}, "revision": seller.revision if seller else 0}, "products": ProductSerializer(Product.objects.filter(owner=user, archived=False), many=True, context={"request": request}).data}


# 功能：查询算法可使用的本人背景资料。
# 逻辑：所选身份来自会话或实验身份，不接受任意 owner 参数。
# 约束：只读，无自动补全。
class SellerContextView(SalesView):
    # 功能：返回统一资料快照。
    # 输入：`request` 的业务身份。
    # 输出：基础资料与各自版本。
    # 逻辑：复用 seller_context，不写库。
    # 约束：附件通过既有私有文件接口读取。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(seller_context(request.user, request))


# 功能：构建单条商机的算法输入。
# 逻辑：商机、信号、结果均经过业务权限；私人邮件另校验 owner。
# 约束：不把其他商机金额合并进当前商机。
class OpportunityContextView(SalesView):
    # 功能：读取上下文。
    # 输入：`request` 和 `opportunity_id`。
    # 输出：商机、客户、原始业务邮件、销售方资料、历史订单、信号及最近评分。
    # 逻辑：按权威商机归属选择销售方；正式共享用户无法读取 owner 私人背景和邮件。
    # 约束：非 owner 的正式访问返回 404；实验模式允许跨账号，日志仅记标识。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, opportunity_id):
        query = scope(Opportunity, request.user).filter(archived=False).select_related("company", "owner")
        if not enabled():
            query = query.filter(owner=request.user)
        opportunity = get_object_or_404(query, pk=opportunity_id)
        _, context = context_pair(opportunity.company, include_priority=False)
        serializer_context = {"request": request}
        signals = scope(OpportunitySignal, request.user).filter(opportunity=opportunity, archived=False).order_by("detected_at", "id")
        latest = scope(OpportunityPriority, request.user).filter(opportunity=opportunity, archived=False).order_by("-scored_at", "-created_at", "-id").first()
        logger.info("opportunity_context_read actor_id=%s opportunity_id=%s", request.user.pk, opportunity.pk)
        return Response({"opportunity": OpportunitySerializer(opportunity, context=serializer_context).data, "company": {"id": str(opportunity.company_id), "name": opportunity.company.name}, "customer_context": context, "seller_context": seller_context(opportunity.owner, request), "historical_orders": historical_orders(opportunity.owner_id), "signals": OpportunitySignalSerializer(signals, many=True, context=serializer_context).data, "priority": OpportunityPrioritySerializer(latest, context=serializer_context).data if latest else None})


# 功能：提供商机级优先级展示。
# 逻辑：最新评分只读取存储结果，空分在最后。
# 约束：不回退到公司级分数，不重算权重。
class PriorityBoardView(SalesView):
    # 功能：分页读取当前活跃商机。
    # 输入：`request` 的 page/page_size、q 和可选 company。
    # 输出：每条商机、客户名称、最新评分及解释。
    # 逻辑：通过关联子查询按 scored_at 选最新记录，按分数降序；同分按商机 ID 稳定排序。
    # 约束：虚拟来源原样返回，前端须显示；未知条件不影响数据库权限。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        latest = scope(OpportunityPriority, request.user).filter(opportunity_id=OuterRef("pk"), archived=False).order_by("-scored_at", "-created_at", "-id")
        query = scope(Opportunity, request.user).filter(archived=False, status__in=ACTIVE_STAGES).select_related("company")
        if request.query_params.get("company"):
            query = query.filter(company_id=request.query_params["company"])
        if request.query_params.get("q"):
            query = query.filter(title__icontains=request.query_params["q"])
        query = query.annotate(current_priority_id=Subquery(latest.values("id")[:1]), current_score=Subquery(latest.values("priority_score")[:1])).order_by(F("current_score").desc(nulls_last=True), "id")
        rows, pagination = paged(query, request)
        priorities = {row.pk: row for row in scope(OpportunityPriority, request.user).filter(pk__in=[row.current_priority_id for row in rows if row.current_priority_id])}
        result = []
        for row in rows:
            priority = priorities.get(row.current_priority_id)
            result.append({"opportunity": OpportunitySerializer(row, context={"request": request}).data, "company_name": row.company.name, "priority": OpportunityPrioritySerializer(priority, context={"request": request}).data if priority else None})
        return Response({**pagination, "results": result})
