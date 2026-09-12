/**
 * 职责：实现员工 Gmail 收件箱、授权管理和客户工作区的原生浏览器交互。
 * 实现：哈希路由、同源 API 和可访问表单；本地自动会话跳过登录页；邮件文本先转义再渲染。
 * 关联：api.js 处理 HTTP；index.html 提供页面骨架；app.css 定义响应式布局。
 * 目录：$、date、companyName、pill、notice、busy、renderStats、renderRow、loadList、
 * renderDimension、renderDetail、renderEmails、loadDetail、navigate、loadMailboxes、renderGmailAccounts、openGmail、
 * startGmailAuthorization、pollGmailSync、requestGmailSync、disconnectGmail、openMail、openRegister、loginSubmit、
 * mailSubmit、registerSubmit、initialize、bindEvents。
 * 变量索引：$ 为元素定位函数；state 保存分页、会话能力、当前详情和方向；
 * signals、sizes、dimensions、jobNames、gmailStates 为后端枚举的中文展示映射。
 */
import { request, escapeHtml as e } from './api.js';

/** 功能：按 ID 定位页面元素。输入：id。输出：Element 或 null。逻辑：原生 DOM 查询。约束：调用方使用已声明 ID。 */
const $ = id => document.getElementById(id);
const state = { page: 1, count: 0, runtime: null, mailboxes: [], detail: null, direction: 'all', navigation: 0, gmailPolling: 0 };
const signals = { unknown: '待确认', inquiry_intent: '询盘', new_lead_no_profile: '新线索未建档', quoted_not_closed: '已报价未成交', repeat_purchase: '复购' };
const sizes = { unknown: '规模未知', lt_50: '少于 50 人', '50_100': '50–99 人', '100_200': '100–199 人', '200_500': '200–499 人', gte_500: '500 人及以上' };
const dimensions = { industry_context: '行业情况', company_ops: '公司经营分析', intent: '意向分析', timeline: '时间轴', opportunity: '商机分析', risk: '风险分析', guidance: '下一步引导' };
const jobNames = { pending: '等待处理', running: '正在分析', completed: '分析完成', skipped: '已复用缓存', failed: '处理失败' };
const gmailStates = { authorization_required: '未授权', sync_requested: '等待 Agent 同步', sync_running: '正在同步', completed: '同步完成', failed: '同步失败', ok: '同步完成' };

/** 功能：按配置时区显示时间。输入：value 为 ISO 时间。输出：日期时间字符串。
 * 逻辑：Intl 使用后端声明时区。约束：空时间保持未知，不猜测时间。 */
