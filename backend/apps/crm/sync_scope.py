"""职责：校验 Gmail/QQ 每次同步的显式范围并冻结时间窗口。
实现：天数与封数至少提供一项，均为正整数；Gmail 默认最多 50 封，明确批准后才可扩大。
关联：qq_views、views 校验 HTTP 输入；processing 固定批次范围；durable_sync、qq_sync 执行筛选。
目录：
- MailboxSyncOptionsSerializer：声明无预填的同步限制。
- MailboxSyncOptionsSerializer.validate：拒绝空范围。
- SyncRequestSerializer：声明通用同步请求的必填邮箱范围。
- snapshot：将用户选择转为固定 UTC 时间窗口。
变量索引：
- MailboxSyncOptionsSerializer.recent_days：可空的最近天数。
- MailboxSyncOptionsSerializer.max_messages：可空的本批次处理封数。
- MailboxSyncOptionsSerializer.allow_large_sync：对明确超量封数的本次批准，可省略。
- SyncRequestSerializer.sync_options：Gmail/QQ 共用的范围对象。
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from agent.tools.gmail_scope import gmail_message_limit

from .serializers import StrictSerializer


# 功能：限制每次邮箱同步的处理范围。
# 逻辑：允许只填天数、只填封数或两项；超量批准本身不能替代范围，由服务层按提供方处理上限。
# 约束：字段须为正整数；未知字段由 StrictSerializer 拒绝。
class MailboxSyncOptionsSerializer(StrictSerializer):
    recent_days = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    max_messages = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    allow_large_sync = serializers.BooleanField(required=False)

    # 功能：确保用户明确选择至少一种限制。
    # 输入：`attrs` 为完成字段验证的范围对象。
    # 输出：保留范围及显式提供的批准字段，未填范围项为 None。
    # 逻辑：无任何范围正整数时返回 400，批准标记不构成有效范围。
    # 约束：不把空选择解释为全量同步。
    def validate(self, attrs):
        if not (attrs.get("recent_days") or attrs.get("max_messages")):
            raise serializers.ValidationError("请填写最近 N 天或最多 N 封，至少一项。")
        return {"recent_days": attrs.get("recent_days"), "max_messages": attrs.get("max_messages"),
                **({"allow_large_sync": attrs["allow_large_sync"]} if "allow_large_sync" in attrs else {})}


# 功能：声明共用邮箱同步请求的必填范围。
# 逻辑：Gmail 与 QQ 均须提供 sync_options。
# 约束：空请求不能启动全量同步。
class SyncRequestSerializer(StrictSerializer):
    sync_options = MailboxSyncOptionsSerializer()


# 功能：冻结一次普通邮箱同步的范围。
# 输入：`options` 为用户选择；`gmail` 指定 Gmail 秒级窗口及 50 封策略；隐式读取服务器 UTC 当前时间。
# 输出：包含 recent_days、max_messages、since、until 及显式批准的 JSON 对象。
# 逻辑：校验后以排队时刻为上界，Gmail 截断到整秒并冻结有效封数；按 24 小时乘天数计算下界。
# 约束：时间溢出明确拒绝；重试使用原快照而不调用本函数推进时间。
def snapshot(options, *, gmail=False):
    serializer = MailboxSyncOptionsSerializer(data=options or {})
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    until = timezone.now()
    if gmail:
        try:
            values["max_messages"] = gmail_message_limit(values)
        except ValueError as error:
            raise serializers.ValidationError({"max_messages": str(error)}) from None
        until = until.replace(microsecond=0)
    try:
        since = until - timedelta(days=values["recent_days"]) if values["recent_days"] else None
    except (OverflowError, ValueError):
        raise serializers.ValidationError({"recent_days": "天数超出支持的日期范围。"}) from None
    return {**values, "since": since.isoformat() if since else None, "until": until.isoformat()}
