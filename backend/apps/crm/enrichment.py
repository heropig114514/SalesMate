"""职责：向已授权公司分析提供共享实验资料并判定快照是否仍有效。
实现：复用实验清单投影，在后端一次完成唯一精确匹配、指纹与来源归档。
关联：selectors 提供上下文，results 保存时重读，Agent 原样归并；不依赖额外 Tool 凭证。
目录：
- resolve：解析当前公司对应的获准实验资料。
- snapshot_current：检查快照补充资料是否仍与当前来源一致。
变量索引：
- logger：只记录公司、批次和状态的诊断日志。
"""

import logging

from django.db import connection, transaction
from rest_framework.exceptions import NotFound

from apps.sales import experiments
from integrations.company_enrichment import digest
from .access import Conflict

logger = logging.getLogger("salesmate.enrichment")


# 功能：解析当前公司对应的获准实验资料。
# 输入：`company` 为调用者已授权读取的 Company。
# 输出：含 status、match_basis、source、facts、enrichment_version 的独立对象。
# 逻辑：独立读取在只读重复读事务中核验清单；完整域名优先，受标记限制的全名次之，候选不唯一返回 ambiguous。
# 约束：只读批准清单；批次缺失或完整性失败显式 unavailable 并记录原因，普通无匹配返回 not_found；不修改 CRM。
@transaction.atomic
def resolve(company):
    if len(connection.atomic_blocks) == 1:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    rows = []
    failure = None
    for batch in experiments.APPROVED_BATCHES:
        try:
            rows.extend(experiments.table_rows(experiments.load_batch(batch), "crm.Company"))
        except (NotFound, Conflict) as error:
            failure = "batch_unavailable" if isinstance(error, NotFound) else "integrity_error"
            logger.warning("company_enrichment_unavailable company_id=%s batch=%s reason=%s", company.pk, batch, failure)
    domains = {str(value).strip().casefold().rstrip(".") for value in company.domains if value}
    candidates = [row for row in rows if domains.intersection(
        str(value).strip().casefold().rstrip(".") for value in row["fields"]["domains"] if value)]
    basis = "exact_domain"
    if not candidates and any(batch in (company.name or "") for batch in experiments.APPROVED_BATCHES):
        basis = "exact_name"
        candidates = [row for row in rows if (company.name or "").strip().casefold() == (row["fields"]["name"] or "").strip().casefold()
                      and (not domains or not row["fields"]["domains"])]
    result = {"status": "unavailable" if failure else "not_found", "match_basis": None, "source": None, "facts": {}}
    if failure:
        result["reason"] = failure
    elif len(candidates) > 1:
        result["status"] = "ambiguous"
    elif len(candidates) == 1:
        row = candidates[0]
        customer = row["fields"]["customer"]
        count = customer.get("employee_count")
        industry = customer.get("industry_from_crm")
        result.update(status="matched", match_basis=basis, source={
            "source_id": f"experiment:{row['batch']}:crm.Company:{row['pk']}",
            "batch": row["batch"], "model": "crm.Company", "record_pk": row["pk"],
            "synthetic": True, "owner": row["owner"], "fingerprint": row["fingerprint"],
        }, facts={"employee_count": count if type(count) is int and count >= 0 else None,
                  "industry": industry if isinstance(industry, str) else None})
    result["enrichment_version"] = digest(result)
    return result


# 功能：检查快照补充资料是否仍与当前来源一致。
# 输入：`snapshot` 为 AnalysisInput；`company` 为对应公司；`current` 可复用本次已解析的资料。
# 输出：可继续使用返回 True，否则 False。
# 逻辑：含补充字段的快照比较完整对象，涵盖内容变化、歧义、新匹配和批准撤销。
# 约束：旧客户端未启用补充字段时保持原协议；不删除历史快照或自动触发模型调用。
def snapshot_current(snapshot, company, current=None):
    business = snapshot.payload.get("business_context", {})
    if "company_enrichment" not in business:
        return True
    return business["company_enrichment"] == (resolve(company) if current is None else current)
