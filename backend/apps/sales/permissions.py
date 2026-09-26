"""Responsibility: Bound authorization for sales business sharing, personal conversations, and team management.
Implementation: Authenticated users share read access to global news/events; other records retain personal/team isolation. Experiment mode retains open rules.
Relationships: Serialized relations and transactional services share this module; never authorize using client-declared owner values.
Directory:
- visible_company_ids: Return a query of company identifiers accessible to the user.
- company_access: Validate company business read/write permissions.
- managed_team_ids: Return identifiers of teams managed by the user.
- scope: Build model-level visible querysets.
- require_edit: Verify object edit permission.
Variable index:
- PRIVATE_MODELS: Assistant, file, product, and connection models accessible only to owners.
- SHARED_INSIGHTS: News/event models with shared reads and writes controlled by original owners.
"""

from django.db.models import Q
from common.laboratory import enabled, owner_only
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.crm.models import Company
from . import models

PRIVATE_MODELS = (
    models.Conversation,
    models.Message,
    models.Draft,
    models.ToolAction,
    models.Attachment,
    models.Notification,
    models.Product,
    models.Connection,
)
SHARED_INSIGHTS = (models.WorldEvent, models.WorldNews)


# Function: Return a query of company identifiers accessible to the user.
# Inputs: `user`: authenticated user.
# Outputs: Company primary-key QuerySet.
# Logic: Personal isolation returns owned companies only; experiment mode returns all, while production collaboration includes team sharing.
# Constraints: This scope must not authorize private email or L1-L4 input reads.
def visible_company_ids(user):
    if enabled():
        return Company.objects.values_list("pk", flat=True)
    if owner_only():
        return Company.objects.filter(owner=user).values_list("pk", flat=True)
    teams = models.Team.objects.filter(archived=False).filter(
        Q(owner=user) | Q(memberships__user=user, memberships__archived=False)
    )
    return (
        Company.objects.filter(
            Q(owner=user)
            | Q(business_grants__team__in=teams, business_grants__archived=False)
        )
        .values_list("pk", flat=True)
        .distinct()
    )


# Function: Validate company business read/write permissions.
# Inputs: `user`, `company`, and `write`, defaulting to read-only.
# Outputs: Original Company; lack of access returns the same 404 as absence.
# Logic: Personal isolation rejects other owners; experiment mode allows directly, while collaboration validates teams/grants.
# Constraints: No mailbox permission upgrades; owners always retain business management rights.
def company_access(user, company, write=False):
    if company.owner_id == user.pk:
        return company
    if enabled():
        return company
    if owner_only():
        raise NotFound("公司不存在或未授权。")
    teams = models.Team.objects.filter(archived=False).filter(
        Q(owner=user) | Q(memberships__user=user, memberships__archived=False)
    )
    if write:
        teams = teams.filter(
            Q(owner=user)
            | Q(
                memberships__user=user,
                memberships__archived=False,
                memberships__role__in=["editor", "manager"],
            )
        )
    grants = models.CompanyGrant.objects.filter(
        company=company, team__in=teams, archived=False
    )
    if write:
        grants = grants.filter(role="editor")
    if not grants.exists():
        raise NotFound("公司不存在或未授权。")
    return company


# Function: Return identifiers of teams managed by the user.
# Inputs: `user`: current user.
# Outputs: Team primary-key queryset.
# Logic: Personal isolation manages owned teams only; experiment mode opens all, while collaboration includes active managers.
# Constraints: Archived teams reject membership changes.
def managed_team_ids(user):
    if owner_only():
        return models.Team.objects.filter(owner=user, archived=False).values_list("pk", flat=True)
    if enabled():
        return models.Team.objects.values_list("pk", flat=True)
    return (
        models.Team.objects.filter(archived=False)
        .filter(
            Q(owner=user)
            | Q(
                memberships__user=user,
                memberships__role="manager",
                memberships__archived=False,
            )
        )
        .values_list("pk", flat=True)
        .distinct()
    )


# Function: Build model-level visible querysets.
# Inputs: `model`: allowlisted model class; `user`: current user.
# Outputs: QuerySet filtered by personal/business-sharing permissions, including archived records for explicit filtering.
# Logic: Authenticated users may read public news/events even under personal isolation; other models retain existing isolation rules.
# Constraints: Sharing expands reads only; writes still use require_edit. Serializers project event-linked business fields per user.
def scope(model, user):
    if model in SHARED_INSIGHTS:
        return model.objects.all() if user and user.is_authenticated else model.objects.none()
    if owner_only():
        return model.objects.filter(owner=user)
    if enabled():
        return model.objects.all()
    if model in PRIVATE_MODELS:
        return model.objects.filter(owner=user)
    if model is models.Team:
        return model.objects.filter(
            Q(owner=user) | Q(memberships__user=user, memberships__archived=False)
        ).distinct()
    if model is models.Membership:
        return model.objects.filter(team__in=scope(models.Team, user))
    if model is models.CompanyGrant:
        return model.objects.filter(company__owner=user)
    if model is models.AuditEvent:
        return model.objects.filter(
            Q(owner=user)
            | Q(
                company_id__in=visible_company_ids(user),
                object_type__in=[
                    "companysettings",
                    "contactprofile",
                    "ticket",
                    "opportunity",
                    "quote",
                    "quoteline",
                    "salesorder",
                    "orderline",
                    "followup",
                ],
            )
        )
    if model in (models.CompanySettings, models.CompanyAlias):
        return model.objects.filter(company_id__in=visible_company_ids(user))
    if model is models.ContactProfile:
        return model.objects.filter(contact__company_id__in=visible_company_ids(user))
    if model is models.QuoteLine:
        return model.objects.filter(quote__company_id__in=visible_company_ids(user))
    if model is models.OrderLine:
        return model.objects.filter(order__company_id__in=visible_company_ids(user))
    if issubclass(model, models.CompanyRecord):
        return model.objects.filter(company_id__in=visible_company_ids(user))
    raise PermissionDenied("该模型未开放业务访问。")


# Function: Verify object edit permission.
# Inputs: `instance`: existing record; `user`: authenticated actor.
# Outputs: None; unauthorized access raises 404 or PermissionDenied.
# Logic: Shared news expands reads only. Personal isolation rejects other-owner edits; experiment mode/original ownership permits them; otherwise check collaboration authorization.
# Constraints: Authorization cannot be transferred by changing owner, company, or document ownership.
def require_edit(instance, user):
    if enabled() or instance.owner_id == user.pk:
        return
    if owner_only():
        raise NotFound("记录不存在或未授权。")
    if isinstance(instance, models.Membership) and instance.team_id in managed_team_ids(
        user
    ):
        return
    if isinstance(
        instance,
        (
            models.CompanySettings,
            models.CompanyAlias,
            models.CompanyGrant,
            *PRIVATE_MODELS,
            *SHARED_INSIGHTS,
            models.Team,
        ),
    ):
        raise PermissionDenied("仅记录所有者可修改。")
    company = getattr(instance, "company", None)
    if isinstance(instance, models.ContactProfile):
        company = instance.contact.company
    if isinstance(instance, models.QuoteLine):
        company = instance.quote.company
    if isinstance(instance, models.OrderLine):
        company = instance.order.company
    if company is None:
        raise PermissionDenied("该记录不可编辑。")
    company_access(user, company, write=True)
