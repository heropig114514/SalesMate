/**
 * Responsibility: Implement native browser interactions for employee Gmail/QQ inboxes, authorization management, and customer workspaces.
 * Implementation: Registration/login, hash routing, compact mail-group cards, and continuous reads for one customer. Details are read-only by default; analysis and fact upgrades require separate explicit submissions. The shared floating entry preserves the current conversation. Mail settings use a separate page, and only explicit connection actions open authorization popups. Each sync asks for scope; QQ capabilities control access, stale responses are isolated, and drafts remain separate.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: v1.2.1 refreshes the review module cache for asynchronous error isolation. Navigation cache versions reflect removal of the sidebar priority and experiment entries and support for opportunity priorities. Chat Markdown, 0919 interface, shared language/API, and account-reset navigation resources use coordinated cache versions. workspace.js shares navigation and the bottom Profile entry; assistant-widget.js manages floating chat; api.js handles communication; qq.js manages QQ; gmail-scope.js manages Gmail scope; runtime qq_enabled controls entry points, synchronization, and polling; mail-source.js labels sources; assistant.js manages chat/drafts; notice.js manages notifications.
 * Directory: $, date, companyName, pill, notice, busy, renderStats, renderRow, loadList,
 * renderDimension, renderDetail, renderEmails, revealSource, setDetailLiveStatus, loadDetail, upgradeFacts, navigate, loadMailboxes, renderGmailAccounts, openEmailSettings, showGmailAuthorization,
 * startGmailAuthorization, pollGmailSync, requestGmailSync, refreshInbox,
 * disconnectGmail, openMail, openRegister, showAuthForm, signupSubmit, loginSubmit,
 * mailSubmit, registerSubmit, initialize, bindEvents.
 * Variable index: $ locates elements; state.replyDrafts stores per-customer reply drafts for this page; state holds pagination, account identity, session capabilities, current details, direction, and the list response signature.
 * closeEvidence cleans up source previews; detailObserver manages read-only customer polling and failure pauses; signals, sizes, dimensions, and gmailStates map backend enums into current-language labels; assistant manages chat; notices manages page notification lifetimes; extractionLabels holds fact-upgrade and sample-status text; emailSettingsLabels holds bilingual static mail-settings text.
 */
import { mountReplyComposer } from './channel-reply.js?v=20260921-product';
import { mountEvidencePreview } from './evidence-preview.js?v=20260921-product';
import { chooseGmailScope } from './gmail-scope.js?v=20260921-product';
import { t, h, locale, language } from './i18n.js?v=20260921-product';

import { initProcessingUI, updateRunProgress, refreshReviewBadge, openMailboxEmails } from './processing.js?v=1.2.1';
import { mailSourceLabel } from './mail-source.js?v=20260921-product';
import { initQQ, renderQQAccounts, chooseQQScope } from './qq.js?v=20260921-product';
import { mountWorkspace, setWorkspaceContext, refreshWorkspace, businessHref } from './workspace.js?v=20260922-sidebar';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { DetailObserver, patchHTML, preserveReading } from './live-detail.js?v=20260921-product';
import { getAssistant, enableAssistant, openAssistantLink } from './assistant-widget.js?v=20260921-markdown';
import { Notice } from './notice.js?v=20260921-product';

const assistant = getAssistant();
const closeEvidence = mountEvidencePreview(document.getElementById('detail-content'), () => state.detail, revealSource);

/** Function: Locate a page element by ID. Inputs: id. Outputs: Element or null. Logic: Native DOM lookup. Constraints: Callers use declared IDs. */
const $ = id => document.getElementById(id);
const notices = new Notice($('notice'));
const state = { replyDrafts: new Map(), account: null, page: 1, count: 0, runtime: null, mailboxes: [], detail: null, direction: 'all', navigation: 0, gmailPolling: 0, listSignature: null };
const signals = { unknown: t('待确认'), inquiry_intent: t('询盘'), new_lead_no_profile: t('新线索未建档'), quoted_not_closed: t('已报价未成交'), repeat_purchase: t('复购') };
const sizes = { unknown: t('规模未知'), lt_50: t('少于 50 人'), '50_100': t('50–99 人'), '100_200': t('100–199 人'), '200_500': t('200–499 人'), gte_500: t('500 人及以上') };
const dimensions = { industry_context: t('行业情况'), company_ops: t('公司经营分析'), intent: t('意向分析'), timeline: t('时间轴'), opportunity: t('商机分析'), risk: t('风险分析'), guidance: t('下一步引导') };
const emailSettingsLabels = language === 'en' ? {
  description: 'Manage connected mailboxes and choose when to sync. Opening settings does not require Google authorization.',
  add: 'Add Google mailbox', reconnect: 'Reconnect', progress: 'View sync progress',
  empty: 'Use “Add Google mailbox” to connect an account. Authorization starts only after you confirm.',
  selectAccount: 'Select this account on Google: ',
} : {
  description: '管理已连接的邮箱，自行选择同步时间和范围。进入设置无需再次进行 Google 授权。',
  add: '添加 Google 邮箱', reconnect: '重新授权', progress: '查看同步进度',
  empty: '点击“添加 Google 邮箱”连接账号，确认后才会进入 Google 授权。',
  selectAccount: '请在 Google 授权页选择此账号：',
};
const extractionLabels = language === 'en' ? {
  upgrade: 'Upgrade email facts', required: 'Historical email facts need an upgrade before analysis.',
  pending: 'Email fact upgrade in progress; analysis will be queued when complete.',
  failed: 'Email fact upgrade failed. Use Upgrade email facts to retry explicitly.',
  queued: 'Upgrade requested. Status will update automatically.', synthetic: 'Synthetic sample', unscored: 'No score generated',
} : {
  upgrade: '升级邮件事实', required: '历史邮件事实需要升级后才能分析。',
  pending: '邮件事实正在升级，完成后会自动排队分析。', failed: '邮件事实升级失败，可点击“升级邮件事实”明确重试。',
  queued: '已提交升级请求，状态将自动更新。', synthetic: '虚构样例', unscored: '尚未生成评分',
};
const gmailStates = { connected: t('已连接，待选择同步范围'), authorization_required: t('未授权'), sync_requested: t('等待 Agent 同步'), sync_running: t('正在同步'), completed: t('同步完成'), failed: t('同步失败'), partial: t('部分完成'), ok: t('同步完成') };
const detailObserver = new DetailObserver({
  read: id => request(`companies/${encodeURIComponent(id)}/`),
  apply: data => {
    if ($('workspace').hidden || location.hash !== `#company/${data.company_id}`) { detailObserver.stop(); return; }
    renderDetail(data);
  },
  fail: error => setDetailLiveStatus(t`自动更新已暂停：${error.message} 当前内容可能已过时。`, true),
});


