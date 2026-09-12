/**
 * 职责：提供客户专属助手侧栏、持久化会话和可编辑聊天草稿。
 * 实现：显式保存至员工私有 API；上下文和请求代次隔离异步响应，窄屏保持模态焦点。
 * 关联：app.js 传入客户上下文；sales-api.js 通信；index.html 提供历史、草稿及保存控件。
 * 目录：AssistantPanel、AssistantPanel.constructor、AssistantPanel.setContext、AssistantPanel.open、
 * AssistantPanel.close、AssistantPanel.syncLayout、AssistantPanel.handleKeydown、AssistantPanel.reset、
 * AssistantPanel.load、AssistantPanel.ensureConversation、AssistantPanel.save、AssistantPanel.draw、AssistantPanel.run。
 * 变量索引：无模块变量；nodes 保存 DOM，drafts 保存本页尚未提交文本，companyId 为当前公司；
 * conversations 保存当前客户会话，conversation/draft 保存所选记录及版本，epoch 防止旧请求覆盖；
 * busy 控制提交，needsLoad 暂存操作期间新的展开请求；messageKey 是单次消息幂等键，narrow/isOpen 控制布局。
 */
import { escapeHtml as esc } from "./api.js";
import { salesRequest, allRows } from "./sales-api.js";

/** 功能：管理客户助手的会话和草稿交互。
 * 逻辑：消息只记录明确用户输入；所有外部工具另经业务管理页审阅确认。
 * 约束：当前聊天模型尚未接入，不生成模拟助手回复。 */
