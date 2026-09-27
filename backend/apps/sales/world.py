"""Responsibility: Provide global-insight database queries and map business aggregation.
Implementation: Return source monetary fields directly from public events. Private opportunity links are filtered by the serializer; customer countries only control highlights. No CRM amount calculation, currency conversion, or aggregation of unrelated source amounts.
Relationships: world-news.js consumes this view; generic CRUD and MCP still maintain events/news.
Directory:
- country_code: Normalize known country names/codes.
- WorldView: Return paginated events and global country statistics.
- WorldView.get: Read visible records and project map data.
Variable index:
- COUNTRY_CODES: Explicit mappings between common country names in existing company profiles and ISO codes.
"""

from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from apps.crm.models import Company
from .models import WorldEvent, CompanySettings
from .permissions import scope, visible_company_ids
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


# Function: Generate map and event-list data.
# Logic: Query public event facts through scope; source monetary fields pass through unchanged.
# Constraints: No external calls or automatic data filling.
class WorldView(SalesView):
    # Function: Return database events and statistics.
    # Inputs: `request`: page/page_size, country, event_type, from/to, and optional q.
    # Outputs: Paginated events with source amounts, country statistics, and unknown-country counts.
    # Logic: Serialize shared events and scope-filter private IDs. Count visible companies for country highlights; no opportunity amounts are queried.
    # Constraints: Do not aggregate different grants, budgets or fees; archived facts are excluded and company permissions remain unchanged.
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
        companies = Company.objects.filter(pk__in=visible_company_ids(request.user)).exclude(pk__in=excluded)
        countries, unmapped = {}, 0
        for company in companies:
            code = country_code(company.customer.get("country"))
            if not code:
                unmapped += 1
                continue
            countries.setdefault(code, {"code": code, "customer_count": 0, "event_count": 0})["customer_count"] += 1
        for event in region_events:
            countries.setdefault(event.country, {"code": event.country, "customer_count": 0, "event_count": 0})["event_count"] += 1
        result = WorldEventSerializer(rows, many=True, context={"request": request}).data
        return Response({**pagination, "results": result, "countries": sorted(countries.values(), key=lambda row: row["code"]), "unmapped_customer_count": unmapped})
