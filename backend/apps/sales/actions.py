"""职责：保存可审查的外部动作、明确审批并执行 Google 或 QQ 工具。
实现：允许工作空间草稿用于明确指定客户的待审阅动作；冻结动作和连接版本、原子领取；禁用 QQ 时阻止准备、批准、执行和核对，无隐式重试。
关联：integrations 提供已授权凭证，后台命令执行已批准动作，报价实际发送后才同步 Agent。
目录：
- validate_parameters：形成包含完整内容的可确认动作快照。
- create_action：幂等创建待确认动作。
- decide_action：批准或取消尚未执行的动作。
- execute_provider：执行一次真实 Google 请求或 QQ SMTP 提交。
- run_action：领取并执行一个已批准动作，记录明确或未知结果。
- reconcile_action：把已中断动作标记为未知结果。
- verify_action：只读核对未知外部动作的真实标识。
变量索引：
- logger：外部动作状态日志，不输出正文、收件人或凭证。
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

from apps.crm.access import Conflict, InvalidState, check_version
from apps.crm.models import Company
from . import models
from .integrations import credentials_for
from .services import audit, sync_company
from . import qq_smtp
from common.mail_features import require_qq_enabled

logger = logging.getLogger("salesmate.actions")


# 功能：形成包含完整内容的可确认动作快照。
# 输入：`actor`、`company`、`tool`、`parameters`。
# 输出：普通 JSON 参数，包括连接身份和明确发送内容。
# 逻辑：检查 QQ 能力后读取草稿及报价，QQ 冻结连接版本与 ASCII 信封；日历验证时间与通知方式。
# 约束：不执行外部调用；未知参数拒绝，来源必须属于员工，旧客户草稿必须与明确指定公司一致。
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


# 功能：幂等创建待确认动作。
# 输入：`actor`、`data` 含 company、tool、parameters、idempotency_key 和可选 conversation。
# 输出：ToolAction。
# 逻辑：按员工串行化创建；允许工作空间或当前客户历史会话，相同键只接受相同语义。
# 约束：仅保存计划；用户必须另行批准，不从聊天文本推断批准。
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


# 功能：批准或取消尚未执行的动作。
# 输入：`action`、`actor`、`expected`、`decision`，仅 approved/cancelled。
# 输出：更新后的动作。
# 逻辑：要求 owner 提交正确版本；QQ 关闭时仅允许取消，批准后参数冻结。
# 约束：执行中的动作不能保证撤销，不接受取消；不在审批请求内发送外部消息。
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


# 功能：执行一次真实 Google 请求或 QQ SMTP 提交。
# 输入：`action` 为 running 动作，`credentials` 为已验证凭证。
# 输出：外部标识、可选链接与 QQ 提交状态的结果字典。
# 逻辑：QQ 使用冻结内容和 qq_smtp，Gmail 使用 MIME Base64URL，日历使用稳定事件 ID。
# 约束：Google 仅 execute(num_retries=0)，QQ 仅提交一次 DATA；不重试、不生成内容、不扩大收件人范围。
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


# 功能：领取并执行一个已批准动作。
# 输入：`action_id` 为动作 UUID。
# 输出：动作最终状态；非 approved 直接返回当前状态。
# 逻辑：领取后核对 QQ 能力及连接版本；禁用时在网络前记为 failed，SMTP 结果不明为 uncertain。
# 约束：SMTP 接受不代表最终送达；不自动重试；进程中断留下 running，需人工核对。
def run_action(action_id):
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


# 功能：将已中断或结果未知的动作标记为待人工处理。
# 输入：`action`、`actor`、`expected`。
# 输出：标记 uncertain 后的动作。
# 逻辑：仅接受 running，保留参数和执行时间，记录人工核对需要。
# 约束：不会声明外部成功或重新执行；外部事件真实性需要独立查询确认。
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


# 功能：只读核对未知动作是否已存在于外部服务。
# 输入：`action`、`actor`、`expected` 为当前版本。
# 输出：找到明确标识则更新成功，否则保持 uncertain 并抛明确错误。
# 逻辑：QQ 开启后才核对 IMAP 副本，Gmail 查 Message-ID，日历查事件 ID；写回前校验版本。
# 约束：不重新发送或创建；未找到不能证明未执行，不能自动解除报价冻结。
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
