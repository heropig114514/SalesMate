/**
 * 职责：为邮件、业务及世界消息页面提供精简共享导航、客户上下文和真实待办概览及底部聊天入口。
 * 实现：URL 保存客户身份；所有概览来自授权 GET，独立失败显示未知，链接不提交业务操作。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：app.js、business.js 与 world-news.js 调用；workspace.css 与 product-header.js 提供统一外壳；复核及交易沿用原接口。
 * 目录：businessHref、renderWorkspaceNav、mountWorkspace、setWorkspaceContext、refreshWorkspace。
 * 变量索引：customerResources 为客户下的四类业务入口；context 为当前客户；activePage 为当前页面；refreshSequence 防止旧响应覆盖。
 */
import { t, h } from './i18n.js?v=20260920-i18n';

import './product-header.js';
import { enableAssistant } from './assistant-widget.js?v=20260920-floating';
import { request, escapeHtml as e } from './api.js';

const customerResources = [['opportunities', t('商机')], ['quotes', t('报价')], ['orders', t('订单')], ['tickets', t('工单')]];
let context = null, activePage = 'home', refreshSequence = 0;

/** 功能：生成保留客户的业务路由。输入：resource、company、extra 查询项。输出：同源 URL。
 * 逻辑：URLSearchParams 编码身份，hash 仅标识资源。约束：导航不授权访问，不推断客户。 */
export function businessHref(resource, company = context?.id, extra = {}) {
  const query = new URLSearchParams(extra);
  if (company) query.set('company', company);
  return `/business/${query.size ? '?' + query : ''}#${encodeURIComponent(resource)}`;
}

/** 功能：重建共享导航。输入：模块中的 activePage/context。输出：无。
 * 逻辑：按产品目录展示工作台、Channels 与客户，四类销售业务从属于客户；当前资源保留选中状态。约束：客户仅传递到业务相关链接。 */
function renderWorkspaceNav() {
  const nav = document.getElementById('workspace-nav');
  const link = (key, title, href) => `<a href="${e(href)}" ${activePage === key ? 'aria-current="page"' : ''}>${e(title)}</a>`;
  nav.innerHTML = h`<p class="workspace-nav-label">我的工作空间</p>${link('home', t('工作台'), '/#home')}${link('inbox', 'Channels', context ? '/#company/' + encodeURIComponent(context.id) : '/#inbox')}${link('directory', t('客户'), businessHref('directory'))}<div class="workspace-customer-nav" role="group" aria-label="${e(t('客户'))}">${customerResources.map(([key, name]) => link(key, name, businessHref(key))).join('')}</div>`;
}

/** 功能：挂载同一导航外壳。输入：active 为当前页。输出：无。
 * 逻辑：替换共享导航；启用悬浮入口，重复点击同一复核或连接路由仍打开界面。约束：业务页面登录后调用，公开世界消息仅挂载入口；会话权限由 API 校验，不提交请求。 */
export function mountWorkspace(active = 'home') {
  activePage = active;
  enableAssistant();
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
 * 输出：无。逻辑：业务页生成当前客户的操作入口；邮件与分析页不显示重复客户导航；清除入口明确返回全量目录。
 * 约束：不把客户保存到 localStorage，不通过导航修改业务数据。 */
export function setWorkspaceContext(company, active = activePage) {
  context = company;
  activePage = active;
  renderWorkspaceNav();
  const panel = document.getElementById('workspace-context');
  panel.hidden = !company || active === 'inbox';
  if (panel.hidden) { panel.innerHTML = ''; return; }
  panel.innerHTML = h`<div><small>当前客户</small><strong>${e(company.name)}</strong></div><nav aria-label="当前客户工作区"><a href="${e(businessHref('directory'))}">客户档案</a><a href="/#company/${encodeURIComponent(company.id)}">邮件与分析</a><a href="${e(businessHref('quotes'))}">报价</a><a href="${e(businessHref('orders'))}">订单</a><a href="${e(businessHref('follow-ups'))}">跟进</a><a href="${e(businessHref('actions'))}">沟通动作</a></nav><a class="context-clear" href="/business/#directory">全部客户 ↗</a>`;
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
    [t('待复核邮件'), reviews?.pending_count, '/#reviews', t('检查原文，确认是否进入客户流程')],
    [t('待跟进'), overview?.open_follow_ups, businessHref('follow-ups', null, { status: 'open' }), t('查看待办并记录下一次沟通')],
    [t('待确认动作'), actions?.count, businessHref('actions', null, { status: 'pending_confirmation' }), t('审阅邮件或会议计划，再确认执行')],
    [t('同步异常邮箱'), failed, '/#processing', t('查看最近批次进度并明确重试')],
  ];
  const status = document.getElementById('workspace-status');
  status.innerHTML = cards.map(([title, count, href]) => `<a href="${e(href)}">${e(title)} <strong>${count ?? '—'}</strong></a>`).join('') + h`<a href="/#processing">同步排队 / 处理中 <strong>${processing ?? '—'}</strong></a><button type="button" id="workspace-status-refresh" aria-label="刷新待办概览">↻</button>`;
  document.getElementById('workspace-status-refresh').onclick = refreshWorkspace;
  const dashboard = document.getElementById('workspace-tasks');
  if (dashboard) dashboard.innerHTML = cards.map(([title, count, href, note]) => `<a class="workspace-task" href="${e(href)}"><span>${e(title)} <span aria-hidden="true">↗</span></span><strong>${count ?? t('暂不可用')}</strong><p>${e(note)}</p></a>`).join('');
  const error = document.getElementById('workspace-load-error');
  error.hidden = sources.every(result => result.status === 'fulfilled');
  error.textContent = sources.flatMap((result, index) => result.status === 'rejected' ? [t`${[t('复核'), t('业务概览'), t('动作'), t('邮箱')][index]}读取失败：${result.reason.message}`] : []).join('；');
}
