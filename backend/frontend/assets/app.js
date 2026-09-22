/**
 * 职责：实现员工 Gmail/QQ 收件箱、授权管理和客户工作区的原生浏览器交互。
 * 实现：注册/登录、哈希路由、紧凑邮件组卡片和单客户持续读取；客户详情默认只读，分析与事实升级分别显式提交；共享悬浮入口保留当前会话，邮箱设置使用独立页面，只有显式连接操作打开授权弹窗；邮箱同步每次询问范围，QQ 能力控制入口，旧响应隔离并保留独立草稿。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：侧栏优先级入口移除后更新导航缓存；共享导航使用移除实验入口后的缓存版本；导航依赖更新至商机优先级支持版本；聊天 Markdown 模块依赖使用统一缓存版本；0919 界面及共享语言资源统一缓存版本；导航资源使用账号清空版本以更新缓存；共享语言/API 资源随需求界面统一版本；workspace.js 共享主导航与底部 Profile；assistant-widget.js 管理悬浮聊天入口；api.js 通信，qq.js 管理 QQ，gmail-scope.js 管理 Gmail 范围，运行时 qq_enabled 控制入口、同步及轮询，mail-source.js 标注来源，assistant.js 管理聊天与草稿，notice.js 管理提示。
 * 目录：$、date、companyName、pill、notice、busy、renderStats、renderRow、loadList、
 * renderDimension、renderDetail、renderEmails、revealSource、setDetailLiveStatus、loadDetail、upgradeFacts、navigate、loadMailboxes、renderGmailAccounts、openEmailSettings、showGmailAuthorization、
 * startGmailAuthorization、pollGmailSync、requestGmailSync、refreshInbox、
 * disconnectGmail、openMail、openRegister、showAuthForm、signupSubmit、loginSubmit、
 * mailSubmit、registerSubmit、initialize、bindEvents。
 * 变量索引：$ 为元素定位函数；state.replyDrafts 保存按客户隔离的本页回复草稿；state 保存分页、账号身份、会话能力、当前详情、方向和列表响应签名；
 * closeEvidence 清理来源预览；detailObserver 管理当前客户的只读轮询与失败暂停；signals、sizes、dimensions、gmailStates 为后端枚举的当前语言展示映射；assistant 管理聊天，notices 管理页面提示生命周期；extractionLabels 保存事实升级及样例状态文案；emailSettingsLabels 保存邮箱设置的中英文静态文案。
 */
import { mountReplyComposer } from './channel-reply.js?v=20260921-product';
import { mountEvidencePreview } from './evidence-preview.js?v=20260921-product';
import { chooseGmailScope } from './gmail-scope.js?v=20260921-product';
import { t, h, locale, language } from './i18n.js?v=20260921-product';

import { initProcessingUI, updateRunProgress, refreshReviewBadge, openMailboxEmails } from './processing.js?v=20260921-product';
import { mailSourceLabel } from './mail-source.js?v=20260921-product';
import { initQQ, renderQQAccounts, chooseQQScope } from './qq.js?v=20260921-product';
import { mountWorkspace, setWorkspaceContext, refreshWorkspace, businessHref } from './workspace.js?v=20260922-sidebar';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { DetailObserver, patchHTML, preserveReading } from './live-detail.js?v=20260921-product';
import { getAssistant, enableAssistant, openAssistantLink } from './assistant-widget.js?v=20260921-markdown';
import { Notice } from './notice.js?v=20260921-product';

const assistant = getAssistant();
const closeEvidence = mountEvidencePreview(document.getElementById('detail-content'), () => state.detail, revealSource);

/** 功能：按 ID 定位页面元素。输入：id。输出：Element 或 null。逻辑：原生 DOM 查询。约束：调用方使用已声明 ID。 */
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


/** 功能：按配置时区显示时间。输入：value 为 ISO 时间。输出：日期时间字符串。
 * 逻辑：Intl 使用当前界面语言和后端声明时区。约束：空时间保持未知，不猜测时间。 */