function date(value) {
  return value ? new Intl.DateTimeFormat('zh-CN', { timeZone: state.runtime?.timezone || 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : '尚无记录';
}

/** 功能：生成公司显示名称。输入：row 公司投影。输出：已确定姓名或域名/联系人标识。
 * 逻辑：缺少公司名时使用已有身份信息。约束：不推断不存在的公司名。 */
function companyName(row) {
  return row.company_name || row.domains[0] || row.contacts[0]?.contact_email || '待确认公司';
}

/** 功能：生成安全的标签 HTML。输入：text、kind。输出：span HTML。
 * 逻辑：两项都转义。约束：kind 仅用于 CSS 类，不执行代码。 */
function pill(text, kind = '') { return `<span class="pill ${e(kind)}">${e(text)}</span>`; }

/** 功能：展示操作结果或错误。输入：message、error 是否失败。输出：无。
 * 逻辑：使用 textContent，提供关闭按钮。约束：不插入服务器 HTML，不静默吞错。 */
function notice(message, error = true) {
  const box = $('notice');
  box.textContent = message;
  box.className = error ? 'notice error' : 'notice success';
  box.hidden = false;
  const close = document.createElement('button');
  close.textContent = '×'; close.setAttribute('aria-label', '关闭提示'); close.onclick = () => { box.hidden = true; };
  box.append(close);
}

/** 功能：在异步操作期间禁用触发按钮并展示失败。输入：button、operation 异步函数。输出：无。
 * 逻辑：finally 恢复状态，失败可见。约束：不重复执行、不隐式重试。 */
async function busy(button, operation) {
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = '处理中…'; }
  try { await operation(); }
  catch (error) { notice(error.message); }
  finally {
    if (button?.isConnected) {
      button.disabled = button.id === 'previous' ? state.page <= 1 : button.id === 'next' ? state.page * 20 >= state.count : false;
      button.textContent = original;
    }
  }
}

/** 功能：显示公司及今日新邮件概览。输入：stats。输出：无。
 * 逻辑：统计完全来自后端，注明统计时区。约束：不拿过滤结果冒充全局统计。 */
function renderStats(stats) {
  $('stats').innerHTML = [['公司总数', stats.companies, '按客户公司整理往来', '▦'], ['待建档客户', stats.unregistered, '补齐档案，积累客户上下文', '♧'], ['今日新邮件', stats.new_emails_today, `统计时区 ${state.runtime.timezone}`, '✉']].map(([label, value, note, icon]) => `<article class="stat-card"><div><span class="stat-label">${e(label)}</span><strong>${e(value)}</strong><p>${e(note)}</p></div><span class="stat-icon">${icon}</span></article>`).join('');
  $('nav-count').textContent = stats.companies;
}

/** 功能：构造单个公司列表行。输入：row。输出：可键盘访问的链接 HTML。
 * 逻辑：摘要、信号、未知分和来源同时展示。约束：公司 ID 与文本经转义，不输出内联脚本。 */
function renderRow(row) {
  const name = companyName(row);
  return `<a class="company-row" href="#company/${encodeURIComponent(row.company_id)}"><div class="company-main"><div class="avatar">${e(name.slice(0, 1))}</div><div><div class="row-title"><h3>${e(name)}</h3>${row.crm_status === 'registered' ? pill('已建档', 'subtle') : ''}</div><p class="identity">${e(row.contacts[0]?.contact_email || '联系人待确认')} <span>·</span> ${row.email_count} 封往来</p><p class="summary">${e(row.headline_summary)}</p><div class="row-tags">${pill(row.industry === 'unknown' ? '行业未知' : row.industry, 'subtle')}${pill(sizes[row.size_band], 'subtle')}${row.stale ? pill('分析待更新', 'warning') : ''}${row.job_status === 'failed' ? pill('处理失败', 'warning') : ''}</div></div></div><div class="row-signal">${pill(signals[row.signal], row.signal === 'unknown' ? 'subtle' : 'green')}<small>${row.provider === 'rules' ? '规则占位' : row.provider === 'agent' ? 'Agent 分析' : '等待分析'}</small></div><div class="row-score">${row.score === null ? '<span class="unscored">—</span><small>资料不足 · 未评分</small>' : `<strong>${row.score}<small> / 100</small></strong><div class="score-track"><span style="width:${Number(row.score)}%"></span></div><small>跟进优先级${row.provider === 'rules' ? ' · 占位' : ''}</small>`}</div><div class="row-date">${e(date(row.last_message_at))}<span>查看客户 →</span></div></a>`;
}

/** 功能：加载列表及分页状态。输入：表单与 state.page 隐式状态。输出：无。
 * 逻辑：以查询参数调用后端，展示空、错误和成功状态。约束：不在请求失败时保留看似最新的旧列表。 */
async function loadList() {
  $('company-list').innerHTML = '<div class="empty">正在读取当前员工的 Gmail 客户列表…</div>';
  const params = new URLSearchParams(new FormData($('filters')));
  params.set('page', state.page); params.set('page_size', 20);
  try {
    const data = await request('companies/?' + params);
    state.count = data.count;
    renderStats(data.stats);
    $('result-count').textContent = data.count;
    $('company-list').innerHTML = data.results.length ? data.results.map(renderRow).join('') : '<div class="empty"><span class="empty-icon">✉</span><h3>这个员工的收件箱还没有客户邮件</h3><p>连接 Gmail 并请求同步，或在规则模式导入演示样例。</p></div>';
    $('page-number').textContent = `${state.page} / ${Math.max(1, Math.ceil(data.count / 20))}`;
    $('previous').disabled = state.page <= 1;
    $('next').disabled = state.page * 20 >= data.count;
  } catch (error) {
    $('company-list').innerHTML = '<div class="empty">加载失败，请检查上方提示后点击刷新。</div>';
    throw error;
  }
}

/** 功能：生成含来源跳转的分析维度。输入：key、value。输出：安全 HTML。
 * 逻辑：事实和推断分开展示，缺失项单独标记。约束：来源只通过 data-ref 导航，不执行邮件内容。 */
function renderDimension(key, value) {
  return `<article class="dimension"><h4>${e(dimensions[key] || key)}</h4>${value.facts.map(item => `<p>${e(item.text)} ${item.source_refs.map(ref => `<button class="source-ref" data-ref="${e(ref)}" title="查看原始依据">↗ 依据</button>`).join('')}</p>`).join('')}${value.inferences.map(item => `<p class="inference">${pill('推断', 'subtle')} ${e(item.text)}<small>依据：${e(item.basis)} · 置信等级 ${e(item.confidence)}</small></p>`).join('')}${!value.facts.length && !value.inferences.length ? '<p class="muted">尚无充分依据</p>' : ''}${value.missing_fields.length ? `<div class="missing">待补充：${value.missing_fields.map(e).join('、')}</div>` : ''}</article>`;
}

/** 功能：渲染客户详情三栏。输入：data 为完整客户响应。输出：无。
 * 逻辑：邮件、同版分析、联系人和评分说明各有独立区域。约束：不展示未实现的发送按钮。 */
function renderDetail(data) {
  const name = companyName(data), analysis = data.analysis;
  $('detail-crumb').textContent = ' / ' + name;
  $('detail-header').innerHTML = `<a href="#" class="back-link">← 返回我的 Gmail 收件箱</a><div class="detail-title"><div class="avatar large">${e(name.slice(0, 1))}</div><div class="detail-identity"><h1>${e(name)}</h1><p>${e(data.domains.join(' · ') || '公共邮箱 · 按联系人独立归组')} ${pill(data.crm_status === 'registered' ? '已建档' : '未建档', 'subtle')}</p></div><div class="actions"><button id="register" class="secondary">${data.crm_status === 'registered' ? '编辑客户档案' : '建立客户档案'}</button><button id="reanalyze" class="primary">↻ 更新分析</button></div></div><div class="analysis-status">${pill(data.provider === 'rules' ? '规则占位结果' : data.provider === 'agent' ? 'Agent 分析' : '尚无分析', 'green')}<span>${e(jobNames[data.job_status] || '未创建任务')} · ${e(date(data.generated_at))}</span>${data.stale ? pill('上下文已变化，当前展示旧分析', 'warning') : ''}${data.job_error ? `<span class="failure">${e(data.job_error.message)}</span>` : ''}<button id="detail-refresh" class="text-btn">刷新状态</button></div>`;
  $('detail-content').innerHTML = `<div class="detail-grid"><section class="mail-panel"><div class="section-title"><h2>邮件往来 <span class="count-pill">${data.email_count}</span></h2></div><div class="tabs" role="group" aria-label="邮件方向">${[['all', '全部'], ['inbound', '收件'], ['outbound', '发件']].map(([key, label]) => `<button data-direction="${key}" class="${state.direction === key ? 'selected' : ''}">${label}</button>`).join('')}</div><div id="emails"></div></section><section class="analysis-panel"><div class="section-title"><h2>客户画像</h2><span class="muted">基于已有业务事实</span></div>${analysis ? ['industry_context', 'company_ops', 'intent'].map(key => renderDimension(key, analysis.detail_view.profile[key])).join('') : '<div class="empty">分析尚未生成，请更新分析或等待 Agent。</div>'}<div class="section-title analysis-divider"><h2>客户分析</h2></div>${analysis ? ['timeline', 'opportunity', 'risk', 'guidance'].map(key => renderDimension(key, analysis.detail_view.analysis[key])).join('') : ''}${analysis?.detail_view.conflicts.length ? `<div class="missing">事实变化与冲突：${analysis.detail_view.conflicts.map(item => e(item.summary)).join('；')}</div>` : ''}</section><aside class="context-panel"><article class="context-card"><p class="eyebrow">FOLLOW-UP PRIORITY</p><h3>跟进优先级</h3><div class="priority-number">${data.score === null ? '—' : data.score}<span>${data.score === null ? '资料不足，未评分' : '/ 100'}</span></div><p class="fine">${data.provider === 'rules' ? '当前为规则占位分数，用于前后端联调。' : '该分数表示处理优先级。'} 不代表成交概率。</p>${data.score_reasons.map(item => `<p class="score-reason">${e(item.note)}<strong>${item.feature === 'insufficient_data' ? '—' : e(item.contribution)}</strong></p>`).join('')}</article><article class="context-card"><h3>待补充信息</h3><div class="missing-tags">${(analysis?.detail_view?.missing_fields || []).map(text => pill(text, 'warning')).join('') || (analysis ? '<p class="muted">当前分析未列出缺失项</p>' : '<p class="muted">暂未生成缺失项清单</p>')}</div><p class="fine">${e(analysis?.detail_view?.context_completeness?.note || '可从左侧邮件查看原始依据。')}</p></article><article class="context-card"><h3>联系人</h3>${data.contacts.map(contact => `<div class="contact"><strong>${e(contact.contact_name || '姓名待确认')}${contact.is_primary ? ' · 主要联系人' : ''}</strong><span>${e(contact.contact_email)}</span><small>${contact.interaction_count} 封往来</small></div>`).join('')}</article><article class="context-card"><h3>业务记录</h3><p class="muted">工单 ${data.context.tickets.length} · 报价 ${data.context.quotes.length} · 订单 ${data.context.orders.length}</p><p class="fine">只展示后端已有记录。邮件中提到报价，不代表实际已发送报价。</p></article></aside></div>`;
  renderEmails();
  $('register').onclick = openRegister;
  $('reanalyze').onclick = event => busy(event.currentTarget, async () => { await request(`companies/${data.company_id}/analyze/`, { method: 'POST' }); await loadDetail(data.company_id, false); });
  $('detail-refresh').onclick = event => busy(event.currentTarget, () => loadDetail(data.company_id, false));
}

/** 功能：按方向显示原始邮件。输入：state.detail、state.direction。输出：无。
 * 逻辑：时间线显示来源、抽取失败和逐字正文。约束：所有正文转义，禁止执行 HTML 或指令。 */
function renderEmails() {
  const emails = state.detail.context.emails.filter(item => state.direction === 'all' || item.direction === state.direction);
  $('emails').innerHTML = emails.map(item => { const sender = item.from || item.mailbox_address || '未知发件人'; return `<article class="email-card" data-email-ref="${e(item.dedupe_key)}"><div class="email-top"><span class="avatar tiny">${e(sender.slice(0, 1).toUpperCase())}</span><div><strong>${e(sender)}</strong><small>${e(date(item.sent_at || item.received_at))} · ${{ inbound: '收件', outbound: '发件', unknown: '方向未知' }[item.direction] || '方向未知'}</small></div></div><h4>${e(item.subject)}</h4><div class="email-source">${pill(({ gmail_real: '真实 Gmail', synthetic_sample: '合成样例', simulated: '模拟数据', research_dataset: '研究数据' })[item.source] || item.source, 'subtle')}${item.extract_status !== 'completed' ? pill('未解析：' + item.extract_status, 'warning') : ''}</div><pre>${e(item.body_text)}</pre>${item.extract_error ? `<p class="failure">${e(item.extract_error)}</p>` : ''}</article>`; }).join('') || '<div class="empty">没有此方向的邮件</div>';
}

/** 功能：获取客户详情并可触发打开事件。输入：id、trigger 是否请求分析。
 * 输出：无。逻辑：版本化 API 加请求序号阻止旧导航覆盖新页面。
 * 约束：仅首次打开触发 POST；刷新 GET 不隐式写入。 */
async function loadDetail(id, trigger = true) {
  const sequence = ++state.navigation;
  if (trigger) await request(`companies/${encodeURIComponent(id)}/analyze/`, { method: 'POST' });
  const data = await request(`companies/${encodeURIComponent(id)}/`);
  if (sequence !== state.navigation || location.hash !== `#company/${id}`) return;
  state.detail = data;
  renderDetail(data);
}

/** 功能：按哈希切换列表和详情。输入：location.hash 隐式状态。输出：无。
 * 逻辑：只接受已知 company 路由，未知路径显示列表。
 * 约束：路由错误通过 notice 展示，不伪造空数据。 */
async function navigate() {
  $('notice').hidden = true;
  const match = location.hash.match(/^#company\/([\w-]+)$/);
  $('list-page').hidden = Boolean(match); $('detail-page').hidden = !match;
  if (match) {
    $('detail-header').innerHTML = '<p class="muted">正在读取客户资料…</p>';
    $('detail-content').innerHTML = '';
    state.direction = 'all';
    await loadDetail(match[1]);
  } else {
    ++state.navigation;
    $('detail-crumb').textContent = '';
    await loadList();
  }
}


/** 功能：读取并渲染当前员工自己的 Gmail 连接。输入：当前会话。输出：邮箱数组。
 * 逻辑：授权状态与同步状态分别显示。约束：响应中不包含 Google 凭证。 */
async function loadMailboxes() {
  state.mailboxes = await request('mailboxes/');
  const authorized = state.mailboxes.filter(item => item.gmail_authorized);
  const primary = authorized[0];
  $('gmail-chip-label').textContent = primary ? primary.address : '连接 Gmail';
  $('gmail-manage').textContent = primary ? '管理 Gmail' : '连接 Gmail';
  $('gmail-channel-status').textContent = primary
    ? `${primary.address} · ${gmailStates[primary.sync_state?.status] || '状态未知'}`
    : '未授权 · 点击连接';
  $('inbox-scope').textContent = authorized.length
    ? `已授权账号：${authorized.map(item => item.address).join('、')} · 客户邮件按公司域名归组。`
    : '尚未授权 Gmail；连接后只显示当前员工同步的客户邮件。';
  renderGmailAccounts();
  return state.mailboxes;
}

/** 功能：渲染 Gmail 授权弹窗中的员工账号。输入：state.mailboxes。输出：无。
 * 逻辑：只给已授权账号显示同步和移除操作。约束：令牌不进入 DOM。 */
function renderGmailAccounts() {
  const authorized = state.mailboxes.filter(item => item.gmail_authorized);
  $('gmail-accounts').innerHTML = authorized.length
    ? authorized.map(item => {
        const status = item.sync_state?.status || 'authorization_required';
        const lastSync = item.sync_state?.last_synced_at;
        return `<article class="gmail-account"><div><strong>${e(item.address)}</strong><p>${e(gmailStates[status] || status)}${lastSync ? ` · 上次完成 ${e(date(lastSync))}` : ''}</p>${item.sync_state?.error ? `<small class="failure">${e(item.sync_state.error)}</small>` : ''}</div><div class="account-actions"><button class="secondary" data-gmail-sync="${e(item.mailbox_id)}" ${status === 'sync_running' ? 'disabled' : ''}>${status === 'sync_running' ? '同步中…' : '同步 Gmail'}</button><button class="text-btn" data-gmail-disconnect="${e(item.mailbox_id)}">移除授权</button></div></article>`;
      }).join('')
    : '<div class="gmail-empty"><strong>尚未连接 Google 邮箱</strong><p>点击下方按钮，选择当前业务员使用的 Gmail 账号。</p></div>';
}

/** 功能：打开当前员工 Gmail 授权管理。输入：浏览器会话。输出：无。
 * 逻辑：先刷新邮箱状态再显示弹窗。约束：不会创建占位邮箱。 */
async function openGmail() {
  await loadMailboxes();
  $('gmail-dialog').showModal();
}

/** 功能：开始服务端 Google OAuth。输入：当前员工会话。输出：浏览器跳转。
 * 逻辑：后端生成带 state 的授权地址。约束：前端不接触 access token。 */
async function startGmailAuthorization() {
  const result = await request('mailboxes/gmail-authorize/', { method: 'POST' });
  if (!result.authorization_url) throw new Error('后端未返回 Google 授权地址。');
  location.assign(result.authorization_url);
}

/** 功能：轮询后台 Gmail 同步状态并刷新客户列表。输入：mailboxIds。输出：无。
 * 逻辑：同步完成或失败即停止，最长等待十分钟。约束：新一轮轮询会终止旧轮询。 */
async function pollGmailSync(mailboxIds) {
  const polling = ++state.gmailPolling;
  const tracked = new Set(mailboxIds);
  for (let attempt = 0; attempt < 200; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 3000));
    if (polling !== state.gmailPolling) return;
    await loadMailboxes();
    const mailboxes = state.mailboxes.filter(item => tracked.has(item.mailbox_id));
    const pending = mailboxes.some(item => ['sync_requested', 'sync_running'].includes(item.sync_state?.status));
    if (pending) continue;

    await loadList();
    const failed = mailboxes.find(item => item.sync_state?.status === 'failed');
    if (failed) {
      notice(`Gmail 同步失败：${failed.sync_state?.error || '请查看后端 Agent 日志。'}`);
    } else {
      notice('Gmail 同步和客户分析已完成，收件箱已刷新。', false);
    }
    return;
  }
  await loadList();
  notice('Gmail 同步仍在后台运行，可稍后再次点击刷新查看结果。', false);
}

