/** Responsibility: Display public sales leads, source amounts, and evidence in news.
 * Implementation: Separate facts/inferences and retain currency, amount type, and scope; group decimal strings as text without floating-point conversion.
 * Relationships: world-news.js calls this for cards/details; amounts never enter event maps or CRM aggregates.
 * Directory: text, label, formatSourceAmount, signalSummary, signalDetail.
 * Variable index: signalLabels describes event types; amountLabels describes amount definitions; scopeLabels describes amount coverage.
 */
import { language } from './i18n.js?v=20260921-product';
import { escapeHtml as e } from './api.js?v=20260921-product';

const signalLabels = { expansion: ['扩产', 'Expansion'], new_factory: ['新建工厂', 'New factory'], tender: ['招标', 'Tender'], equipment_upgrade: ['设备升级', 'Equipment upgrade'], procurement: ['采购', 'Procurement'], other: ['其他事件', 'Other event'] };
const amountLabels = { total_investment: ['项目总投资', 'Total investment'], procurement_budget: ['采购预算', 'Procurement budget'], tender_amount: ['招标金额', 'Tender amount'], contract_amount: ['合同金额', 'Contract amount'], other: ['其他来源金额', 'Other reported amount'] };
const scopeLabels = { whole_project: ['整个项目', 'Whole project'], equipment_procurement: ['设备采购', 'Equipment procurement'], other: ['其他范围', 'Other scope'] };

/** Function: Select interface language. Inputs: zh/en text. Outputs: Text. Logic: Shared language settings. Constraints: Never translate news facts. */
function text(zh, en) { return language === 'en' ? en : zh; }
/** Function: Explain contract enums. Inputs: labels table and value. Outputs: Escapable text. Logic: Preserve unknown values. Constraints: Never classify unrelated amount types as procurement budgets. */
function label(labels, value) { return labels[value]?.[language === 'en' ? 1 : 0] || value || ''; }
/** Function: Format source amounts precisely. Inputs: amount is a backend nonnegative decimal string or null/undefined. Outputs: A thousands-separated string or missing-value message.
 * Logic: Operate on characters only, removing trailing fractional zeros while retaining significant digits and zero amounts. Constraints: No conversion, rounding, or unknown-to-zero substitution. */
export function formatSourceAmount(amount) {
  if (amount === null || amount === undefined) return text('未提供', 'Not provided');
  const [integer, fraction = ''] = amount.split('.');
  const digits = fraction.replace(/0+$/, '');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (digits ? '.' + digits : '');
}
/** Function: Build a news-card lead summary. Inputs: item is a news record. Outputs: Escaped HTML. Logic: Show company, event, and source-amount definitions. Constraints: Never fabricate leads without structured data or display estimated orders. */
export function signalSummary(item) {
  const context = [item.company_name, label(signalLabels, item.signal_type)].filter(Boolean).join(' · ');
  const amount = item.amount !== null && item.amount !== undefined ? `${label(amountLabels, item.amount_type)} · ${item.currency} ${formatSourceAmount(item.amount)} · ${label(scopeLabels, item.amount_scope)}` : '';
  return (context ? `<p class="news-signal-summary">${e(context)}</p>` : '') + (amount ? `<p class="news-source-amount">${e(amount)}</p>` : '');
}
/** Function: Present complete public-lead facts, inferences, and source evidence. Inputs: item is a news API record. Outputs: Safe HTML.
 * Logic: Display optional fields without filling missing values; show amount evidence separately and explicitly label potential demand/relevance as inference.
 * Constraints: Never claim backend verification of external facts, generate/link system opportunities, or fill empty historical records. */
export function signalDetail(item) {
  const facts = [
    [text('公司 / 机构', 'Company / organization'), item.company_name],
    [text('事件类型', 'Event type'), label(signalLabels, item.signal_type)],
    [text('项目名称', 'Project'), item.project_name],
    [text('新闻披露的需求', 'Reported demand'), item.demand_description],
    [text('时间节点', 'Time window'), item.time_window],
  ].filter(([, value]) => value);
  const inferred = item.potential_sales_need || item.opportunity_reason;
  const hasAmount = item.amount !== null && item.amount !== undefined;
  if (!facts.length && !inferred && !item.evidence && !hasAmount) return `<section class="news-signal"><h2>${text('新闻销售线索', 'News sales signal')}</h2><p>${text('暂无结构化线索信息。', 'No structured signal information available.')}</p></section>`;
  return `<section class="news-signal"><h2>${text('新闻销售线索', 'News sales signal')}</h2>
    <dl>${facts.map(([key, value]) => `<dt>${e(key)}</dt><dd>${e(value)}</dd>`).join('')}</dl>
    ${item.evidence ? `<h3>${text('来源原文证据', 'Source evidence')}</h3><blockquote>${e(item.evidence)}</blockquote>` : ''}
    <section class="news-reported-amount"><h3>${text('来源披露金额', 'Reported amount')}</h3>
      ${hasAmount ? `<p class="news-source-amount">${e(item.currency)} ${e(formatSourceAmount(item.amount))}</p><p>${e(label(amountLabels, item.amount_type))} · ${e(label(scopeLabels, item.amount_scope))}</p><blockquote>${e(item.amount_evidence)}</blockquote><p>${text('按来源口径记录，不代表我们的订单金额。', 'Recorded in the source context; not our sales order value.')}</p>` : `<p>${text('暂无结构化金额信息。', 'No structured amount information available.')}</p>`}
    </section>
    ${inferred ? `<section class="news-inference"><h3>${text('需求推断 · 非已确认采购需求', 'Inferred need · not confirmed procurement')}</h3>${item.potential_sales_need ? `<p>${e(item.potential_sales_need)}</p>` : ''}${item.opportunity_reason ? `<h4>${text('产品相关性判断', 'Product relevance reasoning')}</h4><p>${e(item.opportunity_reason)}</p>` : ''}</section>` : ''}
  </section>`;
}