/** Function: Display time in the configured time zone. Inputs: value is an ISO timestamp. Outputs: A date-time string.
 * Logic: Intl uses the current UI language and backend-declared time zone. Constraints: Empty timestamps remain unknown rather than being inferred. */
function date(value) {
  return value ? new Intl.DateTimeFormat(locale, { timeZone: state.runtime?.timezone || 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : t('尚无记录');
}

/** Function: Build a company display name. Inputs: row is a company projection. Outputs: An established name or domain/contact identifier.
 * Logic: Use existing identity information when the company name is missing. Constraints: Do not infer an unknown company name. */
function companyName(row) {
  return row.company_name || row.domains[0] || row.contacts[0]?.contact_email || t('待确认公司');
}

/** Function: Generate safe label HTML. Inputs: text and kind. Outputs: span HTML.
 * Logic: Escape both values. Constraints: kind is used only as a CSS class, never executed. */
function pill(text, kind = '') { return `<span class="pill ${e(kind)}">${e(text)}</span>`; }

/** Function: Display an operation result or error. Inputs: message and the error flag. Outputs: None.
 * Logic: Delegate plain-text display to Notice, with type-dependent dismissal, reading pauses, and manual dismissal.
 * Constraints: Never insert server HTML, alter business state, or automatically retry failed requests. */
function notice(message, error = true) {
  notices.show(message, error);
}

/** Function: Disable the triggering button during an asynchronous operation and display failures. Inputs: button and asynchronous operation. Outputs: None.
 * Logic: Restore state in finally and keep failures visible. Constraints: No duplicate execution or implicit retries. */
async function busy(button, operation) {
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = t('处理中…'); }
  try { await operation(); }
  catch (error) { notice(error.message); }
  finally {
    if (button?.isConnected) {
      button.disabled = button.id === 'previous' ? state.page <= 1 : button.id === 'next' ? state.page * 20 >= state.count : false;
      button.textContent = original;
    }
  }
}

/** Function: Display the company and today's new-mail overview. Inputs: stats. Outputs: None.
 * Logic: The home page shows mail customers and today's mail; Channels retains the count awaiting CRM registration. Display backend definitions and time zone. Constraints: Do not replace global backend totals with the current page's counts. */
function renderStats(stats) {
  $('stats').innerHTML = [[t('邮件客户'), stats.companies, t('已保存业务邮件的客户'), '▦'], [t('待建档客户'), stats.unregistered, t('补齐档案，积累客户上下文'), '♧'], [t('今日新邮件'), stats.new_emails_today, t`统计时区 ${state.runtime.timezone}`, '✉']].filter((_, index) => index !== 1 || (location.hash && location.hash !== '#home')).map(([label, value, note, icon]) => `<article class="stat-card"><div><span class="stat-label">${e(label)}</span><strong>${e(value)}</strong><p>${e(note)}</p></div><span class="stat-icon">${icon}</span></article>`).join('');
}

/** Function: Generate a company mail-group card. Inputs: row is the backend projection of company, score, and mail sources. Outputs: Escaped link HTML.
 * Logic: Group signal, industry, and size on the first line; show known domains and contacts below; retain date and priority on the right.
 * Constraints: Never calculate or modify scores; explicitly mark unknown information and keep demo sources and failure states visible. */
function renderRow(row) {
  const name = companyName(row);
  const initials = /^[A-Za-z]/.test(name) ? name.split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase() : name.slice(0, 1);
  return h`<a class="company-row" href="#company/${encodeURIComponent(row.company_id)}">
    <div class="company-main"><div class="avatar" aria-hidden="true">${e(initials)}</div><div>
      <div class="row-title"><h3>${e(name)}</h3><span class="row-signal">${pill(signals[row.signal], row.signal === 'unknown' ? 'subtle' : 'green')}</span>${pill(row.industry === 'unknown' ? t('行业未知') : t(row.industry), 'subtle')}${pill(sizes[row.size_band], 'subtle')}</div>
      <p class="identity">${row.domains?.length ? e('@' + row.domains[0]) + ' <span>·</span> ' : ''}${e(row.contacts[0]?.contact_email || t('联系人待确认'))} <span>·</span> ${row.email_count} 封往来</p>
      <p class="summary">${e(row.headline_summary)}</p>
      <div class="row-tags">${(row.email_sources || []).map(source => pill(mailSourceLabel(source), source === 'synthetic_sample' ? 'warning' : 'subtle')).join('')}${row.stale ? pill(t('分析待更新'), 'warning') : ''}${row.job_status === 'failed' ? pill(t('处理失败'), 'warning') : ''}${pill(row.provider === 'rules' ? t('规则占位') : row.provider === 'agent' ? t('Agent 分析') : t('等待分析'), row.provider === 'rules' ? 'warning' : 'subtle')}</div>
    </div></div>
    <div class="row-date">${e(date(row.last_message_at))}<span>${row.crm_status === 'registered' ? t('已建档') : t('未建档')}</span></div>
    <div class="row-score">${row.score === null ? h('<span class="unscored">—</span><small>资料不足 · 未评分</small>') : h`<strong>${row.score}<small> / 100</small></strong><div class="score-track"><span style="width:${Number(row.score)}%"></span></div><small>跟进优先级${row.provider === 'rules' ? t(' · 占位') : ''}</small>`}</div>
  </a>`;
}

/** Function: Load list data and pagination state. Inputs: The form, state.page, and whether to show a loading placeholder. Outputs: The list response.
 * Logic: Capture account identity to reject responses for a previous account; call the backend with query parameters. Retain the list during background polling to avoid clearing/rebuilding flicker every three seconds. Constraints: A normal load failure must not leave an old list looking current. */
async function loadList({ showLoading = true } = {}) {
  const account = state.account;
  if (showLoading) {
    $('company-list').innerHTML = h('<div class="empty">正在读取当前员工的客户列表…</div>');
  }
  const params = new URLSearchParams(new FormData($('filters')));
  params.set('page', state.page); params.set('page_size', 20);
  try {
    const data = await request('companies/?' + params);
    if (account !== state.account) return;
    const signature = JSON.stringify(data);
    if (!showLoading && signature === state.listSignature) return data;
    state.listSignature = signature;
    state.count = data.count;
    renderStats(data.stats);
    $('result-count').textContent = data.count;
    $('company-list').innerHTML = data.results.length ? data.results.map(renderRow).join('') : h('<div class="empty"><span class="empty-icon">✉</span><h3>这个员工的收件箱还没有客户邮件</h3><p>连接 Gmail 或 QQ 并同步；非业务邮件与待复核邮件可在邮箱原文入口查看。</p></div>');
    $('page-number').textContent = `${state.page} / ${Math.max(1, Math.ceil(data.count / 20))}`;
    $('previous').disabled = state.page <= 1;
    $('next').disabled = state.page * 20 >= data.count;
    return data;
  } catch (error) {
    if (account !== state.account) return;
    if (showLoading) {
      $('company-list').innerHTML = h('<div class="empty">加载失败，请检查上方提示后点击刷新。</div>');
    }
    throw error;
  }
}

/** Function: Generate an analysis dimension with source previews. Inputs: key and value. Outputs: Safe HTML.
 * Logic: Display facts and inferences separately and mark missing fields. Constraints: Preview data-ref sources on hover, focus, or click; an explicit action locates the original source. Never execute email content. */
function renderDimension(key, value) {
  return `<article class="dimension" data-live-key="${e(key)}"><h4>${e(dimensions[key] || key)}</h4>${value.facts.map(item => `<p>${e(item.text)} ${item.source_refs.map(ref => h`<button class="source-ref" data-ref="${e(ref)}" aria-haspopup="dialog" aria-expanded="false" aria-label="预览引用来源">↗ 依据</button>`).join('')}</p>`).join('')}${value.inferences.map(item => h`<p class="inference">${pill(t('推断'), 'subtle')} ${e(item.text)}<small>依据：${e(item.basis)} · 置信等级 ${e(item.confidence)}</small></p>`).join('')}${!value.facts.length && !value.inferences.length ? h('<p class="muted">尚无充分依据</p>') : ''}${value.missing_fields.length ? h`<div class="missing">待补充：${value.missing_fields.map(e).join('、')}</div>` : ''}</article>`;
}

/** Function: Render customer details with email conversations, profile analysis, and assistant support. Inputs: data is the complete customer response. Outputs: None.
 * Logic: Display fact-upgrade status and explicit actions; do not interpret an empty score as insufficient information. Compare the full response, not just revision; reuse unchanged nodes and correct the reading position. Rebuild shared context only when customer identity changes. Keep secondary statistics in the middle column with expansion state preserved. Isolate reply drafts by customer and pass them to the assistant only on explicit action; chat retains the workspace conversation.
 * Constraints: The separate assistant and edit forms are outside partial updates; preserve unsaved drafts, focus, and mail direction without waiting for the whole batch. */
function renderDetail(data) {
  const previous = state.detail;
  if ($('detail-content').querySelector('.detail-grid') && JSON.stringify(previous) === JSON.stringify(data)) return;
  state.detail = data;
  try {
    preserveReading($('detail-page'), () => {
    const name = companyName(data), analysis = data.analysis;
    const upgrade = data.extraction_upgrade, repairs = upgrade?.repairs || {};
    const upgrading = Boolean(repairs.pending || repairs.running);
    const upgradeMessage = repairs.failed ? extractionLabels.failed : upgrading ? extractionLabels.pending : upgrade?.incompatible_emails ? extractionLabels.required : '';
    if (!previous || previous.company_id !== data.company_id || companyName(previous) !== name) setWorkspaceContext({ id: data.company_id, name }, 'inbox');
    $('detail-crumb').textContent = ' / ' + name;
    patchHTML($('detail-header'), h`<div class="detail-title"><a href="#inbox" class="back-link" aria-label="返回 Channels">←</a><div class="detail-identity"><h1>${e(name)}</h1><p>${e(data.domains.join(' · ') || t('公共邮箱 · 按联系人独立归组'))} ${pill(data.crm_status === 'registered' ? t('已建档') : t('未建档'), 'subtle')}</p></div><div class="actions"><button id="register" class="secondary">${data.crm_status === 'registered' ? t('编辑客户档案') : t('建立客户档案')}</button>${upgrade?.incompatible_emails || repairs.failed ? `<button id="upgrade-facts" class="secondary" ${upgrading ? 'disabled' : ''}>${e(extractionLabels.upgrade)}</button>` : ''}<button id="reanalyze" class="secondary">↻ 更新分析</button><button id="detail-refresh" class="text-btn">刷新状态</button></div></div>${upgradeMessage ? `<p class="analysis-alert" role="status">${e(upgradeMessage)}</p>` : ''}${data.stale || data.job_error ? `<p class="analysis-alert" role="status">${data.stale ? e(t('上下文已变化，当前展示旧分析')) : ''} ${e(data.job_error?.message || '')}</p>` : ''}`);
    patchHTML($('detail-content'), h`<div class="detail-grid"><section class="mail-panel"><div class="section-title"><h2>邮件往来 <span class="count-pill">${data.email_count}</span></h2></div><div class="tabs" role="group" aria-label="邮件方向">${[['all', t('全部')], ['inbound', t('收件')], ['outbound', t('发件')]].map(([key, label]) => `<button data-direction="${key}" class="${state.direction === key ? 'selected' : ''}">${label}</button>`).join('')}</div><div id="emails" data-live-preserve></div><div id="channel-reply-composer" class="channel-reply-composer" data-live-preserve></div><nav class="channel-mail-actions" aria-label="客户沟通操作"><a href="${e(businessHref('quotes', data.company_id, { create: '1' }))}">＋ 创建报价</a><a href="${e(businessHref('follow-ups', data.company_id, { create: '1' }))}">＋ 安排跟进</a><a href="${e(businessHref('actions', data.company_id))}">准备沟通动作 →</a></nav></section><section class="analysis-panel"><div class="section-title"><h2>客户画像</h2><span class="muted">基于已有业务事实</span></div>${analysis ? ['industry_context', 'company_ops', 'intent'].map(key => renderDimension(key, analysis.detail_view.profile[key])).join('') : h('<div class="empty">分析尚未生成，请更新分析或等待 Agent。</div>')}<div class="section-title analysis-divider"><h2>客户分析</h2></div>${analysis ? ['timeline', 'opportunity', 'risk', 'guidance'].map(key => renderDimension(key, analysis.detail_view.analysis[key])).join('') : ''}${analysis?.detail_view.conflicts.length ? h`<div class="missing">事实变化与冲突：${analysis.detail_view.conflicts.map(item => e(item.summary)).join('；')}</div>` : ''}<div class="context-panel"><details class="context-card priority-details" data-live-key="score" ${$('detail-content').querySelector('.priority-details')?.open ? 'open' : ''}><summary>${e(t("跟进优先级"))} · ${data.score ?? "—"}</summary><p class="eyebrow">FOLLOW-UP PRIORITY</p><h3>跟进优先级</h3><div class="priority-number">${data.score === null ? '—' : data.score}<span>${data.score === null ? extractionLabels.unscored : '/ 100'}</span></div><p class="fine">${data.provider === 'rules' ? t('当前为规则占位分数，用于前后端联调。') : t('该分数表示处理优先级。')} 不代表成交概率。</p>${data.score_reasons.map(item => `<p class="score-reason">${e(item.note)}<strong>${item.feature === 'insufficient_data' ? '—' : e(item.contribution)}</strong></p>`).join('')}</details><article class="context-card" data-live-key="missing"><h3>待补充信息</h3><div class="missing-tags">${(analysis?.detail_view?.missing_fields || []).map(text => pill(text, 'warning')).join('') || (analysis ? h('<p class="muted">当前分析未列出缺失项</p>') : h('<p class="muted">暂未生成缺失项清单</p>'))}</div><p class="fine">${e(analysis?.detail_view?.context_completeness?.note || t('可从左侧邮件查看原始依据。'))}</p></article><article class="context-card" data-live-key="contacts"><h3>联系人</h3>${data.contacts.map(contact => h`<div class="contact"><strong>${e(contact.contact_name || t('姓名待确认'))}${contact.is_primary ? t(' · 主要联系人') : ''}</strong><span>${e(contact.contact_email)}</span><small>${contact.interaction_count} 封往来</small></div>`).join('')}</article><article class="context-card" data-live-key="business-context"><h3>业务记录</h3><p class="muted">工单 ${data.context.tickets.length} · 报价 ${data.context.quotes.length} · 订单 ${data.context.orders.length}</p><p class="fine">只展示后端已有记录。邮件中提到报价，不代表实际已发送报价。</p></article></div></section><aside class="channel-assistant-slot"><span class="assistant-mark" aria-hidden="true">✧</span><h2>AI 助手</h2><p class="muted">讨论问题、起草邮件、翻译文字，或一起梳理工作计划。无需选择客户。</p><button class="secondary" id="detail-assistant-open" type="button">打开 AI 助手</button></aside></div>`);
    renderEmails();
    mountReplyComposer($('channel-reply-composer'), data.company_id, state.replyDrafts, assistant);
    $('detail-assistant-open').onclick = event => assistant.open(event.currentTarget);
    $('register').onclick = openRegister;
    if ($('upgrade-facts')) $('upgrade-facts').onclick = event => busy(event.currentTarget, () => upgradeFacts(data));
    $('reanalyze').onclick = event => busy(event.currentTarget, () => loadDetail(data.company_id, true));
    $('detail-refresh').onclick = event => busy(event.currentTarget, () => loadDetail(data.company_id, false));
    });
  } catch (error) { state.detail = previous; throw error; }
}

/** Function: Display original emails by direction. Inputs: state.detail and state.direction. Outputs: None.
 * Logic: Arrange incoming/outgoing bubbles and show synthetic markers, sources, extraction failures, and verbatim bodies. Constraints: Escape all body text and never execute HTML or instructions. */
function renderEmails() {
  const emails = state.detail.context.emails.filter(item => state.direction === 'all' || item.direction === state.direction);
  patchHTML($('emails'), emails.map(item => { const sender = item.from || item.mailbox_address || t('未知发件人'); return `<article class="email-card ${item.direction === 'outbound' ? 'email-outbound' : 'email-inbound'}" tabindex="-1" data-email-ref="${e(item.dedupe_key)}"><div class="email-top"><span class="avatar tiny">${e(sender.slice(0, 1).toUpperCase())}</span><div><strong>${e(sender)}</strong><small>${e(date(item.sent_at || item.received_at))} · ${{ inbound: t('收件'), outbound: t('发件'), unknown: t('方向未知') }[item.direction] || t('方向未知')}</small></div></div><h4>${e(item.subject)}</h4><div class="email-source">${pill(mailSourceLabel(item.source), 'subtle')}${item.synthetic_batch ? pill(extractionLabels.synthetic, 'warning') : ''}${item.extract_status !== 'completed' ? pill(t('未解析：') + item.extract_status, 'warning') : ''}</div><pre>${e(item.body_text)}</pre>${item.extract_error ? `<p class="failure">${e(item.extract_error)}</p>` : ''}</article>`; }).join('') || h('<div class="empty">没有此方向的邮件</div>'));
}

/** Function: Locate the email source explicitly selected by the user. Inputs: ref is an original reference key in the current detail. Outputs: None.
 * Logic: Restore the all-mail filter, remove old highlights, and focus the matching email; show an explicit message when it is missing.
 * Constraints: Change only the frontend reading position; never request or retry analysis, or assume non-email references necessarily belong to CRM. */
function revealSource(ref) {
  state.direction = 'all';
  document.querySelectorAll('[data-direction]').forEach(button => button.classList.toggle('selected', button.dataset.direction === 'all'));
  renderEmails();
  const email = [...document.querySelectorAll('[data-email-ref]')].find(element => element.dataset.emailRef === ref);
  document.querySelectorAll('.email-card.highlight').forEach(node => node.classList.remove('highlight'));
  if (email) { email.scrollIntoView({ behavior: 'smooth', block: 'center' }); email.classList.add('highlight'); email.focus({ preventScroll: true }); }
  else notice(t('当前详情中没有这条引用的邮件原文。'), false);
}

/** Function: Display automatic detail-update status. Inputs: message and paused, indicating whether manual recovery is required. Outputs: None.
 * Logic: Render status independently of details and show recovery only on failure. Constraints: Never present network failures as analysis success or failure. */
function setDetailLiveStatus(message, paused = false) {
  $('detail-live-message').textContent = message;
  $('detail-live-resume').hidden = !paused;
}

/** Function: Read customer details, requesting analysis only after an explicit click. Inputs: id and trigger, defaulting to false.
 * Outputs: The current read or analysis Promise. Logic: Read/render details and start read-only observation before explicit analysis; navigation generations isolate late responses.
 * Constraints: Preserve loaded details and observation after analysis failure and propagate the error to the caller; pause after read failure without automatic upgrades or retries. */
async function loadDetail(id, trigger = false) {
  detailObserver.stop();
  const sequence = ++state.navigation;
  setDetailLiveStatus(t('正在读取最新结果…'));
  try {
    const data = await request(`companies/${encodeURIComponent(id)}/`);
    if (sequence !== state.navigation || location.hash !== `#company/${id}`) return;
    renderDetail(data);
    setDetailLiveStatus(t('自动更新已开启 · 约每 3 秒检查新邮件、画像与评分'));
    detailObserver.start(id);
  } catch (error) {
    if (sequence !== state.navigation || location.hash !== `#company/${id}`) return;
    setDetailLiveStatus(t`自动更新已暂停：${error.message} 当前内容可能已过时。`, true);
    throw error;
  }
  if (trigger) await request(`companies/${encodeURIComponent(id)}/analyze/`, { method: 'POST' });
}

/** Function: Explicitly request an upgrade of the customer's historical email facts. Inputs: data contains current details and revision.
 * Outputs: The request/refresh Promise. Logic: Submit an empty body with the observed version; after success refresh by reading and retain automatic observation.
 * Constraints: Do not fetch the mailbox again; show request failures directly without automatic retries or changes to existing facts. */
async function upgradeFacts(data) {
  await request(`companies/${encodeURIComponent(data.company_id)}/extraction-upgrade/`, { method: 'POST', data: {}, version: data.revision });
  if (location.hash !== `#company/${data.company_id}`) return;
  await loadDetail(data.company_id);
  notice(extractionLabels.queued, false);
}

/** Function: Switch between list/details by hash and open legacy chat links in the floating widget. Inputs: Implicit location.hash. Outputs: None.
 * Logic: Stop detail observation when navigating while preserving workspace chat; mail settings display a separate account-management page and mark the Profile entry active. The widget preserves its conversation; legacy chat links open over the workspace without triggering analysis.
 * Constraints: All navigation is read-only; analysis and fact upgrades require their respective button clicks. */
async function navigate() {
  closeEvidence();
  detailObserver.stop();
  ++state.navigation;
  state.detail = null;
  $('notice').hidden = true;
  const chat = location.hash.match(/^#assistant(?:\/([\w-]+))?$/);
  if (chat) history.replaceState({}, '', location.pathname + location.search + '#home');
  const match = location.hash.match(/^#company\/([\w-]+)$/);
  const home = !location.hash || location.hash === '#home';
  const emailSettings = location.hash === '#gmail';
  document.body.classList.toggle('channel-detail', Boolean(match));
  $('workspace').querySelector('.topbar-right').hidden = !home;
  updateRunProgress();
  const active = emailSettings ? 'gmail' : home ? 'home' : 'inbox';
  mountWorkspace(active);
  $('workspace-overview').hidden = !home;
  $('list-page').querySelector('.page-heading').hidden = home;
  $('customer-list-title').textContent = home ? t('优先跟进客户') : t('邮件与客户分析');
  $('workspace-crumb').textContent = emailSettings ? 'Emails Connections' : home ? t('工作台') : 'Channels';
  $('email-settings-page').hidden = !emailSettings;
  $('list-page').hidden = Boolean(match) || emailSettings; $('detail-page').hidden = !match;
  if (match) {
    $('detail-header').innerHTML = h('<p class="muted">正在读取客户资料…</p>');
    $('detail-content').innerHTML = '';
    state.direction = 'all';
    await loadDetail(match[1]);
  } else {
    const navigation = ++state.navigation;
    setWorkspaceContext(null, active);
    $('detail-crumb').textContent = '';
    if (emailSettings) await openEmailSettings(); else await loadList();
    if (navigation !== state.navigation) return;
    if (location.hash === '#reviews') await openMailboxEmails();
    if (location.hash === '#processing') {
      const mailboxIds = state.mailboxes.filter(item => item.sync_state?.run_id && (!item.qq_authorized || state.runtime.qq_enabled)).map(item => item.mailbox_id);
      if (mailboxIds.length) void pollGmailSync(mailboxIds).catch(error => notice(error.message));
      else { $('sync-progress').hidden = false; $('sync-progress').textContent = t('暂无同步批次，可连接 Gmail 后发起同步。'); }
      $('sync-progress').scrollIntoView({ block: 'center' });
    }
  }
  if (chat) openAssistantLink();
}


/** Function: Read the current employee's Gmail and QQ connections. Inputs: Current session. Outputs: A mailbox array.
 * Logic: Accept responses for the current account only and discard stale responses after account changes; show that account's authorized Gmail at the top. Constraints: Never expose credentials to the browser. */
async function loadMailboxes() {
  const account = state.account;
  const mailboxes = await request('mailboxes/');
  if (account !== state.account) return [];
  state.mailboxes = mailboxes;
  const authorized = state.mailboxes.filter(item => item.gmail_authorized);
  const connected = state.mailboxes.filter(item => item.gmail_authorized || (state.runtime?.qq_enabled && item.qq_authorized));
  const primary = authorized[0];
  $('gmail-chip-label').textContent = primary ? primary.address : t('连接 Gmail');
  $('gmail-manage').textContent = primary ? t('管理 Gmail') : t('连接 Gmail');
  $('inbox-scope').textContent = connected.length
    ? t`已连接账号：${connected.map(item => item.address).join('、')} · 客户邮件按公司域名归组。`
    : (state.runtime?.qq_enabled ? t('尚未连接邮箱；连接 Gmail 或 QQ 后显示当前员工同步的客户邮件。') : t('尚未连接 Gmail；连接后显示当前员工同步的客户邮件。'));
  renderGmailAccounts();
  renderQQAccounts(state.mailboxes);
  await refreshReviewBadge();
  return state.mailboxes;
}

/** Function: Render connected Gmail accounts on the mail-settings page. Inputs: state.mailboxes. Outputs: None.
 * Logic: Show synchronization, explicit reauthorization, and removal only for authorized accounts; do not infer revoked authorization from ordinary sync failures. Constraints: Tokens never enter the DOM. */
function renderGmailAccounts() {
  const authorized = state.mailboxes.filter(item => item.gmail_authorized);
  $('gmail-accounts').innerHTML = authorized.length
    ? authorized.map(item => {
        const status = item.sync_state?.status || 'authorization_required';
        const lastSync = item.sync_state?.last_synced_at;
        return h`<article class="gmail-account"><div><strong>${e(item.address)}</strong><p>${e(gmailStates[status] || status)}${lastSync ? t` · 上次完成 ${e(date(lastSync))}` : ''}</p>${item.sync_state?.error ? `<small class="failure">${e(item.sync_state.error)}</small>` : ''}</div><div class="account-actions"><button class="secondary" data-gmail-sync="${e(item.mailbox_id)}" ${['sync_requested', 'sync_running'].includes(status) ? 'disabled' : ''}>${['sync_requested', 'sync_running'].includes(status) ? t('同步中…') : t('同步 Gmail')}</button><button class="text-btn" data-gmail-reconnect="${e(item.mailbox_id)}">${e(emailSettingsLabels.reconnect)}</button><button class="text-btn" data-gmail-disconnect="${e(item.mailbox_id)}">移除授权</button></div></article>`;
      }).join('')
    : h`<div class="gmail-empty"><strong>尚未连接 Google 邮箱</strong><p>${e(emailSettingsLabels.empty)}</p></div>`;
}

/** Function: Open mail settings and read connection status. Inputs: Current browser route and session. Outputs: Asynchronous completion.
 * Logic: Navigate other pages to #gmail for centralized route rendering; when already in settings, refresh mailboxes only.
 * Constraints: Never open an authorization popup, invoke OAuth, create a mailbox, or start synchronization automatically. */
async function openEmailSettings() {
  if (location.hash !== '#gmail') { location.hash = '#gmail'; return; }
  await loadMailboxes();
}

/** Function: Show permission information for explicitly adding or reauthorizing an account. Inputs: address is an optional existing account.
 * Outputs: None. Logic: Display the target account and wait for the user to click Google authorization.
 * Constraints: Do not call the authorization API here; log only the add/reauthorize action type, never the email address; do not interpret ordinary sync errors as invalid tokens. */
function showGmailAuthorization(address = '') {
  console.info('gmail_authorization_dialog_opened', { reason: address ? 'reconnect' : 'add' });
  $('gmail-authorization-account').textContent = address ? emailSettingsLabels.selectAccount + address : '';
  $('gmail-authorization-account').hidden = !address;
  $('gmail-dialog').showModal();
}

/** Function: Start server-side Google OAuth. Inputs: Current employee session. Outputs: Browser navigation.
 * Logic: Record the explicit authorization request, then use the backend-generated authorization URL containing state. Constraints: Logs contain neither URLs nor tokens; the frontend never accesses access tokens. */
async function startGmailAuthorization() {
  console.info('gmail_authorization_requested');
  const result = await request('mailboxes/gmail-authorize/', { method: 'POST' });
  if (!result.authorization_url) throw new Error(t('后端未返回 Google 授权地址。'));
  location.assign(result.authorization_url);
}

/** Function: Poll persisted batch and overall profile status. Inputs: mailboxIds identifies this operation's mailboxes. Outputs: None.
 * Logic: Read complete batch progress and refresh shared tasks after completion; company pagination does not determine completion. Constraints: Errors remain visible; a new operation stops old polling. */
async function pollGmailSync(mailboxIds) {
  const polling = ++state.gmailPolling;
  const tracked = new Set(mailboxIds);
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (polling !== state.gmailPolling) return;
    await loadMailboxes();
    const runIds = state.mailboxes.filter(item => tracked.has(item.mailbox_id)).map(item => item.sync_state?.run_id).filter(Boolean);
    const runs = await Promise.all(runIds.map(id => request(`mailbox-sync-runs/${encodeURIComponent(id)}/`)));
    updateRunProgress(runs);
    await loadList({ showLoading: false });
    await refreshReviewBadge();
    if (runs.length && runs.every(run => !['queued', 'running'].includes(run.status) && run.analysis_pending_count === 0)) {
      const failed = runs.some(run => ['failed', 'partial'].includes(run.status) || run.analysis_failed_count > 0);
      notice(failed ? t('处理已结束，存在失败项，请查看同步进度。') : t('邮件和客户画像已处理完成。'), failed);
      await refreshWorkspace();
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 3000));
  }
  notice(t('任务仍未结束，进度已保存，可稍后刷新查看。'), false);
}

/** Function: Request synchronization of one authorized employee mailbox. Inputs: mailboxId. Outputs: None.
 * Logic: Ask for scope for both Gmail and QQ; reject QQ requests when disabled. The backend queues work for a Worker, and the page polls batch results. Constraints: Cancellation sends no sync request. */
async function requestGmailSync(mailboxId) {
  const mailbox = state.mailboxes.find(item => item.mailbox_id === mailboxId);
  if (mailbox?.qq_authorized && !state.runtime.qq_enabled) throw new Error(t('QQ 邮箱功能暂时停用。'));
  const scope = mailbox?.qq_authorized ? await chooseQQScope(mailbox.address) : await chooseGmailScope(mailbox?.address || '');
  if (!scope) return;
  await request(`mailboxes/${encodeURIComponent(mailboxId)}/request-sync/`, { method: 'POST', data: { sync_options: scope } });
  await loadMailboxes();
  notice(t('同步请求已提交，Agent 正在后台读取和分析邮件。'), false);
  void pollGmailSync([mailboxId]).catch(error => notice(error.message));
}

/** Function: Refresh the current employee's inbox. Inputs: Currently authorized mailboxes. Outputs: None.
 * Logic: Exclude disabled QQ, ask for each mailbox's scope, submit synchronization for enabled mailboxes, and poll; active mailboxes retain their current batch. Constraints: Cancelling any scope prevents new submissions; without authorization, refresh page data only. */
async function refreshInbox() {
  await loadMailboxes();
  const authorized = state.mailboxes.filter(item => item.gmail_authorized || (state.runtime?.qq_enabled && item.qq_authorized));
  if (!authorized.length) {
    await loadList();
    notice(t('尚未连接邮箱，当前只刷新了已有客户数据。'));
    return;
  }
  const submissions = [];
  for (const item of authorized) {
    if (['sync_requested', 'sync_running'].includes(item.sync_state?.status)) continue;
    const scope = item.qq_authorized ? await chooseQQScope(item.address) : await chooseGmailScope(item.address);
    if (!scope) return;
    submissions.push({ item, scope });
  }
  await Promise.all(submissions.map(({ item, scope }) => request(`mailboxes/${encodeURIComponent(item.mailbox_id)}/request-sync/`, { method: 'POST', data: { sync_options: scope } })));
  await loadMailboxes();
  notice(t('正在同步邮箱并运行客户分析，完成后页面会自动刷新。'), false);
  void pollGmailSync(authorized.map(item => item.mailbox_id)).catch(error => notice(error.message));
}

/** Function: Remove an employee's Gmail authorization. Inputs: mailboxId. Outputs: None.
 * Logic: Delete only backend-stored authorization and retain historical customer data. Constraints: Other employees' mailboxes cannot be changed. */
async function disconnectGmail(mailboxId) {
  await request(`mailboxes/${encodeURIComponent(mailboxId)}/gmail-authorization/`, { method: 'DELETE' });
  await loadMailboxes();
  notice(t('Gmail 授权已移除，历史邮件和客户分析仍然保留。'), false);
}

/** Function: Open the simulated incoming-mail form and read available business mailboxes. Inputs: Current session. Outputs: None.
 * Logic: Explicitly create a simulation-only business mailbox when none exists.
 * Constraints: Never request Gmail credentials or create a real OAuth connection. */
async function openMail() {
  let mailboxes = await request('mailboxes/');
  if (!mailboxes.length) mailboxes = [await request('mailboxes/', { method: 'POST', data: { address: 'sales@salesmate.example' } })];
  $('mail-form').elements.mailbox_id.innerHTML = mailboxes.map(item => `<option value="${e(item.mailbox_id)}">${e(item.address)}</option>`).join('');
  $('mail-dialog').showModal();
}

/** Function: Open the customer registration form. Inputs: state.detail. Outputs: None.
 * Logic: Populate authoritative CRM fields without using Agent inferences for employee counts.
 * Constraints: Submit the observed revision for optimistic locking. */
function openRegister() {
  const form = $('register-form'), data = state.detail;
  form.elements.company_name.value = data.company_name || '';
  form.elements.industry_from_crm.value = data.context.customer.industry_from_crm || 'unknown';
  form.elements.employee_count.value = data.context.customer.employee_count ?? '';
  form.elements.employee_count_source.value = data.context.customer.employee_count_source || '';
  $('register-dialog').showModal();
}

/** Function: Switch between login and account-registration forms. Inputs: signup selects the registration form. Outputs: None.
 * Logic: Show one form, clear passwords in both, and focus the target username field.
 * Constraints: No API requests, changes to established sessions, or username clearing. */
function showAuthForm(signup) {
  $('login-form').hidden = signup;
  $('signup-form').hidden = !signup;
  for (const form of [$('login-form'), $('signup-form')]) {
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
  }
  $('notice').hidden = true;
  $(signup ? 'signup-form' : 'login-form').elements.username.focus();
}

/** Function: Submit ordinary account registration and enter the workspace. Inputs: event is the registration form submission. Outputs: None.
 * Logic: Compare both passwords first and send only username/password to the backend; on success clear the form, return home, and read the new session.
 * Constraints: No email/phone verification or automatic retries; preserve failed input for correction and never save passwords in browser storage. */
async function signupSubmit(event) {
  event.preventDefault();
  const form = event.target;
  if (form.elements.password.value !== form.elements.password_confirmation.value) {
    notice(t('两次输入的密码不一致，请重新确认。'));
    form.elements.password_confirmation.focus();
    return;
  }
  await busy(event.submitter, async () => {
    await request('accounts/register/', { method: 'POST', data: {
      username: form.elements.username.value, password: form.elements.password.value,
    } });
    form.reset();
    $('login-form').reset();
    history.replaceState({}, '', location.pathname + '#home');
    await initialize();
    notice(t('注册成功，你的工作空间已准备好。'), false);
  });
}

/** Function: Submit session login. Inputs: event is the form event. Outputs: None.
 * Logic: Clear password fields and initialize the workspace after API success.
 * Constraints: No automatic login retries or password storage. */
async function loginSubmit(event) {
  event.preventDefault();
  await busy(event.submitter, async () => {
    await request('session/', { method: 'POST', data: Object.fromEntries(new FormData(event.target)) });
    event.target.reset();
    await initialize();
  });
}

/** Function: Submit one simulated email. Inputs: event is the form event. Outputs: None.
 * Logic: Close the dialog and navigate to the company after saving; retain input on failure.
 * Constraints: Do not retry, which could create a second independent simulated email. */
async function mailSubmit(event) {
  event.preventDefault();
  await busy(event.submitter, async () => {
    const result = await request('demo/email/', { method: 'POST', data: Object.fromEntries(new FormData(event.target)) });
    $('mail-dialog').close(); event.target.reset();
    const hash = `#company/${result[0].company_id}`;
    if (location.hash === hash) await loadDetail(result[0].company_id, false); else location.hash = hash;
    notice(t('模拟邮件已保存，来源标记为合成样例。'), false);
  });
}

/** Function: Submit a customer profile. Inputs: event is the form event. Outputs: None.
 * Logic: Map an empty employee count to null and use the current revision to prevent overwriting concurrent changes.
 * Constraints: Preserve the form and show version conflicts; the user refreshes and confirms manually. */
async function registerSubmit(event) {
  event.preventDefault();
  await busy(event.submitter, async () => {
    const data = Object.fromEntries(new FormData(event.target));
    data.employee_count = data.employee_count === '' ? null : Number(data.employee_count);
    data.employee_count_source = data.employee_count_source || null;
    await request(`companies/${state.detail.company_id}/register/`, { method: 'POST', data, version: state.detail.revision });
    $('register-dialog').close();
    await loadDetail(state.detail.company_id, false);
    notice(t('客户档案已保存。'), false);
  });
}

/** Function: Initialize session and service capabilities. Inputs: Current browser session. Outputs: None.
 * Logic: Cancel old detail reads before checking the session; on account changes immediately clear mailbox markers, search, chat, and customer content. New users enter persisted onboarding. Authenticated users load capabilities, hide disabled entries, and mount the workspace; anonymous users clear the widget and restore login.
 * Constraints: Keep failures visible and never show false synchronization success when Gmail is disconnected. */
async function initialize() {
  closeEvidence();
  detailObserver.stop();
  ++state.navigation;
  const session = await request('session/');
  $('login-screen').hidden = session.authenticated;
  $('workspace').hidden = !session.authenticated;
  $('logout').hidden = session.debug_auto_login;
  if (!session.authenticated) { state.replyDrafts.clear(); updateRunProgress([]); state.account = null; enableAssistant(false); showAuthForm(false); return; }
  if (state.account !== session.username) {
    state.mailboxes = [];
    $('gmail-chip-label').textContent = t('连接 Gmail');
    state.replyDrafts.clear(); updateRunProgress([]);
    enableAssistant(false);
    $('filters').reset();
    $('company-list').textContent = '';
    $('detail-content').textContent = '';
    state.detail = null; state.page = 1; state.listSignature = null;
    state.account = session.username;
    setWorkspaceContext(null);
  }
  if (session.onboarding_required) { location.replace('/settings/company/?onboarding=1'); return; }
  $('username').textContent = session.username;
  mountWorkspace();
  void refreshWorkspace();
  state.runtime = await request('demo/runtime/');
  for (const id of ['qq-manage', 'qq-manage-top']) $(id).hidden = !state.runtime.qq_enabled;
  await loadMailboxes();
  const isRules = state.runtime.provider === 'rules';
  $('seed').hidden = !isRules; $('compose').hidden = !isRules;
  $('workspace-footnote').textContent = isRules
    ? t('当前员工数据空间 · 支持 Gmail 授权与离线规则演示 · 统计时区：') + state.runtime.timezone
    : t('当前员工数据空间 · Gmail 同步与 Agent 分析独立执行 · 统计时区：') + state.runtime.timezone;
  await navigate();
  const trackedMailboxes = state.mailboxes.filter(item => item.sync_state?.run_id && (!item.qq_authorized || state.runtime.qq_enabled)).map(item => item.mailbox_id);
  if (trackedMailboxes.length) void pollGmailSync(trackedMailboxes).catch(error => notice(error.message));
  const callback = new URLSearchParams(location.search);
  if (callback.get('gmail') === 'authorized') {
    const address = callback.get('address') || '';
    notice(t`Gmail ${address} 已授权，请选择本次同步范围。`, false);
    history.replaceState({}, '', location.pathname + location.hash);
    const mailbox = state.mailboxes.find(item => item.address === address);
    if (mailbox && !['sync_requested', 'sync_running'].includes(mailbox.sync_state?.status)) void requestGmailSync(mailbox.mailbox_id).catch(error => notice(error.message));
  } else if (callback.get('gmail') === 'error') {
    const reason = callback.get('reason');
    const oauthErrors = {
      access_denied: t('Google 拒绝了授权。请确认当前账号已加入 OAuth 测试用户，并在授权页允许 Gmail 只读权限。'),
      InvalidState: t('授权会话状态已失效。请始终使用 127.0.0.1 打开页面，并重新发起授权。'),
      InvalidGrantError: t('Google 授权码无效或已过期。请重新发起一次授权，不要重复打开旧回调地址。'),
      InvalidClientError: t('Google OAuth 客户端 ID 或密钥不匹配。请检查根目录 .env 中的 Web application 凭据。'),
      HttpError: t('Gmail API 调用失败。请确认项目已启用 Gmail API，且当前账号拥有授权权限。'),
    };
    notice(oauthErrors[reason] || t`Google 邮箱授权失败${reason ? `（${reason}）` : ''}，请查看后端终端中的完整回调错误。`);
    history.replaceState({}, '', location.pathname + location.hash);
  }
}

/** Function: Register static-form and dynamic-content events. Inputs: Existing DOM. Outputs: None.
 * Logic: Bind registration and business forms; logout clears the customer route and account changes clear navigation context. Review lives in mail settings, whose navigation is separate from explicit authorization. The widget retains its conversation independently; recovery resumes read-only observation; pagehide cancels stale responses.
 * Constraints: Bind once; never execute code through eval or string-based inline event handlers. */
function bindEvents() {
  initProcessingUI(async () => { await loadList(); await refreshWorkspace(); }, mailboxId => pollGmailSync([mailboxId]));
  initQQ({ refresh: loadMailboxes, sync: requestGmailSync, view: openMailboxEmails, track: mailboxId => pollGmailSync([mailboxId]) });
  $('detail-live-resume').onclick = event => {
    const match = location.hash.match(/^#company\/([\w-]+)$/);
    if (match) busy(event.currentTarget, () => loadDetail(match[1], false));
  };
  $('login-form').addEventListener('submit', loginSubmit);
  $('signup-form').addEventListener('submit', signupSubmit);
  $('show-signup').onclick = () => showAuthForm(true);
  $('show-login').onclick = () => showAuthForm(false);
  $('mail-form').addEventListener('submit', mailSubmit);
  $('register-form').addEventListener('submit', registerSubmit);
  $('logout').onclick = event => busy(event.currentTarget, async () => { await request('session/', { method: 'DELETE' }); state.detail = null; history.replaceState({}, '', location.pathname + '#home'); await initialize(); });
  $('filters').onsubmit = event => { event.preventDefault(); state.page = 1; busy(event.submitter, loadList); };
  $('filters').onreset = () => { state.page = 1; setTimeout(() => busy(null, loadList), 0); };
  $('refresh').onclick = event => busy(event.currentTarget, refreshInbox);
  $('previous').onclick = event => { state.page -= 1; busy(event.currentTarget, loadList); };
  $('next').onclick = event => { state.page += 1; busy(event.currentTarget, loadList); };
  $('compose').onclick = event => busy(event.currentTarget, openMail);
  $('seed').onclick = event => busy(event.currentTarget, async () => { const result = await request('demo/seed/', { method: 'POST' }); await loadList(); notice(t`已导入 ${result.created_emails} 封合成样例。重复导入不会新增已有邮件。`, false); });
  $('gmail-manage').onclick = event => busy(event.currentTarget, openEmailSettings);
  $('gmail-manage-top').onclick = () => openEmailSettings().catch(error => notice(error.message));
  $('email-settings-description').textContent = emailSettingsLabels.description;
  $('gmail-add').textContent = emailSettingsLabels.add;
  $('gmail-progress-link').textContent = emailSettingsLabels.progress;
  $('gmail-add').onclick = () => showGmailAuthorization();
  $('gmail-settings-refresh').onclick = event => busy(event.currentTarget, loadMailboxes);
  $('gmail-authorize').onclick = event => busy(event.currentTarget, startGmailAuthorization);
  $('gmail-accounts').onclick = event => {
    const sync = event.target.closest('[data-gmail-sync]');
    if (sync) busy(sync, () => requestGmailSync(sync.dataset.gmailSync));
    const reconnect = event.target.closest('[data-gmail-reconnect]');
    if (reconnect) {
      const mailbox = state.mailboxes.find(item => item.mailbox_id === reconnect.dataset.gmailReconnect);
      if (mailbox) showGmailAuthorization(mailbox.address);
    }
    const disconnect = event.target.closest('[data-gmail-disconnect]');
    if (disconnect) busy(disconnect, () => disconnectGmail(disconnect.dataset.gmailDisconnect));
  };
  document.querySelectorAll('.close-dialog').forEach(button => { button.onclick = () => button.closest('dialog').close(); });
  $('detail-content').addEventListener('click', event => {
    const direction = event.target.closest('[data-direction]');
    if (direction) { state.direction = direction.dataset.direction; document.querySelectorAll('[data-direction]').forEach(button => button.classList.toggle('selected', button === direction)); renderEmails(); }

  });
  window.addEventListener('pagehide', () => { closeEvidence(); detailObserver.stop(); ++state.navigation; });
  window.addEventListener('pageshow', event => {
    const match = location.hash.match(/^#company\/([\w-]+)$/);
    if (event.persisted && match && !$('workspace').hidden) busy(null, () => loadDetail(match[1], false));
  });
  window.addEventListener('hashchange', () => { if (!$('workspace').hidden) busy(null, navigate); });
}

bindEvents();
initialize().catch(error => notice(error.message));