/** 功能：请求同步一个已授权员工邮箱。输入：mailboxId。输出：无。
 * 逻辑：后端排队并自动启动 Agent，页面随后轮询结果。约束：不在浏览器读取 Gmail。 */
async function requestGmailSync(mailboxId) {
  await request(`mailboxes/${encodeURIComponent(mailboxId)}/request-sync/`, { method: 'POST' });
  await loadMailboxes();
  notice('同步请求已提交，Agent 正在后台读取和分析邮件。', false);
  void pollGmailSync([mailboxId]).catch(error => notice(error.message));
}

/** 功能：刷新当前员工收件箱。输入：当前已授权邮箱。输出：无。
 * 逻辑：对每个授权邮箱请求同步并自动等待 Agent 结果。约束：未授权时只刷新已有页面数据。 */
async function refreshInbox() {
  await loadMailboxes();
  const authorized = state.mailboxes.filter(item => item.gmail_authorized);
  if (!authorized.length) {
    await loadList();
    notice('尚未授权 Gmail，当前只刷新了已有客户数据。');
    return;
  }
  await Promise.all(
    authorized.map(item => request(`mailboxes/${encodeURIComponent(item.mailbox_id)}/request-sync/`, { method: 'POST' }))
  );
  await loadMailboxes();
  notice('正在同步 Gmail 并运行客户分析，完成后页面会自动刷新。', false);
  void pollGmailSync(authorized.map(item => item.mailbox_id)).catch(error => notice(error.message));
}

