"""Responsibility: Persist reviewable external actions, explicit approval, and Google/QQ tool execution.
Implementation: Allow workspace drafts for reviewable actions with an explicit customer; freeze action/connection versions and claim atomically. Hold the shared account lock during execution to isolate resets; disabling QQ blocks preparation, approval, execution, and reconciliation without implicit retries.
Relationships: integrations supplies authorized credentials; background commands execute approved actions and synchronize quotations to the agent only after actual sending.
Directory:
- validate_parameters: Build a confirmable action snapshot containing complete content.
- create_action: Idempotently create an action awaiting confirmation.
- decide_action: Approve or cancel an unexecuted action.
- execute_provider: Perform one real Google request or QQ SMTP submission.
- run_action: Execute an external action under the shared account lock.
- _run_action: Claim and execute an approved action, recording a definite or uncertain result.
- reconcile_action: Mark an interrupted action as having an uncertain outcome.
- verify_action: Read-only reconciliation of real identifiers for uncertain external actions.
Variable index:
- logger: External-action state logs without bodies, recipients, or credentials.
"""

import base64
import logging
import uuid
from email.message import EmailMessage

from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.reset_locks import account_lock
from apps.accounts.reset_models import AccountReset
from apps.crm.access import Conflict, InvalidState, check_version
from apps.crm.models import Company
from . import models
from .integrations import credentials_for
from .services import audit, sync_company
from . import qq_smtp
from common.mail_features import require_qq_enabled

logger = logging.getLogger("salesmate.actions")


# Function: Build a confirmable action snapshot containing complete content.
# Inputs: `actor`、`company`、`tool`、`parameters`.
# Outputs: Plain JSON parameters containing connection identity and explicit outgoing content.
# Logic: Check QQ capability before reading drafts/quotations; freeze QQ connection version and ASCII envelope; validate calendar times and notification mode.
# Constraints: No external calls; reject unknown parameters, require employee-owned sources, and match legacy customer drafts to the explicitly selected company.
def validate_parameters(actor, company, tool, parameters):
    if tool == "qq.send":
        require_qq_enabled("prepare_send")
    if not isinstance(parameters, dict):
        raise ValidationError("parameters 必须是对象。")
    provider = {
        "gmail.send": "gmail",
        "qq.send": "qq",
        "calendar.create": "calendar",
    }.get(tool)
    if provider is None:
        raise ValidationError("未注册的外部工具。")
    connection = models.Connection.objects.filter(
        pk=parameters.get("connection_id"),
        owner=actor,
        provider=provider,
        archived=False,
    ).first()
    if connection is None:
        raise NotFound("外部连接不存在或未授权。")
    result = {"connection_id": str(connection.pk), "account": connection.account}
    if tool in {"gmail.send", "qq.send"}:
        if set(parameters) - {"connection_id", "draft_id", "quote_id"}:
            raise ValidationError("发信只接受 connection_id、draft_id、quote_id。")
        draft = (
            models.Draft.objects.filter(
                pk=parameters.get("draft_id"),
                owner=actor,
                kind="email",
                archived=False,
                conversation__owner=actor,
                conversation__archived=False,
            )
            .filter(
                Q(conversation__company__isnull=True) | Q(conversation__company=company)
            )
            .first()
        )
        if (
            draft is None
            or not draft.recipients
            or not draft.content.strip()
            or not draft.subject.strip()
        ):
            raise ValidationError(
                "需要本人工作空间或同客户会话中含主题正文和收件人的有效邮件草稿。"
            )
        if "\r" in draft.subject or "\n" in draft.subject:
            raise ValidationError("邮件主题不能包含换行。")
        if tool == "qq.send":
            if not isinstance(draft.recipients, list) or not all(
                isinstance(address, str) and address.isascii()
                for address in draft.recipients
            ):
                raise ValidationError("QQ 发信收件地址须为普通 ASCII 邮箱地址。")
            if len(set(draft.recipients)) != len(draft.recipients):
                raise ValidationError("QQ 发信收件地址不能重复。")
            for address in draft.recipients:
                validate_email(address)
            result["connection_revision"] = connection.revision
        result.update(
            {
                "draft_id": str(draft.pk),
                "draft_revision": draft.revision,
                "subject": draft.subject,
                "body": draft.content,
                "to": draft.recipients,
            }
        )
        if parameters.get("quote_id"):
            quote = models.Quote.objects.filter(
                pk=parameters["quote_id"],
                owner=actor,
                company=company,
                status="approved",
                archived=False,
            ).first()
            if quote is None:
                raise ValidationError("报价必须是当前客户已审核且尚未发送的报价。")
            from .serializers import QuoteSerializer

            document = QuoteSerializer(quote).data
            rendered = "\n".join(
                f"{line['description']} | {line['quantity']} × {line['unit_price']} | 整行折扣 {line['discount']}"
                for line in document["lines"]
            )
            result["body"] += (
                f"\n\n报价 {quote.number}（{quote.currency}，不含未声明税费）\n{rendered}\n净额：{document['total']}"
            )
            result.update({"quote_id": str(quote.pk), "quote_revision": quote.revision})
    else:
        required = {
            "connection_id",
            "calendar_id",
            "title",
            "description",
            "start",
            "end",
            "attendees",
            "send_updates",
        }
        if set(parameters) != required:
            raise ValidationError(
                "日历参数必须明确指定 calendar_id、title、description、start、end、attendees、send_updates 和 connection_id。"
            )
        for key in ("calendar_id", "title", "description", "start", "end"):
            if not isinstance(parameters[key], str):
                raise ValidationError(f"{key} 必须是字符串。")
        start, end = (
            parse_datetime(parameters["start"]),
            parse_datetime(parameters["end"]),
        )
        if (
            start is None
            or end is None
            or timezone.is_naive(start)
            or timezone.is_naive(end)
            or end <= start
        ):
            raise ValidationError("会议起止时间必须带时区且结束晚于开始。")
        if not parameters["title"].strip() or not parameters["calendar_id"].strip():
            raise ValidationError("会议标题和 calendar_id 不得为空。")
        attendees = parameters["attendees"]
        if (
            not isinstance(attendees, list)
            or not all(isinstance(item, str) for item in attendees)
            or len(set(attendees)) != len(attendees)
        ):
            raise ValidationError("attendees 必须是不重复的邮箱数组。")
        for address in attendees:
            validate_email(address)
        if parameters["send_updates"] not in ("none", "all", "externalOnly"):
            raise ValidationError("必须明确选择 none/all/externalOnly 的通知方式。")
        result.update(
            {key: parameters[key] for key in required if key != "connection_id"}
        )
    return result


