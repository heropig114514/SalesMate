/**
 * 职责：协调世界消息筛选、地图气泡、摘要侧栏和详情页。
 * 实现：显式 demo 数据源；URL 保存筛选和选择，History API 支持详情刷新/返回，统一消息事件更新列表与地图。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：workspace.js 同时启用可收起的共享悬浮助手；world.html/world-news.css 展示；NewsFeed 校验消息；WorldMap 聚合标记；workspace.js 统一导航；地图、行业色和外壳按相同静态资源版本加载。
 * 目录：date、mapHref、detailHref、tag、card、visibleNews、showError、renderPanel、renderDetail、renderOverview、selectNews、selectGroup、closePanel、navigate、renderRoute、receiveWorldNews、start。
 * 变量索引：$ 为 DOM 查询，feed 为演示快照；mapView 地图，industry 筛选，selectedId 选择，groupIds 聚合列表，highlightId 最新到达消息，originButton 返回焦点目标。
 */
import { t, h, locale } from './i18n.js?v=20260920-i18n';

import { escapeHtml as e } from './api.js';
import { mountWorkspace } from './workspace.js?v=20260920-floating';
import { INDUSTRIES, NewsFeed } from './world-feed.js?v=20260920-i18n';
import { DEMO_NEWS, DEMO_PUSH } from './world-demo.js';
import { WorldMap } from './world-map.js?v=20260920-i18n';

const $ = id => document.getElementById(id);
const feed = new NewsFeed('demo', DEMO_NEWS);
let mapView = null, industry = 'all', selectedId = null, groupIds = null, highlightId = null, originButton = null;

/** 功能：显示明确时间。输入：value ISO 时间。输出：按当前界面语言显示的上海时区月日时分。
 * 逻辑：保留消息时间，不伪装刚刚抓取。约束：仅接收已校验值。 */
function date(value) { return new Intl.DateTimeFormat(locale, { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)); }
/** 功能：生成可分享的地图状态。输入：id 可空消息标识，隐式 industry。输出：相对 URL。
 * 逻辑：查询参数编码选择。约束：不传递员工、凭证或客户信息。 */
function mapHref(id = null) { const query = new URLSearchParams(); if (industry !== 'all') query.set('industry', industry); if (id) query.set('news', id); return '/world/' + (query.size ? '?' + query : ''); }
/** 功能：生成独立详情地址。输入：id。输出：同源路径。
 * 逻辑：编码消息标识。约束：未找到消息时页面明确显示不存在。 */
function detailHref(id) { return `/world/news/${encodeURIComponent(id)}/`; }
/** 功能：构造行业标签。输入：item 已校验消息。输出：安全 HTML。
 * 逻辑：样式颜色只来自固定字典。约束：新闻文本均转义。 */
function tag(item) { const kind = INDUSTRIES[item.industry]; return `<span class="industry-tag" style="--tag-color:${kind.color}"><i></i>${e(kind.label)}</span>`; }
/** 功能：生成摘要列表卡片。输入：item。输出：带消息 ID 的按钮 HTML。
 * 逻辑：文本转义并标记演示与发布时间。约束：不直接执行来源内容。 */
function card(item) { return `<button type="button" class="news-card ${item.id === highlightId ? 'is-new' : ''}" data-select="${e(item.id)}">${tag(item)}<span class="card-arrow" aria-hidden="true">↗</span><h3>${e(item.title)}</h3><p>${e(item.summary)}</p><div class="card-meta"><span>${e(item.location.name)}</span><time datetime="${e(item.published_at)}">${e(date(item.published_at))}</time></div></button>`; }
/** 功能：读取当前行业消息。输入：feed 与 industry。输出：已排序数组。
 * 逻辑：all 保留全部。约束：不修改消息或其他筛选状态。 */
function visibleNews() { return feed.list().filter(item => industry === 'all' || item.industry === industry); }
/** 功能：显示可操作的失败。输入：error 与 stage。输出：可见错误及安全日志。
 * 逻辑：保留页面已有状态。约束：不记录消息正文、凭证或自动重试。 */