/** 功能：移除一个员工 Gmail 授权。输入：mailboxId。输出：无。
 * 逻辑：仅删除后端保存的授权，保留历史客户资料。约束：不能操作其他员工邮箱。 */
async function disconnectGmail(mailboxId) {
  await request(`mailboxes/${encodeURIComponent(mailboxId)}/gmail-authorization/`, { method: 'DELETE' });
  await loadMailboxes();
  notice('Gmail 授权已移除，历史邮件和客户分析仍然保留。', false);
}

/** 功能：打开模拟来信表单并读取可用业务邮箱。输入：当前会话。输出：无。
 * 逻辑：无邮箱时显式创建仅用于模拟的业务邮箱。
 * 约束：不索取 Gmail 凭证，不创建真实 OAuth 连接。 */
async function openMail() {
  let mailboxes = await request('mailboxes/');
  if (!mailboxes.length) mailboxes = [await request('mailboxes/', { method: 'POST', data: { address: 'sales@salesmate.example' } })];
  $('mail-form').elements.mailbox_id.innerHTML = mailboxes.map(item => `<option value="${e(item.mailbox_id)}">${e(item.address)}</option>`).join('');
  $('mail-dialog').showModal();
}

/** 功能：打开客户建档表单。输入：state.detail。输出：无。
 * 逻辑：从权威 CRM 字段填表，不从 Agent 推断回填人数。
 * 约束：提交时使用读取 revision 做乐观锁。 */
