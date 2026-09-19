/**
 * 职责：提供通用及客户专属聊天、来源引用、持久化会话和可编辑草稿。
 * 实现：显式提问入队，先取状态再取消息避免快速回答竞态，有界轮询读取真实回答；模式/客户/会话切换取消旧观察，窄屏保持模态焦点。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：app.js 与 assistant-entry.js 传入可空客户上下文；sales-api.js 通信；index.html 提供历史、草稿及保存控件。
 * 目录：AssistantPanel、AssistantPanel.constructor、AssistantPanel.setContext、AssistantPanel.open、
 * AssistantPanel.mount、AssistantPanel.close、AssistantPanel.syncLayout、AssistantPanel.handleKeydown、AssistantPanel.reset、
 * AssistantPanel.load、AssistantPanel.ensureConversation、AssistantPanel.save、AssistantPanel.draw、AssistantPanel.run、
 * AssistantPanel.stopPolling、AssistantPanel.watch、AssistantPanel.poll、AssistantPanel.refreshAnswers、AssistantPanel.pausePolling、AssistantPanel.retryAnswer。
 * 变量索引：无模块变量；nodes 保存 DOM，drafts 保存本页尚未提交文本，companyId 为当前公司或通用模式的 null；inline 表示页面内挂载；
 * conversations 保存当前模式会话，conversation/draft 保存所选记录及版本，epoch 防止旧请求覆盖；
 * busy 控制提交，needsLoad 暂存操作期间新的展开请求；messageKey 是单次消息幂等键，narrow/isOpen 控制布局，opener 记录关闭后的焦点目标；
 * answers 保存当前会话请求；pollTimer/pollController/pollEpoch 管理取消，pollCount 限制每轮最多 120 次、间隔 2 秒。
 */
import { t, h, locale } from './i18n.js?v=20260920-i18n';

import { escapeHtml as esc } from "./api.js";
import { salesRequest, allRows } from "./sales-api.js";

/** 功能：管理通用及客户助手的会话和草稿交互。
 * 逻辑：问题显式入队，状态、回答和引用均来自后端；外部工具另经业务管理审阅确认。
 * 约束：不在浏览器推理或伪造回复，失败后只允许明确重试。 */
