"""Responsibility: Project the caller's knowledge as bounded, traceable initial evidence for workspace chat.
Implementation: Read explicitly imported knowledge, always restricted to the authenticated caller; request-bound read-only tools query customer data on demand.
Relationships: ``chat.services`` first calls this under employee lock and freezes its result; does not execute models or external network access.
Directory:
- item: Generate a strict four-field evidence item.
- build_context: Assemble ``internal`` context for the current request.
Variable index:
- None
"""

from common.laboratory import owner_scope

from .models import KnowledgeEntry


# Function: Construct a source consumable by the Agent.
# Inputs: Stable identifier ``source_id``, type ``source_type``, title ``title``, and evidence content ``content``.
# Outputs: Strict four-field dictionary.
# Logic: Retain content and truncate to the established 2,000-character Agent limit with explicit truncation marker.
# Constraints: Truncation is not described as a complete document and does not produce inference or supplemental facts.
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


# Function: Assemble bounded, employee-isolated evidence for this request.
# Inputs: ``request`` is an authorized workspace request object and the caller holds the employee lock.
# Outputs: Strict internal ``AnswerContext``; unavailable records are represented as gaps.
# Logic: Select the caller's or all-account knowledge by mode, reuse established budget of at most four entries, and always leave initial customer evidence empty.
# Constraints: Does not automatically select a company or read email, execute external retrieval, and separately persists sources from tool queries.
def build_context(request):
    entries = KnowledgeEntry.objects.filter(owner_scope(request.owner), active=True).order_by(
        "-created_at", "id"
    )
    # Question matching prioritizes complete question terms; unmatched entries remain ordered by version time for the Agent to judge relevance.
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
