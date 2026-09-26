"""Responsibility: Manage company directories, contacts, and explicit manual email grouping.
Implementation: Experiment mode uses public cross-account business scope; owner locks and revision checks protect bulk moves. Merges preserve historical analyses and propagate transaction ownership changes to other company scores.
Relationships: crm.ingestion uses exact CompanyAlias mappings; sales API exposes only the business directory to shared users.
Directory:
- directory_row: Build a company directory entry without private emails.
- create_company: Create an explicitly named company.
- save_contact: Create or edit a manual contact.
- move_emails: Move explicitly selected emails and contact relations to a target company belonging to the same employee.
- merge_companies: Merge companies belonging to the same employee and archive the source.
Variable index:
- logger: Log manual grouping counts and object identifiers without email content.
"""

from common.laboratory import owner_scope

import logging
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.crm.access import Conflict, check_version, company_for
from apps.crm.models import Company, Contact, Email
from . import models
from .permissions import company_access
from .services import audit, enqueue_analysis, sync_company

logger = logging.getLogger("salesmate.grouping")


# Function: Build a company directory entry without private emails.
# Inputs: `company`: authorized company.
# Outputs: Dictionary of basic profile, contacts, and version.
# Logic: Return only manual business data without reusing CRM selectors containing emails and analyses.
# Constraints: Sharing does not thereby grant mailbox access; archival state comes from CompanySettings.
def directory_row(company):
    settings = models.CompanySettings.objects.filter(company=company).first()
    return {
        "id": str(company.pk),
        "name": company.name,
        "owner": company.owner_id,
        "domains": company.domains,
        "crm_status": company.crm_status,
        "customer": company.customer,
        "revision": company.revision,
        "created_at": company.created_at.isoformat(),
        "settings_id": str(settings.pk) if settings else None,
        "archived": settings.archived if settings else False,
        "contacts": [
            {"id": c.pk, "email": c.email, "name": c.name}
            for c in company.contacts.order_by("id")
        ],
    }


# Function: Create an explicitly named company.
# Inputs: `actor`: user; `name`: nonempty name.
# Outputs: A new Company.
# Logic: Use an independent manual UUID grouping key without inferring a company domain.
# Constraints: Write only the explicitly supplied company name to CRM; other fields remain unknown.
@transaction.atomic
def create_company(actor, name):
    if not isinstance(name, str) or not name.strip() or len(name) > 240:
        raise ValidationError("公司名称须为 1–240 字符。")
    company = Company.objects.create(
        owner=actor,
        group_key=f"manual:{uuid.uuid4()}",
        name=name.strip(),
        crm_status="registered",
    )
    company.customer = {"customer_id": str(company.pk)}
    company.save(update_fields=["customer"])
    settings = models.CompanySettings.objects.create(owner=actor, company=company)
    audit(actor, settings, "company_created")
    return company


# Function: Create or edit a manual contact.
# Inputs: `actor`; `company_id`; `expected`: company version; `data`: email/name and optional id.
# Outputs: Contact identifier and new company version.
# Logic: Lock owner and company; prohibit changing email addresses of contacts with emails to preserve the original grouping basis.
# Constraints: An empty name means unknown; do not overwrite facts extracted from emails.
@transaction.atomic
def save_contact(actor, company_id, expected, data):
    company = Company.objects.get(pk=company_id)
    get_user_model().objects.select_for_update().get(pk=company.owner_id)
    company = Company.objects.select_for_update().get(pk=company_id)
    company_access(actor, company, write=True)
    check_version(expected, company.revision)
    if set(data) - {"id", "email", "name"}:
        raise ValidationError("联系人仅接受 id、email、name。")
    from django.core.validators import validate_email

    address = data.get("email", "").strip().casefold()
    validate_email(address)
    name = data.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 240):
        raise ValidationError("联系人姓名格式无效。")
    if data.get("id"):
        contact = Contact.objects.select_for_update().get(
            pk=data["id"], company=company
        )
        if contact.messages.exists() and address != contact.email:
            raise Conflict("该联系人已有往来邮件；请新建邮箱身份，不修改历史身份。")
        contact.email, contact.name = address, name
        contact.full_clean()
        contact.save()
    else:
        contact = Contact(company=company, email=address, name=name)
        contact.full_clean()
        contact.save()
    profile, _ = models.ContactProfile.objects.get_or_create(
        contact=contact, defaults={"owner": company.owner}
    )
    audit(actor, profile, "contact_saved", {"contact_id": contact.pk})
    company.revision += 1
    company.external_version += 1
    company.save(update_fields=["revision", "external_version"])
    enqueue_analysis(company, "external_updated")
    return {
        "id": contact.pk,
        "email": contact.email,
        "name": contact.name,
        "company_revision": company.revision,
    }