export class AssistantPanel {
  /** 功能：连接静态侧栏并绑定操作。输入：无参数，读取 DOM。
   * 输出：实例。逻辑：保存、提问、重试和新建为显式请求，文本编辑暂存于本页。
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
    this.inline = false;
    this.opener = null;
    this.epoch = 0;
    this.conversations = [];
    this.conversation = null;
    this.draft = null;
    this.busy = false;
    this.needsLoad = false;
    this.messageKey = crypto.randomUUID();
    this.answers = [];
    this.pollTimer = null;
    this.pollController = null;
    this.pollEpoch = 0;
    this.pollCount = 0;
    this.narrow = window.matchMedia("(max-width: 1000px)");
    this.nodes.close.addEventListener("click", () => this.close());
    this.nodes.input.addEventListener("input", () => {
      this.drafts.set(
        `${this.companyId}:${this.conversation?.id || "new"}`,
        this.nodes.input.value,
      );
      this.messageKey = crypto.randomUUID();
      this.nodes["draft-note"].textContent = t("有未保存修改，请点击保存草稿。");
    });
    this.nodes.clear.addEventListener("click", () => {
      this.nodes.input.value = "";
      this.nodes.input.dispatchEvent(new Event("input"));
      this.nodes.input.focus();
    });
    this.nodes.shortcuts.addEventListener("click", (event) => {
      const button = event.target.closest("[data-assistant-prompt]");
      if (!button || this.busy) return;
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
    this.nodes.history.addEventListener("click", (event) => {
      const retry = event.target.closest("[data-chat-retry]");
      if (retry) this.run(() => this.retryAnswer(retry.dataset.chatRetry));
    });
    this.nodes["draft-note"].addEventListener("click", (event) => {
      if (event.target.closest("[data-chat-resume]"))
        this.run(() => this.refreshAnswers());
    });
    document.addEventListener("keydown", (event) => this.handleKeydown(event));
    this.narrow.addEventListener("change", () => this.syncLayout());
  }

  /** 功能：绑定当前会话模式。输入：company 含 id/name，null 表示通用会话。
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
      this.answers = [];
      this.nodes.input.value = this.drafts.get(`${this.companyId}:new`) || "";
      this.nodes.history.textContent = "";
      this.nodes.sessions.innerHTML = h('<option value="">尚未选择会话</option>');
    }
    this.nodes.company.textContent = company?.name || t("通用聊天");
    const customer = Boolean(company);
    this.nodes.panel.querySelector('.assistant-development').textContent = customer ? t('客户问答') : t('通用助手');
    document.getElementById('assistant-context-label').textContent = customer ? t('当前客户') : t('当前会话');
    document.getElementById('assistant-welcome-title').textContent = customer ? t('围绕这位客户，一起想清楚下一步。') : t('有什么想聊的？');
    document.getElementById('assistant-welcome-copy').textContent = customer ? t('基于当前客户资料提问，查看回答与来源依据。') : t('讨论问题、起草邮件、翻译文字，或一起梳理工作计划。无需选择客户。');
    document.getElementById('assistant-unavailable').textContent = customer ? t('基于当前客户资料回答') : t('通用问答 · 写作 · 计划');
    document.getElementById('assistant-availability-copy').textContent = customer ? t('回答标注可用来源，资料不足时会明确说明。') : t('可协助讨论与起草；当前聊天不会自动发送邮件或修改业务记录。');
    this.nodes.input.placeholder = customer ? t('想为这位客户做些什么？') : t('输入问题，或告诉我你想完成什么…');
    const prompts = customer ? [
      [t('总结客户需求'), t('请总结这位客户的需求，并列出需要进一步确认的信息。')],
      [t('起草回复'), t('请结合这位客户的邮件，起草一封供我审核的回复。')],
      [t('建议下一步'), t('请分析这位客户的跟进风险，并建议下一步行动。')],
    ] : [
      [t('起草一封邮件'), t('帮我起草一封专业的商务邮件，请先问我需要哪些信息。')],
      [t('梳理工作计划'), t('帮我梳理今天的工作计划，请先了解我的目标和待办。')],
      [t('解释一个概念'), t('我想了解一个新概念，请用容易理解的方式与我讨论。')],
    ];
    this.nodes.shortcuts.innerHTML = prompts.map(([title, prompt]) => h`<button type="button" data-assistant-prompt="${esc(prompt)}"><span class="assistant-task-icon" aria-hidden="true">✧</span><span><strong>${esc(title)}</strong><small>点击填入草稿</small></span><span aria-hidden="true">↗</span></button>`).join('');
    document
      .getElementById("assistant-toggle")
      ?.setAttribute("aria-expanded", String(this.isOpen));
  }

  /** 功能：展开或收起当前聊天界面。输入：opener 可选触发元素，默认读取详情页按钮；隐式当前上下文。
   * 输出：无。逻辑：展开后读取持久化会话及草稿，侧栏焦点移至关闭入口，页面模式保留自然焦点。
   * 约束：读取不会新建会话或触发分析。 */
  open(opener = null) {
    if (this.isOpen) {
      this.close();
      return;
    }
    this.opener = opener || document.getElementById("assistant-toggle");
    this.isOpen = true;
    this.nodes.panel.hidden = false;
    document.body.classList.toggle("assistant-open", !this.inline);
    document
      .getElementById("assistant-toggle")
      ?.setAttribute("aria-expanded", "true");
    this.syncLayout();
    if (!this.inline) this.nodes.close.focus();
    if (this.busy) this.needsLoad = true;
    else this.run(() => this.load(this.conversation?.id));
    console.info("assistant_panel_opened");
  }

  /** 功能：在独立聊天页与客户侧栏之间移动面板。输入：host 可空挂载节点。
   * 输出：无。逻辑：页面内模式保留导航交互并隐藏关闭按钮。
   * 约束：调用前先关闭面板；不创建第二份会话状态。 */
  mount(host = null) {
    this.inline = Boolean(host);
    (host || document.body).append(this.nodes.panel);
    this.nodes.close.hidden = this.inline;
    this.syncLayout();
  }

