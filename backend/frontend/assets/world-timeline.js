/** Responsibility: Build a time-ordered map timeline from distinct news and event records.
 * Implementation: Apply explicit retention windows, include only evidenced news coordinates, and rank by distance from now; ongoing events rank at zero.
 * Relationships: world-news.js supplies policy and filters; world-dates.js preserves event calendar boundaries. No database mutation or monetary scoring.
 * Directory: timelineItems, filterTimeline, timelineDate.
 * Variable index: DAY_MS is the duration used for rolling retention windows.
 */
import { eventWindow, eventDates } from './world-dates.js?v=20260924-insights';
const DAY_MS = 86400000;

/** Function: Combine eligible map records. Inputs: events/news arrays, explicit now Date and policy with newsDays/eventHistoryDays.
 * Outputs: New sorted objects with kind, timelineAt and distanceMs; original records remain unchanged.
 * Logic: Retain upcoming/ongoing events and recently ended events; retain published news inside the window only with complete location evidence. Sort solely by time, then timestamp/ID for deterministic ties.
 * Constraints: Do not fabricate event dates for news, use creation time, infer coordinates, or give monetary amounts ranking weight. */
export function timelineItems(events, news, now, policy) {
  const clock = now.getTime();
  const eventItems = events.flatMap(item => {
    const window = eventWindow(item), start = window.start.getTime(), end = window.end.getTime();
    if (end < clock - policy.eventHistoryDays * DAY_MS) return [];
    return [{ ...item, kind: 'event', timelineAt: start, distanceMs: start > clock ? start - clock : end > clock ? 0 : clock - end }];
  });
  const newsItems = news.flatMap(item => {
    const published = new Date(item.published_at).getTime();
    if (published > clock || published < clock - policy.newsDays * DAY_MS || !item.city || !item.country || !item.location_evidence || !item.location_source_url || !Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) return [];
    return [{ ...item, kind: 'news', timelineAt: published, distanceMs: clock - published }];
  });
  return [...eventItems, ...newsItems].sort((a, b) => a.distanceMs - b.distanceMs || b.timelineAt - a.timelineAt || a.id.localeCompare(b.id));
}

/** Function: Intersect map filters. Inputs: items is the retained timeline; filters has type/time/country; now is the current clock.
 * Outputs: Ordered subset. Logic: News uses past publication windows; event 30-day/quarter views include ongoing and upcoming events. Country/type never rerank results.
 * Constraints: The all option still respects retention already applied by timelineItems; no hidden amount filter. */
export function filterTimeline(items, filters, now) {
  const quarterStart = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1);
  const quarterEnd = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3 + 3, 1);
  return items.filter(item => {
    if (filters.country !== 'all' && item.country !== filters.country) return false;
    if (filters.type !== 'all' && (item.kind === 'news' ? 'news' : item.event_type) !== filters.type) return false;
    if (filters.time === 'all') return true;
    if (item.kind === 'news') return item.timelineAt >= (filters.time === '30' ? now.getTime() - 30 * DAY_MS : quarterStart.getTime());
    const window = eventWindow(item);
    return window.end > now && window.start < (filters.time === '30' ? new Date(now.getTime() + 30 * DAY_MS) : quarterEnd);
  });
}

/** Function: Label a map record date. Inputs: item is a news/event timeline record. Outputs: Source publication or event start date string.
 * Logic: Preserve the source date and existing date-only event projection. Constraints: Never substitute collected/updated timestamps. */
export function timelineDate(item) { return item.kind === 'news' ? item.published_at.slice(0, 10) : eventDates(item).start; }
