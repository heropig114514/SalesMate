"""职责：限定销售业务共享、个人会话和团队管理的授权范围。
实现：全球资讯和活动对已认证用户共享读取；其他记录保留个人/团队隔离；实验模式沿用开放规则。
关联：序列化关系字段和事务服务共用本模块；不按客户端自报 owner 授权。
目录：
- visible_company_ids：返回用户可访问的公司标识查询。
- company_access：验证公司业务读写权限。
- managed_team_ids：返回用户管理的团队标识。
- scope：生成模型级可见查询集。
- require_edit：确认对象编辑权限。
变量索引：
- PRIVATE_MODELS：只允许 owner 访问的助手、文件、产品和连接模型。
- SHARED_INSIGHTS：共享读取但仍按原所有者控制写入的资讯和活动模型。
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


# 功能：返回用户可访问的公司标识查询。
# 输入：`user` 为已认证用户。
# 输出：公司主键 QuerySet。
# 逻辑：个人隔离只返回自有公司；实验模式返回全部，正式协作模式含团队共享。
# 约束：返回范围不能用于读取私人邮件或 L1–L4 输入。
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


# 功能：验证公司业务读写权限。
# 输入：`user`、`company` 和 `write`，默认只读。
# 输出：原 Company；无权限返回与不存在一致的 404。
# 逻辑：个人隔离拒绝其他 owner；实验模式直接允许，协作模式校验团队和授权。
# 约束：不升级邮箱权限；所有者始终保有业务管理权。
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


# 功能：返回用户管理的团队标识。
# 输入：`user` 为当前用户。
# 输出：团队主键查询集。
# 逻辑：个人隔离仅管理自有团队；实验模式开放全部，协作模式含有效 manager。
# 约束：已归档团队不接受成员变更。
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


# 功能：生成模型级可见查询集。
# 输入：`model` 为白名单模型类，`user` 为当前用户。
# 输出：按个人或业务共享权限过滤的 QuerySet，包含归档记录供显式筛选。
# 逻辑：已认证用户可读公共资讯和活动，包括个人隔离模式；其他模型沿用既定隔离规则。
# 约束：共享只扩展读取，写入仍经 require_edit；活动关联业务字段由序列化器按用户投影。
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


# 功能：确认对象编辑权限。
# 输入：`instance` 为已有记录，`user` 为认证操作者。
# 输出：无；无权限抛 404 或 PermissionDenied。
# 逻辑：共享资讯仅扩展可读范围；个人隔离拒绝修改其他 owner，实验模式或原所有者允许，其余检查协作授权。
# 约束：授权不能通过修改 owner、公司或单据归属来转移。
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