# Function: Move explicitly selected emails to the same employee's target company.
# Inputs: `actor`, `source_id`, `target_id`, `source_revision`, `target_revision`, and `keys`: email deduplication-key array.
# Outputs: Moved count and new versions of both companies.
# Logic: Query and lock both companies according to the current mode; experiment mode permits cross-account moves. Reuse contacts by email, retain each company's settings ownership, and preserve email bodies.
# Constraints: Do not authorize shared users to move private email; do not automatically transfer transactions or change future domain routing.
@transaction.atomic
def move_emails(actor, source_id, target_id, source_revision, target_revision, keys):
    if str(source_id) == str(target_id):
        raise ValidationError("源公司和目标公司必须不同。")
    if (
        not isinstance(keys, list)
        or not keys
        or not all(isinstance(key, str) for key in keys)
        or len(keys) != len(set(keys))
    ):
        raise ValidationError("必须提供不重复的邮件去重键数组。")
    get_user_model().objects.select_for_update().get(pk=actor.pk)
    list(
        Company.objects.select_for_update()
        .filter(owner_scope(actor), pk__in=[source_id, target_id])
        .order_by("id")
    )
    source, target = company_for(actor, source_id), company_for(actor, target_id)
    check_version(source_revision, source.revision)
    check_version(target_revision, target.revision)
    if models.CompanySettings.objects.filter(company=target, archived=True).exists():
        raise Conflict("目标公司已归档。")
    emails = list(
        Email.objects.select_for_update()
        .filter(company=source, dedupe_key__in=keys)
        .select_related("contact")
    )
    if len(emails) != len(keys):
        raise ValidationError("部分邮件不属于源公司。")
    for email in emails:
        replacement = None
        if email.contact_id:
            replacement, _ = Contact.objects.get_or_create(
                company=target,
                email=email.contact.email,
                defaults={"name": email.contact.name},
            )
        email.company, email.contact = target, replacement
        email.save(update_fields=["company", "contact"])
    for company in (source, target):
        company.revision += 1
        company.save(update_fields=["revision"])
        enqueue_analysis(company, "grouping_changed")
        settings, _ = models.CompanySettings.objects.get_or_create(
            company=company, defaults={"owner": company.owner}
        )
        audit(
            actor,
            settings,
            "emails_regrouped",
            {"count": len(emails), "source": str(source.pk), "target": str(target.pk)},
        )
    logger.info(
        "emails_regrouped count=%s source=%s target=%s",
        len(emails),
        source.pk,
        target.pk,
    )
    return {
        "moved": len(emails),
        "source_revision": source.revision,
        "target_revision": target.revision,
    }


