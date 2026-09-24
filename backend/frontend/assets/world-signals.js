/** 职责：展示新闻中的公开销售线索、来源金额和证据。
 * 实现：事实与推断分区，来源金额保留币种、类型及范围；十进制字符串按文本分组，不经过浮点转换。
 * 关联：world-news.js 在资讯卡片与详情调用；金额不传入活动地图或 CRM 汇总。
 * 目录：text、label、formatSourceAmount、signalSummary、signalDetail。
 * 变量索引：signalLabels 为事件类型文案；amountLabels 为金额口径；scopeLabels 为金额覆盖范围。
 */
import { language } from './i18n.js?v=20260921-product';
import { escapeHtml as e } from './api.js?v=20260921-product';

const signalLabels = { expansion: ['扩产', 'Expansion'], new_factory: ['新建工厂', 'New factory'], tender: ['招标', 'Tender'], equipment_upgrade: ['设备升级', 'Equipment upgrade'], procurement: ['采购', 'Procurement'], other: ['其他事件', 'Other event'] };
const amountLabels = { total_investment: ['项目总投资', 'Total investment'], procurement_budget: ['采购预算', 'Procurement budget'], tender_amount: ['招标金额', 'Tender amount'], contract_amount: ['合同金额', 'Contract amount'], other: ['其他来源金额', 'Other reported amount'] };
const scopeLabels = { whole_project: ['整个项目', 'Whole project'], equipment_procurement: ['设备采购', 'Equipment procurement'], other: ['其他范围', 'Other scope'] };

/** 功能：选择界面语言。输入：zh/en 文案。输出：文本。逻辑：沿用共享语言设置。约束：不翻译新闻事实。 */
function text(zh, en) { return language === 'en' ? en : zh; }
/** 功能：解释契约枚举。输入：labels 文案表和 value 值。输出：可转义的文本。逻辑：未知值原样显示。约束：不把其他口径归为采购预算。 */
function label(labels, value) { return labels[value]?.[language === 'en' ? 1 : 0] || value || ''; }
/** 功能：精确格式化来源金额。输入：amount 为后端非负十进制字符串或 null/undefined。输出：带千位分隔的字符串或未提供提示。
 * 逻辑：只操作字符，去掉小数末尾零而保留有效位和零金额。约束：不换汇、不四舍五入，不把未知转换为零。 */
export function formatSourceAmount(amount) {
  if (amount === null || amount === undefined) return text('未提供', 'Not provided');
  const [integer, fraction = ''] = amount.split('.');
  const digits = fraction.replace(/0+$/, '');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (digits ? '.' + digits : '');
}
/** 功能：构造新闻卡片中的线索概览。输入：item 新闻记录。输出：转义后的 HTML 片段。逻辑：显示公司、事件和来源金额口径。约束：没有结构化信息时不伪造线索；不显示订单预估。 */
export function signalSummary(item) {
  const context = [item.company_name, label(signalLabels, item.signal_type)].filter(Boolean).join(' · ');
  const amount = item.amount !== null && item.amount !== undefined ? `${label(amountLabels, item.amount_type)} · ${item.currency} ${formatSourceAmount(item.amount)} · ${label(scopeLabels, item.amount_scope)}` : '';
  return (context ? `<p class="news-signal-summary">${e(context)}</p>` : '') + (amount ? `<p class="news-source-amount">${e(amount)}</p>` : '');
}
/** 功能：呈现公开线索的完整事实、推断和来源证据。输入：item 为新闻 API 记录。输出：安全 HTML。
 * 逻辑：逐项展示可选字段，缺失不补值；金额证据单独展示，潜在需求和相关性明确为推断。
 * 约束：不声明外部事实已被后端核实，不生成或关联系统商机，旧记录保持空态。 */
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
