"""职责：构建正式 L4 的权威公司级上下文并传播共享评分依赖变化。
实现：按业务 owner 隔离商机、订单和销售方画像；同币种活跃商机汇总，历史订单逐单计入均值。
关联：crm.selectors 读取上下文；销售事务和销售方资料接口在 owner 锁内调用版本传播。
目录：
- canonical_names：去重并稳定排序规范名称。
- historical_orders：读取已确认成交的历史订单及规范产品与金额。
- company_deal：汇总当前客户的活跃商机。
- similar_won：按行业和产品重叠判断历史赢单，资料不足保持未知。
- priority_context：构造不含重复邮件的 customer、deal、seller。
- refresh_owner_priority：更新受影响公司的业务版本并排入现有分析任务。
变量索引：
- ACTIVE_STAGES：经用户确认的活跃商机状态。
- WON_STATUSES：经用户确认纳入历史均值的订单状态。
- logger：只记录依赖传播数量和身份的诊断日志。
"""

import logging
from decimal import Decimal

from .models import Opportunity, Product, SalesOrder, SellerProfile

ACTIVE_STAGES = ("new", "qualified", "proposal")
WON_STATUSES = ("confirmed", "fulfilled")
logger = logging.getLogger("salesmate.priority")


# 功能：去重并稳定排序规范名称。
# 输入：`values` 为权威字符串名称序列。
# 输出：去首尾空白、忽略大小写去重的字符串列表。
# 逻辑：同一规范名称仅保留一个展示值，避免数据库顺序影响快照。
# 约束：不翻译、不推断同义词；上游负责名称和字段类型校验。
def canonical_names(values):
    names = {}
    for value in sorted(values):
        if isinstance(value, str) and value.strip():
            names.setdefault(value.strip().casefold(), value.strip())
    return [names[key] for key in sorted(names)]


# 功能：读取已确认成交的历史订单及规范产品与金额。
# 输入：`owner_id` 为业务所有者主键。
# 输出：每张订单一个字典，包含币种、可空金额、行业和可空产品数组。
# 逻辑：只读同 owner 未归档 confirmed/fulfilled 订单，复用金额计算，无明细或缺少产品关系时保留未知。
# 约束：不把商机 won 或邮件提及当作成交订单；无历史日期窗口，不跨员工取样。
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


# 功能：汇总当前客户的活跃商机。
# 输入：`company` 为已授权客户。
# 输出：公司级 deal 字典；无活跃记录返回空字典。
# 逻辑：同币种且金额全部已知时求和；产品也须全部已知，多币种不合并金额。
# 约束：仅使用显式商机，不重复叠加报价；零总额不输出 deal_value，不隐式选择其中一条商机。
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


# 功能：按行业和产品重叠判断历史赢单，资料不足保持未知。
# 输入：`customer` 为权威客户字段，`deal` 为公司级商机，`history` 为当前 owner 的可靠成交订单。
# 输出：证实相似返回 True，样本资料完整且无匹配返回 False，否则返回 None。
# 逻辑：同一规范行业且至少一个相同产品即有正证据；未找到时需所有样本资料完整才能否定。
# 约束：空历史不等于不匹配；不增加相似度阈值，不调用模型。
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


# 功能：构造不含重复邮件的 customer、deal、seller。
# 输入：`company` 为已授权客户；`history` 可提供同 owner 已读取订单以复用一次统计。
# 输出：priority_context 字典，未知业务字段保持缺失。
# 逻辑：映射 CRM 权威字段，按商机币种计算逐单平均额，合并目标画像和产品目录。
# 约束：Agent 输入调用者须在公司锁内读取；写方在提交共享依赖前更新所有受影响公司的版本。
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


# 功能：更新受影响公司的业务版本并排入现有分析任务。
# 输入：`owner_id` 为持有行锁的业务所有者，`exclude` 为本事务已经同步更新的公司主键序列。
# 输出：更新的公司数量。
# 逻辑：按主键顺序锁定同 owner 公司，两个版本同步递增，使用 external_updated 合并待办。
# 约束：必须在已持有 owner 锁的原业务事务内调用；不跨员工、不启动模型、不自动重试。
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