# Function: Merge companies belonging to the same employee and archive the source.
# Inputs: `actor`、`source_id`、`target_id`、`source_revision`、`target_revision`.
# Outputs: Target company directory entry.
# Logic: Reject conflicting fields and transfer business relations; retain historical analyses at the source. Transaction-order ownership changes also update other companies' scoring-context versions.
# Constraints: Production allows only company owners, while experiment mode permits cross-account access. Team-sharing grants do not expand automatically; revoke source sharing grants first.
@transaction.atomic
def merge_companies(actor, source_id, target_id, source_revision, target_revision):
    get_user_model().objects.select_for_update().get(pk=actor.pk)
    source, target = company_for(actor, source_id), company_for(actor, target_id)
    if source.pk == target.pk:
        raise ValidationError("不能合并同一公司。")
    list(
        Company.objects.select_for_update()
        .filter(pk__in=[source.pk, target.pk])
        .order_by("id")
    )
    source.refresh_from_db()
    target.refresh_from_db()
    check_version(source_revision, source.revision)
    check_version(target_revision, target.revision)
    if models.CompanyGrant.objects.filter(company=source, archived=False).exists():
        raise Conflict("先撤销源公司的共享授权，再执行合并。")
    if models.CompanySettings.objects.filter(
        company__in=[source, target], archived=True
    ).exists():
        raise Conflict("归档公司须先恢复才能合并。")
    if models.ToolAction.objects.filter(
        company__in=[source, target], status__in=["approved", "running", "uncertain"]
    ).exists():
        raise Conflict("公司存在已批准或待核对的外部动作，不能合并。")
    conflicts = [
        key
        for key, value in source.customer.items()
        if key != "customer_id"
        and value is not None
        and target.customer.get(key) is not None
        and target.customer[key] != value
    ]
    if conflicts:
        raise Conflict("客户资料存在冲突，请先明确处理：" + ", ".join(conflicts))
    source_settings = (
        models.CompanySettings.objects.filter(company=source)
        .select_related("primary_contact")
        .first()
    )
    target_settings, _ = models.CompanySettings.objects.get_or_create(
        company=target, defaults={"owner": target.owner}
    )
    if source_settings:
        if (
            source_settings.notes
            and target_settings.notes
            and source_settings.notes != target_settings.notes
        ):
            raise Conflict("两份客户备注存在差异，请先人工处理。")
        if (
            source_settings.primary_contact_id
            and target_settings.primary_contact_id
            and source_settings.primary_contact.email
            != target_settings.primary_contact.email
        ):
            raise Conflict("两份客户的主要联系人不同，请先明确合并后的主要联系人。")
    keys = list(source.emails.values_list("dedupe_key", flat=True))
    if keys:
        move_emails(actor, source.pk, target.pk, source.revision, target.revision, keys)
        source.refresh_from_db()
        target.refresh_from_db()
    for contact in source.contacts.all():
        replacement, _ = Contact.objects.get_or_create(
            company=target, email=contact.email, defaults={"name": contact.name}
        )
        profile = models.ContactProfile.objects.filter(contact=contact).first()
        if profile:
            other = models.ContactProfile.objects.filter(contact=replacement).first()
            if other and any(
                getattr(other, key)
                and getattr(profile, key)
                and getattr(other, key) != getattr(profile, key)
                for key in ("title", "phone", "notes")
            ):
                raise Conflict("联系人补充资料冲突，请先人工处理。")
            if other:
                for key in ("title", "phone", "notes"):
                    setattr(other, key, getattr(other, key) or getattr(profile, key))
                other.revision += 1
                other.save()
            else:
                profile.contact, profile.revision = replacement, profile.revision + 1
                profile.save()
    if source_settings:
        target_settings.notes = target_settings.notes or source_settings.notes
        if (
            not target_settings.primary_contact_id
            and source_settings.primary_contact_id
        ):
            target_settings.primary_contact = target.contacts.get(
                email=source_settings.primary_contact.email
            )
        target_settings.revision += 1
        target_settings.save()
    for model in (
        models.Ticket,
        models.Opportunity,
        models.Quote,
        models.SalesOrder,
        models.FollowUp,
        models.Conversation,
        models.ToolAction,
        models.Attachment,
    ):
        model.objects.filter(company=source).update(
            company=target, revision=F("revision") + 1
        )
    for alias in models.CompanyAlias.objects.filter(company=source):
        alias.company = target
        alias.revision += 1
        alias.save(update_fields=["company", "revision", "updated_at"])
    for key in {source.group_key, *[f"domain:{domain}" for domain in source.domains]}:
        if key.startswith(("domain:", "contact:")):
            alias, created = models.CompanyAlias.objects.get_or_create(
                owner=actor, group_key=key, defaults={"company": target}
            )
            if not created and alias.company_id != target.pk:
                raise Conflict("源归组键已映射到其他公司。")
    target.domains = sorted(set(target.domains + source.domains))
    target.customer = {
        **source.customer,
        **{key: value for key, value in target.customer.items() if value is not None},
        "customer_id": str(target.pk),
    }
    for field, id_field in (
        ("tickets", "ticket_id"),
        ("quotes", "quote_id"),
        ("orders", "order_id"),
    ):
        entries = {item[id_field]: item for item in getattr(target, field)}
        for item in getattr(source, field):
            if item[id_field] in entries and entries[item[id_field]] != item:
                raise Conflict("历史业务记录 ID 冲突，无法自动合并。")
            entries[item[id_field]] = item
        setattr(target, field, list(entries.values()))
    target.save(update_fields=["domains", "customer", "tickets", "quotes", "orders"])
    sync_company(target)
    sync_company(source)
    from .priority import refresh_owner_priority
    refresh_owner_priority(actor.pk, exclude=[target.pk, source.pk])
    source.refresh_from_db()
    settings, _ = models.CompanySettings.objects.get_or_create(
        company=source, defaults={"owner": actor}
    )
    settings.archived = True
    settings.revision += 1
    settings.save()
    source.revision += 1
    source.save(update_fields=["revision"])
    enqueue_analysis(source, "grouping_changed")
    audit(actor, settings, "company_merged", {"target": str(target.pk)})
    target.refresh_from_db()
    return directory_row(target)