# Function: Idempotently create an action awaiting confirmation.
# Inputs: `actor` and `data` containing company, tool, parameters, idempotency_key, and optional conversation.
# Outputs: ToolAction.
# Logic: Serialize creation per employee; allow workspace or current-customer historical conversations, accepting the same key only for identical semantics.
# Constraints: Persist plans only; require separate user approval rather than inferring it from chat text.
@transaction.atomic
def create_action(actor, data):
    if not isinstance(data, dict) or set(data) - {
        "company",
        "tool",
        "parameters",
        "idempotency_key",
        "conversation",
    }:
        raise ValidationError("动作字段无效。")
    get_user_model().objects.select_for_update().get(pk=actor.pk)
    company = Company.objects.get(pk=data.get("company"))
    if company.owner_id != actor.pk:
        raise NotFound("外部动作只允许客户所有者创建。")
    key = uuid.UUID(str(data.get("idempotency_key")))
    conversation = None
    if data.get("conversation"):
        conversation = (
            models.Conversation.objects.filter(
                pk=data["conversation"], owner=actor, archived=False
            )
            .filter(Q(company__isnull=True) | Q(company=company))
            .first()
        )
        if conversation is None:
            raise ValidationError(
                "会话必须属于当前员工，且为工作空间或当前客户的历史会话。"
            )
    existing = models.ToolAction.objects.filter(
        owner=actor, idempotency_key=key
    ).first()
    if existing:
        if (
            existing.company_id != company.pk
            or existing.tool != data["tool"]
            or existing.parameters.get("request") != data.get("parameters")
            or existing.conversation_id != (conversation.pk if conversation else None)
        ):
            raise Conflict("同一幂等键不能用于不同动作。")
        return existing
    if models.CompanySettings.objects.filter(company=company, archived=True).exists():
        raise InvalidState("客户已归档。")
    parameters = validate_parameters(
        actor, company, data.get("tool"), data.get("parameters")
    )
    parameters["request"] = data["parameters"]
    action = models.ToolAction.objects.create(
        owner=actor,
        company=company,
        conversation=conversation,
        tool=data["tool"],
        parameters=parameters,
        idempotency_key=key,
    )
    audit(actor, action, "action_prepared", {"tool": action.tool})
    return action


