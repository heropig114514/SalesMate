"""Responsibility: Validate employee-owned customer creation through chat approvals.
Implementation: Preserve the normal directory handler; enforce private request identity
and reject an existing active exact-name customer before proposing or approving.
Relationships: approvals invokes this boundary; action_services supplies strict identity
checks that remain active in laboratory mode; sales.grouping performs actual creation.
Directory:
- validate_creation: Check private ownership and exact-name conflicts without mutation.
Variable index:
- None
"""

from apps.crm.access import Conflict
from apps.crm.models import Company
from rest_framework.exceptions import ValidationError
from .action_services import require_request


# Function: Validate a named customer before freezing or executing its creation.
# Inputs: Locked AnswerRequest `request` and registry-validated `arguments` containing name.
# Outputs: None, or permission/conflict errors without modifying CRM or approval state.
# Logic: Require the request's actual employee and nonblank name; compare trimmed names with active owned
# companies while the caller holds that employee's lock to serialize chat approvals.
# Constraints: Never merge, infer domains, or reuse another owner's laboratory records;
# normal directory UI behavior is unchanged and an explicit conflict needs user review.
def validate_creation(request, arguments):
    require_request(request.owner, request)
    if not arguments["name"].strip():
        raise ValidationError("客户名称不能为空。")
    if Company.objects.filter(owner=request.owner, name__iexact=arguments["name"].strip()).exclude(
        business_settings__archived=True
    ).exists():
        raise Conflict("已存在同名客户；请重新搜索并确认目标，不会重复录入或自动合并。")
