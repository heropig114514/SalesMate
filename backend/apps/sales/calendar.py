"""Responsibility: Provide authorized read-only Google Calendar queries.
Implementation: Restrict employee connections and timezone-aware windows; explicitly query events or free/busy once.
Relationships: integrations manages credentials; event creation must use the actions approval workflow.
Directory:
- read_calendar: Read events or free/busy without creating events or notifying attendees.
Variable index:
- logger: Redacted calendar-call error logs.
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


# Function: Read events or free/busy within an explicit time range.
# Inputs: `actor`; `operation`: events/freebusy; `parameters`: connection, calendar, start/end, and optional page_token.
# Outputs: A bounded event list and next_page_token, or busy intervals and external calendar errors.
# Logic: Validate account ownership, timezones, and limits, then issue one Google API request.
# Constraints: At most 366 days per window and 100 events per page; no automatic pagination, meeting creation, or retries.
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
