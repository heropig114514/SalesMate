/** Responsibility: Supply a frozen source-currency to SGD reference table and map sizing.
 * Implementation: Multiply source amounts by fixed SGD-per-unit rates; derive a bounded logarithmic diameter independently of filters.
 * Relationships: world-signals.js formats reference values; world-map.js and world-news.js share the same sizing/legend. No database or network writes.
 * Directory: amountInSgd, bubbleDiameter.
 * Variable index: FX_REFERENCE records the common reference date and primary sources; SGD_RATES maps every supported insight currency to SGD per unit; BUBBLE_SCALE holds missing/min/max diameters, SGD baseline and pixels per decade.
 */
export const FX_REFERENCE = Object.freeze({
  date: '2026-09-21',
  ecb: 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.cn.html',
  twd: 'https://www.cbc.gov.tw/en/lp-700-2-1-60.html',
});
// ECB 2026-09-21 units per EUR: SGD 1.4647, USD 1.1490, CNY 7.6930,
// GBP .85780, JPY 180.70, KRW 1576.63, HKD 9.0144, INR 110.0975,
// CAD 1.6091, AUD 1.6098, CHF .9438. SGD per unit = 1.4647 / quoted rate.
// CBC's same-day TWD per USD is 31.758: TWD rate = (1.4647 / 1.1490) / 31.758.
// Freeze to eight decimal places for reference display; source monetary strings are never overwritten.
export const SGD_RATES = Object.freeze({
  CNY: 0.19039386, USD: 1.27476066, EUR: 1.46470000, GBP: 1.70750758,
  JPY: 0.00810570, KRW: 0.00092901, SGD: 1.00000000, TWD: 0.04013983,
  HKD: 0.16248447, INR: 0.01330366, CAD: 0.91026039, AUD: 0.90986458,
  CHF: 1.55191778,
});
export const BUBBLE_SCALE = Object.freeze({ missing: 14, min: 18, max: 80, baselineSgd: 100, pixelsPerDecade: 6 });

/** Function: Convert a source amount for approximate display. Inputs: item with decimal-string amount and supported currency.
 * Outputs: SGD number or null for absent amounts. Logic: Validate the source representation/rate before multiplying.
 * Constraints: Floating point is used only for reference visualization, never persistence or source text. Invalid values or unmapped currencies throw explicitly; zero is known. */
export function amountInSgd(item) {
  if (item.amount === null || item.amount === undefined) return null;
  if (typeof item.amount !== 'string' || !/^\d+(\.\d+)?$/.test(item.amount)) throw new TypeError('Invalid source amount for SGD conversion');
  if (!Object.hasOwn(SGD_RATES, item.currency)) throw new RangeError(`Missing fixed SGD rate: ${item.currency}`);
  const value = Number(item.amount) * SGD_RATES[item.currency];
  if (!Number.isFinite(value)) throw new RangeError('SGD reference amount exceeds display range');
  return value;
}

/** Function: Size a geographic marker. Inputs: amountSgd is nonnegative finite SGD or null.
 * Outputs: Diameter in CSS pixels. Logic: 18 + 6*log10(1+SGD/100), capped at 80; missing amounts use a distinct 14px marker.
 * Constraints: Fixed scale preserves comparisons after filtering; logarithmic diameter is not proportional monetary area. The 80px cap protects map usability for extreme valid source amounts. */
export function bubbleDiameter(amountSgd) {
  if (amountSgd === null) return BUBBLE_SCALE.missing;
  if (!Number.isFinite(amountSgd) || amountSgd < 0) throw new RangeError('Invalid SGD amount for map sizing');
  return Math.min(BUBBLE_SCALE.max, BUBBLE_SCALE.min + BUBBLE_SCALE.pixelsPerDecade * Math.log10(1 + amountSgd / BUBBLE_SCALE.baselineSgd));
}
