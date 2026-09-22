/** 职责：展示数据库商机评分、解释和信号证据。
 * 实现：分页读取最新评分，按需读取授权上下文；任意 JSON 解释（含空数组元素）转为纯文本，虚拟结果明确标记。
 * 关联：侧栏优先级入口移除后更新导航缓存；共享导航使用移除实验入口后的缓存版本；priority-board、opportunity-context API；不调用模型、不修改评分。
 * 目录：$、display、collection、renderDetail、showSource、select、load。
 * 变量索引：$ DOM 查询；state 保存分页、请求序号和已选上下文。
 */
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { mountWorkspace } from './workspace.js?v=20260922-sidebar';
const $ = id => document.getElementById(id);
const state = { page: 1, q: '', sequence: 0, detailSequence: 0, context: null };
/** 功能：格式化开放结构。输入：value。输出：纯文本。逻辑：复杂值使用 JSON。约束：不解释 HTML。 */
function display(value) { return value == null ? '—' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value); }
/** 功能：兼容逐步完善的结果结构。输入：value。输出：数组。逻辑：单个对象作为一项。约束：不丢弃非数组解释。 */
function collection(value) { return value == null ? [] : Array.isArray(value) ? value : [value]; }
/** 功能：展示单商机详情。输入：data。输出：DOM。逻辑：解释取原始提交，信号按来源可展开。约束：未评分不伪造结果。 */
function renderDetail(data) {
  const result = data.priority;
  $('priority-detail').innerHTML = `<h2>${e(data.opportunity.title)}</h2><p>${e(data.company.name)}</p><div class="priority-score">${result?.priority_score ?? '—'}</div><p>${result ? e(result.data_source === 'synthetic' ? '虚拟占位分数 · 非算法结果' : result.data_source) + ' · ' + e(result.scored_at) : '等待算法提交评分'}</p><h3>评分分项</h3><pre>${e(display(result?.score_breakdown))}</pre><h3>主要原因</h3><ul>${collection(result?.top_reasons).map(item => `<li>${e(item && typeof item === 'object' ? item.title || display(item) : display(item))}${item?.evidence ? `<p>${e(display(item.evidence))}</p>` : ''}${item?.source_id ? `<button data-source="${e(item.source_id)}">查看来源</button>` : ''}</li>`).join('') || '<li>尚未提供</li>'}</ul><h3>建议下一步</h3><p>${e(result?.recommended_next_action || '尚未提供')}</p><h3>证据</h3>${collection(result?.evidence).map(item => `<p>${e(item?.text || display(item))} ${item?.source_id ? `<button data-source="${e(item.source_id)}">查看来源</button>` : ''}</p>`).join('') || '<p>尚未提供</p>'}<h3>结构化信号</h3>${data.signals.map(item => `<details><summary>${e(item.signal_type)} · ${e(item.status)}${item.data_source === 'synthetic' ? ' · 虚拟' : ''}</summary><p>${e(display(item.signal_value))}</p><p>${e(item.evidence_text)}</p><p>置信度：${e(display(item.confidence))}</p><button data-source="${e(item.source_id)}">查看来源</button></details>`).join('') || '<p>尚无信号</p>'}<p><a href="/business/?company=${e(data.company.id)}#opportunities">查看客户商机</a></p>`;
}
/** 功能：展示已授权原文。输入：id。输出：对话框。逻辑：优先在当前客户邮件中精确匹配，否则展示信号提交证据并注明非原件。约束：不按不可信来源 URL 自动请求。 */
function showSource(id) { const message = state.context.customer_context.emails.find(item => item.dedupe_key === id); const signal = state.context.signals.find(item => item.source_id === id); $('source-title').textContent = message ? message.subject || '邮件原文' : '提交的证据（未关联到邮件原文）'; $('source-body').textContent = message ? message.body_text || '' : signal ? `${signal.data_source} · ${signal.source_type}\n${signal.evidence_text}` : `来源标识：${id}\n当前上下文没有对应原文。`; $('source-dialog').showModal(); }
/** 功能：读取选择详情。输入：id。输出：Promise。逻辑：请求序号拒绝晚到旧响应。约束：失败展示错误，不显示别的商机结果。 */
async function select(id) { const sequence = ++state.detailSequence; $('priority-detail').textContent = '正在加载…'; try { const data = await request(`sales/opportunity-context/${encodeURIComponent(id)}/`); if (sequence !== state.detailSequence) return; state.context = data; renderDetail(data); document.querySelectorAll('[data-opportunity]').forEach(node => node.setAttribute('aria-current', node.dataset.opportunity === id)); } catch (error) { if (sequence !== state.detailSequence) return; state.context = null; $('priority-detail').textContent = error.message; console.error('priority_context_failed', { status: error.status }); } }
/** 功能：读取分页列表。输入：当前筛选。输出：Promise。逻辑：显示后台最新结果顺序；查询失败有错误态。约束：无自动重试。 */
async function load() { const sequence = ++state.sequence; ++state.detailSequence; $('priority-error').hidden = true; state.context = null; $('priority-detail').textContent = '请选择商机。'; try { const data = await request('sales/priority-board/?' + new URLSearchParams({ page: state.page, page_size: 20, q: state.q })); if (sequence !== state.sequence) return; $('priority-count').textContent = `${data.count} 条活跃商机`; $('priority-page').textContent = state.page; $('priority-prev').disabled = state.page <= 1; $('priority-next').disabled = state.page * 20 >= data.count; $('priority-list').innerHTML = data.results.map(row => `<button class="priority-row" data-opportunity="${e(row.opportunity.id)}"><strong>${row.priority?.priority_score ?? '—'} · ${e(row.opportunity.title)}</strong><span>${e(row.company_name)}</span><small>${e(row.opportunity.currency)} ${e(row.opportunity.amount ?? '金额未知')}${row.priority?.data_source === 'synthetic' ? ' · 虚拟占位' : ''}</small></button>`).join('') || '<p>尚无活跃商机。</p>'; } catch (error) { if (sequence !== state.sequence) return; $('priority-error').hidden = false; $('priority-error').textContent = error.message; $('priority-list').textContent = ''; console.error('priority_list_failed', { status: error.status }); } }
mountWorkspace('priorities');
$('priority-filter').onsubmit = event => { event.preventDefault(); state.q = new FormData(event.target).get('q'); state.page = 1; load(); };
$('priority-prev').onclick = () => { state.page--; load(); }; $('priority-next').onclick = () => { state.page++; load(); };
$('priority-list').onclick = event => { const button = event.target.closest('[data-opportunity]'); if (button) select(button.dataset.opportunity); };
$('priority-detail').onclick = event => { const button = event.target.closest('[data-source]'); if (button && state.context) showSource(button.dataset.source); };
$('source-close').onclick = () => $('source-dialog').close();
load();
