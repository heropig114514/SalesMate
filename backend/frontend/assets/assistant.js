/**
 * 职责：提供客户专属 AI 助手侧栏的界面占位和临时输入草稿。
 * 实现：按公司 ID 隔离内存草稿；桌面并排展示，窄屏使用具有焦点约束的全屏面板。
 * 关联：app.js 在详情渲染和路由变化时传入上下文；index.html 与 app.css 提供视图。
 * 目录：AssistantPanel、AssistantPanel.constructor、AssistantPanel.setContext、
 * AssistantPanel.open、AssistantPanel.close、AssistantPanel.syncLayout、
 * AssistantPanel.handleKeydown、AssistantPanel.reset。
 * 变量索引：无模块变量；实例 nodes 保存 DOM，drafts 保存公司草稿，companyId 为当前归属，
 * narrow 为响应式媒体查询，isOpen 为展开状态。草稿不持久化，不调用网络或模型。
 */

/** 功能：管理开发中助手的可访问界面与客户上下文。
 * 逻辑：由主应用明确切换上下文，只有用户输入与快捷任务可修改内存草稿。
 * 约束：不发送消息，不生成回复，不声明工具已执行；日志不记录客户信息或草稿。 */
export class AssistantPanel {
  /** 功能：连接静态侧栏并注册一次性监听。输入：无参数，读取页面 DOM 和窗口尺寸。
   * 输出：AssistantPanel 实例。逻辑：输入保存在当前公司键下；快捷任务仅填入编辑框。
   * 约束：DOM 必须已加载；媒体查询只改变交互布局，不改变业务配置。 */
  constructor() {
    this.nodes = Object.fromEntries(['panel', 'company', 'input', 'close', 'clear', 'form', 'shortcuts', 'draft-note'].map(name => [name, document.getElementById(`assistant-${name}`)]));
    this.drafts = new Map();
    this.companyId = null;
    this.isOpen = false;
    this.narrow = window.matchMedia('(max-width: 1000px)');
    this.nodes.close.addEventListener('click', () => this.close());
    this.nodes.input.addEventListener('input', () => {
      if (this.companyId) this.drafts.set(this.companyId, this.nodes.input.value);
      this.nodes['draft-note'].textContent = '草稿仅保留在当前页面，刷新后清空。';
    });
    this.nodes.clear.addEventListener('click', () => {
      this.drafts.delete(this.companyId);
      this.nodes.input.value = '';
      this.nodes['draft-note'].textContent = '当前客户的草稿已清空。';
      this.nodes.input.focus();
    });
    this.nodes.shortcuts.addEventListener('click', event => {
      const button = event.target.closest('[data-assistant-prompt]');
      if (!button || !this.companyId) return;
      const current = this.nodes.input.value;
      this.nodes.input.value = current ? `${current}\n${button.dataset.assistantPrompt}` : button.dataset.assistantPrompt;
      this.drafts.set(this.companyId, this.nodes.input.value);
      this.nodes['draft-note'].textContent = '已填入草稿；助手开放后才可发送。';
      this.nodes.input.focus();
    });
    // 即使浏览器产生隐式提交，也只保留草稿，不访问尚未实现的消息接口。
    this.nodes.form.addEventListener('submit', event => event.preventDefault());
    document.addEventListener('keydown', event => this.handleKeydown(event));
    this.narrow.addEventListener('change', () => this.syncLayout());
  }

  /** 功能：绑定当前客户或离开详情。输入：company 为含 id、name 的对象，或 null。
   * 输出：无。逻辑：切换归属前收起侧栏，恢复对应草稿，同一客户刷新保留输入和展开状态。
   * 约束：只使用 textContent 展示客户名称；不读取邮件正文，路由失败时无可操作的旧上下文。 */
  setContext(company) {
    if (this.companyId !== (company?.id || null)) this.close(false);
    this.companyId = company?.id || null;
    this.nodes.company.textContent = company?.name || '请先打开客户详情';
    this.nodes.input.value = this.drafts.get(this.companyId) || '';
    this.nodes['draft-note'].textContent = '草稿仅保留在当前页面，刷新后清空。';
    const trigger = document.getElementById('assistant-toggle');
    if (trigger) trigger.setAttribute('aria-expanded', String(this.isOpen));
  }

  /** 功能：展开当前客户的助手。输入：当前 companyId 和 isOpen 实例状态。
   * 输出：无。逻辑：已有展开状态则收起；否则显现面板并移动焦点到关闭入口。
   * 约束：无客户时不打开，不触发既有 Agent 分析接口。 */
  open() {
    if (!this.companyId) return;
    if (this.isOpen) { this.close(); return; }
    this.isOpen = true;
    this.nodes.panel.hidden = false;
    document.body.classList.add('assistant-open');
    document.getElementById('assistant-toggle')?.setAttribute('aria-expanded', 'true');
    this.syncLayout();
    this.nodes.close.focus();
    console.info('assistant_panel_opened');
  }

  /** 功能：收起面板并恢复背景交互。输入：restoreFocus，默认 true 表示返回触发按钮。
   * 输出：无。逻辑：保留内存草稿，移除窄屏背景 inert 和布局状态。
   * 约束：路由切换使用 false，避免焦点返回即将被移除的按钮。 */
  close(restoreFocus = true) {
    const wasOpen = this.isOpen;
    this.isOpen = false;
    document.getElementById('workspace').inert = false;
    document.body.classList.remove('assistant-open');
    this.nodes.panel.hidden = true;
    const trigger = document.getElementById('assistant-toggle');
    trigger?.setAttribute('aria-expanded', 'false');
    if (wasOpen && restoreFocus) trigger?.focus();
    if (wasOpen) console.info('assistant_panel_closed');
  }

  /** 功能：同步桌面侧栏与窄屏模态语义。输入：isOpen、narrow 及当前焦点。
   * 输出：无。逻辑：仅窄屏打开时禁用背景，切换断点后确保焦点仍在面板内。
   * 约束：桌面允许同时浏览客户资料；窄屏关闭后恢复原背景交互。 */
  syncLayout() {
    const modal = this.isOpen && this.narrow.matches;
    document.getElementById('workspace').inert = modal;
    this.nodes.panel.setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) this.nodes.panel.setAttribute('aria-modal', 'true');
    else this.nodes.panel.removeAttribute('aria-modal');
    if (modal && !this.nodes.panel.contains(document.activeElement)) this.nodes.close.focus();
  }

  /** 功能：支持 Escape 收起与窄屏 Tab 循环。输入：event 为键盘事件。
   * 输出：无。逻辑：仅面板打开时处理；已有原生对话框优先处理 Escape。
   * 约束：不捕获文本 Enter；只在窄屏模态状态约束焦点，排除禁用按钮。 */
  handleKeydown(event) {
    if (!this.isOpen || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') { event.preventDefault(); this.close(); return; }
    if (event.key !== 'Tab' || !this.narrow.matches) return;
    const controls = [...this.nodes.panel.querySelectorAll('button:not(:disabled), textarea')];
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  /** 功能：销毁当前登录会话的临时草稿。输入：无参数，读取实例草稿集合。
   * 输出：无。逻辑：清空集合并解除客户上下文，防止同一页面重新登录后复用前一用户输入。
   * 约束：不删除后端数据，不访问本地存储。 */
  reset() {
    this.drafts.clear();
    this.setContext(null);
  }
}