function openRegister() {
  const form = $('register-form'), data = state.detail;
  form.elements.company_name.value = data.company_name || '';
  form.elements.industry_from_crm.value = data.context.customer.industry_from_crm || 'unknown';
  form.elements.employee_count.value = data.context.customer.employee_count ?? '';
  form.elements.employee_count_source.value = data.context.customer.employee_count_source || '';
  $('register-dialog').showModal();
}

/** 功能：提交会话登录。输入：event 表单事件。输出：无。
 * 逻辑：API 成功后清除密码表单并初始化工作台。
 * 约束：无自动登录重试，不保存密码。 */
async function loginSubmit(event) {
  event.preventDefault();
  await busy(event.submitter, async () => {
    await request('session/', { method: 'POST', data: Object.fromEntries(new FormData(event.target)) });
    event.target.reset();
    await initialize();
  });
}

/** 功能：提交一封模拟邮件。输入：event 表单事件。输出：无。
 * 逻辑：保存成功关闭弹窗并跳到对应公司；失败保留填写内容。
 * 约束：不重试以免产生第二封独立模拟邮件。 */
async function mailSubmit(event) {
  event.preventDefault();
  await busy(event.submitter, async () => {
    const result = await request('demo/email/', { method: 'POST', data: Object.fromEntries(new FormData(event.target)) });
    $('mail-dialog').close(); event.target.reset();
    const hash = `#company/${result[0].company_id}`;
    if (location.hash === hash) await loadDetail(result[0].company_id, false); else location.hash = hash;
    notice('模拟邮件已保存，来源标记为合成样例。', false);
  });
}

