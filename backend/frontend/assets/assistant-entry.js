/**
 * 职责：让共享导航直接进入客户聊天，无须先打开客户详情。
 * 实现：读取当前员工的可见邮件客户，支持搜索与分页；显式选择后复用 AssistantPanel。
 * 关联：app.js 挂载 #assistant 路由；index.html 提供入口页，companies API 保持既有权限范围。
 * 目录：AssistantEntry、AssistantEntry.constructor、AssistantEntry.show、AssistantEntry.hide、AssistantEntry.load、AssistantEntry.open、AssistantEntry.fail。
 * 变量索引：panel 为既有助手，nodes 为 DOM；page/query 为筛选，items 为当前页；active/generation 隔离路由和选择，controller/detailController 取消只读请求。
 */
import { request, escapeHtml as e } from './api.js';

/** 功能：管理聊天入口的客户选择。
 * 逻辑：只读加载客户与现有会话，用户提交问题才触发生成。
 * 约束：不自动选第一位客户，不创建跨客户会话或额外分析任务。 */
export class AssistantEntry {
  /** 功能：绑定客户搜索、分页与选择。输入：panel 既有 AssistantPanel。
   * 输出：实例。逻辑：委托按钮点击，不依赖详情页 DOM。
   * 约束：构造时不请求网络。 */
  constructor(panel) {
    this.panel = panel;
    this.nodes = Object.fromEntries(['page', 'search', 'results', 'error', 'previous', 'next', 'pagination'].map(name => [name, document.getElementById(`assistant-entry-${name}`)]));
    this.page = 1; this.query = ''; this.items = []; this.active = false; this.generation = 0;
    this.controller = null; this.detailController = null;
    this.nodes.search.onsubmit = event => { event.preventDefault(); this.query = new FormData(this.nodes.search).get('q').trim(); this.page = 1; void this.load(); };
    this.nodes.previous.onclick = () => { this.page -= 1; void this.load(); };
    this.nodes.next.onclick = () => { this.page += 1; void this.load(); };
    this.nodes.results.onclick = event => {
      const button = event.target.closest('[data-chat-company]');
      const company = this.items.find(item => item.company_id === button?.dataset.chatCompany);
      if (company) this.open(company, button);
    };
  }
  /** 功能：展示入口并可直接恢复指定客户。输入：preferredId 可空公司标识。
   * 输出：Promise。逻辑：加载选择列表；有上下文时单独授权读取该客户并展开助手。
   * 约束：路由切换或用户新选择使旧读取失效；不调用 analyze 或创建会话。 */
  async show(preferredId = null) {
    this.active = true; const generation = ++this.generation;
    this.nodes.page.hidden = false;
    const list = this.load();
    if (preferredId) {
      this.detailController = new AbortController();
      try {
        const company = await request(`companies/${encodeURIComponent(preferredId)}/`, { signal: this.detailController.signal });
        if (this.active && generation === this.generation) this.open(company, document.querySelector('#workspace-nav a[aria-current="page"]'));
      } catch (error) { if (error.name !== 'AbortError' && this.active && generation === this.generation) this.fail(error); }
    }
    await list;
  }
  /** 功能：离开入口。输入：实例状态。输出：无。
   * 逻辑：取消只读请求并隔离已返回的旧响应。约束：不删除服务器会话或草稿。 */
  hide() { this.active = false; ++this.generation; this.controller?.abort(); this.detailController?.abort(); this.nodes.page.hidden = true; }
  /** 功能：读取一页员工客户。输入：page/query。输出：Promise。
   * 逻辑：新查询取消旧查询，加载/空结果/错误均明确显示，按钮根据真实总数启用。
   * 约束：每页 6 条仅为新入口的显示参数，不修改既有邮箱分页或实验条件。 */
  async load() {
    this.controller?.abort(); const controller = new AbortController(); this.controller = controller;
    this.nodes.error.hidden = true; this.nodes.previous.disabled = true; this.nodes.next.disabled = true;
    this.nodes.results.innerHTML = '<p class="assistant-entry-empty" role="status">正在读取你的客户…</p>';
    try {
      const data = await request('companies/?' + new URLSearchParams({ q: this.query, page: this.page, page_size: 6 }), { signal: controller.signal });
      if (!this.active || controller !== this.controller || controller.signal.aborted) return;
      this.items = data.results;
      this.nodes.results.innerHTML = data.results.length ? data.results.map(company => {
        const name = company.company_name || company.domains[0] || company.contacts[0]?.contact_email || '待确认客户';
        return `<button type="button" class="assistant-entry-customer" data-chat-company="${e(company.company_id)}"><span class="avatar">${e(name.slice(0, 1))}</span><span><strong>${e(name)}</strong><small>${e(company.headline_summary || '围绕客户需求、往来邮件和跟进方向提问')}</small></span><span class="assistant-entry-arrow" aria-hidden="true">开始聊天 ↗</span></button>`;
      }).join('') : `<div class="assistant-entry-empty"><strong>${this.query ? '没有找到匹配的客户' : '还没有可用于聊天的邮件客户'}</strong><p>${this.query ? '换个公司名、域名或联系人再试试。' : '先连接邮箱并同步客户邮件，再来聊需求、风险和跟进方向。'}</p>${this.query ? '' : '<a href="/#gmail">连接邮箱 →</a>'}</div>`;
      this.nodes.pagination.textContent = `${this.page} / ${Math.max(1, Math.ceil(data.count / 6))}`;
      this.nodes.previous.disabled = this.page <= 1; this.nodes.next.disabled = this.page * 6 >= data.count;
    } catch (error) {
      if (error.name === 'AbortError' || !this.active || controller !== this.controller) return;
      this.items = []; this.nodes.results.innerHTML = ''; this.nodes.pagination.textContent = '加载失败'; this.fail(error);
    }
  }
  /** 功能：明确选择客户并展开原聊天。输入：company 授权记录、trigger 焦点恢复元素。
   * 输出：无。逻辑：取消旧客户读取，替换 URL 和导航链接便于刷新/再次打开；已经打开时保持展开。
   * 约束：保留助手原有会话、草稿和权限逻辑；不自动提交问题。 */
  open(company, trigger) {
    ++this.generation; this.detailController?.abort();
    const name = company.company_name || company.domains[0] || company.contacts[0]?.contact_email || '待确认客户';
    this.panel.setContext({ id: company.company_id, name });
    history.replaceState(null, '', `#assistant/${encodeURIComponent(company.company_id)}`);
    document.querySelector('#workspace-nav a[data-assistant-entry]').href = `/#assistant/${encodeURIComponent(company.company_id)}`;
    if (!this.panel.isOpen) this.panel.open(trigger);
    console.info('assistant_entry_selected', { companyId: company.company_id });
  }
  /** 功能：报告入口失败。输入：error。输出：可见错误和安全日志。
   * 逻辑：提示用户显式搜索重试。约束：不记录客户内容、令牌或静默重试。 */
  fail(error) { this.nodes.error.textContent = `${error.message} 可点击“查找客户”重新查询。`; this.nodes.error.hidden = false; console.error('assistant_entry_failed', { errorType: error.name }); }
}
