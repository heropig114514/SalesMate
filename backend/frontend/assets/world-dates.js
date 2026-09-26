/** Responsibility: Interpret event-date precision explicitly supplied by the backend.
 * Implementation: date uses inclusive starts_on/ends_on; timestamp events retain their original times; all-day calendars use exclusive end dates.
 * Relationships: Shared by world-news.js display, filtering, countdowns, and ICS; no parsing of Agent free text or network access.
 * Directory: eventDates, eventWindow, calendarBounds.
 * Variable index: No module variables; intermediate date values remain function-local.
 */

/** Function: Get display dates. Inputs: item is a backend event. Outputs: start/end date strings.
 * Logic: Only explicit date precision uses backend date projections; timestamp events retain existing ISO date display. Constraints: Missing date fields raise errors rather than inferred placeholder times. */
export function eventDates(item) {
  if (item.time_precision === 'date') {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(item.starts_on || '') || !/^\d{4}-\d{2}-\d{2}$/.test(item.ends_on || '')) throw new Error('Date-only event is missing its calendar dates.');
    return { start: item.starts_on, end: item.ends_on };
  }
  return { start: item.starts_at.slice(0, 10), end: item.ends_at.slice(0, 10) };
}

/** Function: Get filter/countdown boundaries. Inputs: item is a backend event. Outputs: start/end Date values with an exclusive end.
 * Logic: date starts at midnight in the viewer's calendar and ends at midnight after the final day; datetime retains actual instants. Constraints: Never display or treat UTC placeholder times as real date-only event times. */
export function eventWindow(item) {
  if (item.time_precision !== 'date') return { start: new Date(item.starts_at), end: new Date(item.ends_at) };
  const dates = eventDates(item), start = new Date(dates.start + 'T00:00:00'), end = new Date(dates.end + 'T00:00:00');
  end.setDate(end.getDate() + 1);
  return { start, end };
}

/** Function: Generate calendar start/end properties. Inputs: item is a backend event. Outputs: Two ICS time properties.
 * Logic: date uses VALUE=DATE and ends on the day after the source's final date; datetime converts actual instants to UTC. Constraints: No placeholder times or source-date changes. */
export function calendarBounds(item) {
  if (item.time_precision === 'date') {
    const dates = eventDates(item), end = new Date(dates.end + 'T00:00:00Z');
    end.setUTCDate(end.getUTCDate() + 1);
    return ['DTSTART;VALUE=DATE:' + dates.start.replaceAll('-', ''), 'DTEND;VALUE=DATE:' + end.toISOString().slice(0, 10).replaceAll('-', '')];
  }
  return [item.starts_at, item.ends_at].map((value, index) => ['DTSTART:', 'DTEND:'][index] + new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z'));
}
