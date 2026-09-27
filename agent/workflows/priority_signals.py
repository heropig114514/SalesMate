"""Convert existing L1 timing facts into source-bound, deterministic L4 deadlines.

No model calls, new L1 fields, inferred years, relative dates or default timezones.
Only explicit calendar dates and explicitly zoned times are supported. Unsupported
or conditional timing remains in the profile instead of becoming a scoring fact.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone


logger = logging.getLogger("salesmate.agent.priority_signals")
_MONTHS = {name.lower(): index for index, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), 1)}
_MONTHS.update({name[:3]: value for name, value in list(_MONTHS.items())})
_DATE = re.compile(
    r"\b(?:(?P<iso>\d{4}-\d{2}-\d{2})|"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4}))(?=$|[T\s.,;])",
    re.I,
)
_TIME = re.compile(r"^(?:T|\s+(?:at\s+)?)(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(AM|PM))?", re.I)
_ZONE = re.compile(r"^\s*(?:Singapore time|SGT|Asia/Singapore|(?:UTC|GMT)?(?P<offset>[+-]\d{2}:?\d{2})|(?P<utc>UTC|GMT|Z)(?![A-Za-z+\-]))", re.I)
_CANCEL = re.compile(r"\b(?:no|without)\s+(?:(?:new|response|delivery|firm|confirmed|required)\s+)*(?:deadline|cutoff|date|timetable)|\b(?:cancelled|canceled|withdrawn|postponed|superseded)\b", re.I)
_CONDITIONAL = re.compile(r"\b(?:if|unless|might|tentative|tentatively|unconfirmed)\b", re.I)
_RESPONSE = re.compile(r"\b(?:response|respond|reply|quotation|quote|information|submission|submit|proposal|review|handover|cutoff)\b", re.I)


def _explicit_date(text: str) -> str | None:
    """Parse a single explicit date; never turn an unzoned clock time into a date."""
    matches = list(_DATE.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    try:
        if match['iso']:
            day = datetime.strptime(match['iso'], '%Y-%m-%d')
        else:
            month = _MONTHS.get(match['month'].lower())
            if month is None:
                return None
            day = datetime(int(match['year']), month, int(match['day']))
        suffix = text[match.end():]
        time = _TIME.match(suffix)
        if time is None:
            # Unsupported clock formats must not silently gain date-only precision.
            if re.match(r"\s+(?:at\s+)?\d", suffix, re.I):
                return None
            return day.date().isoformat()
        hour, minute, second = int(time[1]), int(time[2]), int(time[3] or 0)
        if time[4]:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if time[4].lower() == 'pm' else 0)
        zone = _ZONE.match(suffix[time.end():])
        if zone is None:
            return None
        if zone['utc']:
            tz = timezone.utc
        elif zone['offset']:
            value = zone['offset'].replace(':', '')
            hours, minutes = int(value[1:3]), int(value[3:5])
            if hours > 14 or minutes > 59 or (hours == 14 and minutes):
                return None
            tz = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if value[0] == '+' else -1)))
        else:
            tz = timezone(timedelta(hours=8))
        return day.replace(hour=hour, minute=minute, second=second, tzinfo=tz).isoformat()
    except ValueError:
        return None


def deadline_signals(emails: list[dict]) -> list[dict]:
    """Read chronological inbound L1 timing facts, keeping the latest per purpose.

The latest timing update replaces earlier deadlines of the same purpose; a
withdrawal clears that purpose. Relative/ambiguous replacements clear old dates
too, preventing the superseded date from remaining urgent. Separate response
and delivery milestones are retained. At most two additional signals are emitted.
"""
    current: dict[str, list[dict]] = {}
    skipped = 0
    for email in emails:
        if email.get('direction') != 'inbound' or email.get('extract_status') != 'completed':
            continue
        updates: dict[str, list[dict]] = {}
        content = '\n'.join(filter(None, [email.get('subject'), email.get('body_text')]))
        sentences = re.split(r'(?<=[.!?])\s+|\n+', content)
        for fact in email['facts'].get('delivery_time', []):
            for evidence in fact['evidences']:
                normalized = ' '.join(evidence.split())
                sentence = next((s for s in sentences if normalized in ' '.join(s.split())), None)
                if sentence is None:
                    skipped += 1
                    continue
                purpose = 'response' if _RESPONSE.search(sentence) else 'delivery'
                updates.setdefault(purpose, [])
                if _CANCEL.search(sentence) or _CONDITIONAL.search(sentence):
                    skipped += 1
                    continue
                value = _explicit_date(sentence)
                if value is None:
                    skipped += 1
                    continue
                signal = {'type': 'DEADLINE', 'value': value, 'confidence': 1.0,
                          'evidence': sentence.strip(), 'source_id': email['dedupe_key']}
                if not any(item['value'] == value for item in updates[purpose]):
                    updates[purpose].append(signal)
        current.update(updates)
    # Several unresolved milestones in one purpose are ambiguous for company-level priority.
    result = [items[0] for items in current.values() if len(items) == 1]
    logger.info('l4_deadlines_prepared signals=%s skipped=%s', len(result), skipped)
    return result
