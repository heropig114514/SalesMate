/**
 * 职责：为邮件与业务页面提供共享导航、客户上下文和真实待办概览。
 * 实现：URL 保存客户身份；所有概览来自授权 GET，独立失败显示未知，链接不提交业务操作。
 * 关联：app.js 与 business.js 调用；workspace.css 提供统一外壳；复核及交易沿用原接口。
 * 目录：businessHref、renderWorkspaceNav、mountWorkspace、setWorkspaceContext、refreshWorkspace。
 * 变量索引：groups 为导航目录；globalResources 为不传递客户的全局资源；context 为当前客户；activePage 为当前页面；refreshSequence 防止旧响应覆盖。
 */
import { request, escapeHtml as e } from './api.js';

const groups = [
  ['销售业务', [['opportunities', '商机'], ['quotes', '报价'], ['orders', '订单'], ['tickets', '工单'], ['products', '产品']]],
  ['沟通与资料', [['actions', '待确认动作'], ['conversations', '会话'], ['messages', '消息'], ['drafts', '草稿'], ['files', '附件']]],
  ['设置与协作', [['connections', '外部连接'], ['contact-profiles', '联系人资料'], ['aliases', '归组规则'], ['teams', '团队'], ['memberships', '团队成员'], ['grants', '客户授权'], ['audit', '操作审计']]],
];
const globalResources = new Set(['products', 'messages', 'connections', 'contact-profiles', 'teams', 'memberships', 'grants']);
let context = null, activePage = 'home', refreshSequence = 0;

/** 功能：生成保留客户的业务路由。输入：resource、company、extra 查询项。输出：同源 URL。
 * 逻辑：URLSearchParams 编码身份，hash 仅标识资源。约束：导航不授权访问，不推断客户。 */
export function businessHref(resource, company = context?.id, extra = {}) {
  const query = new URLSearchParams(extra);
  if (company) query.set('company', company);
  return `/business/${query.size ? '?' + query : ''}#${encodeURIComponent(resource)}`;
}

/** 功能：重建共享导航。输入：模块中的 activePage/context。输出：无。
 * 逻辑：主入口常显，次级资源分组收起，保留用户展开状态和当前资源。约束：客户仅传递到业务相关链接。 */
function renderWorkspaceNav() {
  const nav = document.getElementById('workspace-nav');
  const open = new Set([...nav.querySelectorAll('details[open]')].map(node => node.dataset.group));
  const link = (key, title, href) => `<a href="${e(href)}" ${activePage === key ? 'aria-current="page"' : ''}>${e(title)}</a>`;
  nav.innerHTML = `<p class="workspace-nav-label">我的工作空间</p>${link('home', '工作台', '/#home')}${link('directory', '客户', businessHref('directory'))}${link('inbox', '邮件与分析', context ? '/#company/' + encodeURIComponent(context.id) : '/#inbox')}${link('follow-ups', '跟进', businessHref('follow-ups'))}${link('notifications', '通知', businessHref('notifications', null))}${groups.map(([title, items]) => `<details data-group="${e(title)}" ${open.has(title) || items.some(([key]) => key === activePage) ? 'open' : ''}><summary>${e(title)}</summary>${items.map(([key, name]) => link(key, name, businessHref(key, globalResources.has(key) ? null : context?.id))).join('')}</details>`).join('')}<a href="/#gmail">邮箱连接</a>`;
}

/** 功能：挂载同一导航外壳。输入：active 为当前页。输出：无。
 * 逻辑：替换共享导航；重复点击同一复核/连接路由仍打开界面。约束：登录后调用，不提交请求。 */
export function mountWorkspace(active = 'home') {
  activePage = active;
  renderWorkspaceNav();
  for (const id of ['workspace-nav', 'workspace-status', 'workspace-tasks']) {
    const node = document.getElementById(id);
    if (node) node.onclick = event => {
      const link = event.target.closest('a');
      if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = new URL(link.href);
      if (url.pathname === location.pathname && url.hash === location.hash && ['#reviews', '#gmail', '#processing'].includes(url.hash)) {
        event.preventDefault();
        window.dispatchEvent(new HashChangeEvent('hashchange'));
      }
    };
  }
}

