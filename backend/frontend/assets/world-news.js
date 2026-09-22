/** 职责：从数据库展示活动地图、资讯和邀约模板。
 * 实现：显式读取分页，地图币种仅取活动实际关联金额，提示保留其他币种已知金额；标记虚拟占位，失败显示错误，无静态回退。
 * 关联：地图锚点与金额提示修复使用新版资源；侧栏优先级入口移除后更新导航缓存；共享导航使用移除实验入口后的缓存版本；sales/world、world-news、seller-context 接口和 WorldMap。
 * 目录：$、text、countryName、loadPages、eventRows、money、render、selectEvent、renderDetail、renderNews、renderArticle、foldLine、calendarText、calendarText.escape、calendarText.instant、downloadItinerary、inviteDraft、start。
 * 变量索引：$ 查询 DOM；state 数据快照和筛选，currencies 仅包含活动地图金额币种；categories 分类；regionNames 地区名称；map 地图实例。
 */
import { language } from './i18n.js?v=20260921-product';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { mountWorkspace } from './workspace.js?v=20260922-sidebar';
import { WorldMap } from './world-map.js?v=20260922-map-currency';
const $ = id => document.getElementById(id);
const state = { events: [], news: [], countries: [], currencies: [], selected: null, country: 'all', currency: '', type: 'all', time: 'all', view: 'global', seller: null };
const categories = { regulation: ['监管', 'Regulation'], industry: ['产业', 'Industry'], competition: ['竞争', 'Competition'], price: ['价格', 'Price'] };
const regionNames = new Intl.DisplayNames([language === 'en' ? 'en' : 'zh-CN'], { type: 'region' });
let map = null;
/** 功能：选择文案。输入：zh/en。输出：文本。逻辑：沿用语言。约束：不翻译业务内容。 */
function text(zh, en) { return language === 'en' ? en : zh; }
/** 功能：显示国家。输入：code。输出：名称。逻辑：ISO 地区名称。约束：不猜测地址。 */
function countryName(code) { return regionNames.of(code) || code; }
/** 功能：读取分页快照。输入：path。输出：合并结果。逻辑：按 count 显式请求下一页。约束：失败抛错，不重试。 */
async function loadPages(path) {
  const first = await request(`${path}?page_size=100&page=1`), results = [...first.results];
  for (let page = 2; results.length < first.count; page++) {
    const next = await request(`${path}?page_size=100&page=${page}`);
    if (!next.results.length) throw new Error(text('数据发生变化，请刷新。', 'Data changed; refresh.'));
    results.push(...next.results);
  }
  return { ...first, results };
}
/** 功能：筛选活动。输入：includeCountry。输出：数组。逻辑：类型、未结束时间窗口及国家取交集。约束：不写数据库。 */
function eventRows(includeCountry = true) {
  const now = new Date(), end = state.time === '30' ? new Date(now.getTime() + 30 * 86400000) : new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3 + 3, 1);
  return state.events.filter(item => (state.type === 'all' || item.event_type === state.type) && (!includeCountry || state.country === 'all' || item.country === state.country) && (state.time === 'all' || (new Date(item.ends_at) >= now && new Date(item.starts_at) < end)));
}
/** 功能：金额显示。输入：amounts。输出：字符串。逻辑：分币种展示。约束：不汇率换算，未知不补零。 */
function money(amounts) { return Object.entries(amounts).map(([currency, amount]) => `${currency} ${Number(amount).toLocaleString(undefined, { maximumFractionDigits: 2 })}`).join(' · ') || text('金额未知', 'Amount unknown'); }
/** 功能：渲染筛选结果。输入：state。输出：DOM。逻辑：国家数量、列表和地图联动，所选币种缺金额保留 null。约束：空态清除旧详情，地图使用后端去重金额。 */
function render() {
  const rows = eventRows(), regional = eventRows(false);
  if (!rows.some(item => item.id === state.selected)) state.selected = rows[0]?.id || null;
  $('event-count').textContent = rows.length; $('map-count').textContent = text(`${rows.length} 项活动`, `${rows.length} events`);
  $('region-filters').innerHTML = `<button data-country="all" aria-pressed="${state.country === 'all'}">${text('全部', 'All')} <span>${regional.length}</span></button>` + state.countries.map(item => `<button data-country="${e(item.code)}" aria-pressed="${state.country === item.code}">${e(countryName(item.code))}<span>${regional.filter(row => row.country === item.code).length}</span></button>`).join('');
  $('event-list').innerHTML = rows.map(item => `<button class="event-card" aria-pressed="${item.id === state.selected}" data-select="${e(item.id)}"><span class="event-card-main"><span>${e(item.city)} · ${e(item.starts_at.slice(0, 10))}</span><strong>${e(item.title)}</strong><small>${e(money(item.amounts))}${item.data_source === 'synthetic' ? text(' · 虚拟占位', ' · Synthetic') : ''}</small></span></button>`).join('') || `<p>${text('没有匹配活动。', 'No matching events.')}</p>`;
  map?.setItems(rows.map(item => ({ ...item, lat: item.latitude, lng: item.longitude, amount: Object.hasOwn(item.map_amounts, state.currency) ? Number(item.map_amounts[state.currency]) : null, currency: state.currency, en: item.title })), state.selected);
  renderDetail(rows.find(item => item.id === state.selected));
  const params = new URLSearchParams({ type: state.type, time: state.time, country: state.country, view: state.view, currency: state.currency });
  if (state.selected) params.set('event', state.selected);
  history.replaceState(null, '', '/world/?' + params);
}
/** 功能：选择活动。输入：id。输出：无。逻辑：统一渲染。约束：不写数据。 */
function selectEvent(id) { state.selected = id; render(); }
/** 功能：展示活动事实。输入：item。输出：详情。逻辑：转义所有业务文本，展示缺项和来源。约束：不生成推荐。 */
function renderDetail(item) {
  if (!item) { $('event-detail').innerHTML = `<p>${text('请选择活动。', 'Select an event.')}</p>`; return; }
  const days = Math.ceil((new Date(item.starts_at) - new Date()) / 86400000);
  $('event-detail').innerHTML = `<div class="event-detail-head"><span>${item.event_type === 'exhibition' ? text('展会', 'Exhibition') : text('销售活动', 'Sales event')}</span><span>${days >= 0 ? text(`${days} 天后`, `In ${days} days`) : text('已开始', 'Started')}</span></div><h2>${e(item.title)}</h2><p>${e(item.city)} · ${e(item.starts_at.slice(0, 10))} — ${e(item.ends_at.slice(0, 10))}</p><p class="badge">${e(item.data_source === 'synthetic' ? text('数据库虚拟占位', 'Synthetic database record') : item.data_source)}</p><div class="event-value"><span>${text('关联在手商机', 'Related pipeline')}</span><strong>${e(money(item.amounts))}</strong><small>${e(item.customers.join(' · '))}</small></div><section><h3>${text('为什么值得去', 'Why attend')}</h3><p>${e(item.description || text('等待补充说明', 'Awaiting details'))}</p></section><section><h3>${text('现场情况', 'On site')}</h3><ul>${item.onsite.map(value => `<li>${e(value)}</li>`).join('')}</ul><p>${text('报名截止', 'Registration closes')}：${e(item.registration_deadline?.slice(0, 10) || '—')}</p></section><section><h3>${text('建议动作', 'Suggested actions')}</h3><ul>${item.suggested_actions.map(value => `<li>${e(value)}</li>`).join('')}</ul></section>${item.source_url ? `<a href="${e(item.source_url)}" target="_blank" rel="noopener noreferrer">${text('原始来源', 'Source')}</a>` : ''}<div class="event-actions"><button id="add-itinerary" class="primary">${text('加入行程', 'Add to itinerary')}</button><button id="create-invite" class="secondary">${text('生成客户邀约邮件', 'Draft invitation')}</button></div><p class="event-action-note">${text('导出日历文件；邀约为可编辑模板，尚未调用 AI。', 'Export calendar file; invitation is an editable template, without AI.')}</p>`;
  $('add-itinerary').onclick = () => downloadItinerary(item); $('create-invite').onclick = () => inviteDraft(item);
}
/** 功能：显示近十四天资讯。输入：快照。输出：DOM。逻辑：保留实际发布时间。约束：不更新虚拟时间。 */
function renderNews() { $('industry-news').innerHTML = state.news.map(item => `<a class="industry-news-card" href="/world/news/${e(item.id)}/"><span>${e((categories[item.category] || ['', ''])[language === 'en' ? 1 : 0])}${item.data_source === 'synthetic' ? text(' · 虚拟', ' · Synthetic') : ''}</span><h3>${e(item.title)}</h3><time>${e(item.published_at.slice(0, 10))}</time></a>`).join('') || `<p>${text('近 14 天暂无资讯。', 'No news in the last 14 days.')}</p>`; }
/** 功能：显示资讯详情。输入：id。输出：Promise。逻辑：按 ID 读取，不依赖列表。约束：错误不回退。 */
async function renderArticle(id) { const item = await request('sales/records/world-news/' + encodeURIComponent(id) + '/'); $('news-detail').innerHTML = `<a href="/world/">← ${text('返回全球洞察', 'Back')}</a><article><p>${e(item.data_source)} · ${e(item.published_at.slice(0, 10))}</p><h1>${e(item.title)}</h1><p>${e(item.summary)}</p>${item.content.split('\n').map(line => `<p>${e(line)}</p>`).join('')}${item.source_url ? `<a href="${e(item.source_url)}" target="_blank" rel="noopener noreferrer">${text('原始来源', 'Source')}</a>` : `<p>${text('虚拟或待补充来源', 'Synthetic or source pending')}</p>`}</article>`; }
/** 功能：折叠 ICS。输入：line。输出：文本。逻辑：UTF-8 每行最多 75 字节。约束：不切断字符。 */
function foldLine(line) { let result = '', count = 0; for (const character of line) { const size = new TextEncoder().encode(character).length; if (count + size > 75) { result += '\r\n '; count = 1; } result += character; count += size; } return result; }
/** 功能：生成日历。输入：item。输出：ICS。逻辑：使用数据库起止时间并转义。约束：不调用外部日历。 */
export function calendarText(item) {
  const escape = value => String(value).replace(/\\/g, '\\\\').replace(/\r?\n/g, '\\n').replace(/[,;]/g, '\\$&');
  const instant = value => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  return ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//SalesMate//Events//EN', 'BEGIN:VEVENT', `UID:${item.id}@salesmate`, 'DTSTAMP:' + instant(new Date()), 'DTSTART:' + instant(item.starts_at), 'DTEND:' + instant(item.ends_at), 'SUMMARY:' + escape((item.data_source === 'synthetic' ? '[Synthetic] ' : '') + item.title), 'LOCATION:' + escape(item.city), 'DESCRIPTION:' + escape(item.description), 'END:VEVENT', 'END:VCALENDAR', ''].map(foldLine).join('\r\n');
}
/** 功能：导出日历。输入：item。输出：下载。逻辑：临时 Blob。约束：不发送邀请。 */
function downloadItinerary(item) { const url = URL.createObjectURL(new Blob([calendarText(item)], { type: 'text/calendar;charset=utf-8' })); const link = document.createElement('a'); link.href = url; link.download = item.id + '.ics'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
/** 功能：填充邀约模板。输入：item。输出：对话框。逻辑：使用本人资料署名。约束：不调用 AI、不猜测收件人、不发送。 */
function inviteDraft(item) { const person = state.seller?.sales_setup?.personal || {}; $('invite-subject').value = text('邀约交流：', 'Invitation: ') + item.title; $('invite-body').value = text(`您好，\n\n希望与您在 ${item.starts_at.slice(0, 10)} 的“${item.title}”（${item.city}）期间预约交流。请告知方便的时间。\n\n`, `Hello,\n\nWould you be available to meet during ${item.title} in ${item.city} on ${item.starts_at.slice(0, 10)}?\n\n`) + [person.name, person.title, person.email].filter(Boolean).join('\n') + (item.data_source === 'synthetic' ? text('\n\n注意：活动为虚拟占位。', '\n\nThis event is synthetic.') : ''); $('invite-dialog').showModal(); }
/** 功能：初始化数据库页面。输入：DOM 和 URL。输出：Promise。逻辑：读取后绑定控件，可选币种来自全部活动 map_amounts，排除无关商机币种；有效 URL 币种优先，否则使用活动首个实际币种；无金额时币种选择为空，失败明确展示。约束：不创建占位、不自动重试、不换汇。 */
async function start() {
  mountWorkspace('world');
  const article = location.pathname.match(/^\/world\/news\/([a-z0-9-]+)\/$/);
  $('world-explorer').hidden = Boolean(article); $('news-detail').hidden = !article;
  try {
    if (article) { await renderArticle(article[1]); return; }
    $('world-data-status').textContent = text('正在读取数据库…', 'Loading database…');
    const [world, news, seller] = await Promise.all([loadPages('sales/world/'), request('sales/records/world-news/?page_size=4&to=' + encodeURIComponent(new Date().toISOString()) + '&from=' + encodeURIComponent(new Date(Date.now() - 14 * 86400000).toISOString())), request('sales/seller-context/')]);
    Object.assign(state, { events: world.results, countries: world.countries, currencies: [...new Set(world.results.flatMap(item => Object.keys(item.map_amounts)))].sort(), news: news.results, seller });
    const query = new URLSearchParams(location.search);
    const eventCurrency = state.events.flatMap(item => Object.keys(item.map_amounts)).find(currency => state.currencies.includes(currency));
    state.currency = state.currencies.includes(query.get('currency')) ? query.get('currency') : eventCurrency || state.currencies[0] || '';
    state.type = ['sales', 'exhibition'].includes(query.get('type')) ? query.get('type') : 'all'; state.time = ['30', 'quarter'].includes(query.get('time')) ? query.get('time') : 'all';
    state.country = state.countries.some(item => item.code === query.get('country')) ? query.get('country') : 'all'; state.view = ['apac', 'europe'].includes(query.get('view')) ? query.get('view') : 'global'; state.selected = query.get('event');
    $('world-data-status').textContent = text(`数据库记录 · ${state.events.filter(item => item.data_source === 'synthetic').length} 项虚拟活动`, `Database records · ${state.events.filter(item => item.data_source === 'synthetic').length} synthetic events`);
    $('event-type').value = state.type; $('event-time').value = state.time;
    $('map-currency').innerHTML = state.currencies.map(currency => `<option>${e(currency)}</option>`).join(''); $('map-currency').value = state.currency;
    $('map-note').textContent = text(`金额按所选币种展示，不换汇；${world.unmapped_customer_count} 个客户未提供可识别国家。`, `No currency conversion; ${world.unmapped_customer_count} customers have no mapped country.`);
    map = new WorldMap($('world-map'), selectEvent); map.setCountries(state.countries.filter(item => item.customer_count > 0).map(item => item.code)); await map.load(); map.setView(state.view);
    document.querySelectorAll('[data-view]').forEach(node => node.setAttribute('aria-pressed', node.dataset.view === state.view));
    $('event-type').onchange = event => { state.type = event.target.value; render(); }; $('event-time').onchange = event => { state.time = event.target.value; render(); }; $('map-currency').onchange = event => { state.currency = event.target.value; render(); };
    $('region-filters').onclick = event => { const button = event.target.closest('[data-country]'); if (button) { state.country = button.dataset.country; render(); } }; $('event-list').onclick = event => { const button = event.target.closest('[data-select]'); if (button) selectEvent(button.dataset.select); };
    $('map-views').onclick = event => { const button = event.target.closest('[data-view]'); if (button) { state.view = button.dataset.view; map.setView(state.view); document.querySelectorAll('[data-view]').forEach(node => node.setAttribute('aria-pressed', node.dataset.view === state.view)); render(); } };
    $('invite-close').onclick = () => $('invite-dialog').close(); $('invite-copy').onclick = async () => { try { await navigator.clipboard.writeText($('invite-subject').value + '\n\n' + $('invite-body').value); $('invite-status').textContent = text('已复制', 'Copied'); } catch (error) { console.error('invite_copy_failed', { type: error.name }); $('invite-status').textContent = text('请手动复制。', 'Copy manually.'); } };
    render(); renderNews();
  } catch (error) { console.error('world_load_failed', { type: error.name, status: error.status }); $('world-error').hidden = false; $('world-error').textContent = text('加载失败，请检查登录或接口后刷新：', 'Load failed. Check access and refresh: ') + error.message; $('world-data-status').textContent = text('加载失败', 'Load failed'); }
}
start();
