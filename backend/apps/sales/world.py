"""职责：提供全球洞察的数据库查询与地图业务聚合。
实现：活动关联金额按商机主键去重、分币种累加；国家高亮来自客户资料，不生成推荐。
关联：world-news.js 消费本视图；活动与资讯仍通过通用 CRUD 和 MCP 维护。
目录：
- country_code：规范已知国家名称或代码。
- amounts：统计有效商机金额。
- WorldView：返回分页活动及全局国家统计。
- WorldView.get：读取可见记录并投影地图数据。
变量索引：
- COUNTRY_CODES：已有客户常用国家名称与 ISO 代码的明确映射。
"""

from decimal import Decimal
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from apps.crm.models import Company
from .models import WorldEvent, Opportunity, CompanySettings
from .permissions import scope, visible_company_ids
from .priority import ACTIVE_STAGES
from .insights import filter_insights
from .serializers import WorldEventSerializer
from .views import SalesView, paged

COUNTRY_CODES = {"singapore": "SG", "新加坡": "SG", "china": "CN", "中国": "CN", "taiwan": "TW", "台湾": "TW", "germany": "DE", "德国": "DE", "japan": "JP", "日本": "JP", "united states": "US", "美国": "US", "south korea": "KR", "韩国": "KR", "united kingdom": "GB", "英国": "GB"}


# 功能：规范权威国家字段。
# 输入：`value` 为客户资料中的名称或 ISO 代码。
# 输出：两字母代码，无法明确映射返回空字符串。
# 逻辑：只匹配明确映射，不根据地址或邮箱猜测。
# 约束：未知国家仍计入 unmapped_customer_count。
def country_code(value):
    text = str(value or "").strip()
    return text.upper() if len(text) == 2 and text.isascii() and text.isalpha() else COUNTRY_CODES.get(text.casefold(), "")


# 功能：按币种统计活跃商机。
# 输入：`opportunities` 为已授权且去重的商机序列。
# 输出：币种至精确十进制金额字符串的映射。
# 逻辑：仅计入已知金额，不汇率换算。
# 约束：未知金额不伪造为零；调用方单独返回未知数量。
def amounts(opportunities):
    totals = {}
    for item in opportunities:
        if item.amount is not None:
            totals[item.currency] = totals.get(item.currency, Decimal("0")) + item.amount
    return {key: str(value) for key, value in sorted(totals.items())}


# 功能：生成地图和活动列表数据。
# 逻辑：全部查询经过 scope，复用活动筛选，金额不从活动说明中提取。
# 约束：无外部调用、无自动数据填充。
class WorldView(SalesView):
    # 功能：返回数据库活动与统计。
    # 输入：`request` 包含 page/page_size、country、event_type、from/to，可选 q。
    # 输出：分页活动、国家统计、高亮国家、可选币种及未知国家客户数。
    # 逻辑：国家数量忽略当前国家筛选；每个活动关联去重商机，并为同坐标活动提供去重的地图金额。
    # 约束：归档客户、归档商机与非活跃状态不计额；客户无国家不猜测；查询不写数据库。
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        query = scope(WorldEvent, request.user).filter(archived=False)
        if request.query_params.get("q"):
            query = query.filter(title__icontains=request.query_params["q"])
        region_params = request.query_params.copy()
        region_params.pop("country", None)
        region_events = list(filter_insights(query, region_params))
        query = filter_insights(query, request.query_params)
        rows, pagination = paged(query, request)
        excluded = CompanySettings.objects.filter(archived=True).values_list("company_id", flat=True)
        opportunities = list(scope(Opportunity, request.user).filter(archived=False, status__in=ACTIVE_STAGES).exclude(company_id__in=excluded).select_related("company"))
        by_id = {str(item.pk): item for item in opportunities}
        companies = Company.objects.filter(pk__in=visible_company_ids(request.user)).exclude(pk__in=excluded)
        countries, unmapped = {}, 0
        for company in companies:
            code = country_code(company.customer.get("country"))
            if not code:
                unmapped += 1
                continue
            countries.setdefault(code, {"code": code, "customer_count": 0, "event_count": 0})["customer_count"] += 1
        city_ids = {}
        for event in region_events:
            countries.setdefault(event.country, {"code": event.country, "customer_count": 0, "event_count": 0})["event_count"] += 1
            key = (event.country, event.latitude, event.longitude)
            city_ids.setdefault(key, set()).update(str(pk) for pk in event.opportunity_ids)
        for code, row in countries.items():
            selected = [item for item in opportunities if country_code(item.company.customer.get("country")) == code]
            row["amounts"] = amounts(selected)
            row["unknown_amount_count"] = sum(item.amount is None for item in selected)
        result = []
        for event in rows:
            selected = [by_id[pk] for pk in dict.fromkeys(str(pk) for pk in event.opportunity_ids) if pk in by_id]
            payload = WorldEventSerializer(event, context={"request": request}).data
            result.append({**payload, "amounts": amounts(selected), "map_amounts": amounts([by_id[pk] for pk in city_ids[(event.country, event.latitude, event.longitude)] if pk in by_id]), "customers": sorted({item.company.name for item in selected}), "unknown_amount_count": sum(item.amount is None for item in selected)})
        return Response({**pagination, "results": result, "countries": sorted(countries.values(), key=lambda row: row["code"]), "currencies": sorted({item.currency for item in opportunities}), "unmapped_customer_count": unmapped})
