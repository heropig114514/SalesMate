"""职责：校验全球活动、资讯事实及查询条件，不生成推荐或评分。
实现：校验共享事实的来源、日期精度及跨账号采集去重；关联商机保持原授权；兼容 Agent 明确日期标记，支持日期与地区筛选。
关联：销售序列化器调用 validate_insight，ResourceView 使用 filter_insights；数据沿用 Record 权限和版本。
目录：
- validate_insight：核对跨字段及引用关系。
- filter_insights：处理活动与资讯的查询条件。
变量索引：
- logger：兼容日期协议和重复采集诊断，不记录正文或令牌。
"""

from urllib.parse import urlsplit
from datetime import time, timezone
import logging
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from rest_framework.exceptions import ValidationError
from .models import Opportunity, WorldEvent
from .permissions import scope
from common.laboratory import enabled
from apps.crm.access import Conflict
from .insight_dates import LEGACY_DATE_MARKER, legacy_date_event

logger = logging.getLogger("salesmate.insights")


# 功能：校验活动与资讯事实。
# 输入：`serializer` 为当前序列化器，`attrs` 为验证后的字段。
# 输出：已校验字段及兼容协议明确指定的 time_precision；无效字段抛 400，同源采集重复抛 409。
# 逻辑：合并旧值验证；Agent 日期占位须完整标记和 UTC 中午匹配；显式 date 接受 UTC 午夜或中午一致边界，结束日排除；数据库约束防止并发重复。
# 约束：不改变原时间或正文；归档不释放来源；商机关联仍校验当前用户权限，不访问外站。
def validate_insight(serializer, attrs):
    url = attrs.get("source_url", getattr(serializer.instance, "source_url", ""))
    parsed = urlsplit(url)
    synthetic = attrs.get("data_source", getattr(serializer.instance, "data_source", "manual")) == "synthetic"
    if (url or not (enabled() or synthetic)) and (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password):
        raise ValidationError("source_url 必须是无凭证的 HTTPS 来源。")
    if serializer.Meta.model is WorldEvent:
        start = attrs.get("starts_at", getattr(serializer.instance, "starts_at", None))
        end = attrs.get("ends_at", getattr(serializer.instance, "ends_at", None))
        deadline = attrs.get("registration_deadline", getattr(serializer.instance, "registration_deadline", None))
        if start and end and end <= start:
            raise ValidationError("ends_at 必须晚于 starts_at。")
        if deadline and end and deadline > end:
            raise ValidationError("报名截止不得晚于活动结束。")
        source = attrs.get("data_source", getattr(serializer.instance, "data_source", "manual"))
        description = attrs.get("description", getattr(serializer.instance, "description", ""))
        if "time_precision" not in attrs and (serializer.instance is None or {"starts_at", "ends_at", "description", "data_source"} & attrs.keys()):
            if source == "agent" and LEGACY_DATE_MARKER in description.splitlines():
                if not legacy_date_event(source, description, start, end):
                    raise ValidationError("Agent 日期标记与 UTC 中午占位边界不一致。")
                attrs["time_precision"] = "date"
                logger.info("insight_date_protocol_accepted model=WorldEvent source_host=%s", parsed.hostname)
        precision = attrs.get("time_precision", getattr(serializer.instance, "time_precision", "datetime"))
        if precision == "date" and start and end:
            start_clock, end_clock = start.astimezone(timezone.utc).time(), end.astimezone(timezone.utc).time()
            if start_clock not in {time(0), time(12)} or end_clock != start_clock:
                raise ValidationError("日期型活动须使用一致的 UTC 午夜或中午边界，结束日期为排除边界。")
        if "opportunity_ids" in attrs:
            ids = [str(value) for value in attrs["opportunity_ids"]]
            attrs["opportunity_ids"] = ids
            if len(set(ids)) != len(ids) or scope(Opportunity, serializer.context["request"].user).filter(archived=False, pk__in=ids).count() != len(ids):
                raise ValidationError("opportunity_ids 必须唯一且指向可访问的未归档商机。")
    source = attrs.get("data_source", getattr(serializer.instance, "data_source", "manual"))
    if source == "agent" and url:
        duplicates = serializer.Meta.model.objects.filter(data_source="agent", source_url=url)
        if serializer.Meta.model is WorldEvent:
            duplicates = duplicates.filter(starts_at=start)
        if serializer.instance:
            duplicates = duplicates.exclude(pk=serializer.instance.pk)
        if duplicates.exists():
            logger.warning("insight_duplicate_rejected model=%s source_host=%s", serializer.Meta.model.__name__, parsed.hostname)
            raise Conflict("同一来源的 Agent 资讯或同场活动已存在（包括已归档记录），请读取现有记录。")
    return attrs


# 功能：筛选活动或资讯查询。
# 输入：`query` 为已按公共读取规则授权的 QuerySet，`params` 为查询参数。
# 输出：附加条件并按时间/ID 排序的 QuerySet。
# 逻辑：活动按开始时间、资讯按发布时间过滤；from/to 为带时区 ISO 时间，区间包含下界不包含上界。
# 约束：不按价值排序，不跨币种汇总；无效条件明确报错，不默默忽略类型字段。
def filter_insights(query, params):
    field = "starts_at" if query.model is WorldEvent else "published_at"
    category = "event_type" if query.model is WorldEvent else "category"
    if set(params) - {"page", "page_size", "archived", "q", "country", "from", "to", category}:
        raise ValidationError("活动资讯查询包含未支持的字段。")
    if "country" in params:
        country = params["country"].upper()
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValidationError("country 必须为两位 ISO 国家地区代码。")
        query = query.filter(country=country)
    if category in params:
        if params[category] not in dict(query.model._meta.get_field(category).choices):
            raise ValidationError("活动或资讯类别无效。")
        query = query.filter(**{category: params[category]})
    times = {}
    for key, lookup in (("from", "gte"), ("to", "lt")):
        if key in params:
            value = parse_datetime(params[key])
            if value is None or not is_aware(value):
                raise ValidationError("时间窗口必须是带时区的 ISO 时间。")
            times[key] = value
            query = query.filter(**{field + "__" + lookup: value})
    if len(times) == 2 and times["from"] >= times["to"]:
        raise ValidationError("from 必须早于 to。")
    return query.order_by(field if query.model is WorldEvent else "-" + field, "id")