export class AssistantPanel {
  /** 功能：连接静态侧栏并绑定操作。输入：无参数，读取 DOM。
   * 输出：实例。逻辑：保存和新建为显式请求，文本编辑暂存于本页。
   * 约束：不会因输入或页面初始化调用模型与外部服务。 */
  constructor() {
    this.nodes = Object.fromEntries(
      [
        "panel",
        "company",
        "input",
        "close",
        "clear",
        "form",
        "shortcuts",
        "draft-note",
        "sessions",
        "new",
        "save",
        "history",
        "submit",
      ].map((name) => [name, document.getElementById(`assistant-${name}`)]),
    );
    this.drafts = new Map();
    this.companyId = null;
    this.isOpen = false;
    this.epoch = 0;
    this.conversations = [];
    this.conversation = null;
    this.draft = null;
    this.busy = false;
    this.needsLoad = false;
    this.messageKey = crypto.randomUUID();
    this.narrow = window.matchMedia("(max-width: 1000px)");
    this.nodes.close.addEventListener("click", () => this.close());
    this.nodes.input.addEventListener("input", () => {
      this.drafts.set(
        `${this.companyId}:${this.conversation?.id || "new"}`,
        this.nodes.input.value,
      );
      this.messageKey = crypto.randomUUID();
      this.nodes["draft-note"].textContent = "有未保存修改，请点击保存草稿。";
    });
    this.nodes.clear.addEventListener("click", () => {
      this.nodes.input.value = "";
      this.nodes.input.dispatchEvent(new Event("input"));
      this.nodes.input.focus();
    });
    this.nodes.shortcuts.addEventListener("click", (event) => {
      const button = event.target.closest("[data-assistant-prompt]");
      if (!button || !this.companyId || this.busy) return;
      this.nodes.input.value +=
        (this.nodes.input.value ? "\n" : "") + button.dataset.assistantPrompt;
      this.nodes.input.dispatchEvent(new Event("input"));
      this.nodes.input.focus();
    });
    this.nodes.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.run(() => this.save(true));
    });
    this.nodes.save.addEventListener("click", () =>
      this.run(() => this.save(false)),
    );
    this.nodes.sessions.addEventListener("change", () =>
      this.run(() => this.load(this.nodes.sessions.value)),
    );
    this.nodes.new.addEventListener("click", () =>
      this.run(async () => {
        this.conversation = null;
        this.draft = null;
        await this.ensureConversation();
        await this.load(this.conversation.id);
      }),
    );
    document.addEventListener("keydown", (event) => this.handleKeydown(event));
    this.narrow.addEventListener("change", () => this.syncLayout());
  }

  /** 功能：绑定当前客户上下文。输入：company 含 id/name 或 null。
   * 输出：无。逻辑：切换时取消旧响应的展示资格，清空旧历史并恢复本页输入。
   * 约束：客户名称以 textContent 展示，不读取邮件正文。 */
  setContext(company) {
    if (this.companyId !== (company?.id || null)) {
      this.close(false);
      this.epoch += 1;
      this.companyId = company?.id || null;
      this.conversation = null;
      this.draft = null;
      this.conversations = [];
      this.nodes.input.value = this.drafts.get(`${this.companyId}:new`) || "";
      this.nodes.history.textContent = "";
      this.nodes.sessions.innerHTML = '<option value="">尚未选择会话</option>';
    }
    this.nodes.company.textContent = company?.name || "请先打开客户详情";
    document
      .getElementById("assistant-toggle")
      ?.setAttribute("aria-expanded", String(this.isOpen));
  }

  /** 功能：展开或收起当前客户侧栏。输入：隐式当前上下文。
   * 输出：无。逻辑：展开后读取持久化会话及草稿，焦点移至关闭入口。
   * 约束：读取不会新建会话或触发分析。 */
  open() {
    if (!this.companyId) return;
    if (this.isOpen) {
      this.close();
      return;
    }
    this.isOpen = true;
    this.nodes.panel.hidden = false;
    document.body.classList.add("assistant-open");
    document
      .getElementById("assistant-toggle")
      ?.setAttribute("aria-expanded", "true");
    this.syncLayout();
    this.nodes.close.focus();
    if (this.busy) this.needsLoad = true;
    else this.run(() => this.load(this.conversation?.id));
    console.info("assistant_panel_opened");
  }

  /** 功能：收起并恢复背景交互。输入：restoreFocus 默认 true。
   * 输出：无。逻辑：保留本页未保存文本，关闭模态语义。
   * 约束：路由切换使用 false，避免聚焦即将移除的元素。 */
  close(restoreFocus = true) {
    const wasOpen = this.isOpen;
    this.isOpen = false;
    document.getElementById("workspace").inert = false;
    document.body.classList.remove("assistant-open");
    this.nodes.panel.hidden = true;
    const trigger = document.getElementById("assistant-toggle");
    trigger?.setAttribute("aria-expanded", "false");
    if (wasOpen && restoreFocus) trigger?.focus();
  }

  /** 功能：同步响应式模态语义。输入：isOpen/narrow 实例状态。
   * 输出：无。逻辑：窄屏使背景 inert，桌面仍允许浏览客户详情。
   * 约束：不改变业务上下文或数据。 */
  syncLayout() {
    const modal = this.isOpen && this.narrow.matches;
    document.getElementById("workspace").inert = modal;
    this.nodes.panel.setAttribute("role", modal ? "dialog" : "complementary");
    if (modal) this.nodes.panel.setAttribute("aria-modal", "true");
    else this.nodes.panel.removeAttribute("aria-modal");
    if (modal && !this.nodes.panel.contains(document.activeElement))
      this.nodes.close.focus();
  }

  /** 功能：处理 Escape 和窄屏焦点循环。输入：event 键盘事件。
   * 输出：无。逻辑：仅展开且无其他原生 dialog 时处理。
   * 约束：不会捕获文本 Enter，不阻止正常输入。 */
  handleKeydown(event) {
    if (!this.isOpen || document.querySelector("dialog[open]")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
      return;
    }
    if (event.key !== "Tab" || !this.narrow.matches) return;
    const controls = [
      ...this.nodes.panel.querySelectorAll(
        "button:not(:disabled),textarea,select,a[href]",
      ),
    ].filter((node) => !node.closest("[hidden]"));
    const first = controls[0],
      last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /** 功能：清除当前登录会话的内存状态。输入：无参数。
   * 输出：无。逻辑：切断旧异步响应的显示资格，清理文本缓存。
   * 约束：不会删除服务器上的会话或草稿。 */
  reset() {
    this.needsLoad = false;
    this.epoch += 1;
    this.drafts.clear();
    this.setContext(null);
  }

  /** 功能：加载指定或最近的当前客户会话。输入：selected 可选会话标识。
   * 输出：无。逻辑：完整分页读取会话、消息和草稿；旧 epoch 响应不更新视图。
   * 约束：当前页未保存文本优先展示，并明确标记未保存。 */
  async load(selected) {
    const company = this.companyId,
      epoch = ++this.epoch;
    if (!company) return;
    const conversations = (
      await allRows(
        `records/conversations/?company=${encodeURIComponent(company)}`,
      )
    ).reverse();
    if (epoch !== this.epoch) return;
    this.conversations = conversations;
    this.conversation =
      conversations.find((c) => c.id === selected) || conversations[0] || null;
    this.nodes.sessions.innerHTML = conversations.length
      ? conversations
          .map(
            (c) =>
              `<option value="${esc(c.id)}" ${c.id === this.conversation.id ? "selected" : ""}>${esc(c.title)} · ${esc(new Date(c.created_at).toLocaleString())}</option>`,
          )
          .join("")
      : '<option value="">尚未建立会话</option>';
    let messages = [],
      draft = null;
    if (this.conversation) {
      const [history, drafts] = await Promise.all([
        allRows(`records/messages/?conversation=${this.conversation.id}`),
        allRows(`records/drafts/?conversation=${this.conversation.id}`),
      ]);
      messages = history;
      draft =
        drafts
          .filter((d) => d.kind === "chat")
          .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))[0] ||
        null;
    }
    if (epoch !== this.epoch) return;
    this.draft = draft;
    const key = `${company}:${this.conversation?.id || "new"}`;
    const previousContent = this.nodes.input.value;
    this.nodes.input.value = this.drafts.has(key)
      ? this.drafts.get(key)
      : draft?.content || "";
    if (this.nodes.input.value !== previousContent)
      this.messageKey = crypto.randomUUID();
    this.nodes["draft-note"].textContent = this.drafts.has(key)
      ? "有本页未保存修改。"
      : "已读取服务器会话与草稿；修改后请手动保存。";
    this.draw(messages);
  }

  /** 功能：在显式保存或新建操作中建立会话。输入：当前公司及会话状态。
   * 输出：当前会话。逻辑：没有选中会话才调用创建接口，保留原输入。
   * 约束：上下文切换导致操作过期时抛错，不把旧会话写到新客户界面。 */
  async ensureConversation() {
    if (this.conversation) return this.conversation;
    const company = this.companyId,
      epoch = this.epoch;
    const conversation = await salesRequest("records/conversations/", {
      method: "POST",
      data: { company, title: "客户工作会话" },
    });
    if (epoch !== this.epoch || company !== this.companyId)
      throw new Error("客户上下文已切换；原客户会话已保存，可重新打开查看。");
    this.conversation = conversation;
    return conversation;
  }

  /** 功能：保存当前草稿或不可变用户消息。输入：asMessage 指定保存目标。
   * 输出：无。逻辑：消息使用稳定幂等键；草稿编辑携带 revision，新建或修改均为显式请求。
   * 约束：不调用聊天模型；保存消息后保留草稿，避免把两次独立写入混为原子操作。 */
  async save(asMessage) {
    const content = this.nodes.input.value;
    if (asMessage && !content.trim()) throw new Error("请先输入消息内容。");
    const company = this.companyId;
    const conversation = await this.ensureConversation();
    const epoch = this.epoch;
    if (asMessage)
      await salesRequest("records/messages/", {
        method: "POST",
        data: {
          conversation: conversation.id,
          content,
          client_key: this.messageKey,
        },
      });
    else {
      const draft = this.draft;
      const saved = await salesRequest(
        `records/drafts/${draft ? draft.id + "/" : ""}`,
        {
          method: draft ? "PATCH" : "POST",
          data: draft
            ? { content }
            : { conversation: conversation.id, kind: "chat", content },
          version: draft?.revision,
        },
      );
      if (epoch === this.epoch) this.draft = saved;
    }
    if (company !== this.companyId || epoch !== this.epoch) return;
    this.drafts.delete(`${company}:new`);
    this.drafts.delete(`${company}:${conversation.id}`);
    await this.load(conversation.id);
    this.nodes["draft-note"].textContent = asMessage
      ? "用户消息已保存。聊天模型尚未接入；已有草稿仍保留。"
      : "草稿已保存到服务器，刷新页面后可以恢复。";
  }

  /** 功能：显示真实持久化的历史消息。输入：messages 数组。
   * 输出：无。逻辑：逐条显示角色、时间和原文；空会话明确说明。
   * 约束：全部文本转义，不伪造回复或执行结果。 */
  draw(messages) {
    this.nodes.history.innerHTML = messages.length
      ? messages
          .map(
            (message) =>
              `<article class="assistant-message"><small>${message.role === "user" ? "我" : "助手"} · ${esc(new Date(message.created_at).toLocaleString())}</small><p>${esc(message.content)}</p></article>`,
          )
          .join("")
      : '<p class="fine">尚无已保存消息。可以先保存草稿，或把消息记录到会话中。</p>';
  }

  /** 功能：串行化当前侧栏操作并显示错误。输入：task 异步回调。
   * 输出：无。逻辑：禁用保存、输入及会话切换；完成后处理操作期间新增的展开请求，错误进入状态区域。
   * 约束：不重试；不记录正文。切换客户后仍可关闭面板。 */
  async run(task) {
    if (this.busy) return;
    this.busy = true;
    for (const name of ["submit", "save", "sessions", "new", "input", "clear"])
      this.nodes[name].disabled = true;
    try {
      await task();
    } catch (error) {
      this.nodes["draft-note"].textContent = error.message;
      console.warn("assistant_operation_failed");
    } finally {
      this.busy = false;
      for (const name of [
        "submit",
        "save",
        "sessions",
        "new",
        "input",
        "clear",
      ])
        this.nodes[name].disabled = false;
      if (this.needsLoad && this.isOpen) {
        this.needsLoad = false;
        this.run(() => this.load(this.conversation?.id));
      }
    }
  }
}
