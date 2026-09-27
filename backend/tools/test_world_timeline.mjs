/** Responsibility: Verify chronology, retention boundaries and mapped-news eligibility.
 * Implementation: Fixed clocks and pure records exercise timeline selection independently of UI layout.
 * Relationships: Imports production world-timeline.js; no browser, database or network calls.
 * Directory: event, news.
 * Variable index: now is a fixed clock; DAY is one day; policy fixes test retention; rows is the ordered output; boundaryNews/boundaryEvent cover exact cutoff behavior.
 */
import assert from 'node:assert/strict';
import { timelineItems, filterTimeline } from '../frontend/assets/world-timeline.js';
const now = new Date('2026-09-27T12:00:00Z'), DAY = 86400000;
const policy = { newsDays: 90, eventHistoryDays: 30 };
/** Function: Construct a timed event fixture. Inputs: id, start/end day offsets. Outputs: Event record.
 * Logic: Offset a fixed clock; all amounts remain unknown. Constraints: Not production data or external verification. */
function event(id, start, end) { return { id, country: 'GB', event_type: 'exhibition', starts_at: new Date(+now + start * DAY).toISOString(), ends_at: new Date(+now + end * DAY).toISOString(), time_precision: 'datetime', amount: null }; }
/** Function: Construct located news. Inputs: id, age in days, optional patch. Outputs: News record.
 * Logic: Supply an explicit evidenced location and fixed publication instant. Constraints: Money must have no sorting influence. */
function news(id, age, patch = {}) { return { id, published_at: new Date(+now - age * DAY).toISOString(), city: 'Glasgow', country: 'GB', latitude: 55.86, longitude: -4.25, location_evidence: 'Glasgow facility', location_source_url: 'https://example.org/location', ...patch }; }
const rows = timelineItems([event('future', 2, 3), event('ongoing', -1, 1), event('old-event', -35, -31), event('next-year', 300, 301)], [news('recent', 1, { amount: null }), news('older-large-amount', 20, { amount: '999999999999' }), news('stale', 91), news('future-news', -1), news('unlocated', 0, { latitude: null })], now, policy);
assert.deepEqual(rows.map(row => row.id), ['ongoing', 'recent', 'future', 'older-large-amount', 'next-year']);
assert.deepEqual(filterTimeline(rows, { type: 'news', time: 'all', country: 'GB' }, now).map(row => row.id), ['recent', 'older-large-amount']);
assert.deepEqual(filterTimeline(rows, { type: 'all', time: '30', country: 'GB' }, now).map(row => row.id), ['ongoing', 'recent', 'future', 'older-large-amount']);
assert.equal(filterTimeline(rows, { type: 'all', time: 'all', country: 'DE' }, now).length, 0);
const boundaryNews = timelineItems([], [news('boundary', 90), news('expired', 90 + 1 / DAY)], now, policy);
assert.deepEqual(boundaryNews.map(row => row.id), ['boundary']);
const boundaryEvent = timelineItems([event('boundary', -31, -30), event('expired', -31, -30 - 1 / DAY)], [], now, policy);
assert.deepEqual(boundaryEvent.map(row => row.id), ['boundary']);
assert.deepEqual(timelineItems([], [news('zero-coordinate', 0, { latitude: 0, longitude: 0 })], now, policy).map(row => row.id), ['zero-coordinate']);
console.log('World timeline: chronology, mixed records, cutoff boundaries, future/ongoing events, missing/zero coordinates and filters passed.');
