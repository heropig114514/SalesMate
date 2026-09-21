"""职责：把本人知识投影为工作空间聊天的有限、可追溯初始证据。
实现：读取显式导入知识，正式模式限本人、实验模式跨账号；客户资料由请求绑定的只读工具按需查询。
关联：chat.services 在员工锁内首次调用并冻结结果；不执行模型或外部网络。
目录：
- item：生成严格四字段证据条目。
- build_context：组装当前请求的 internal 上下文。
变量索引：
- 无
"""

from common.laboratory import owner_scope

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
        "content": (
            content
            if len(content) <= 2000
            else content[:1985] + "\n[节选，原文未完整提供]"
        ),
    }


# 功能：组装有界、员工隔离的本次证据。
# 输入：`request` 为已授权工作空间 AnswerRequest；调用方持有员工锁。
# 输出：严格 internal AnswerContext，不可用资料以缺口描述。
# 逻辑：按模式选择本人或全账号知识，沿用至多 4 条知识的既定预算；初始客户证据始终为空。
# 约束：不自动选择公司或读取邮件，不执行外部检索，工具查询的来源另行持久化。
def build_context(request):
    entries = KnowledgeEntry.objects.filter(owner_scope(request.owner), active=True).order_by(
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
        "customer_context": [],
        "context_items": knowledge,
        "customer_context_status": "completed",
        "knowledge_status": "completed",
        "retrieval_gaps": [],
        "external_available": False,
    }
