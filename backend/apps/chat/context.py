"""职责：把授权客户记录投影为有限、可追溯的聊天证据。
实现：仅读取员工自有业务邮件、当前有效画像和业务投影；知识由显式导入提供。
关联：chat.services 在公司锁内首次调用并冻结结果；不执行模型或外部网络。
目录：
- item：生成严格四字段证据条目。
- readable：把已存储结构转换成带字段标签的文本。
- build_context：组装当前请求的 internal 上下文。
变量索引：
- 无
"""

from apps.crm.models import Analysis
from .models import KnowledgeEntry


# 功能：构造 Agent 可消费的来源。
# 输入：`source_id` 稳定标识，`source_type` 类型，`title` 标题，`content` 证据正文。
# 输出：严格四字段字典。
# 逻辑：保留正文并按既定 Agent 2000 字符上限裁剪，截断明确标记。
# 约束：截断不被描述为完整文档；不产生推断或补充事实。
def item(source_id, source_type, title, content):
    return {
        "source_id": source_id,
        "source_type": source_type,
        "title_or_label": title,
        "content": content
        if len(content) <= 2000
        else content[:1985] + "\n[节选，原文未完整提供]",
    }


# 功能：格式化已有业务结构。
# 输入：`value` JSON 值，`prefix` 当前字段路径。
# 输出：带原字段标签的可读文本。
# 逻辑：递归展开标量，省略空值，不调用 LLM 生成摘要。
# 约束：保留状态、币种及事实/推断字段区别，禁止把缺失值补成确定事实。
def readable(value, prefix=""):
    if isinstance(value, dict):
        return "\n".join(
            filter(
                None,
                (
                    readable(part, f"{prefix}.{key}".strip("."))
                    for key, part in value.items()
                ),
            )
        )
    if isinstance(value, list):
        return "\n".join(filter(None, (readable(part, prefix) for part in value)))
    return f"{prefix}: {value}" if value is not None and value != "" else ""


# 功能：组装有界、员工隔离的本次证据。
# 输入：`request` 为已授权且持有公司锁的 AnswerRequest。
# 输出：严格 internal AnswerContext，不可用资料以缺口描述。
# 逻辑：至多 4 封邮件、1 份画像、每类 1 条业务记录及 4 条知识，合计不超过 Agent 的 12 条。
# 约束：仅自有邮箱业务邮件；画像须当前 revision、未失效且 provider=agent；无外部检索。
def build_context(request):
    company = request.company
    customer = []
    gaps = []
    emails = company.emails.filter(
        mailbox__owner=request.owner, business_classification="business"
    ).order_by("-sent_at", "dedupe_key")[:4]
    for email in emails:
        payload = email.payload
        body = payload.get("body_text", "")
        if isinstance(body, str) and body.strip():
            customer.append(
                item(
                    f"email:{email.pk}:review:{email.review_revision}",
                    "customer_email",
                    payload.get("subject") or "客户邮件",
                    f"时间：{email.sent_at.isoformat()}\n方向：{email.direction}\n主题：{payload.get('subject', '')}\n正文节选：\n{body}",
                )
            )
    analysis = (
        Analysis.objects.filter(
            snapshot__company=company,
            snapshot__revision=company.revision,
            snapshot__invalidation__isnull=True,
            payload__status="completed",
            provider="agent",
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if analysis:
        customer.append(
            item(
                f"analysis:{analysis.pk}",
                "customer_analysis",
                "当前有效客户画像与分析",
                readable(analysis.payload.get("detail_view", {})),
            )
        )
    else:
        gaps.append(
            {
                "scope": "customer_context",
                "code": "analysis_missing",
                "message": "当前没有与客户数据版本一致的有效模型画像。",
            }
        )
    for plural, singular, key, label in (
        ("tickets", "ticket", "ticket_id", "工单"),
        ("quotes", "quote", "quote_id", "报价"),
        ("orders", "order", "order_id", "订单"),
    ):
        # 业务投影保留既有交易语义，特别是未发送报价不冒充真实外发记录。
        records = getattr(company, plural)
        if records:
            record = records[-1]
            if isinstance(record, dict) and record.get(key):
                customer.append(
                    item(
                        f"{singular}:{record[key]}:revision:{company.revision}",
                        singular,
                        f"{label}：{record.get('number') or record[key]}",
                        readable(record),
                    )
                )
    entries = KnowledgeEntry.objects.filter(owner=request.owner, active=True).order_by(
        "-created_at", "id"
    )
    # 提问匹配优先使用完整问题词段；未匹配条目仍按版本时间排序提供，Agent 判断是否相关。
    terms = request.user_message.content.split()
    candidates = list(entries)
    candidates.sort(
        key=lambda entry: sum(
            term in entry.title or term in entry.content for term in terms
        ),
        reverse=True,
    )
    knowledge = [
        item(
            f"knowledge:{entry.pk}",
            "internal_knowledge",
            f"{entry.title}（{entry.version}）",
            entry.content,
        )
        for entry in candidates[:4]
    ]
    return {
        "request_id": str(request.pk),
        "scope": "internal",
        "customer_context": customer,
        "context_items": knowledge,
        "customer_context_status": "completed",
        "knowledge_status": "completed",
        "retrieval_gaps": gaps,
        "external_available": False,
    }