/** 功能：提交客户档案。输入：event 表单事件。输出：无。
 * 逻辑：空人数映射为 null，使用当前 revision 防止覆盖并发修改。
 * 约束：版本冲突保留表单并显示错误，用户刷新后自行确认。 */
async function registerSubmit(event) {
  event.preventDefault();
  await busy(event.submitter, async () => {
    const data = Object.fromEntries(new FormData(event.target));
    data.employee_count = data.employee_count === '' ? null : Number(data.employee_count);
    data.employee_count_source = data.employee_count_source || null;
    await request(`companies/${state.detail.company_id}/register/`, { method: 'POST', data, version: state.detail.revision });
    $('register-dialog').close();
    await loadDetail(state.detail.company_id, false);
    notice('客户档案已保存。', false);
  });
}

/** 功能：初始化会话与服务能力。输入：当前浏览器会话。输出：无。
 * 逻辑：服务端本地自动会话直接进入工作台并隐藏退出；其余匿名访问显示登录页。
 * 约束：失败保持可见，未连接 Gmail 不展示假同步成功。 */
async function initialize() {
  const session = await request('session/');
  $('login-screen').hidden = session.authenticated;
  $('workspace').hidden = !session.authenticated;
  $('logout').hidden = session.debug_auto_login;
  if (!session.authenticated) return;
  $('username').textContent = session.username;
  state.runtime = await request('demo/runtime/');
  await loadMailboxes();
  const isRules = state.runtime.provider === 'rules';
  $('provider-label').textContent = isRules ? '规则占位模式' : 'Agent 接入模式';
  $('provider-note').textContent = isRules ? '当前员工可连接 Gmail；演示分析也可离线生成。' : '授权或刷新 Gmail 后，Agent 会自动读取邮件并更新分析。';
  $('seed').hidden = !isRules; $('compose').hidden = !isRules;
  $('workspace-footnote').textContent = isRules
    ? '当前员工数据空间 · 支持 Gmail 授权与离线规则演示 · 统计时区：' + state.runtime.timezone
    : '当前员工数据空间 · Gmail 同步与 Agent 分析自动运行 · 统计时区：' + state.runtime.timezone;
  await navigate();
  const callback = new URLSearchParams(location.search);
  if (callback.get('gmail') === 'authorized') {
    const address = callback.get('address') || '';
    notice(`Gmail ${address} 已授权，Agent 正在自动同步和分析邮件。`, false);
    history.replaceState({}, '', location.pathname + location.hash);
    const mailbox = state.mailboxes.find(item => item.address === address);
    if (mailbox) void pollGmailSync([mailbox.mailbox_id]).catch(error => notice(error.message));
  } else if (callback.get('gmail') === 'error') {
    const reason = callback.get('reason');
    const oauthErrors = {
      access_denied: 'Google 拒绝了授权。请确认当前账号已加入 OAuth 测试用户，并在授权页允许 Gmail 只读权限。',
      InvalidState: '授权会话状态已失效。请始终使用 127.0.0.1 打开页面，并重新发起授权。',
      InvalidGrantError: 'Google 授权码无效或已过期。请重新发起一次授权，不要重复打开旧回调地址。',
      InvalidClientError: 'Google OAuth 客户端 ID 或密钥不匹配。请检查根目录 .env 中的 Web application 凭据。',
      HttpError: 'Gmail API 调用失败。请确认项目已启用 Gmail API，且当前账号拥有授权权限。',
    };
    notice(oauthErrors[reason] || `Google 邮箱授权失败${reason ? `（${reason}）` : ''}，请查看后端终端中的完整回调错误。`);
    history.replaceState({}, '', location.pathname + location.hash);
  }
}

