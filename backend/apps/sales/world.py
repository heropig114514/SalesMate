"""Responsibility: Provide global-insight database queries and map business aggregation.
Implementation: Batch-project public events and filter private opportunity IDs; deduplicate linked amounts by viewer-visible opportunity and sum per currency. Country highlights still derive from visible companies.
Relationships: world-news.js consumes this view; generic CRUD and MCP still maintain events/news.
Directory:
- country_code: Normalize known country names/codes.
- amounts: Aggregate valid opportunity amounts.
- WorldView: Return paginated events and global country statistics.
- WorldView.get: Read visible records and project map data.
Variable index:
- COUNTRY_CODES: Explicit mappings between common country names in existing company profiles and ISO codes.
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


# Function: Normalize authoritative country fields.
# Inputs: `value`: country name or ISO code from company profiles.
# Outputs: Two-letter code, or an empty string when no explicit mapping exists.
# Logic: Match explicit mappings only; never infer from addresses or emails.
# Constraints: Unknown countries still contribute to unmapped_customer_count.
def country_code(value):
    text = str(value or "").strip()
    return text.upper() if len(text) == 2 and text.isascii() and text.isalpha() else COUNTRY_CODES.get(text.casefold(), "")


# Function: Aggregate active opportunities by currency.
# Inputs: `opportunities`: authorized, deduplicated opportunity sequence.
# Outputs: Mapping from currencies to exact decimal amount strings.
# Logic: Count known amounts only, without currency conversion.
# Constraints: Never fabricate unknown amounts as zero; callers return their count separately.
def amounts(opportunities):
    totals = {}
    for item in opportunities:
        if item.amount is not None:
            totals[item.currency] = totals.get(item.currency, Decimal("0")) + item.amount
    return {key: str(value) for key, value in sorted(totals.items())}


# Function: Generate map and event-list data.
# Logic: All queries pass through scope and reuse event filters; never extract amounts from event descriptions.
# Constraints: No external calls or automatic data filling.
class WorldView(SalesView):
    # Function: Return database events and statistics.
    # Inputs: `request`: page/page_size, country, event_type, from/to, and optional q.
    # Outputs: Paginated events, country statistics, highlighted countries, available currencies, and companies with unknown countries.
    # Logic: Events have shared reads and batch serialization filters linked IDs. Country counts ignore the country filter; amount/company aggregation uses deduplicated opportunities visible to the current user.
    # Constraints: Public events do not expand company permissions. Archived companies/opportunities and inactive states contribute no amounts; never infer missing countries or write the database.
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
        payloads = {item["id"]: item for item in WorldEventSerializer(rows, many=True, context={"request": request}).data}
        result = []
        for event in rows:
            selected = [by_id[pk] for pk in dict.fromkeys(str(pk) for pk in event.opportunity_ids) if pk in by_id]
            payload = payloads[str(event.pk)]
            result.append({**payload, "amounts": amounts(selected), "map_amounts": amounts([by_id[pk] for pk in city_ids[(event.country, event.latitude, event.longitude)] if pk in by_id]), "customers": sorted({item.company.name for item in selected}), "unknown_amount_count": sum(item.amount is None for item in selected)})
        return Response({**pagination, "results": result, "countries": sorted(countries.values(), key=lambda row: row["code"]), "currencies": sorted({item.currency for item in opportunities}), "unmapped_customer_count": unmapped})