function showError(error, stage) { console.error('world_news_failed', { stage, errorType: error.name }); $('world-error').textContent = t`世界消息：${error.message}`; $('world-error').hidden = false; }
/** 功能：渲染侧栏。输入：selectedId/groupIds 与当前可见数据。输出：无。
 * 逻辑：单条摘要、聚合列表、全量速览三态；空筛选明确展示。
 * 约束：正文与摘要不使用模型 HTML；移动端选择后成为非模态底部面板。 */
function renderPanel() {
  const item = selectedId ? feed.get(selectedId) : null;
  const panel = $('news-panel'); panel.classList.toggle('is-open', Boolean(item || groupIds));
  if (item) {
    panel.innerHTML = h`<div class="panel-top"><span class="eyebrow">消息摘要</span><button type="button" data-close aria-label="关闭消息摘要">×</button></div><div class="summary-body">${tag(item)}<p class="summary-place">⊕ ${e(item.location.name)}</p><h2 id="summary-title" tabindex="-1">${e(item.title)}</h2><div class="summary-time">${e(date(item.published_at))} · UTC+8 <span class="demo-label">演示消息</span></div><div class="summary-divider"></div><p class="summary-text">${e(item.summary)}</p><div class="summary-source"><span>来源状态</span><strong>${item.sources.length ? t`${item.sources.length} 个来源` : t('虚构示例 · 无真实报道')}</strong></div><a class="detail-button" data-navigate href="${detailHref(item.id)}">阅读完整消息 <span aria-hidden="true">↗</span></a><p class="summary-hint">也可以再次点击该消息的地图气泡进入详情。</p></div>`;
  } else {
    const items = visibleNews().filter(value => !groupIds || groupIds.includes(value.id));
    panel.innerHTML = `<div class="panel-top"><div><span class="eyebrow">${groupIds ? t('此区域的消息') : 'NEWS BRIEFING'}</span><h2>${groupIds ? t('附近动态') : t('消息速览')} <span>${items.length}</span></h2></div>${groupIds ? h('<button type="button" data-close aria-label="关闭区域摘要">×</button>') : '<span class="panel-star" aria-hidden="true">✳</span>'}</div><p class="panel-intro">${groupIds ? t('选择一条消息，查看摘要与完整内容。') : t('点击地图气泡或下方消息，展开摘要。')}</p>${groupIds ? h('<button class="group-zoom" data-zoom-group type="button">放大此区域 ↗</button>') : ''}<div class="news-card-list">${items.length ? items.map(card).join('') : h('<div class="world-empty"><span aria-hidden="true">◎</span><h3>这个行业暂时没有消息</h3><p>切换其他行业，或等待新的消息到达。</p></div>')}</div>`;
  }
}
/** 功能：渲染独立消息详情。输入：id。输出：完整正文或缺失状态。
 * 逻辑：只显示已接收记录；所有外链已在数据层校验并使用安全新窗口属性。
 * 约束：演示明确标记；不声称实时来源已接通，不将未知 ID 映射到其他消息。 */