  /** 功能：收起并恢复背景交互。输入：restoreFocus 默认 true。
   * 输出：无。逻辑：保留本页文本，取消状态观察，关闭模态语义；返回仍存在的原触发元素或一级聊天入口。
   * 约束：路由切换使用 false，避免聚焦即将移除的元素。 */
  close(restoreFocus = true) {
    this.stopPolling();
    const wasOpen = this.isOpen;
    this.isOpen = false;
    document.getElementById("workspace").inert = false;
    document.body.classList.remove("assistant-open");
    this.nodes.panel.hidden = true;
    const trigger = document.getElementById("assistant-toggle");
    trigger?.setAttribute("aria-expanded", "false");
    if (wasOpen && restoreFocus) {
      const target = this.opener?.isConnected ? this.opener : document.querySelector('#workspace-nav a[data-assistant-entry]');
      (target || trigger)?.focus();
    }
  }

  /** 功能：同步响应式模态语义。输入：isOpen/narrow 实例状态。
   * 输出：无。逻辑：仅窄屏客户侧栏使背景 inert，桌面仍允许浏览客户详情。
   * 约束：不改变业务上下文或数据。 */
  syncLayout() {
    const modal = this.isOpen && !this.inline && this.narrow.matches;
    document.getElementById("workspace").inert = modal;
    this.nodes.panel.setAttribute("role", modal ? "dialog" : "complementary");
    if (modal) this.nodes.panel.setAttribute("aria-modal", "true");
    else this.nodes.panel.removeAttribute("aria-modal");
    if (modal && !this.nodes.panel.contains(document.activeElement))
      this.nodes.close.focus();
  }

  /** 功能：处理 Escape 和窄屏焦点循环。输入：event 键盘事件。
   * 输出：无。逻辑：仅浮动侧栏展开且无其他原生 dialog 时处理。
   * 约束：不会捕获文本 Enter，不阻止正常输入。 */
  handleKeydown(event) {
    if (!this.isOpen || this.inline || document.querySelector("dialog[open]")) return;
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
    this.close(false);
    this.companyId = undefined;
    this.setContext(null);
  }

  /** 功能：加载指定或最近的当前模式会话。输入：selected 可选会话标识。
   * 输出：无。逻辑：完整分页读取会话与草稿；先取回答状态再取消息，确保已完成状态对应的消息可见；旧 epoch 响应不更新视图。
   * 约束：当前页未保存文本优先展示，并明确标记未保存。 */
  async load(selected) {
    this.stopPolling();
    this.answers = [];
    const company = this.companyId,
      epoch = ++this.epoch;
    const conversations = (
      await allRows(
        company ? `records/conversations/?company=${encodeURIComponent(company)}` : "records/conversations/?conversation_scope=general",
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
              `<option value="${esc(c.id)}" ${c.id === this.conversation.id ? "selected" : ""}>${esc(c.title)} · ${esc(new Date(c.created_at).toLocaleString(locale))}</option>`,
          )
          .join("")
      : h('<option value="">尚未建立会话</option>');
    let messages = [],
      draft = null;
    if (this.conversation) {
      const conversationId = this.conversation.id;
      const [drafts, answers] = await Promise.all([
        allRows(`records/drafts/?conversation=${conversationId}`),
        allRows(`chat/requests/?conversation=${conversationId}`),
      ]);
      if (epoch !== this.epoch) return;
      // completed 与助手消息同事务提交；随后读取消息，避免终态停止轮询却漏掉答案。
      messages = await allRows(`records/messages/?conversation=${conversationId}`);
      if (epoch !== this.epoch) return;
      this.answers = answers;
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
      ? t("有本页未保存修改。")
      : t("已读取服务器会话与草稿；修改后请手动保存。");
    this.draw(messages);
    this.watch();
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
      data: { company, title: company ? t("客户工作会话") : t("通用会话") },
    });
    if (epoch !== this.epoch || company !== this.companyId)
      throw new Error(t("会话上下文已切换；原会话已保存，可重新打开查看。"));
    this.conversation = conversation;
    return conversation;
  }