# Function: Approve or cancel an unexecuted action.
# Inputs: `action`, `actor`, `expected`, and `decision`, restricted to approved/cancelled.
# Outputs: Updated action.
# Logic: Require the owner and correct version; when QQ is disabled permit cancellation only; freeze parameters after approval.
# Constraints: Reject cancellation of running actions because external cancellation cannot be guaranteed; approval requests send no external messages.
@transaction.atomic
def decide_action(action, actor, expected, decision):
    get_user_model().objects.select_for_update().get(pk=actor.pk)
    action = models.ToolAction.objects.select_for_update().get(
        pk=action.pk, owner=actor
    )
    check_version(expected, action.revision)
    if decision == "approved" and action.tool == "qq.send":
        require_qq_enabled("approve_send")
    allowed = (
        action.status == "pending_confirmation"
        if decision == "approved"
        else action.status in ("pending_confirmation", "approved")
        if decision == "cancelled"
        else False
    )
    if not allowed:
        raise InvalidState("当前动作不可批准或取消。")
    if decision == "approved" and action.parameters.get("quote_id"):
        quote = models.Quote.objects.select_for_update().get(
            pk=action.parameters["quote_id"], owner=actor, company=action.company
        )
        if (
            quote.archived
            or quote.status != "approved"
            or quote.revision != action.parameters["quote_revision"]
        ):
            raise Conflict("报价已经变化，请重新准备动作。")
        if (
            models.ToolAction.objects.filter(
                owner=actor,
                parameters__quote_id=str(quote.pk),
                status__in=["approved", "running", "uncertain"],
            )
            .exclude(pk=action.pk)
            .exists()
        ):
            raise Conflict("报价已有已确认或待核对的发送动作。")
    action.status = decision
    action.revision += 1
    if decision == "approved":
        action.approved_at = timezone.now()
    action.save(update_fields=["status", "revision", "approved_at", "updated_at"])
    audit(actor, action, "action_" + decision)
    return action


