"""Responsibility: Build authoritative company-level L4 context and propagate shared scoring-dependency changes.
Implementation: Isolate opportunities, orders, and seller profiles by business owner; aggregate same-currency active opportunities and include historical orders individually in averages.
Relationships: crm.selectors reads context; sales transactions and seller-profile APIs invoke version propagation under the owner lock.
Directory:
- canonical_names: Deduplicate and stably sort canonical names.
- historical_orders: Read confirmed historical orders with canonical products and amounts.
- company_deal: Aggregate the current company's active opportunities.
- similar_won: Identify similar historical wins by industry/product overlap, retaining unknown when information is insufficient.
- priority_context: Build customer, deal, and seller without duplicate emails.
- refresh_owner_priority: Update affected company business versions and enqueue existing analysis tasks.
Variable index:
- ACTIVE_STAGES: User-confirmed active opportunity states.
- WON_STATUSES: User-confirmed order states included in historical averages.
- logger: Diagnostic logs limited to dependency propagation counts and identities.
"""

import logging
from decimal import Decimal

from .models import Opportunity, Product, SalesOrder, SellerProfile

ACTIVE_STAGES = ("new", "qualified", "proposal")
WON_STATUSES = ("confirmed", "fulfilled")
logger = logging.getLogger("salesmate.priority")


# Function: Deduplicate and stably sort canonical names.
# Inputs: `values`: authoritative string-name sequence.
# Outputs: Trimmed strings deduplicated case-insensitively.
# Logic: Retain one display value per canonical name so database ordering does not affect snapshots.
# Constraints: No translation or synonym inference; upstream validates names and field types.
def canonical_names(values):
    names = {}
    for value in sorted(values):
        if isinstance(value, str) and value.strip():
            names.setdefault(value.strip().casefold(), value.strip())
    return [names[key] for key in sorted(names)]


# Function: Read confirmed historical orders with canonical products and amounts.
# Inputs: `owner_id`: business owner primary key.
# Outputs: One dictionary per order, containing currency, nullable amount, industry, and nullable product list.
# Logic: Read only the owner's unarchived confirmed/fulfilled orders and reuse amount calculations; retain unknown when lines or product relations are missing.
# Constraints: Neither won opportunities nor email mentions count as completed orders; no historical date window or cross-employee sampling.
def historical_orders(owner_id):
    from .serializers import DocumentSerializer

    history = []
    query = SalesOrder.objects.filter(owner_id=owner_id, company__owner_id=owner_id, archived=False,
                                     status__in=WON_STATUSES).select_related("company").prefetch_related("lines__product")
    for order in query.order_by("id"):
        lines = [line for line in order.lines.all() if not line.archived]
        industry = order.company.customer.get("industry_from_crm")
        products = canonical_names(line.product.name for line in lines if line.product_id)
        history.append({
            "currency": order.currency.upper(),
            "amount": Decimal(DocumentSerializer().get_total(order)) if lines else None,
            "industry": industry if isinstance(industry, str) and industry.strip().casefold() != "unknown" else None,
            "products": products if lines and all(line.product_id for line in lines) else None,
        })
    return history


# Function: Aggregate the current company's active opportunities.
# Inputs: `company`: authorized company.
# Outputs: Company-level deal dictionary; empty when no active records exist.
# Logic: Sum only when currencies match and all amounts are known; products must also all be known. Never combine multicurrency amounts.
# Constraints: Use explicit opportunities only, without double-counting quotes; omit deal_value for zero totals and never implicitly select one opportunity.
def company_deal(company):
    opportunities = list(Opportunity.objects.filter(company=company, owner_id=company.owner_id,
                                                    archived=False, status__in=ACTIVE_STAGES).order_by("id"))
    if not opportunities:
        return {}
    deal = {"status": "ACTIVE"}
    currencies = {item.currency.upper() for item in opportunities}
    if len(currencies) == 1:
        deal["currency"] = currencies.pop()
        if all(item.amount is not None for item in opportunities):
            amount = sum((item.amount for item in opportunities), Decimal("0"))
            if amount > 0:
                deal["deal_value"] = format(amount, ".2f")
    if all(item.product_names for item in opportunities):
        deal["product"] = canonical_names(name for item in opportunities for name in item.product_names)
    stages = {item.status for item in opportunities}
    if len(stages) == 1:
        deal["stage"] = stages.pop()
    return deal


