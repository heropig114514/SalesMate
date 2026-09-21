"""职责：提供公司补充资料的无状态版本与人数选择契约。
实现：规范 JSON 摘要绑定输入和资料版本；CRM 人数优先，实验人数独立标记。
关联：Agent L2/L3 与后端保存校验共用，不读取数据库或执行网络请求。
目录：
- digest：计算稳定内容摘要。
- input_version：计算邮件、CRM 和可选补充资料的输入版本。
- employee_size：选择人数及真实来源标签。
- source_refs：提取匹配资料的独立来源。
变量索引：
- 无
"""

import hashlib
import json


# 功能：计算稳定内容摘要。
# 输入：`value` 为可 JSON 序列化的内容。
# 输出：sha256 前缀的十六进制字符串。
# 逻辑：排序对象键并去除无意义空白，保留数组顺序。
# 约束：不接受时间、模型实例等未规范化对象。
def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# 功能：计算邮件、CRM 和可选补充资料的输入版本。
# 输入：`emails` 为邮件上下文；`merge_version` 为归并版本；`external_version` 为 CRM 版本；`enrichment` 为后端补充资料或 None。
# 输出：稳定的输入键。
# 逻辑：原三元素版本结构仅在明确提供补充资料时增加第四元素，绑定完整补充内容。
# 约束：无补充字段的旧客户端保持原哈希；不修改 CRM external_snapshot_version。
def input_version(emails, merge_version, external_version, enrichment=None):
    members = sorted([item["dedupe_key"], item["extract_prompt_version"], item["extract_status"]] for item in emails)
    parts = [members, merge_version, external_version]
    if enrichment is not None:
        parts.append(enrichment)
    return digest(parts)


# 功能：选择人数及真实来源标签。
# 输入：`business` 为已验证的 L2 business_context。
# 输出：(人数或 None, 来源字符串)。
# 逻辑：优先有效 CRM 人数，否则仅使用 matched 补充人数；没有有效人数返回 unknown。
# 约束：不写回 CRM，不将布尔值、负数或模型估计当成人数。
def employee_size(business):
    customer = business.get("customer") or {}
    count = customer.get("employee_count")
    if type(count) is int and count >= 0:
        source = customer.get("employee_count_source")
        return count, source.strip() if isinstance(source, str) and source.strip() else "crm"
    enrichment = business.get("company_enrichment") or {}
    count = enrichment.get("facts", {}).get("employee_count")
    if enrichment.get("status") == "matched" and type(count) is int and count >= 0:
        return count, "synthetic_sample"
    return None, "unknown"


# 功能：提取匹配资料的独立来源。
# 输入：`business` 为已验证的 L2 business_context。
# 输出：零个或一个来源 ID 的集合。
# 逻辑：只登记 matched 对象中的非空 source_id。
# 约束：来源真实性由后端解析与保存核验，纯函数不授予数据权限。
def source_refs(business):
    enrichment = business.get("company_enrichment") or {}
    source = enrichment.get("source") or {}
    ref = source.get("source_id")
    return {ref} if enrichment.get("status") == "matched" and isinstance(ref, str) and ref else set()
