"""职责：校验 QQ 每次同步的显式范围并冻结时间窗口。
实现：天数与封数至少提供一项，均为正整数；两项同时提供时取交集。
关联：qq_views、views 校验 HTTP 输入；processing 固定批次范围；qq_sync 执行筛选。
目录：
- QQSyncOptionsSerializer：声明无预填的同步限制。
- QQSyncOptionsSerializer.validate：拒绝空范围。
- SyncRequestSerializer：声明通用同步请求的可选 QQ 范围。
- snapshot：将用户选择转为固定 UTC 时间窗口。
变量索引：
- QQSyncOptionsSerializer.recent_days：可空的最近天数。
- QQSyncOptionsSerializer.max_messages：可空的本批次处理封数。
- SyncRequestSerializer.sync_options：仅 QQ 需要的范围对象。
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .serializers import StrictSerializer


# 功能：限制每次 QQ 同步的处理范围。
# 逻辑：允许只填天数、只填封数或两项，空值不生成默认数值。
# 约束：字段须为正整数；未知字段由 StrictSerializer 拒绝。
class QQSyncOptionsSerializer(StrictSerializer):
    recent_days = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    max_messages = serializers.IntegerField(min_value=1, required=False, allow_null=True)

    # 功能：确保用户明确选择至少一种限制。
    # 输入：`attrs` 为完成字段验证的范围对象。
    # 输出：保留两项的规范对象，未填项为 None。
    # 逻辑：无任何正整数时返回 400 校验错误。
    # 约束：不把空选择解释为全量同步。
    def validate(self, attrs):
        if not any(attrs.values()):
            raise serializers.ValidationError("请填写最近 N 天或最多 N 封，至少一项。")
        return {"recent_days": attrs.get("recent_days"), "max_messages": attrs.get("max_messages")}


# 功能：声明共用邮箱同步请求的可选范围。
# 逻辑：Gmail 可以保持空请求，QQ 在服务层要求提供 sync_options。
# 约束：范围条件不会静默应用于 Gmail。
class SyncRequestSerializer(StrictSerializer):
    sync_options = QQSyncOptionsSerializer(required=False)


# 功能：冻结一次普通 QQ 同步的范围。
# 输入：`options` 为用户选择；隐式读取服务器 UTC 当前时间。
# 输出：包含 recent_days、max_messages、since、until 的 JSON 对象。
# 逻辑：校验后以排队时刻为上界，按 24 小时乘天数计算下界。
# 约束：时间溢出明确拒绝；重试使用原快照而不调用本函数推进时间。
def snapshot(options):
    serializer = QQSyncOptionsSerializer(data=options or {})
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    until = timezone.now()
    try:
        since = until - timedelta(days=values["recent_days"]) if values["recent_days"] else None
    except (OverflowError, ValueError):
        raise serializers.ValidationError({"recent_days": "天数超出支持的日期范围。"}) from None
    return {**values, "since": since.isoformat() if since else None, "until": until.isoformat()}