function date(value) {
  return value ? new Intl.DateTimeFormat(locale, { timeZone: state.runtime?.timezone || 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : t('尚无记录');
}

/** 功能：生成公司显示名称。输入：row 公司投影。输出：已确定姓名或域名/联系人标识。
 * 逻辑：缺少公司名时使用已有身份信息。约束：不推断不存在的公司名。 */
function companyName(row) {
  return row.company_name || row.domains[0] || row.contacts[0]?.contact_email || t('待确认公司');
}

/** 功能：生成安全的标签 HTML。输入：text、kind。输出：span HTML。
 * 逻辑：两项都转义。约束：kind 仅用于 CSS 类，不执行代码。 */
function pill(text, kind = '') { return `<span class="pill ${e(kind)}">${e(text)}</span>`; }

/** 功能：展示操作结果或错误。输入：message、error 是否失败。输出：无。
 * 逻辑：交给 Notice 显示纯文本，按类型自动关闭，阅读时暂停并允许手动关闭。
 * 约束：不插入服务器 HTML，不改变业务状态或自动重试失败请求。 */
function notice(message, error = true) {
  notices.show(message, error);
}

/** 功能：在异步操作期间禁用触发按钮并展示失败。输入：button、operation 异步函数。输出：无。
 * 逻辑：finally 恢复状态，失败可见。约束：不重复执行、不隐式重试。 */
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

/** 功能：显示公司及今日新邮件概览。输入：stats。输出：无。
 * 逻辑：首页仅展示邮件客户与今日邮件，Channels 保留待建档统计；展示后端口径与时区。约束：后端总数不受当前分页替代。 */
function renderStats(stats) {
  $('stats').innerHTML = [[t('邮件客户'), stats.companies, t('已保存业务邮件的客户'), '▦'], [t('待建档客户'), stats.unregistered, t('补齐档案，积累客户上下文'), '♧'], [t('今日新邮件'), stats.new_emails_today, t`统计时区 ${state.runtime.timezone}`, '✉']].filter((_, index) => index !== 1 || (location.hash && location.hash !== '#home')).map(([label, value, note, icon]) => `<article class="stat-card"><div><span class="stat-label">${e(label)}</span><strong>${e(value)}</strong><p>${e(note)}</p></div><span class="stat-icon">${icon}</span></article>`).join('');
}

/** 功能：生成公司邮件组卡片。输入：row 为后端的公司、评分与邮件来源投影。输出：转义后的链接 HTML。
 * 逻辑：首行集中展示信号、行业与规模，次行展示已有域名和联系人，右侧保留日期与优先级。
 * 约束：不计算或修改评分；未知信息仍明确标注，演示来源与失败状态保持可见。 */
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

/** 功能：加载列表及分页状态。输入：表单、state.page 和是否显示加载占位。输出：列表响应。
 * 逻辑：捕获账号身份拒绝旧账号响应；以查询参数调用后端；后台轮询时保留现有列表，避免每三秒清空并重建造成闪烁。约束：普通加载失败时不保留看似最新的旧列表。 */
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

/** 功能：生成含来源预览的分析维度。输入：key、value。输出：安全 HTML。
 * 逻辑：事实和推断分开展示，缺失项单独标记。约束：来源通过 data-ref 悬停、聚焦及点击预览，显式操作再定位原文，不执行邮件内容。 */
function renderDimension(key, value) {
  return `<article class="dimension" data-live-key="${e(key)}"><h4>${e(dimensions[key] || key)}</h4>${value.facts.map(item => `<p>${e(item.text)} ${item.source_refs.map(ref => h`<button class="source-ref" data-ref="${e(ref)}" aria-haspopup="dialog" aria-expanded="false" aria-label="预览引用来源">↗ 依据</button>`).join('')}</p>`).join('')}${value.inferences.map(item => h`<p class="inference">${pill(t('推断'), 'subtle')} ${e(item.text)}<small>依据：${e(item.basis)} · 置信等级 ${e(item.confidence)}</small></p>`).join('')}${!value.facts.length && !value.inferences.length ? h('<p class="muted">尚无充分依据</p>') : ''}${value.missing_fields.length ? h`<div class="missing">待补充：${value.missing_fields.map(e).join('、')}</div>` : ''}</article>`;
}

/** 功能：渲染邮件会话、画像分析与助手的客户详情。输入：data 为完整客户响应。输出：无。
 * 逻辑：展示事实升级状态和显式入口，空评分不推断为资料不足；比较完整响应（不只 revision）；复用未变化节点并校正阅读位置，客户身份变化才重建共享上下文，辅助统计收纳在中栏并保留展开状态，回复草稿按客户隔离并只在明确操作时交给助手；聊天始终保留工作空间会话。
 * 约束：独立助手和编辑表单不在局部更新范围，未保存草稿、焦点和邮件方向保留；不等待整批结束。 */
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

/** 功能：按方向显示原始邮件。输入：state.detail、state.direction。输出：无。
 * 逻辑：按收发方向排列气泡，显示合成标记、来源、抽取失败和逐字正文。约束：所有正文转义，禁止执行 HTML 或指令。 */
function renderEmails() {
  const emails = state.detail.context.emails.filter(item => state.direction === 'all' || item.direction === state.direction);
  patchHTML($('emails'), emails.map(item => { const sender = item.from || item.mailbox_address || t('未知发件人'); return `<article class="email-card ${item.direction === 'outbound' ? 'email-outbound' : 'email-inbound'}" tabindex="-1" data-email-ref="${e(item.dedupe_key)}"><div class="email-top"><span class="avatar tiny">${e(sender.slice(0, 1).toUpperCase())}</span><div><strong>${e(sender)}</strong><small>${e(date(item.sent_at || item.received_at))} · ${{ inbound: t('收件'), outbound: t('发件'), unknown: t('方向未知') }[item.direction] || t('方向未知')}</small></div></div><h4>${e(item.subject)}</h4><div class="email-source">${pill(mailSourceLabel(item.source), 'subtle')}${item.synthetic_batch ? pill(extractionLabels.synthetic, 'warning') : ''}${item.extract_status !== 'completed' ? pill(t('未解析：') + item.extract_status, 'warning') : ''}</div><pre>${e(item.body_text)}</pre>${item.extract_error ? `<p class="failure">${e(item.extract_error)}</p>` : ''}</article>`; }).join('') || h('<div class="empty">没有此方向的邮件</div>'));
}

/** 功能：在会话中定位用户明确选择的邮件来源。输入：ref 为当前详情内的原始引用键。输出：无。
 * 逻辑：恢复全部邮件筛选，移除旧高亮并聚焦匹配邮件；缺失时保持明确提示。
 * 约束：只改变前端阅读位置，不请求或重试分析，不假设非邮件引用一定属于 CRM。 */
function revealSource(ref) {
  state.direction = 'all';
  document.querySelectorAll('[data-direction]').forEach(button => button.classList.toggle('selected', button.dataset.direction === 'all'));
  renderEmails();
  const email = [...document.querySelectorAll('[data-email-ref]')].find(element => element.dataset.emailRef === ref);
  document.querySelectorAll('.email-card.highlight').forEach(node => node.classList.remove('highlight'));
  if (email) { email.scrollIntoView({ behavior: 'smooth', block: 'center' }); email.classList.add('highlight'); email.focus({ preventScroll: true }); }
  else notice(t('当前详情中没有这条引用的邮件原文。'), false);
}

/** 功能：显示详情自动更新状态。输入：message、paused 是否需要手动恢复。输出：无。
 * 逻辑：状态区独立于详情渲染；只有失败才显示恢复按钮。约束：不把网络失败显示为分析失败或成功。 */
function setDetailLiveStatus(message, paused = false) {
  $('detail-live-message').textContent = message;
  $('detail-live-resume').hidden = !paused;
}

/** 功能：读取客户详情，只有明确点击才请求分析。输入：id、trigger 默认为 false。
 * 输出：当前读取或分析 Promise。逻辑：先读取并渲染详情、启动只读观察，再执行显式分析；导航代次隔离迟到响应。
 * 约束：分析失败仍保留已读取详情及观察，错误向调用方传播；读取失败暂停，不自动升级或重试。 */
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

/** 功能：显式请求升级当前客户的历史邮件事实。输入：data 为当前详情及 revision。
 * 输出：请求及刷新 Promise。逻辑：携带读取版本提交空正文，成功后只读刷新并保留自动观察。
 * 约束：不会重新拉邮箱；请求失败直接显示，不自动重试或修改旧事实。 */
async function upgradeFacts(data) {
  await request(`companies/${encodeURIComponent(data.company_id)}/extraction-upgrade/`, { method: 'POST', data: {}, version: data.revision });
  if (location.hash !== `#company/${data.company_id}`) return;
  await loadDetail(data.company_id);
  notice(extractionLabels.queued, false);
}

/** 功能：按哈希切换列表与详情，旧聊天链接打开浮窗。输入：location.hash 隐式状态。输出：无。
 * 逻辑：切换时停止详情观察并保留工作空间聊天；邮箱设置显示独立账号管理页面并标记 Profile 当前入口；浮窗保留会话，旧聊天链接在工作台上打开，不触发分析。
 * 约束：所有导航均只读，分析和事实升级只在独立按钮点击时提交。 */
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


/** 功能：读取当前员工 Gmail 和 QQ 连接。输入：当前会话。输出：邮箱数组。
 * 逻辑：只使用当前账号邮箱响应，账号切换后丢弃旧响应；顶部展示该账号的已授权 Gmail。约束：不向浏览器提供凭证。 */
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

/** 功能：渲染邮箱设置页面中的已连接 Gmail 账号。输入：state.mailboxes。输出：无。
 * 逻辑：只给已授权账号显示同步、显式重新授权和移除操作，不按普通同步失败猜测授权失效。约束：令牌不进入 DOM。 */
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

/** 功能：打开邮箱设置并读取连接状态。输入：浏览器当前路由和会话。输出：异步完成。
 * 逻辑：其他页面先切换到 #gmail，由路由统一渲染；已在设置时仅刷新邮箱。
 * 约束：不打开授权弹窗，不调用 OAuth，不创建邮箱或自动发起同步。 */
async function openEmailSettings() {
  if (location.hash !== '#gmail') { location.hash = '#gmail'; return; }
  await loadMailboxes();
}

/** 功能：展示显式添加或重新授权的权限说明。输入：address 为可选的现有账号。
 * 输出：无。逻辑：显示目标账号提示，等待用户点击 Google 授权按钮。
 * 约束：本函数不调用授权接口；日志仅记录添加或重新授权类型，不记录邮箱；不把普通同步错误判定为令牌失效。 */
function showGmailAuthorization(address = '') {
  console.info('gmail_authorization_dialog_opened', { reason: address ? 'reconnect' : 'add' });
  $('gmail-authorization-account').textContent = address ? emailSettingsLabels.selectAccount + address : '';
  $('gmail-authorization-account').hidden = !address;
  $('gmail-dialog').showModal();
}

/** 功能：开始服务端 Google OAuth。输入：当前员工会话。输出：浏览器跳转。
 * 逻辑：记录显式授权请求后，由后端生成带 state 的授权地址。约束：日志不记录地址或令牌，前端不接触 access token。 */
async function startGmailAuthorization() {
  console.info('gmail_authorization_requested');
  const result = await request('mailboxes/gmail-authorize/', { method: 'POST' });
  if (!result.authorization_url) throw new Error(t('后端未返回 Google 授权地址。'));
  location.assign(result.authorization_url);
}

/** 功能：轮询持久批次及整体画像状态。输入：mailboxIds 为本次邮箱集合。输出：无。
 * 逻辑：读取完整批次进度，结束后刷新共享待办；公司分页不参与完成判断。约束：错误可见，新操作停止旧轮询。 */
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

/** 功能：请求同步一个已授权员工邮箱。输入：mailboxId。输出：无。
 * 逻辑：Gmail/QQ 均先询问范围，QQ 关闭时拒绝请求，后端排队后由 Worker 执行，页面轮询批次结果。约束：取消不发同步请求。 */
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

/** 功能：刷新当前员工收件箱。输入：当前已授权邮箱。输出：无。
 * 逻辑：先排除停用的 QQ，再逐个询问邮箱范围，提交启用邮箱的同步并轮询；活动邮箱沿用当前批次。约束：取消任一范围不提交新请求，未授权时只刷新页面数据。 */
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

/** 功能：移除一个员工 Gmail 授权。输入：mailboxId。输出：无。
 * 逻辑：仅删除后端保存的授权，保留历史客户资料。约束：不能操作其他员工邮箱。 */
async function disconnectGmail(mailboxId) {
  await request(`mailboxes/${encodeURIComponent(mailboxId)}/gmail-authorization/`, { method: 'DELETE' });
  await loadMailboxes();
  notice(t('Gmail 授权已移除，历史邮件和客户分析仍然保留。'), false);
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

/** 功能：切换登录与账号注册表单。输入：signup 为是否显示注册表单。输出：无。
 * 逻辑：只展示一种表单，清除两个表单的密码并聚焦目标用户名。
 * 约束：不发出 API 请求，不修改已经建立的会话，不清除用户名。 */
function showAuthForm(signup) {
  $('login-form').hidden = signup;
  $('signup-form').hidden = !signup;
  for (const form of [$('login-form'), $('signup-form')]) {
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
  }
  $('notice').hidden = true;
  $(signup ? 'signup-form' : 'login-form').elements.username.focus();
}

/** 功能：提交普通账号注册并进入工作台。输入：event 为注册表单提交事件。输出：无。
 * 逻辑：先比较两次密码，只向后端发送用户名和密码；成功后清除表单、回到首页并读取新会话。
 * 约束：无邮箱或手机验证，无自动重试；失败保留输入以便修改，不保存密码到浏览器存储。 */
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
    notice(t('模拟邮件已保存，来源标记为合成样例。'), false);
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
    notice(t('客户档案已保存。'), false);
  });
}

/** 功能：初始化会话与服务能力。输入：当前浏览器会话。输出：无。
 * 逻辑：先取消旧详情读取，再核验会话；账号变化时立即清空邮箱标记、搜索、聊天和客户内容，新注册用户进入持久化引导；已登录读取能力开关、隐藏停用入口并挂载工作台，匿名清理浮窗并恢复登录表单。
 * 约束：失败保持可见，未连接 Gmail 不展示假同步成功。 */
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

/** 功能：注册静态表单与动态内容事件。输入：现有 DOM。输出：无。
 * 逻辑：绑定账号注册和业务表单；退出清理客户路由，账号切换清理客户导航上下文；复核入口位于邮箱设置，邮箱设置导航与显式授权按钮分离；浮窗独立保留当前会话，恢复按钮只读观察，pagehide 取消旧响应。
 * 约束：只绑定一次，不通过 eval 或字符串内联事件执行代码。 */
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