function renderDetail(id) {
  const item = feed.get(id);
  document.title = item ? t`${item.title} · 世界消息` : t('消息未找到 · SalesMate');
  $('news-detail').innerHTML = item ? h`<a class="detail-back" data-navigate href="${mapHref(id)}">← 返回地图与摘要</a><article class="news-article"><div class="article-meta">${tag(item)}<span class="demo-label">演示消息</span></div><h1 tabindex="-1">${e(item.title)}</h1><div class="article-byline"><span>⊕ ${e(item.location.name)}</span><time datetime="${e(item.published_at)}">${e(date(item.published_at))} · UTC+8</time></div><p class="article-lead">${e(item.summary)}</p><div class="article-content">${item.body.map(paragraph => `<p>${e(paragraph)}</p>`).join('')}</div><section class="article-sources"><h2>来源与核对</h2>${item.sources.length ? item.sources.map(source => `<a href="${e(source.url)}" target="_blank" rel="noopener noreferrer">${e(source.label)} ↗</a>`).join('') : h('<p>本消息为虚构演示，没有对应的真实报道，请勿据此作业务判断。</p>')}</section><footer class="article-footer">消息编号 ${e(item.id)} · 版本 ${item.version}</footer></article>` : h('<a class="detail-back" data-navigate href="/world/">← 返回世界地图</a><div class="world-empty"><h1 tabindex="-1">消息未找到</h1><p>该消息未在当前数据源中，可能尚未接入或仅存在于另一页面会话。</p></div>');
}
/** 功能：刷新列表、统计和地图。输入：当前 feed、筛选与选择。输出：无。
 * 逻辑：统计反映当前筛选，推送不改变地图视角；详情打开时同步当前版本。
 * 约束：不创建定时模拟推送，不隐式改变用户筛选。 */
function renderOverview() {
  const items = visibleNews();
  $('news-total').textContent = items.length;
  $('location-total').textContent = new Set(items.map(item => item.location.name)).size;
  $('industry-total').textContent = new Set(items.map(item => item.industry)).size;
  $('map-count').textContent = t`${items.length} 条消息 · ${new Set(items.map(item => item.location.name)).size} 个地点`;
  $('industry-filters').innerHTML = [['all', { label: t('全部行业') }], ...Object.entries(INDUSTRIES)].map(([key, kind]) => `<button type="button" data-industry="${key}" aria-pressed="${industry === key}">${kind.color ? `<i style="background:${kind.color}"></i>` : '<span aria-hidden="true">⊞</span>'}${kind.label}</button>`).join('');
  renderPanel();
  mapView?.setItems(items, selectedId, highlightId);
}
/** 功能：选择消息或再次点击进入详情。输入：id、button 触发元素。
 * 输出：无。逻辑：首次打开摘要并保存 URL；同一消息再次选择进入详情。
 * 约束：仅选择当前存在消息，键盘焦点移至摘要标题。 */
function selectNews(id, button) {
  if (!feed.get(id)) return;
  if (selectedId === id) { navigate(detailHref(id)); return; }
  originButton = button; selectedId = id; groupIds = null;
  history.replaceState(null, '', mapHref(id)); renderOverview(); $('summary-title')?.focus({ preventScroll: true });
}
/** 功能：处理地图消息组。输入：items、button。输出：无。
 * 逻辑：单条直接摘要；已选消息所在组再次点击进入该条详情；其余展示组内列表。
 * 约束：聚合不任意选择代表消息作为事实详情。 */
function selectGroup(items, button) {
  if (items.length === 1) { selectNews(items[0].id, button); return; }
  if (selectedId && items.some(item => item.id === selectedId)) { navigate(detailHref(selectedId)); return; }
  originButton = button; selectedId = null; groupIds = items.map(item => item.id);
  history.replaceState(null, '', mapHref()); renderOverview(); $('news-panel').querySelector('[data-close]')?.focus({ preventScroll: true });
}
/** 功能：关闭当前摘要。输入：模块选择与焦点状态。输出：无。
 * 逻辑：恢复速览并清理 URL；优先返回原触发按钮，否则回到地图。
 * 约束：Escape 与显式关闭使用同一流程，不丢弃消息。 */
function closePanel() { selectedId = null; groupIds = null; history.replaceState(null, '', mapHref()); renderOverview(); if (originButton?.isConnected) originButton.focus(); else $('world-map').focus({ preventScroll: true }); }
/** 功能：同源地图/详情导航。输入：href。输出：无。
 * 逻辑：记录浏览历史并渲染路由。约束：保留浏览器返回；外链不经过此函数。 */
