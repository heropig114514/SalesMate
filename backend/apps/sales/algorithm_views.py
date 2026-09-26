"""Responsibility: Provide algorithm data context and opportunity-score display queries.
Implementation: Reuse original serializers/permissions and expose opportunities, signals, and scores unchanged, without model calls or scoring-rule changes.
Relationships: Algorithms may call fixed MCP tools; the frontend opportunity-priority page reads paginated results.
Directory:
- seller_context: Collect the current identity's basic data and versions.
- SellerContextView: Basic data retrieval.
- SellerContextView.get: Return data for the current identity.
- OpportunityContextView: Read one opportunity and related business data.
- OpportunityContextView.get: Return opportunity input, signals, and results.
- PriorityBoardView: Opportunity-level priority list.
- PriorityBoardView.get: Sort and paginate by latest results.
Variable index:
- logger: Diagnostic logs for context and result queries.
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


# Function: Collect seller data available to algorithms.
# Inputs: `user`: selected business identity; `request`: serialization context.
# Outputs: Company, personal, reference-product, solution, transaction-product, and seller-target data.
# Logic: Read original data with each revision; use unsaved objects when absent.
# Constraints: Do not synchronize reference catalogs into transaction catalogs implicitly or read another identity's personal data.
def seller_context(user, request):
    company = CompanyProfile.objects.filter(owner=user).first() or CompanyProfile(owner=user)
    seller = SellerProfile.objects.filter(owner=user).first()
    return {"company_profile": CompanyProfileSerializer(company).data, "sales_setup": snapshot(user), "seller_profile": {"profile": seller.profile if seller else {}, "revision": seller.revision if seller else 0}, "products": ProductSerializer(Product.objects.filter(owner=user, archived=False), many=True, context={"request": request}).data}


# Function: Query the current identity's background available to algorithms.
# Logic: The selected identity comes from the session or experimental identity, never arbitrary owner parameters.
# Constraints: Read-only, without automatic completion.
class SellerContextView(SalesView):
    # Function: Return a unified data snapshot.
    # Inputs: Business identity from `request`.
    # Outputs: Basic data and individual versions.
    # Logic: Reuse seller_context without database writes.
    # Constraints: Read attachments through existing private-file endpoints.
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(seller_context(request.user, request))


# Function: Build algorithm input for one opportunity.
# Logic: Apply business permissions to opportunities, signals, and results; separately check owner for private emails.
# Constraints: Do not combine other opportunities' amounts into this opportunity.
class OpportunityContextView(SalesView):
    # Function: Read context.
    # Inputs: `request` and `opportunity_id`.
    # Outputs: Opportunity, customer, original business emails, seller data, historical orders, signals, and latest score.
    # Logic: Select seller data by authoritative opportunity ownership; ordinary shared users cannot read the owner's private background or mail.
    # Constraints: Non-owner production access returns 404; experimental mode permits cross-account access, logging identifiers only.
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


# Function: Provide opportunity-level priority display.
# Logic: Read only stored latest scores, placing null scores last.
# Constraints: Do not fall back to company scores or recompute weights.
class PriorityBoardView(SalesView):
    # Function: Read current active opportunities with pagination.
    # Inputs: `request`: page/page_size, q, and optional company.
    # Outputs: Each opportunity, customer name, latest score, and explanation.
    # Logic: Select the latest record by scored_at through a correlated subquery, sort scores descending, and break ties by opportunity ID.
    # Constraints: Return synthetic origins unchanged for frontend display; unknown filters do not change database permissions.
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