  /** 功能：保存草稿或明确提交问答。输入：asMessage 指定是否请求回答。
   * 输出：无。逻辑：提问使用稳定幂等键，后端原子保存问题及任务；草稿仍按 revision 保存。
   * 约束：只有成功提交后才更换幂等键，网络失败保留输入和键供用户明确重传。 */
  async save(asMessage) {
    const content = this.nodes.input.value;
    if (asMessage && !content.trim()) throw new Error(t("请先输入消息内容。"));
    const company = this.companyId;
    const conversation = await this.ensureConversation();
    const epoch = this.epoch;
    if (asMessage)
      await salesRequest("chat/messages/", {
        method: "POST",
        data: {
          conversation_id: conversation.id,
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
    if (asMessage) {
      this.drafts.set(`${company}:${conversation.id}`, "");
      this.messageKey = crypto.randomUUID();
    }
    await this.load(conversation.id);
    if (!asMessage) this.nodes["draft-note"].textContent = t("草稿已保存到服务器，刷新页面后可以恢复。");
  }

  /** 功能：显示真实持久化的历史消息。输入：messages 数组。
   * 输出：无。逻辑：消息关联本次请求状态；证据默认收起，画像来源指向可读客户页，其他来源按需展开；最后一次失败可明确重试。
   * 约束：全部文本转义，引用不执行 HTML 或不可信 URL；不伪造回复或执行结果。 */
  draw(messages) {
    const byMessage = new Map(this.answers.filter((row) => row.assistant_message_id).map((row) => [row.assistant_message_id, row]));
    const byQuestion = new Map(this.answers.map((row) => [row.user_message_id, row]));
    const labels = { pending: t("等待回答"), processing: t("正在生成回答"), completed: t("回答完成"), failed: t("回答失败") };
    this.nodes.panel.querySelector(".assistant-welcome").hidden = messages.length > 0;
    this.nodes.history.innerHTML = messages.length
      ? messages
          .map((message) => {
            const answer = byMessage.get(message.id), question = byQuestion.get(message.id);
            const sources = answer?.citations || [];
            const citations = sources.length
              ? h`<details class="assistant-sources"><summary>查看依据（${sources.length}）</summary><div class="assistant-source-list">${sources.map((citation) => citation.source_type === "customer_analysis"
                ? h`<div class="assistant-source"><strong>[${esc(citation.position)}] ${esc(citation.title_or_label)}</strong><p class="assistant-source-note">依据来自客户画像与分析，可在客户页面按维度查看。</p>${this.companyId ? h`<a href="/#company/${encodeURIComponent(this.companyId)}">查看当前客户画像 →</a>` : ""}</div>`
                : `<details class="assistant-source"><summary>[${esc(citation.position)}] ${esc(citation.title_or_label)}</summary><div class="assistant-source-content">${esc(citation.content)}</div></details>`).join("")}</div></details>`
              : "";
            const state = question ? `<p class="fine">${labels[question.status] || t("状态未知")}${question.error ? `：${esc(question.error.message)}` : ""}</p>${question.status === "failed" ? h`<button type="button" class="text-btn" data-chat-retry="${esc(question.request_id)}">重新回答</button>` : ""}` : "";
            return `<article class="assistant-message"><small>${message.role === "user" ? t("我") : t("助手")} · ${esc(new Date(message.created_at).toLocaleString(locale))}</small><p>${esc(message.content)}</p>${citations}${state}</article>`;
          })
          .join("")
      : h('<p class="fine">尚无消息。可以先保存草稿，或直接发送问题。</p>');
  }

  /** 功能：取消当前状态观察。输入：实例定时器、控制器及观察代次。
   * 输出：无。逻辑：清除定时器、取消 fetch 并使旧响应失效。
   * 约束：不取消后端回答任务，也不改变消息。 */
  stopPolling() {
    clearTimeout(this.pollTimer);
    this.pollController?.abort();
    this.pollController = null;
    this.pollEpoch += 1;
  }

  /** 功能：观察当前会话活动请求。输入：answers 和面板状态。
   * 输出：无。逻辑：每轮最多 120 次，每次响应后间隔 2 秒；同一时刻只有一次读取。
   * 约束：没有活动任务或已关闭时不发送请求，不创建模型工作。 */
  watch() {
    this.stopPolling();
    const active = this.answers.find((row) => ["pending", "processing"].includes(row.status));
    this.nodes.submit.disabled = this.busy || Boolean(active);
    if (!active || !this.isOpen) return;
    this.pollCount = 0;
    this.nodes["draft-note"].textContent = active.status === "pending" ? t("问题已提交，等待回答。") : t("正在生成回答。");
    const epoch = this.pollEpoch;
    this.pollTimer = setTimeout(() => this.poll(active.request_id, epoch), 2000);
  }

  /** 功能：读取一次生成状态。输入：requestId 绑定任务，epoch 观察代次。
   * 输出：无。逻辑：终态刷新消息，活动状态继续有界观察；错误暂停并提供手动恢复。
   * 约束：取消、切换客户和关闭后的响应均不展示；不隐式重试失败 HTTP。 */
  async poll(requestId, epoch) {
    if (epoch !== this.pollEpoch || !this.isOpen) return;
    this.pollController = new AbortController();
    try {
      const result = await salesRequest(`chat/requests/${requestId}/`, { signal: this.pollController.signal });
      if (epoch !== this.pollEpoch || !this.isOpen) return;
      this.answers = this.answers.map((row) => row.request_id === requestId ? result : row);
      if (["completed", "failed"].includes(result.status)) {
        await this.refreshAnswers();
        return;
      }
      this.nodes["draft-note"].textContent = result.status === "pending" ? t("等待回答。") : t("正在生成回答。");
      this.pollCount += 1;
      if (this.pollCount >= 120) throw new Error(t("等待时间较长，自动查询已暂停。"));
      this.pollTimer = setTimeout(() => this.poll(requestId, epoch), 2000);
    } catch (error) {
      if (epoch !== this.pollEpoch || error.name === "AbortError") return;
      this.pausePolling(error.message);
    }
  }

  /** 功能：刷新答案而保留用户正在编辑的文本。输入：当前客户、会话及 epoch。
   * 输出：无。逻辑：先读取请求状态再读取消息，避免终态与旧消息快照混用；检查绑定和观察代次；任一读取失败显示明确恢复入口。
   * 约束：不重新加载草稿、不抢焦点；保留历史区阅读位置，不静默吞掉刷新错误。 */
  async refreshAnswers() {
    this.stopPolling();
    const conversation = this.conversation?.id, epoch = this.epoch, observation = this.pollEpoch;
    if (!conversation) return;
    let messages, answers;
    try {
      answers = await allRows(`chat/requests/?conversation=${conversation}`);
      if (epoch !== this.epoch || observation !== this.pollEpoch || !this.isOpen) return;
      messages = await allRows(`records/messages/?conversation=${conversation}`);
    } catch (error) {
      if (epoch === this.epoch && observation === this.pollEpoch && this.isOpen)
        this.pausePolling(error.message);
      return;
    }
    if (epoch !== this.epoch || observation !== this.pollEpoch || conversation !== this.conversation?.id || !this.isOpen) return;
    this.answers = answers;
    const scrollContainer = this.nodes.history.parentElement, position = scrollContainer.scrollTop;
    this.draw(messages);
    scrollContainer.scrollTop = position;
    this.nodes["draft-note"].textContent = t("回答状态已更新；输入中的未保存内容已保留。");
    this.watch();
  }

  /** 功能：显示观察暂停原因和明确恢复入口。输入：message 安全提示。
   * 输出：无。逻辑：取消观察后用转义文本显示错误，不影响后端任务。
   * 约束：不会因网络错误自动重新提交问题或恢复查询。 */
  pausePolling(message) {
    this.stopPolling();
    this.nodes["draft-note"].innerHTML = h`${esc(message)} <button type="button" class="text-btn" data-chat-resume>继续查询</button>`;
    console.warn("assistant_poll_paused");
  }

  /** 功能：明确请求重新回答失败的问题。输入：requestId 原失败请求。
   * 输出：无。逻辑：后端创建新尝试，前端重新读取状态。
   * 约束：不覆盖原失败记录，不在错误处理分支自动调用。 */
  async retryAnswer(requestId) {
    const epoch = this.epoch;
    await salesRequest(`chat/requests/${requestId}/retry/`, { method: "POST", data: {} });
    if (epoch === this.epoch) await this.refreshAnswers();
  }

  /** 功能：串行化当前侧栏操作并显示错误。输入：task 异步回调。
   * 输出：无。逻辑：禁用保存、输入及会话切换；完成后活动任务继续禁止新提问，错误进入状态区域。
   * 约束：不重试；不记录正文。切换客户后仍可关闭面板。 */
  async run(task) {
    if (this.busy) return;
    const company = this.companyId;
    this.busy = true;
    for (const name of ["submit", "save", "sessions", "new", "input", "clear"])
      this.nodes[name].disabled = true;
    try {
      await task();
    } catch (error) {
      if (company === this.companyId) this.nodes["draft-note"].textContent = error.message;
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
      this.nodes.submit.disabled = this.answers.some((row) => ["pending", "processing"].includes(row.status));
      if (this.needsLoad && this.isOpen) {
        this.needsLoad = false;
        this.run(() => this.load(this.conversation?.id));
      }
    }
  }
}
