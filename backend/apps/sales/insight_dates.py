"""职责：解释全球活动的日期精度与现有 Agent 日期占位协议。
实现：仅识别明确的 Agent 标记及 UTC 中午边界；日期型结束边界排除当天，输出日期范围包含末日。
关联：insights 在写入时校验，WorldEventSerializer 为前端提供日期字段；不导入或修改 Agent。
目录：
- legacy_date_event：确认旧 Agent 是否明确声明日期型活动。
- date_range：从日期型活动的 UTC 边界获取包含末日的日期范围。
变量索引：
- LEGACY_DATE_MARKER：Agent 现有实现声明占位钟点的完整句子。
"""

from datetime import time, timedelta, timezone

LEGACY_DATE_MARKER = "来源仅提供日期；起止钟点是系统占位值，请以来源页为准。"


# 功能：识别无需修改 Agent 的明确日期协议。
# 输入：`data_source` 来源、`description` 原文说明、`start` 开始和 `end` 结束时间。
# 输出：bool；仅标记和占位时间均匹配时返回 True。
# 逻辑：要求 agent 来源、完整独立标记行，以及两个 UTC 中午时刻和递增日期。
# 约束：不从普通文本或午夜时间推测日期精度；无数据库或日志副作用。
def legacy_date_event(data_source, description, start, end):
    return bool(
        data_source == "agent" and LEGACY_DATE_MARKER in (description or "").splitlines()
        and start and end and start.utcoffset() is not None and end.utcoffset() is not None
        and start.astimezone(timezone.utc).time() == time(12)
        and end.astimezone(timezone.utc).time() == time(12) and end > start
    )


# 功能：投影日期型活动的真实日历范围。
# 输入：`start`、`end` 为已验证的带时区 DateTime 边界。
# 输出：开始日期和包含末日的结束日期二元组。
# 逻辑：UTC 日期承载来源日历日期，排除式结束边界减一天；不应用访问者时区。
# 约束：仅用于显式 date 精度，普通时间型活动不得调用以推断日期。
def date_range(start, end):
    return start.astimezone(timezone.utc).date(), end.astimezone(timezone.utc).date() - timedelta(days=1)
