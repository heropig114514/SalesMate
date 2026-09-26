/** Responsibility: Display database-backed event maps, news, and invitation templates.
 * Implementation: Missing-amount bubbles remain interactive and legends distinguish locations from amounts. News cards/details show public leads, source-amount definitions, and inference labels. Shared events use backend date precision for display/filtering/all-day calendar export. Read pagination explicitly; map currencies come only from actual event-linked amounts, while labels retain other known currencies. Mark synthetic placeholders, display failures, and provide no static fallback.
 * Relationships: Map-anchor/amount-label fixes use updated resources; navigation versions reflect removal of sidebar priority/experiment entries; uses sales/world, world-news, seller-context, and WorldMap.
 * Directory: $, text, countryName, loadPages, eventRows, money, render, selectEvent, renderDetail, renderNews, renderArticle, foldLine, calendarText, calendarText.escape, calendarText.instant, downloadItinerary, inviteDraft, start.
 * Variable index: $ queries DOM; state holds snapshots/filters; currencies contains only event-map amount currencies; categories classifies events; regionNames supplies region labels; map is the map instance.
 */
import { language } from './i18n.js?v=20260921-product';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { mountWorkspace } from './workspace.js?v=20260922-sidebar';
import { eventDates, eventWindow, calendarBounds } from './world-dates.js?v=20260924-insights';
import { signalSummary, signalDetail } from './world-signals.js?v=20260924-signals';
import { WorldMap } from './world-map.js?v=20260924-empty-bubbles';
const $ = id => document.getElementById(id);
const state = { events: [], news: [], countries: [], currencies: [], selected: null, country: 'all', currency: '', type: 'all', time: 'all', view: 'global', seller: null };
const categories = { regulation: ['监管', 'Regulation'], industry: ['产业', 'Industry'], competition: ['竞争', 'Competition'], price: ['价格', 'Price'] };
const regionNames = new Intl.DisplayNames([language === 'en' ? 'en' : 'zh-CN'], { type: 'region' });
let map = null;
/** Function: Select interface text. Inputs: zh/en. Outputs: Text. Logic: Use current language. Constraints: Never translate business content. */
function text(zh, en) { return language === 'en' ? en : zh; }
/** Function: Display a country. Inputs: code. Outputs: Name. Logic: ISO region names. Constraints: Never infer addresses. */
function countryName(code) { return regionNames.of(code) || code; }
/** Function: Read paginated snapshots. Inputs: path. Outputs: Combined results. Logic: Explicitly request subsequent pages according to count. Constraints: Throw on failure without retries. */
async function loadPages(path) {
  const first = await request(`${path}?page_size=100&page=1`), results = [...first.results];
  for (let page = 2; results.length < first.count; page++) {
    const next = await request(`${path}?page_size=100&page=${page}`);
    if (!next.results.length) throw new Error(text('数据发生变化，请刷新。', 'Data changed; refresh.'));
    results.push(...next.results);
  }
  return { ...first, results };
}
/** Function: Filter events. Inputs: includeCountry. Outputs: An array. Logic: Intersect type, unfinished time window, and country filters; date-only events use complete calendar days. Constraints: No database writes. */
function eventRows(includeCountry = true) {
  const now = new Date(), end = state.time === '30' ? new Date(now.getTime() + 30 * 86400000) : new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3 + 3, 1);
  return state.events.filter(item => (state.type === 'all' || item.event_type === state.type) && (!includeCountry || state.country === 'all' || item.country === state.country) && (state.time === 'all' || ((item.time_precision === 'date' ? eventWindow(item).end > now : eventWindow(item).end >= now) && eventWindow(item).start < end)));
}
/** Function: Display amounts. Inputs: amounts. Outputs: A string. Logic: Separate currencies. Constraints: No exchange-rate conversion or replacement of unknowns with zero. */
function money(amounts) { return Object.entries(amounts).map(([currency, amount]) => `${currency} ${Number(amount).toLocaleString(undefined, { maximumFractionDigits: 2 })}`).join(' · ') || text('金额未知', 'Amount unknown'); }
/** Function: Render filtered results. Inputs: state. Outputs: DOM. Logic: Date-only events use source dates; country counts, lists, and map stay synchronized; missing selected-currency amounts remain null. Constraints: Empty states clear old details; the map uses backend-deduplicated amounts. */
function render() {
  const rows = eventRows(), regional = eventRows(false);
  if (!rows.some(item => item.id === state.selected)) state.selected = rows[0]?.id || null;
  $('event-count').textContent = rows.length; $('map-count').textContent = text(`${rows.length} 项活动`, `${rows.length} events`);
  $('region-filters').innerHTML = `<button data-country="all" aria-pressed="${state.country === 'all'}">${text('全部', 'All')} <span>${regional.length}</span></button>` + state.countries.map(item => `<button data-country="${e(item.code)}" aria-pressed="${state.country === item.code}">${e(countryName(item.code))}<span>${regional.filter(row => row.country === item.code).length}</span></button>`).join('');
  $('event-list').innerHTML = rows.map(item => `<button class="event-card" aria-pressed="${item.id === state.selected}" data-select="${e(item.id)}"><span class="event-card-main"><span>${e(item.city)} · ${e(eventDates(item).start)}</span><strong>${e(item.title)}</strong><small>${e(money(item.amounts))}${item.data_source === 'synthetic' ? text(' · 虚拟占位', ' · Synthetic') : ''}</small></span></button>`).join('') || `<p>${text('没有匹配活动。', 'No matching events.')}</p>`;
  map?.setItems(rows.map(item => ({ ...item, lat: item.latitude, lng: item.longitude, amount: Object.hasOwn(item.map_amounts, state.currency) ? Number(item.map_amounts[state.currency]) : null, currency: state.currency, en: item.title })), state.selected);
  renderDetail(rows.find(item => item.id === state.selected));
  const params = new URLSearchParams({ type: state.type, time: state.time, country: state.country, view: state.view, currency: state.currency });
  if (state.selected) params.set('event', state.selected);
  history.replaceState(null, '', '/world/?' + params);
}
/** Function: Select an event. Inputs: id. Outputs: None. Logic: Use shared rendering. Constraints: No data writes. */
function selectEvent(id) { state.selected = id; render(); }
/** Function: Show event facts. Inputs: item. Outputs: Details. Logic: Escape shared facts and viewer-visible relations; date-only events show the source's final day and indicate unspecified time. Constraints: Do not generate recommendations. */
function renderDetail(item) {
  if (!item) { $('event-detail').innerHTML = `<p>${text('请选择活动。', 'Select an event.')}</p>`; return; }
  const days = Math.ceil((eventWindow(item).start - new Date()) / 86400000);
  $('event-detail').innerHTML = `<div class="event-detail-head"><span>${item.event_type === 'exhibition' ? text('展会', 'Exhibition') : text('销售活动', 'Sales event')}</span><span>${days >= 0 ? text(`${days} 天后`, `In ${days} days`) : text('已开始', 'Started')}</span></div><h2>${e(item.title)}</h2><p>${e(item.city)} · ${e(eventDates(item).start)} — ${e(eventDates(item).end)}${item.time_precision === 'date' ? text(' · 仅日期，具体时间未提供', ' · Dates only; time not provided') : ''}</p><p class="badge">${e(item.data_source === 'synthetic' ? text('数据库虚拟占位', 'Synthetic database record') : item.data_source)}</p><div class="event-value"><span>${text('关联在手商机', 'Related pipeline')}</span><strong>${e(money(item.amounts))}</strong><small>${e(item.customers.join(' · '))}</small></div><section><h3>${text('为什么值得去', 'Why attend')}</h3><p>${e(item.description || text('等待补充说明', 'Awaiting details'))}</p></section><section><h3>${text('现场情况', 'On site')}</h3><ul>${item.onsite.map(value => `<li>${e(value)}</li>`).join('')}</ul><p>${text('报名截止', 'Registration closes')}：${e(item.registration_deadline?.slice(0, 10) || '—')}</p></section><section><h3>${text('建议动作', 'Suggested actions')}</h3><ul>${item.suggested_actions.map(value => `<li>${e(value)}</li>`).join('')}</ul></section>${item.source_url ? `<a href="${e(item.source_url)}" target="_blank" rel="noopener noreferrer">${text('原始来源', 'Source')}</a>` : ''}<div class="event-actions"><button id="add-itinerary" class="primary">${text('加入行程', 'Add to itinerary')}</button><button id="create-invite" class="secondary">${text('生成客户邀约邮件', 'Draft invitation')}</button></div><p class="event-action-note">${text('导出日历文件；邀约为可编辑模板，尚未调用 AI。', 'Export calendar file; invitation is an editable template, without AI.')}</p>`;
  $('add-itinerary').onclick = () => downloadItinerary(item); $('create-invite').onclick = () => inviteDraft(item);
}
/** Function: Display news from the last fourteen days. Inputs: Snapshot. Outputs: DOM. Logic: Preserve actual publication time and show company, event, and source-amount definitions on cards. Constraints: News amounts never enter the map; synthetic timestamps remain unchanged. */
function renderNews() { $('industry-news').innerHTML = state.news.map(item => `<a class="industry-news-card" href="/world/news/${e(item.id)}/"><span>${e((categories[item.category] || ['', ''])[language === 'en' ? 1 : 0])}${item.data_source === 'synthetic' ? text(' · 虚拟', ' · Synthetic') : ''}</span><h3>${e(item.title)}</h3>${signalSummary(item)}<time>${e(item.published_at.slice(0, 10))}</time></a>`).join('') || `<p>${text('近 14 天暂无资讯。', 'No news in the last 14 days.')}</p>`; }
/** Function: Display article details. Inputs: id. Outputs: Promise. Logic: Read complete public leads by ID, finish loading state, and distinguish facts, inferences, source amounts, and evidence. Constraints: Never link private opportunities or fall back after errors. */
async function renderArticle(id) { const item = await request('sales/records/world-news/' + encodeURIComponent(id) + '/'); $('world-data-status').textContent = text('数据库记录 · 资讯', 'Database record · News'); $('news-detail').innerHTML = `<a href="/world/">← ${text('返回全球洞察', 'Back')}</a><article><p>${e(item.data_source)} · ${e(item.published_at.slice(0, 10))}</p><h1>${e(item.title)}</h1><p>${e(item.summary)}</p>${signalDetail(item)}${item.content.split('\n').map(line => `<p>${e(line)}</p>`).join('')}${item.source_url ? `<a href="${e(item.source_url)}" target="_blank" rel="noopener noreferrer">${text('原始来源', 'Source')}</a>` : `<p>${text('虚拟或待补充来源', 'Synthetic or source pending')}</p>`}</article>`; }
/** Function: Fold ICS lines. Inputs: line. Outputs: Text. Logic: Limit each UTF-8 line to 75 bytes. Constraints: Never split characters. */
function foldLine(line) { let result = '', count = 0; for (const character of line) { const size = new TextEncoder().encode(character).length; if (count + size > 75) { result += '\r\n '; count = 1; } result += character; count += size; } return result; }
/** Function: Generate a calendar entry. Inputs: item. Outputs: ICS. Logic: date emits all-day VALUE=DATE; datetime retains actual instants; preserve text escaping/folding. Constraints: No external calendar calls. */
export function calendarText(item) {
  const escape = value => String(value).replace(/\\/g, '\\\\').replace(/\r?\n/g, '\\n').replace(/[,;]/g, '\\$&');
  const instant = value => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  return ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//SalesMate//Events//EN', 'BEGIN:VEVENT', `UID:${item.id}@salesmate`, 'DTSTAMP:' + instant(new Date()), ...calendarBounds(item), 'SUMMARY:' + escape((item.data_source === 'synthetic' ? '[Synthetic] ' : '') + item.title), 'LOCATION:' + escape(item.city), 'DESCRIPTION:' + escape(item.description), 'END:VEVENT', 'END:VCALENDAR', ''].map(foldLine).join('\r\n');
}
/** Function: Export a calendar entry. Inputs: item. Outputs: A download. Logic: Temporary Blob. Constraints: Never send invitations. */
function downloadItinerary(item) { const url = URL.createObjectURL(new Blob([calendarText(item)], { type: 'text/calendar;charset=utf-8' })); const link = document.createElement('a'); link.href = url; link.download = item.id + '.ics'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
/** Function: Fill an invitation template. Inputs: item. Outputs: A dialog. Logic: Use explicit event dates and the user's profile for the signature. Constraints: No AI calls, inferred recipients, or sending. */
function inviteDraft(item) { const person = state.seller?.sales_setup?.personal || {}; $('invite-subject').value = text('邀约交流：', 'Invitation: ') + item.title; $('invite-body').value = text(`您好，\n\n希望与您在 ${eventDates(item).start} 的“${item.title}”（${item.city}）期间预约交流。请告知方便的时间。\n\n`, `Hello,\n\nWould you be available to meet during ${item.title} in ${item.city} on ${eventDates(item).start}?\n\n`) + [person.name, person.title, person.email].filter(Boolean).join('\n') + (item.data_source === 'synthetic' ? text('\n\n注意：活动为虚拟占位。', '\n\nThis event is synthetic.') : ''); $('invite-dialog').showModal(); }
/** Function: Initialize the database-backed page. Inputs: DOM and URL. Outputs: Promise. Logic: Read before binding controls; available currencies come from all event map_amounts, excluding unrelated opportunity currencies. Prefer a valid URL currency, otherwise the first actual event currency. With no amounts, leave currency selection empty and explain that white bubbles mark locations only. Display failures explicitly. Constraints: No placeholder creation, automatic retries, or currency conversion. */
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
    $('map-note').textContent = text(`金额按所选币种展示，不换汇；白色半透明小气泡表示无可用金额，不代表零；${world.unmapped_customer_count} 个客户未提供可识别国家。`, `No currency conversion; small translucent white bubbles indicate unavailable amounts, not zero; ${world.unmapped_customer_count} customers have no mapped country.`);
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
