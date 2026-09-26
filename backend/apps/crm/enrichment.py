"""职责：向已授权公司分析提供共享实验资料并判定快照是否仍有效。
实现：个人隔离不读取共享实验资料；一次批量解析内共享已核验清单，逐公司完成精确匹配；不跨请求缓存。
关联：selectors 提供上下文，results 保存时重读，Agent 原样归并；不依赖额外 Tool 凭证。
目录：
- resolve：解析当前公司对应的获准实验资料。
- resolve_many：在同一只读事务内核验一次清单并解析多家公司。
- match_company：使用已核验资料完成单家公司的精确匹配。
- snapshot_current：检查快照补充资料是否仍与当前来源一致。
变量索引：
- logger：只记录公司、批次和状态的诊断日志。
"""

import logging

from django.db import connection, transaction
from rest_framework.exceptions import NotFound

from common.laboratory import owner_only
from apps.sales import experiments
from integrations.company_enrichment import digest
from .access import Conflict

logger = logging.getLogger("salesmate.enrichment")


# 功能：解析当前公司对应的获准实验资料。
# 输入：`company` 为调用者已授权读取的 Company。
# 输出：含 status、match_basis、source、facts、enrichment_version 的独立对象。
# 逻辑：委托批量解析单元素集合，保留独立读取的只读重复读事务与匹配规则。
# 约束：只读批准清单；批次缺失或完整性失败显式 unavailable 并记录原因，普通无匹配返回 not_found；不修改 CRM。
def resolve(company):
    return resolve_many([company])[company.pk]


# 功能：一次读取和核验批准清单，解析多家公司的资料。
# 输入：`companies` 为调用者已授权的公司序列。
# 输出：以公司主键索引的完整解析结果；空序列返回空字典。
# 逻辑：最外层事务使用只读重复读；嵌套时沿用调用者事务。清单核验保留原函数，结果按各公司独立匹配。
# 约束：不跨调用缓存、不省略指纹或批准检查；清单失败使全部结果显式 unavailable，逐公司记录安全日志。
@transaction.atomic
def resolve_many(companies):
    companies = list(companies)
    if not companies:
        return {}
    if len(connection.atomic_blocks) == 1:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    rows = []
    failure = None
    for batch in (() if owner_only() else experiments.APPROVED_BATCHES):
        try:
            rows.extend(experiments.table_rows(experiments.load_batch(batch), "crm.Company"))
        except (NotFound, Conflict) as error:
            failure = "batch_unavailable" if isinstance(error, NotFound) else "integrity_error"
            for company in companies:
                logger.warning("company_enrichment_unavailable company_id=%s batch=%s reason=%s", company.pk, batch, failure)
    return {company.pk: match_company(company, rows, failure) for company in companies}


# 功能：从本次核验的资料中生成公司补充信息。
# 输入：`company` 为目标公司；`rows` 为批准清单的当前投影；`failure` 为清单错误代码或 None。
# 输出：含来源、事实和摘要版本的独立字典。
# 逻辑：完整域名优先，受标记限制的全名次之；歧义拒绝唯一匹配；保留人数与行业的类型检查。
# 约束：仅用于当前解析调用，不读取数据库、不改写源资料；失败不会回退为普通无匹配。
def match_company(company, rows, failure):
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