/** 功能：注册静态表单与动态内容事件。输入：现有 DOM。输出：无。
 * 逻辑：委托监听来源、方向；所有异步操作集中展示异常。
 * 约束：只绑定一次，不通过 eval 或字符串内联事件执行代码。 */
function bindEvents() {
  $('login-form').addEventListener('submit', loginSubmit);
  $('mail-form').addEventListener('submit', mailSubmit);
  $('register-form').addEventListener('submit', registerSubmit);
  $('logout').onclick = event => busy(event.currentTarget, async () => { await request('session/', { method: 'DELETE' }); state.detail = null; await initialize(); });
  $('filters').onsubmit = event => { event.preventDefault(); state.page = 1; busy(event.submitter, loadList); };
  $('filters').onreset = () => { state.page = 1; setTimeout(() => busy(null, loadList), 0); };
  $('refresh').onclick = event => busy(event.currentTarget, refreshInbox);
  $('previous').onclick = event => { state.page -= 1; busy(event.currentTarget, loadList); };
  $('next').onclick = event => { state.page += 1; busy(event.currentTarget, loadList); };
  $('compose').onclick = event => busy(event.currentTarget, openMail);
  $('seed').onclick = event => busy(event.currentTarget, async () => { const result = await request('demo/seed/', { method: 'POST' }); await loadList(); notice(`已导入 ${result.created_emails} 封合成样例。重复导入不会新增已有邮件。`, false); });
  $('gmail-channel').onclick = () => openGmail().catch(error => notice(error.message));
  $('gmail-manage').onclick = event => busy(event.currentTarget, openGmail);
  $('gmail-manage-top').onclick = () => openGmail().catch(error => notice(error.message));
  $('gmail-authorize').onclick = event => busy(event.currentTarget, startGmailAuthorization);
  $('gmail-accounts').onclick = event => {
    const sync = event.target.closest('[data-gmail-sync]');
    if (sync) busy(sync, () => requestGmailSync(sync.dataset.gmailSync));
    const disconnect = event.target.closest('[data-gmail-disconnect]');
    if (disconnect) busy(disconnect, () => disconnectGmail(disconnect.dataset.gmailDisconnect));
  };
  document.querySelectorAll('.close-dialog').forEach(button => { button.onclick = () => button.closest('dialog').close(); });
  $('detail-content').addEventListener('click', event => {
    const direction = event.target.closest('[data-direction]');
    if (direction) { state.direction = direction.dataset.direction; document.querySelectorAll('[data-direction]').forEach(button => button.classList.toggle('selected', button === direction)); renderEmails(); }
    const reference = event.target.closest('[data-ref]');
    if (reference) {
      state.direction = 'all';
      document.querySelectorAll('[data-direction]').forEach(button => button.classList.toggle('selected', button.dataset.direction === 'all'));
      renderEmails();
      const email = [...document.querySelectorAll('[data-email-ref]')].find(element => element.dataset.emailRef === reference.dataset.ref);
      if (email) { email.scrollIntoView({ behavior: 'smooth', block: 'center' }); email.classList.add('highlight'); }
      else notice('该依据来自已保存的 CRM 业务记录，来源 ID：' + reference.dataset.ref, false);
    }
  });
  window.addEventListener('hashchange', () => { if (!$('workspace').hidden) busy(null, navigate); });
}

bindEvents();
initialize().catch(error => notice(error.message));
