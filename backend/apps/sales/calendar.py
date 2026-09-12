"""职责：提供 Google 日历的受授权只读查询。
实现：限定员工连接和带时区的时间窗口，显式单次查询事件及忙闲。
关联：integrations 管理凭证；创建事件必须通过 actions 审批流程。
目录：
- read_calendar：读取事件或忙闲，不创建或通知参会人。
变量索引：
- logger：日历调用的脱敏错误日志。
"""

import logging
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from googleapiclient.discovery import build
from rest_framework.exceptions import ValidationError
from apps.crm.access import InvalidState
from .integrations import credentials_for
from .models import Connection

logger = logging.getLogger("salesmate.calendar")


# 功能：读取明确时间范围的事件或忙闲。
# 输入：`actor`、`operation` 为 events/freebusy、`parameters` 含连接、日历、start/end 及可选 page_token。
# 输出：有限事件列表和 next_page_token，或忙闲区间与外部日历错误。
# 逻辑：验证账号所有权、时间时区和上限，再执行一次 Google API 请求。
# 约束：窗口至多 366 天，事件每页 100 条；不自动翻页、不创建会议、不重试。
def read_calendar(actor, operation, parameters):
    required = {"connection_id", "calendar_id", "start", "end"}
    if (
        operation not in ("events", "freebusy")
        or not required.issubset(parameters)
        or set(parameters) - required - {"page_token"}
    ):
        raise ValidationError(
            "需要 connection_id、calendar_id、start、end 和可选 page_token。"
        )
    start, end = parse_datetime(parameters["start"]), parse_datetime(parameters["end"])
    if (
        start is None
        or end is None
        or timezone.is_naive(start)
        or timezone.is_naive(end)
        or not 0 < (end - start).total_seconds() <= 366 * 86400
    ):
        raise ValidationError("日历范围须带时区、结束晚于开始且不超过 366 天。")
    connection = Connection.objects.get(
        pk=parameters["connection_id"], owner=actor, provider="calendar", archived=False
    )
    credentials = credentials_for(connection)
    try:
        api = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        if operation == "freebusy":
            result = (
                api.freebusy()
                .query(
                    body={
                        "timeMin": start.isoformat(),
                        "timeMax": end.isoformat(),
                        "items": [{"id": parameters["calendar_id"]}],
                    }
                )
                .execute(num_retries=0)
            )
            return {
                "start": result["timeMin"],
                "end": result["timeMax"],
                "calendars": result.get("calendars", {}),
            }
        result = (
            api.events()
            .list(
                calendarId=parameters["calendar_id"],
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=100,
                singleEvents=True,
                orderBy="startTime",
                pageToken=parameters.get("page_token"),
            )
            .execute(num_retries=0)
        )
        return {
            "results": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "summary",
                        "description",
                        "start",
                        "end",
                        "status",
                        "htmlLink",
                    )
                }
                for item in result.get("items", [])
            ],
            "next_page_token": result.get("nextPageToken"),
        }
    except Exception as exception:
        logger.warning(
            "calendar_query_failed connection_id=%s operation=%s error_type=%s",
            connection.pk,
            operation,
            type(exception).__name__,
        )
        raise InvalidState("日历查询失败，请检查权限、日历标识和服务连接。") from None
