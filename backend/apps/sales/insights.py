"""职责：校验全球活动、资讯事实及查询条件，不生成推荐或评分。
实现：实验模式和显式虚拟数据允许缺来源；商机关联使用统一权限范围，限制已有 URL 和时间窗口；支持显式日期与地区过滤。
关联：销售序列化器调用 validate_insight，ResourceView 使用 filter_insights；数据沿用 Record 权限和版本。
目录：
- validate_insight：核对跨字段及引用关系。
- filter_insights：处理活动与资讯的查询条件。
变量索引：
- 无
"""

from urllib.parse import urlsplit
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from rest_framework.exceptions import ValidationError
from .models import Opportunity, WorldEvent
from .permissions import scope
from common.laboratory import enabled


# 功能：校验活动与资讯事实。
# 输入：`serializer` 为当前序列化器，`attrs` 为验证后的字段。
# 输出：原字段字典；无效来源、时间或外部账号关联抛 400。
# 逻辑：更新合并旧值比较；商机关联遵循业务 scope，实验模式可跨账号；synthetic 或实验模式可不填来源，已填 URL 仍须无凭证 HTTPS。
# 约束：不读取外站，不根据客户名称推断关系，不更改商机或评分输入。
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
        if "opportunity_ids" in attrs:
            ids = [str(value) for value in attrs["opportunity_ids"]]
            attrs["opportunity_ids"] = ids
            if len(set(ids)) != len(ids) or scope(Opportunity, serializer.context["request"].user).filter(archived=False, pk__in=ids).count() != len(ids):
                raise ValidationError("opportunity_ids 必须唯一且指向可访问的未归档商机。")
    return attrs


# 功能：筛选活动或资讯查询。
# 输入：`query` 为已限定 owner 的 QuerySet，`params` 为查询参数。
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