/** 功能：同步客户上下文和跨模块操作。输入：company 为 id/name 对象或 null，active 为页面。
 * 输出：无。逻辑：生成当前客户的档案、邮件、报价订单跟进入口；清除入口明确返回全量目录。
 * 约束：不把客户保存到 localStorage，不通过导航修改业务数据。 */
export function setWorkspaceContext(company, active = activePage) {
  context = company;
  activePage = active;
  renderWorkspaceNav();
  const panel = document.getElementById('workspace-context');
  panel.hidden = !company;
  if (!company) { panel.innerHTML = ''; return; }
  panel.innerHTML = `<div><small>当前客户</small><strong>${e(company.name)}</strong></div><nav aria-label="当前客户工作区"><a href="${e(businessHref('directory'))}">客户档案</a><a href="/#company/${encodeURIComponent(company.id)}">邮件与分析</a><a href="${e(businessHref('quotes'))}">报价</a><a href="${e(businessHref('orders'))}">订单</a><a href="${e(businessHref('follow-ups'))}">跟进</a><a href="${e(businessHref('actions'))}">沟通动作</a></nav><a class="context-clear" href="/business/#directory">全部客户 ↗</a>`;
}

/** 功能：更新跨页待办和首页摘要。输入：无，读取当前登录身份。输出：无。
 * 逻辑：独立 GET 并行读取复核、概览、待确认动作及邮箱；使用服务器总数，邮箱仅统计最新批次。
 * 约束：失败显示“暂不可用”与诊断，不以零代替；不自动同步、不确认动作、不改变业务状态。 */
export async function refreshWorkspace() {
  const sequence = ++refreshSequence;
  const sources = await Promise.allSettled([
    request('email-reviews/?status=pending&page=1'), request('sales/overview/'),
    request('sales/records/actions/?status=pending_confirmation&page_size=1'), request('mailboxes/'),
  ]);
  if (sequence !== refreshSequence) return;
  const values = sources.map(result => result.status === 'fulfilled' ? result.value : null);
  const [reviews, overview, actions, mailboxes] = values;
  const failed = mailboxes?.filter(box => ['failed', 'partial'].includes(box.sync_state?.status)).length;
  const processing = mailboxes?.filter(box => ['sync_requested', 'sync_running', 'queued', 'running'].includes(box.sync_state?.status)).length;
  const cards = [
    ['待复核邮件', reviews?.pending_count, '/#reviews', '检查原文，确认是否进入客户流程'],
    ['待跟进', overview?.open_follow_ups, businessHref('follow-ups', null, { status: 'open' }), '查看待办并记录下一次沟通'],
    ['待确认动作', actions?.count, businessHref('actions', null, { status: 'pending_confirmation' }), '审阅邮件或会议计划，再确认执行'],
    ['同步异常邮箱', failed, '/#processing', '查看最近批次进度并明确重试'],
  ];
  const status = document.getElementById('workspace-status');
  status.innerHTML = cards.map(([title, count, href]) => `<a href="${e(href)}">${e(title)} <strong>${count ?? '—'}</strong></a>`).join('') + `<a href="/#processing">同步排队 / 处理中 <strong>${processing ?? '—'}</strong></a><button type="button" id="workspace-status-refresh" aria-label="刷新待办概览">↻</button>`;
  document.getElementById('workspace-status-refresh').onclick = refreshWorkspace;
  const dashboard = document.getElementById('workspace-tasks');
  if (dashboard) dashboard.innerHTML = cards.map(([title, count, href, note]) => `<a class="workspace-task" href="${e(href)}"><span>${e(title)} <span aria-hidden="true">↗</span></span><strong>${count ?? '暂不可用'}</strong><p>${e(note)}</p></a>`).join('');
  const error = document.getElementById('workspace-load-error');
  error.hidden = sources.every(result => result.status === 'fulfilled');
  error.textContent = sources.flatMap((result, index) => result.status === 'rejected' ? [`${['复核', '业务概览', '动作', '邮箱'][index]}读取失败：${result.reason.message}`] : []).join('；');
}
