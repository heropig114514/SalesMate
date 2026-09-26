"""Responsibility: Interpret global-event date precision and the existing Agent date-placeholder protocol.
Implementation: Recognize only explicit Agent markers and UTC-noon boundaries; date-only end boundaries exclude that day, while output ranges include the final day.
Relationships: insights validates writes and WorldEventSerializer supplies frontend date fields; do not import or modify Agent.
Directory:
- legacy_date_event: Check whether the legacy Agent explicitly declares a date-only event.
- date_range: Derive an inclusive date range from a date-only event's UTC boundaries.
Variable index:
- LEGACY_DATE_MARKER: Complete sentence used by the existing Agent to declare placeholder times.
"""

from datetime import time, timedelta, timezone

LEGACY_DATE_MARKER = "来源仅提供日期；起止钟点是系统占位值，请以来源页为准。"


# Function: Recognize the explicit date protocol without modifying Agent.
# Inputs: `data_source`: source; `description`: original description; `start` and `end`: timestamps.
# Outputs: bool; True only when both the marker and placeholder times match.
# Logic: Require an agent source, a complete standalone marker line, two UTC-noon timestamps, and increasing dates.
# Constraints: Do not infer date precision from ordinary prose or midnight times; no database or logging effects.
def legacy_date_event(data_source, description, start, end):
    return bool(
        data_source == "agent" and LEGACY_DATE_MARKER in (description or "").splitlines()
        and start and end and start.utcoffset() is not None and end.utcoffset() is not None
        and start.astimezone(timezone.utc).time() == time(12)
        and end.astimezone(timezone.utc).time() == time(12) and end > start
    )


# Function: Project the actual calendar range of a date-only event.
# Inputs: `start`, `end`: validated timezone-aware DateTime boundaries.
# Outputs: Tuple of start date and inclusive end date.
# Logic: UTC dates carry the source calendar dates; subtract one day from the exclusive end boundary without applying the viewer's timezone.
# Constraints: Use only for explicit date precision; never infer dates for ordinary timestamp-based events.
def date_range(start, end):
    return start.astimezone(timezone.utc).date(), end.astimezone(timezone.utc).date() - timedelta(days=1)