function navigate(href) { history.pushState(null, '', href); renderRoute(); window.scrollTo({ top: 0, behavior: 'instant' }); }
/** 功能：按 URL 恢复页面。输入：location。输出：无。
 * 逻辑：详情路径显示文章，地图查询参数恢复筛选和消息；无效行业显示全部。
 * 约束：返回地图后更新 Leaflet 尺寸，已知消息可刷新直达。 */
function renderRoute() {
  const match = location.pathname.match(/^\/world\/news\/([a-z0-9-]+)\/$/);
  $('world-explorer').hidden = Boolean(match); $('news-detail').hidden = !match;
  if (match) { renderDetail(match[1]); $('news-detail').querySelector('h1')?.focus({ preventScroll: true }); return; }
  const query = new URLSearchParams(location.search), requested = query.get('industry');
  industry = Object.hasOwn(INDUSTRIES, requested) ? requested : 'all';
  selectedId = query.get('news'); groupIds = null;
  if (selectedId && !visibleNews().some(item => item.id === selectedId)) selectedId = null;
  document.title = t('世界消息 · SalesMate'); renderOverview(); mapView?.map.invalidateSize();
}
/** 功能：预留后续推送的页面入口。输入：event 标准 news.upsert 事件。
 * 输出：数据源的布尔结果；失败展示错误并抛回调用者。逻辑：全部更新经同一校验与幂等路径。
 * 约束：当前数据源明确为 demo，只接受演示消息；未来 live 数据源须独立初始化，不连接虚构接口。 */
export function receiveWorldNews(event) { try { return feed.receive(event); } catch (error) { showError(error, 'receive'); throw error; } }
/** 功能：初始化页面交互。输入：页面 DOM 与本地资源。输出：Promise。
 * 逻辑：挂载导航，绑定筛选/历史/键盘/模拟事件，再读取底图；资源失败单独显示。
 * 约束：不会启动真实推送、调用模型或业务写 API；未接入状态常显。 */
async function start() {
  mountWorkspace('world');
  feed.addEventListener('change', event => {
    highlightId = event.detail.id;
    $('world-status').textContent = t`${event.detail.inserted ? t('收到一条演示消息') : t('消息已更新')}：${feed.get(highlightId).title}。当前行业筛选保持不变。`;
    renderOverview(); const match = location.pathname.match(/^\/world\/news\/([a-z0-9-]+)\/$/); if (match) renderDetail(match[1]);
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('button'), link = event.target.closest('a[data-navigate]');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) { event.preventDefault(); navigate(link.getAttribute('href')); return; }
    if (!button) return;
    if (button.hasAttribute('data-industry')) { industry = button.dataset.industry; selectedId = null; groupIds = null; history.replaceState(null, '', mapHref()); renderOverview(); $('industry-filters').querySelector(`[data-industry="${industry}"]`).focus(); }
    if (button.dataset.select) selectNews(button.dataset.select, button);
    if (button.hasAttribute('data-close')) closePanel();
    if (button.hasAttribute('data-zoom-group')) mapView?.focusGroup(groupIds.map(id => feed.get(id)).filter(Boolean));
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('world-explorer').hidden && (selectedId || groupIds)) closePanel(); });
  window.addEventListener('popstate', renderRoute);
  $('demo-push').onclick = () => { receiveWorldNews({ type: 'news.upsert', item: DEMO_PUSH }); $('demo-push').disabled = true; $('demo-push').textContent = t('演示消息已到达 ✓'); };
  $('reset-map').onclick = () => mapView?.reset();
  renderOverview();
  // 先在可见容器建立全球视角，再应用详情路由；直接打开详情后返回不会留下零尺寸地图。
  try { mapView = new WorldMap($('world-map'), selectGroup); renderRoute(); await mapView.load(); renderOverview(); console.info('world_news_initialized', { mode: feed.mode, count: feed.list().length }); }
  catch (error) { renderRoute(); showError(error, 'map'); $('map-count').textContent = t('地图加载失败'); }
  finally { $('map-loading').hidden = true; }
}
start().catch(error => showError(error, 'initialize'));
