/**
 * 职责：为邮件、业务、世界洞察及设置页面提供共享主导航、底部 Profile 设置、客户上下文和真实待办概览及底部聊天入口。
 * 实现：URL 保存客户身份；概览来自授权 GET；Profile 提供明确确认后的账号内部数据清空，保留登录身份。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：0919 界面及共享语言资源统一缓存版本；共享语言/API 资源随需求界面统一版本；工作空间聊天模块使用统一升级版本以避免旧公司入口缓存；app.js、business.js、world-news.js 与 company-settings.js 调用；workspace.css 与 product-header.js 提供统一外壳；复核及交易沿用原接口。
 * 目录：businessHref、renderWorkspaceNav、mountWorkspace、setWorkspaceContext、refreshWorkspace。
 * 变量索引：customerResources 为客户下的四类业务入口；context 为当前客户；activePage 为当前页面；refreshSequence 防止旧响应覆盖。
 */
import { t, h } from './i18n.js?v=20260921-product';

import './product-header.js?v=20260921-product';
import { enableAssistant } from './assistant-widget.js?v=20260921-product';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { resetAccountData } from './account-reset.js?v=20260921-product';
import { language } from './i18n.js?v=20260921-product';

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
 * 逻辑：展示工作台、全球洞察、Channels 与客户，底部设置不携带客户身份；账号清空按钮委托专用确认流程。约束：客户仅传递到业务相关链接。 */
function renderWorkspaceNav() {
  const nav = document.getElementById('workspace-nav');
  const link = (key, title, href) => `<a href="${e(href)}" ${activePage === key ? 'aria-current="page"' : ''}>${e(title)}</a>`;
  nav.innerHTML = h`<p class="workspace-nav-label">我的工作空间</p>${link('home', t('工作台'), '/#home')}${link('world', 'Global Insights', '/world/')}${link('inbox', 'Channels', context ? '/#company/' + encodeURIComponent(context.id) : '/#inbox')}${link('directory', t('客户'), businessHref('directory'))}<div class="workspace-customer-nav" role="group" aria-label="${e(t('客户'))}">${customerResources.map(([key, name]) => link(key, name, businessHref(key))).join('')}</div>`;
  const profile = document.getElementById('workspace-profile');
  if (profile) {
    profile.innerHTML = `<p class="workspace-profile-label">Profile</p>${link('company-settings', 'Company Setting', '/settings/company/')}${link('gmail', 'Emails Connections', '/#gmail')}<button type="button" class="text-btn" id="reset-account-data">${language === 'en' ? 'Clear account data' : '清空账号数据'}</button>`;
    profile.querySelector('#reset-account-data').onclick = event => resetAccountData(event.currentTarget);
  }
}

/** 功能：挂载同一导航外壳。输入：active 为当前页。输出：无。
 * 逻辑：替换主导航与 Profile；启用悬浮入口，重复点击同一复核或邮箱设置路由仍打开界面。约束：业务页面登录后调用，公开世界消息仅挂载入口；会话权限由 API 校验，不提交请求。 */
export function mountWorkspace(active = 'home') {
  activePage = active;
  enableAssistant();
  renderWorkspaceNav();
  for (const id of ['workspace-nav', 'workspace-profile', 'workspace-tasks']) {
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

/** 功能：更新首页待办摘要。输入：无，读取当前登录身份。输出：无。
 * 逻辑：仅含首页摘要的页面独立 GET 并行读取复核、概览及邮箱；三张待办卡使用服务器总数，不再渲染重复全局状态栏。
 * 约束：失败显示“暂不可用”与诊断，不以零代替；不自动同步、不确认动作、不改变业务状态。 */
export async function refreshWorkspace() {
  const sequence = ++refreshSequence;
  if (!document.getElementById('workspace-tasks')) return;
  const sources = await Promise.allSettled([
    request('email-reviews/?status=pending&page=1'), request('sales/overview/'),
    request('mailboxes/'),
  ]);
  if (sequence !== refreshSequence) return;
  const values = sources.map(result => result.status === 'fulfilled' ? result.value : null);
  const [reviews, overview, mailboxes] = values;
  const failed = mailboxes?.filter(box => ['failed', 'partial'].includes(box.sync_state?.status)).length;
  const cards = [
    [t('待复核邮件'), reviews?.pending_count, '/#reviews', t('检查原文，确认是否进入客户流程')],
    [t('待跟进'), overview?.open_follow_ups, businessHref('follow-ups', null, { status: 'open' }), t('查看待办并记录下一次沟通')],
    [t('同步异常邮箱'), failed, '/#processing', t('查看最近批次进度并明确重试')],
  ];
  const dashboard = document.getElementById('workspace-tasks');
  if (dashboard) dashboard.innerHTML = cards.map(([title, count, href, note]) => `<a class="workspace-task" href="${e(href)}"><span>${e(title)} <span aria-hidden="true">↗</span></span><strong>${count ?? t('暂不可用')}</strong><p>${e(note)}</p></a>`).join('');
  const error = document.getElementById('workspace-load-error');
  error.hidden = sources.every(result => result.status === 'fulfilled');
  error.textContent = sources.flatMap((result, index) => result.status === 'rejected' ? [t`${[t('复核'), t('业务概览'), t('邮箱')][index]}读取失败：${result.reason.message}`] : []).join('；');
}