# Function: Perform one real Google request or QQ SMTP submission.
# Inputs: `action`: running action; `credentials`: validated credentials.
# Outputs: Result dictionary with external identifiers, optional links, and QQ submission state.
# Logic: QQ uses frozen content and qq_smtp, Gmail uses MIME Base64URL, and calendar uses stable event IDs.
# Constraints: Google uses only execute(num_retries=0) and QQ submits DATA once; no retries, content generation, or recipient expansion.
def execute_provider(action, credentials):
    data = action.parameters
    if action.tool == "qq.send":
        return qq_smtp.send(action, credentials)
    if action.tool == "gmail.send":
        message = EmailMessage()
        message["From"] = data["account"]
        message["To"] = ", ".join(data["to"])
        message["Subject"] = data["subject"]
        message["Message-ID"] = f"<{action.pk}@salesmate.local>"
        message.set_content(data["body"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = (
            build("gmail", "v1", credentials=credentials, cache_discovery=False)
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute(num_retries=0)
        )
        return {"message_id": response["id"], "thread_id": response.get("threadId")}
    if action.tool == "calendar.create":
        body = {
            "id": action.pk.hex,
            "summary": data["title"],
            "description": data["description"],
            "start": {"dateTime": data["start"]},
            "end": {"dateTime": data["end"]},
            "attendees": [{"email": address} for address in data["attendees"]],
        }
        response = (
            build("calendar", "v3", credentials=credentials, cache_discovery=False)
            .events()
            .insert(
                calendarId=data["calendar_id"],
                body=body,
                sendUpdates=data["send_updates"],
            )
            .execute(num_retries=0)
        )
        return {"event_id": response["id"], "url": response.get("htmlLink")}
    raise InvalidState("未注册的外部工具。")


# Function: Protect mutual exclusion between external-action execution and account reset.
# Inputs: `action_id`: existing action UUID.
# Outputs: Original action state; incomplete cleanup raises InvalidState and removed queue records return cancelled.
# Logic: Find the account, hold its shared lock, and recheck existence across claim, external call, and report; stale queue keys do not terminate the shared worker.
# Constraints: Do not restore cleared queue jobs or retry external requests.
def run_action(action_id):
    owner_id = models.ToolAction.objects.filter(pk=action_id).values_list("owner_id", flat=True).first()
    if owner_id is None:
        logger.info("external_action_cancelled action_id=%s reason=record_removed", action_id)
        return "cancelled"
    with account_lock(owner_id):
        if not models.ToolAction.objects.filter(pk=action_id).exists():
            logger.info("external_action_cancelled action_id=%s reason=record_removed", action_id)
            return "cancelled"
        if AccountReset.objects.filter(owner_id=owner_id, cleaning=True).exists():
            raise InvalidState("账户清理未完成，请先继续清空操作。")
        return _run_action(action_id)


# Function: Claim and execute one approved action.
# Inputs: `action_id`: action UUID.
# Outputs: Final action state; return the current state immediately unless approved.
# Logic: After claiming, recheck QQ capability/connection version; disabled QQ fails before network access and unclear SMTP outcomes become uncertain.
# Constraints: SMTP acceptance does not guarantee delivery; no automatic retries. Interrupted processes leave running actions requiring manual reconciliation.
def _run_action(action_id):
    with transaction.atomic():
        owner_id = models.ToolAction.objects.values_list("owner_id", flat=True).get(
            pk=action_id
        )
        get_user_model().objects.select_for_update().get(pk=owner_id)
        action = models.ToolAction.objects.select_for_update().get(pk=action_id)
        if action.status != "approved":
            return action.status
        action.status, action.started_at = "running", timezone.now()
        action.revision += 1
        action.save(update_fields=["status", "started_at", "revision", "updated_at"])
        audit(action.owner, action, "action_running")
    result, error, status = None, None, "failed"
    sent = False
    try:
        if action.tool == "qq.send":
            require_qq_enabled("execute_send")
        connection = models.Connection.objects.get(
            pk=action.parameters["connection_id"], owner=action.owner, archived=False
        )
        if (
            not action.owner.is_active
            or connection.account != action.parameters["account"]
            or (
                action.tool == "qq.send"
                and (
                    connection.provider != "qq"
                    or connection.revision
                    != action.parameters.get("connection_revision")
                )
            )
        ):
            raise InvalidState("账号已停用或外部连接身份已变化。")
        if models.CompanySettings.objects.filter(
            company=action.company, archived=True
        ).exists():
            raise InvalidState("客户已归档。")
        if action.parameters.get("quote_id"):
            quote = models.Quote.objects.get(
                pk=action.parameters["quote_id"],
                owner=action.owner,
                company=action.company,
            )
            if (
                quote.status != "approved"
                or quote.revision != action.parameters["quote_revision"]
                or quote.archived
            ):
                raise InvalidState("报价已变化，请取消旧动作并重新准备。")
        credentials = credentials_for(connection)
        sent = True
        result = execute_provider(action, credentials)
        status = "succeeded"
    except qq_smtp.QQSMTPError as exception:
        status = "uncertain" if exception.uncertain else "failed"
        error = {
            "code": "qq_smtp_result_unknown"
            if exception.uncertain
            else "qq_smtp_rejected",
            "stage": exception.stage,
            "message": str(exception),
        }
    except HttpError as exception:
        code = int(exception.resp.status)
        status = "uncertain" if sent and (code >= 500 or code == 408) else "failed"
        error = {
            "code": "provider_http_error",
            "message": f"外部服务返回 HTTP {code}，请核对连接或在外部系统确认是否完成。",
        }
    except Exception as exception:
        status = "uncertain" if sent else "failed"
        error = {
            "code": "external_result_unknown" if sent else "preflight_failed",
            "message": "外部结果未确认，请人工核对后处理。"
            if sent
            else "执行前校验失败，请检查连接授权、账号状态和单据版本。",
            "type": type(exception).__name__,
        }
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=action.owner_id)
        action = models.ToolAction.objects.select_for_update().get(pk=action.pk)
        action.status, action.result, action.error = status, result, error
        action.finished_at, action.revision = timezone.now(), action.revision + 1
        action.save()
        if status == "succeeded" and action.parameters.get("quote_id"):
            quote = models.Quote.objects.select_for_update().get(
                pk=action.parameters["quote_id"]
            )
            quote.status, quote.sent_at = "sent", timezone.now()
            quote.external_message_id = result["message_id"]
            quote.revision += 1
            quote.save()
            sync_company(quote.company)
        audit(action.owner, action, "action_" + status)
    logger.info("external_action_finished action_id=%s status=%s", action.pk, status)
    return status


# Function: Mark interrupted or uncertain actions for manual handling.
# Inputs: `action`、`actor`、`expected`.
# Outputs: Action marked uncertain.
# Logic: Accept only running state, preserve parameters/execution time, and record the need for manual reconciliation.
# Constraints: Neither claim external success nor execute again; independent queries must verify external events.
@transaction.atomic
def reconcile_action(action, actor, expected):
    action = models.ToolAction.objects.select_for_update().get(
        pk=action.pk, owner=actor
    )
    check_version(expected, action.revision)
    if action.status != "running":
        raise InvalidState("仅执行中且已确认中断的动作可标记为未知结果。")
    action.status, action.revision = "uncertain", action.revision + 1
    action.error = {
        "code": "interrupted",
        "message": "执行已中断，须在外部系统核对；不会自动重试。",
    }
    action.finished_at = timezone.now()
    action.save()
    audit(actor, action, "action_interrupted")
    return action


# Function: Read-only verification of whether an uncertain action exists in the external service.
# Inputs: `action`, `actor`, and `expected` current version.
# Outputs: Mark successful only when definite identifiers are found; otherwise retain uncertain and raise an explicit error.
# Logic: Check QQ IMAP copies only when enabled, Gmail by Message-ID, and calendar by event ID; validate version before writing back.
# Constraints: Do not resend or recreate; absence does not prove non-execution and cannot automatically unfreeze quotations.
def verify_action(action, actor, expected):
    action = models.ToolAction.objects.get(pk=action.pk, owner=actor)
    if action.tool == "qq.send":
        require_qq_enabled("verify_send")
    check_version(expected, action.revision)
    if action.status != "uncertain":
        raise InvalidState("只有未知结果动作需要外部核对。")
    connection = models.Connection.objects.get(
        pk=action.parameters["connection_id"], owner=actor, archived=False
    )
    if action.tool == "qq.send" and (
        connection.account != action.parameters["account"]
        or connection.provider != "qq"
    ):
        raise InvalidState("外部连接身份已变化，无法核对原动作。")
    credentials = credentials_for(connection)
    try:
        if action.tool == "qq.send":
            result = qq_smtp.verify_sent(action, credentials)
        elif action.tool == "gmail.send":
            api = (
                build("gmail", "v1", credentials=credentials, cache_discovery=False)
                .users()
                .messages()
            )
            results = (
                api.list(
                    userId="me",
                    q=f"in:sent rfc822msgid:{action.pk}@salesmate.local",
                    maxResults=2,
                )
                .execute(num_retries=0)
                .get("messages", [])
            )
            if len(results) != 1:
                raise InvalidState(
                    "未找到唯一已发送邮件，结果仍未知；请核对 Google 账号和已发送目录。"
                )
            message = api.get(
                userId="me",
                id=results[0]["id"],
                format="metadata",
                metadataHeaders=["Message-ID"],
            ).execute(num_retries=0)
            headers = {
                item["name"].lower(): item["value"]
                for item in message.get("payload", {}).get("headers", [])
            }
            if headers.get(
                "message-id"
            ) != f"<{action.pk}@salesmate.local>" or "SENT" not in message.get(
                "labelIds", []
            ):
                raise InvalidState("邮件标识或发送状态无法确认，结果仍未知。")
            result = {"message_id": message["id"], "thread_id": message.get("threadId")}
        else:
            event = (
                build("calendar", "v3", credentials=credentials, cache_discovery=False)
                .events()
                .get(calendarId=action.parameters["calendar_id"], eventId=action.pk.hex)
                .execute(num_retries=0)
            )
            if event.get("id") != action.pk.hex or event.get("status") == "cancelled":
                raise InvalidState("事件不存在或已取消，结果仍须人工核对。")
            result = {"event_id": event["id"], "url": event.get("htmlLink")}
    except InvalidState:
        raise
    except Exception as exception:
        logger.warning(
            "external_verification_failed action_id=%s error_type=%s",
            action.pk,
            type(exception).__name__,
        )
        raise InvalidState(
            "外部查询未能确认结果，请检查授权和外部记录；不会重新执行。"
        ) from None
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=actor.pk)
        action = models.ToolAction.objects.select_for_update().get(
            pk=action.pk, owner=actor
        )
        check_version(expected, action.revision)
        action.status, action.result, action.error = "succeeded", result, None
        action.finished_at, action.revision = timezone.now(), action.revision + 1
        action.save()
        if action.parameters.get("quote_id"):
            quote = models.Quote.objects.select_for_update().get(
                pk=action.parameters["quote_id"], owner=actor
            )
            quote.status, quote.sent_at = "sent", action.started_at
            quote.external_message_id, quote.revision = (
                result["message_id"],
                quote.revision + 1,
            )
            quote.save()
            sync_company(quote.company)
        audit(actor, action, "action_verified")
    return action
