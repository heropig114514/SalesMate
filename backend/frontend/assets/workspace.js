/**
 * Responsibility: Provide shared navigation, bottom Profile settings, customer context, real task summaries, and bottom chat across mail, business, world insights, and settings.
 * Implementation: The sidebar no longer lists opportunity priorities; URLs retain customer identity/shared experiment context. Experiment customers use read-only source entries; summaries use authorized GETs. Profile offers explicitly confirmed internal account-data reset while preserving login identity.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: Header versions reflect removal of experiment navigation; chat Markdown, 0919 interface, and language/API resources use coordinated versions. Workspace chat upgrades avoid cached company-specific entry points. app.js, business.js, world-news.js, and company-settings.js call this module; workspace.css/product-header.js share the shell; review and transactions retain existing APIs.
 * Directory: businessHref, renderWorkspaceNav, mountWorkspace, setWorkspaceContext, refreshWorkspace.
 * Variable index: customerResources contains four customer business entries; context is the current customer; activePage is the current page; refreshSequence prevents stale updates.
 */
import { t, h } from './i18n.js?v=20260921-product';

import './product-header.js?v=20260922-nav';
import { enableAssistant } from './assistant-widget.js?v=20260921-markdown';
import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { resetAccountData } from './account-reset.js?v=20260921-product';
import { language } from './i18n.js?v=20260921-product';

const customerResources = [['opportunities', t('商机')], ['quotes', t('报价')], ['orders', t('订单')], ['tickets', t('工单')]];
let context = null, activePage = 'home', refreshSequence = 0;

/** Function: Generate a business route retaining customer context. Inputs: resource, company, and extra query parameters. Outputs: A same-origin URL.
 * Logic: URLSearchParams encodes identity; the hash identifies only the resource. Constraints: Navigation neither grants access nor infers customers. */
export function businessHref(resource, company = context?.id, extra = {}) {
  const query = new URLSearchParams(extra);
  if (company) query.set('company', company);
  return `/business/${query.size ? '?' + query : ''}#${encodeURIComponent(resource)}`;
}

/** Function: Rebuild shared navigation. Inputs: Module activePage/context. Outputs: None.
 * Logic: Show workspace, global insights, Channels, and customers without a sidebar priority entry. Bottom settings omit customer identity; account reset delegates to its confirmation flow. Constraints: Shared experiment customers never enter private mail routes; other customer identities pass only to relevant business links. */
function renderWorkspaceNav() {
  const nav = document.getElementById('workspace-nav');
  const link = (key, title, href) => `<a href="${e(href)}" ${activePage === key ? 'aria-current="page"' : ''}>${e(title)}</a>`;
  nav.innerHTML = h`<p class="workspace-nav-label">我的工作空间</p>${link('home', t('工作台'), '/#home')}${link('world', 'Global Insights', '/world/')}${link('inbox', 'Channels', context && !context.experiment ? '/#company/' + encodeURIComponent(context.id) : '/#inbox')}${link('directory', t('客户'), businessHref('directory'))}<div class="workspace-customer-nav" role="group" aria-label="${e(t('客户'))}">${customerResources.map(([key, name]) => link(key, name, businessHref(key))).join('')}</div>`;
  const profile = document.getElementById('workspace-profile');
  if (profile) {
    profile.innerHTML = `<p class="workspace-profile-label">Profile</p>${link('company-settings', 'Company Setting', '/settings/company/')}${link('gmail', 'Emails Connections', '/#gmail')}<button type="button" class="text-btn" id="reset-account-data">${language === 'en' ? 'Clear account data' : '清空账号数据'}</button>`;
    profile.querySelector('#reset-account-data').onclick = event => resetAccountData(event.currentTarget);
  }
}

/** Function: Mount the shared navigation shell. Inputs: active is the current page. Outputs: None.
 * Logic: Replace navigation/Profile and enable the floating entry; repeated clicks on the same review/mail-settings route still open the view. Constraints: Business pages call after login; public world news mounts the entry only. APIs enforce session permissions; mounting submits no requests. */
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

/** Function: Synchronize customer context and cross-module actions. Inputs: company contains id/name and optional experiment provenance, or null; active identifies the page.
 * Outputs: None. Logic: Generate business-page customer navigation, replace experiment mail links with source/relations, and return to the full directory on clearing.
 * Constraints: Never store customers in localStorage or modify business data through navigation. */
export function setWorkspaceContext(company, active = activePage) {
  context = company;
  activePage = active;
  renderWorkspaceNav();
  const panel = document.getElementById('workspace-context');
  panel.hidden = !company || active === 'inbox';
  if (panel.hidden) { panel.innerHTML = ''; return; }
  panel.innerHTML = h`<div><small>当前客户</small><strong>${e(company.name)}</strong></div><nav aria-label="当前客户工作区"><a href="${e(businessHref('directory'))}">客户档案</a>${company.experiment ? `<a href="/experiments/#${new URLSearchParams({ table: "crm.Company", pk: company.id })}">${language === "en" ? "Synthetic sources" : "实验来源与关联"}</a>` : `<a href="/#company/${encodeURIComponent(company.id)}">${t("邮件与分析")}</a>`}<a href="${e(businessHref('quotes'))}">报价</a><a href="${e(businessHref('orders'))}">订单</a><a href="${e(businessHref('follow-ups'))}">跟进</a><a href="${e(businessHref('actions'))}">沟通动作</a></nav><a class="context-clear" href="/business/#directory">全部客户 ↗</a>`;
}

/** Function: Update the home task summary. Inputs: None; reads the current login identity. Outputs: None.
 * Logic: Only pages containing the summary issue independent parallel GETs for review, overview, and mailboxes. Three task cards use server totals without a duplicate global status bar.
 * Constraints: Show unavailable status and diagnostics on failure instead of zero; no automatic sync, action confirmation, or business-state changes. */
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
