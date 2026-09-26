"""Responsibility: Validate global events, news facts, and query conditions without generating recommendations or scores.
Implementation: Validate shared facts' sources, date precision, and cross-account collection deduplication; retain original opportunity authorization. Support explicit Agent date markers and date/region filters.
Relationships: Sales serializers call validate_insight and ResourceView uses filter_insights; data retains Record permissions and versioning.
Directory:
- validate_insight: Check cross-field and reference relationships.
- filter_insights: Process event and news query conditions.
Variable index:
- logger: Date-protocol compatibility and duplicate-collection diagnostics without bodies or tokens.
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


# Function: Validate event and news facts.
# Inputs: `serializer`: current serializer; `attrs`: validated fields.
# Outputs: Validated fields and time_precision explicitly specified by the compatibility protocol; invalid fields raise 400 and duplicate source collection raises 409.
# Logic: Merge existing values for validation. Agent date placeholders require a complete marker and UTC-noon match; explicit date precision accepts consistent UTC-midnight or noon boundaries with an exclusive end date. Database constraints prevent concurrent duplicates.
# Constraints: Preserve original timestamps and content; archival does not release sources. Opportunity links still require current-user permissions; no external site access.
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


# Function: Filter event or news queries.
# Inputs: `query`: QuerySet authorized under public-read rules; `params`: query parameters.
# Outputs: QuerySet with added conditions and time/ID ordering.
# Logic: Filter events by start time and news by publication time; from/to are timezone-aware ISO timestamps with inclusive lower and exclusive upper bounds.
# Constraints: No value ranking or cross-currency aggregation; reject invalid conditions explicitly rather than silently ignoring type fields.
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