# Function: Identify similar historical wins by industry/product overlap, retaining unknown when information is insufficient.
# Inputs: `customer`: authoritative company fields; `deal`: company-level opportunity; `history`: reliable completed orders for the current owner.
# Outputs: True for verified similarity, False for complete samples with no match, otherwise None.
# Logic: Matching canonical industry and at least one product provide positive evidence; a negative conclusion requires complete information in every sample.
# Constraints: Empty history does not mean no match; no added similarity thresholds or model calls.
def similar_won(customer, deal, history):
    industry, products = customer.get("industry"), deal.get("product")
    if not industry or not products or not history:
        return None
    wanted = {item.casefold() for item in products}
    complete = True
    for item in history:
        if not item["industry"] or not item["products"]:
            complete = False
            continue
        if item["industry"].strip().casefold() == industry.casefold() and wanted & {name.casefold() for name in item["products"]}:
            return True
    return False if complete else None


# Function: Build customer, deal, and seller without duplicate emails.
# Inputs: `company`: authorized company; `history`: optional previously read same-owner orders for statistical reuse.
# Outputs: priority_context dictionary, with unknown business fields absent.
# Logic: Map authoritative CRM fields, calculate per-order averages by opportunity currency, and merge target profiles/product catalogs.
# Constraints: Agent input callers must read under the company lock; writers update every affected company's version before committing shared dependencies.
def priority_context(company, history=None):
    source = company.customer
    customer = {"customer_id": source.get("customer_id") or str(company.pk), "company_name": company.name}
    for original, target in (("industry_from_crm", "industry"), ("employee_count", "company_size"), ("country", "country")):
        value = source.get(original)
        if value is not None and not (isinstance(value, str) and (not value.strip() or value.strip().casefold() == "unknown")):
            customer[target] = value.strip() if isinstance(value, str) else value
    profile = SellerProfile.objects.filter(owner_id=company.owner_id).first()
    seller = dict(profile.profile) if profile else {}
    names = canonical_names(Product.objects.filter(owner_id=company.owner_id, archived=False).values_list("name", flat=True))
    if names:
        seller["products"] = names
    deal = company_deal(company)
    history = historical_orders(company.owner_id) if history is None else history
    amounts = [item["amount"] for item in history if item["currency"] == deal.get("currency")]
    if amounts and all(amount is not None for amount in amounts):
        average = sum(amounts, Decimal("0")) / len(amounts)
        if average > 0:
            seller["average_deal_value"] = str(average)
            seller["average_deal_currency"] = deal["currency"]
    similar = similar_won(customer, deal, history)
    if similar is not None:
        seller["similar_won_deals"] = similar
    return {"customer": customer, "deal": deal, "seller": seller}


# Function: Update affected company business versions and enqueue existing analysis tasks.
# Inputs: `owner_id`: business owner whose row is locked; `exclude`: company primary keys already synchronized in this transaction.
# Outputs: Number of updated companies.
# Logic: Lock same-owner companies in primary-key order, increment both versions together, and merge pending work using external_updated.
# Constraints: Call within the original business transaction while holding the owner lock; no cross-employee work, model starts, or automatic retries.
def refresh_owner_priority(owner_id, exclude=()):
    from apps.crm.models import Company
    from .services import enqueue_analysis

    count = 0
    for company in Company.objects.select_for_update().filter(owner_id=owner_id).exclude(pk__in=exclude).order_by("pk"):
        company.revision += 1
        company.external_version += 1
        company.save(update_fields=["revision", "external_version"])
        enqueue_analysis(company, "external_updated")
        count += 1
    logger.info("priority_dependencies_updated owner_id=%s companies=%s", owner_id, count)
    return count
