/**
 * Responsibility: Provide workspace chat, Markdown answers, source citations, persisted conversations, and editable drafts.
 * Implementation: Render persisted answers and poll requests; awaiting_approval opens an escaped operation dialog. Explicit decisions resume or cancel the backend request; account/conversation changes discard stale dialogs and responses.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; assistant-widget.js mounts the single workspace entry and provides history, draft, and save controls; sales-api.js handles communication.
 * Directory: AssistantPanel, AssistantPanel.constructor, AssistantPanel.initializeView, AssistantPanel.open,
 * AssistantPanel.close, AssistantPanel.syncLayout, AssistantPanel.handleKeydown, AssistantPanel.reset,
 * AssistantPanel.load, AssistantPanel.ensureConversation, AssistantPanel.save, AssistantPanel.draw, AssistantPanel.run,
 * AssistantPanel.stopPolling, AssistantPanel.watch, AssistantPanel.poll, AssistantPanel.refreshAnswers, AssistantPanel.pausePolling, AssistantPanel.retryAnswer.
 * AssistantPanel.showApproval, AssistantPanel.dismissApproval, AssistantPanel.decideApproval.
 * Variable index: No module variables; nodes holds DOM references; drafts stores unsent page text; background records prior inert state on narrow screens.
 * conversations holds workspace conversations; conversation/draft holds the selected record and version; epoch prevents stale request updates.
 * busy controls submission; needsLoad defers new open requests during an operation; messageKey is the message idempotency key; narrow/isOpen controls layout; opener records the focus target after closing.
 * answers holds current conversation requests; pollTimer/pollController/pollEpoch manages cancellation; pollCount limits each round to 120 polls at two-second intervals.
 * approvalDialog holds the pending operation modal; questions maps displayed question IDs to text for restoring an editable rejected question.
 */
import { t, h, locale } from './i18n.js?v=20260921-product';

import { escapeHtml as esc } from "./api.js?v=20260921-product";
import { salesRequest, allRows } from "./sales-api.js?v=20260921-product";
import { renderAssistantMarkdown } from './assistant-markdown.js?v=20260921-markdown';

/** Function: Manage workspace assistant conversations and drafts.
 * Logic: Explicit questions enter the queue; status, answers, and citations come from the backend. External tools require separate review/confirmation in business management.
 * Constraints: Never infer or fabricate replies in the browser; only explicit retries are allowed after failure. */
export class AssistantPanel {
  /** Function: Connect the shared widget and bind actions. Inputs: None; reads the DOM.
   * Outputs: An instance. Logic: Saving, asking, retrying, and creating are explicit requests; text edits remain in page memory.
   * Constraints: Input and page initialization never invoke models or external services. */
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
    this.isOpen = false;
    this.background = new Map();
    this.opener = null;
    this.epoch = 0;
    this.conversations = [];
    this.conversation = null;
    this.draft = null;
    this.busy = false;
    this.needsLoad = false;
    this.messageKey = crypto.randomUUID();
    this.answers = [];
    this.approvalDialog = null;
    this.questions = new Map();
    this.pollTimer = null;
    this.pollController = null;
    this.pollEpoch = 0;
    this.pollCount = 0;
    this.narrow = window.matchMedia("(max-width: 1000px)");
    this.nodes.close.addEventListener("click", () => this.close());
    this.nodes.input.addEventListener("input", () => {
      this.drafts.set(
        this.conversation?.id || "new",
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

  /** Function: Initialize workspace chat text. Inputs: None; reads the shared widget DOM.
   * Outputs: None. Logic: Every page uses the same conversation scope; the Agent queries customers according to the question.
   * Constraints: Do not read customer data, create conversations, or overwrite saved history. */
  initializeView() {
    this.nodes.company.textContent = t("通用聊天");
    this.nodes.panel.querySelector('.assistant-development').textContent = t('通用助手');
    document.getElementById('assistant-context-label').textContent = t('当前会话');
    document.getElementById('assistant-welcome-title').textContent = t('有什么想聊的？');
    document.getElementById('assistant-welcome-copy').textContent = t('讨论问题、起草邮件、翻译文字，或一起梳理工作计划。无需选择客户。');
    document.getElementById('assistant-unavailable').textContent = t('通用问答 · 写作 · 计划');
    document.getElementById('assistant-availability-copy').textContent = t('可协助讨论、查询与起草；聊天中的数据写入须经你批准后执行。');
    this.nodes.input.placeholder = t('输入问题，或告诉我你想完成什么…');
    const prompts = [
      [t('起草一封邮件'), t('帮我起草一封专业的商务邮件，请先问我需要哪些信息。')],
      [t('梳理工作计划'), t('帮我梳理今天的工作计划，请先了解我的目标和待办。')],
      [t('解释一个概念'), t('我想了解一个新概念，请用容易理解的方式与我讨论。')],
    ];
    this.nodes.shortcuts.innerHTML = prompts.map(([title, prompt]) => h`<button type="button" data-assistant-prompt="${esc(prompt)}"><span class="assistant-task-icon" aria-hidden="true">✧</span><span><strong>${esc(title)}</strong><small>点击填入草稿</small></span><span aria-hidden="true">↗</span></button>`).join('');
  }

  /** Function: Open or toggle the current chat interface. Inputs: Optional opener defaults to the floating button; implicit current context.
   * Outputs: The read Promise when reading starts, otherwise undefined. Logic: On opening, load persisted conversations/drafts and focus the collapse control; allow reply drafts to wait for loading before insertion.
   * Constraints: Reads never create conversations or trigger analysis. */
  open(opener = null) {
    if (this.isOpen) {
      this.close();
      return;
    }
    this.opener = opener || document.getElementById("assistant-launcher");
    this.isOpen = true;
    this.nodes.panel.hidden = false;
    document.body.classList.add("assistant-open");
    this.syncLayout();
    this.nodes.close.focus();
    console.info("assistant_panel_opened");
    if (this.busy) this.needsLoad = true;
    else return this.run(() => this.load(this.conversation?.id));
  }

  /** Function: Collapse chat and restore background interaction. Inputs: restoreFocus defaults to true.
   * Outputs: None. Logic: Retain page text, cancel observation, close modal semantics, and return focus to the surviving opener or floating entry; restore original background inert state.
   * Constraints: Route changes use false to avoid focusing an element about to be removed. */
  close(restoreFocus = true) {
    this.dismissApproval();
    this.stopPolling();
    const wasOpen = this.isOpen;
    this.isOpen = false;
    this.syncLayout();
    document.body.classList.remove("assistant-open");
    this.nodes.panel.hidden = true;
    if (wasOpen && restoreFocus) {
      const target = this.opener?.isConnected ? this.opener : document.getElementById("assistant-launcher");
      target?.focus();
    }
  }

  /** Function: Synchronize responsive modal semantics. Inputs: isOpen/narrow instance state.
   * Outputs: None. Logic: On narrow screens mark page nodes outside the panel inert and restore them on collapse; desktop pages remain interactive.
   * Constraints: Do not change business context or data. */
  syncLayout() {
    const modal = this.isOpen && this.narrow.matches;
    if (modal) {
      for (const node of document.body.children) {
        if (node === this.nodes.panel || ['SCRIPT', 'STYLE', 'LINK'].includes(node.tagName)) continue;
        if (!this.background.has(node)) this.background.set(node, node.inert);
        node.inert = true;
      }
    } else {
      for (const [node, inert] of this.background) node.inert = inert;
      this.background.clear();
    }
    document.getElementById('assistant-launcher')?.setAttribute('aria-expanded', String(this.isOpen));
    this.nodes.panel.setAttribute("role", modal ? "dialog" : "complementary");
    if (modal) this.nodes.panel.setAttribute("aria-modal", "true");
    else this.nodes.panel.removeAttribute("aria-modal");
    if (modal && !this.nodes.panel.contains(document.activeElement))
      this.nodes.close.focus();
  }

  /** Function: Handle Escape and narrow-screen focus cycling. Inputs: event is a keyboard event.
   * Outputs: None. Logic: Handle only when the floating panel is open and no other native dialog is active.
   * Constraints: Never capture text Enter or interfere with ordinary input. */
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

  /** Function: Clear memory state for the current login session. Inputs: None.
   * Outputs: None. Logic: Invalidate old asynchronous display updates and clear text caches.
   * Constraints: Do not delete server-side conversations or drafts. */
  reset() {
    this.needsLoad = false;
    this.epoch += 1;
    this.drafts.clear();
    this.close(false);
    this.conversation = null;
    this.draft = null;
    this.conversations = [];
    this.answers = [];
    this.nodes.input.value = "";
    this.nodes.history.textContent = "";
    this.nodes.sessions.innerHTML = h('<option value="">尚未选择会话</option>');
    this.initializeView();
  }

  /** Function: Load a specified or most recent workspace conversation. Inputs: selected is an optional conversation identifier.
   * Outputs: None. Logic: Read all conversation/draft pages; read answer status before messages so completed answers are visible. Responses from an old epoch never update the view.
   * Constraints: Prefer unsaved current-page text and explicitly mark it unsaved. */
  async load(selected) {
    this.dismissApproval();
    this.stopPolling();
    this.answers = [];
    const epoch = ++this.epoch;
    const conversations = (
      await allRows(
        "records/conversations/?conversation_scope=general",
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
      // Completion and assistant messages commit in one transaction; read messages afterward so terminal-state polling does not miss the answer.
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
    const key = this.conversation?.id || "new";
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

  /** Function: Establish a conversation during explicit save/create actions. Inputs: Current workspace conversation state.
   * Outputs: The current conversation. Logic: Call creation only when no conversation is selected and preserve existing input.
   * Constraints: Throw when account/conversation changes invalidate the operation; never overwrite the new interface. */
  async ensureConversation() {
    if (this.conversation) return this.conversation;
    const epoch = this.epoch;
    const conversation = await salesRequest("records/conversations/", {
      method: "POST",
      data: { title: t("通用会话") },
    });
    if (epoch !== this.epoch)
      throw new Error(t("会话上下文已切换；原会话已保存，可重新打开查看。"));
    this.conversation = conversation;
    return conversation;
  }

  /** Function: Save a draft or explicitly submit a question. Inputs: asMessage determines whether to request an answer.
   * Outputs: None. Logic: Questions use a stable idempotency key; the backend atomically saves question/task, while drafts use revision-based saves.
   * Constraints: Replace the idempotency key only after successful submission; network failures retain input and key for explicit user retransmission. */
  async save(asMessage) {
    const content = this.nodes.input.value;
    if (asMessage && !content.trim()) throw new Error(t("请先输入消息内容。"));
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
    if (epoch !== this.epoch) return;
    this.drafts.delete("new");
    this.drafts.delete(conversation.id);
    if (asMessage) {
      this.drafts.set(conversation.id, "");
      this.messageKey = crypto.randomUUID();
    }
    await this.load(conversation.id);
    if (!asMessage) this.nodes["draft-note"].textContent = t("草稿已保存到服务器，刷新页面后可以恢复。");
  }

  /** Function: Display actually persisted message history. Inputs: messages array.
   * Outputs: None. Logic: Render only assistant content through assistant-markdown.js; user content and sources remain escaped plain text. Associate messages with status, citations, and failure retries.
   * Constraints: Markdown disables raw HTML and dangerous links; evidence starts collapsed and never executes HTML. Preserve stored source text and generation flow. */
  draw(messages) {
    this.questions = new Map(messages.filter((message) => message.role === "user").map((message) => [message.id, message.content]));
    const byMessage = new Map(this.answers.filter((row) => row.assistant_message_id).map((row) => [row.assistant_message_id, row]));
    const byQuestion = new Map(this.answers.map((row) => [row.user_message_id, row]));
    const labels = { pending: t("等待回答"), processing: t("正在生成回答"), awaiting_approval: t("等待你批准操作"), cancelled: t("已取消待执行操作"), completed: t("回答完成"), failed: t("回答失败") };
    this.nodes.panel.querySelector(".assistant-welcome").hidden = messages.length > 0;
    this.nodes.history.innerHTML = messages.length
      ? messages
          .map((message) => {
            const answer = byMessage.get(message.id), question = byQuestion.get(message.id);
            const sources = answer?.citations || [];
            const citations = sources.length
              ? h`<details class="assistant-sources"><summary>查看依据（${sources.length}）</summary><div class="assistant-source-list">${sources.map((citation) => citation.source_type === "customer_analysis"
                ? h`<div class="assistant-source"><strong>[${esc(citation.position)}] ${esc(citation.title_or_label)}</strong><p class="assistant-source-note">依据来自客户画像与分析，可在客户页面按维度查看。</p></div>`
                : `<details class="assistant-source"><summary>[${esc(citation.position)}] ${esc(citation.title_or_label)}</summary><div class="assistant-source-content">${esc(citation.content)}</div></details>`).join("")}</div></details>`
              : "";
            const state = question ? `<p class="fine">${labels[question.status] || t("状态未知")}${question.error ? `：${esc(question.error.message)}` : ""}</p>${question.status === "failed" ? h`<button type="button" class="text-btn" data-chat-retry="${esc(question.request_id)}">重新回答</button>` : ""}` : "";
            const body = message.role === 'assistant'
              ? `<div class="assistant-markdown">${renderAssistantMarkdown(message.content)}</div>`
              : `<p>${esc(message.content)}</p>`;
            return `<article class="assistant-message"><small>${message.role === "user" ? t("我") : t("助手")} · ${esc(new Date(message.created_at).toLocaleString(locale))}</small>${body}${citations}${state}</article>`;
          })
          .join("")
      : h('<p class="fine">尚无消息。可以先保存草稿，或直接发送问题。</p>');
  }

  /** Function: Cancel current status observation. Inputs: Instance timer, controller, and observation generation.
   * Outputs: None. Logic: Clear the timer, cancel fetch, and invalidate old responses.
   * Constraints: Do not cancel backend answer tasks or change messages. */
  stopPolling() {
    clearTimeout(this.pollTimer);
    this.pollController?.abort();
    this.pollController = null;
    this.pollEpoch += 1;
  }

  /** Function: Observe active requests in the current conversation. Inputs: answers and panel state.
   * Outputs: None. Logic: Awaiting approval opens its persisted review dialog; other active states allow at most 120 reads, two seconds apart, with one read in flight.
   * Constraints: Do not poll while awaiting approval, closed, or without active work; never create model work. */
  watch() {
    this.stopPolling();
    const active = this.answers.find((row) => ["pending", "processing", "awaiting_approval"].includes(row.status));
    this.nodes.submit.disabled = this.busy || Boolean(active);
    if (!active || !this.isOpen) return;
    if (active.status === "awaiting_approval") {
      this.nodes["draft-note"].textContent = t("操作尚未执行，请审阅后批准或拒绝。");
      this.showApproval(active);
      return;
    }
    this.pollCount = 0;
    this.nodes["draft-note"].textContent = active.status === "pending" ? t("问题已提交，等待回答。") : t("正在生成回答。");
    const epoch = this.pollEpoch;
    this.pollTimer = setTimeout(() => this.poll(active.request_id, epoch), 2000);
  }

  /** Function: Read generation status once. Inputs: requestId identifies the bound task; epoch is the observation generation.
   * Outputs: None. Logic: Refresh messages on terminal states and continue bounded observation for active states; errors pause observation and offer manual recovery.
   * Constraints: Discard responses after cancellation, customer changes, or closing; never implicitly retry failed HTTP requests. */
  async poll(requestId, epoch) {
    if (epoch !== this.pollEpoch || !this.isOpen) return;
    this.pollController = new AbortController();
    try {
      const result = await salesRequest(`chat/requests/${requestId}/`, { signal: this.pollController.signal });
      if (epoch !== this.pollEpoch || !this.isOpen) return;
      this.answers = this.answers.map((row) => row.request_id === requestId ? result : row);
      if (["completed", "failed", "cancelled", "awaiting_approval"].includes(result.status)) {
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

  /** Function: Refresh answers while preserving text being edited. Inputs: Current customer, conversation, and epoch.
   * Outputs: None. Logic: Read request status before messages to avoid mixing terminal states with old message snapshots; check bindings and observation generation, and offer explicit recovery if either read fails.
   * Constraints: Preserve drafts and scroll position; an explicit pending approval may move focus into its review dialog. */
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

  /** Function: Show the observation pause reason and an explicit recovery action. Inputs: message is safe display text.
   * Outputs: None. Logic: Cancel observation and display escaped error text without affecting backend tasks.
   * Constraints: Network errors never automatically resubmit questions or resume queries. */
  pausePolling(message) {
    this.stopPolling();
    this.nodes["draft-note"].innerHTML = h`${esc(message)} <button type="button" class="text-btn" data-chat-resume>继续查询</button>`;
    console.warn("assistant_poll_paused");
  }

  /** Function: Explicitly request another answer to a failed question. Inputs: requestId identifies the original failed request.
   * Outputs: None. Logic: The backend creates a new attempt; the frontend reads status again.
   * Constraints: Preserve the original failure record; never invoke automatically from error handling. */
  async retryAnswer(requestId) {
    const epoch = this.epoch;
    await salesRequest(`chat/requests/${requestId}/retry/`, { method: "POST", data: {} });
    if (epoch === this.epoch) await this.refreshAnswers();
  }

  /** Function: Show the exact pending mutation. Inputs: answer is a backend request with its approval.
   * Outputs: None; opens a native dialog with approve/reject buttons and escaped parameters.
   * Logic: Bind the dialog to a request and proposal; no decision is inferred from chat text or a polling result.
   * Constraints: Escape cannot approve or silently reject; closing the panel leaves approval pending on the server. */
  showApproval(answer) {
    const approval = answer.approval;
    if (!approval || this.approvalDialog?.dataset.approvalId === approval.id) return;
    this.dismissApproval();
    const dialog = document.createElement("dialog");
    dialog.className = "assistant-approval";
    dialog.dataset.approvalId = approval.id;
    dialog.setAttribute("aria-labelledby", "assistant-approval-title");
    const operation = { create: t("新增记录"), update: t("修改记录"), delete: t("删除记录") }[approval.tool.split(".").at(-1)] || approval.tool;
    const args = approval.arguments;
    dialog.innerHTML = h`<h3 id="assistant-approval-title">允许执行这项操作？</h3><p>这项操作会实际修改数据。请核对操作、目标和内容。</p><strong>${esc(operation)}</strong><p>实验批次：${esc(args.batch)}<br>数据表：${esc(args.model)}${args.pk ? h`<br>记录：${esc(args.pk)}` : ""}</p>${args.data ? `<pre>${esc(JSON.stringify(args.data, null, 2))}</pre>` : h`<p>将删除上面指定的记录。</p>`}<p>拒绝将取消待执行操作并返回聊天，之前已完成的操作会保留。</p><p class="assistant-approval-error" role="alert"></p><div class="actions"><button type="button" data-decision="reject">拒绝并返回聊天</button><button type="button" data-decision="approve">同意并继续</button></div>`;
    dialog.addEventListener("cancel", (event) => event.preventDefault());
    dialog.addEventListener("click", (event) => {
      const decision = event.target.closest("[data-decision]")?.dataset.decision;
      if (decision) this.run(() => this.decideApproval(answer, decision, dialog));
    });
    this.nodes.panel.append(dialog);
    this.approvalDialog = dialog;
    dialog.showModal();
    dialog.querySelector('[data-decision="reject"]').focus();
  }

  /** Function: Remove the current approval modal. Inputs: approvalDialog instance state.
   * Outputs: None. Logic: Close and detach UI only; server state remains unchanged.
   * Constraints: Used on context changes and successful decisions; never submits an implicit rejection. */
  dismissApproval() {
    this.approvalDialog?.close();
    this.approvalDialog?.remove();
    this.approvalDialog = null;
  }

  /** Function: Submit an explicit browser decision. Inputs: answer identifies the frozen operation, decision is approve/reject, dialog is its current UI.
   * Outputs: None; refreshes authoritative state and resumes polling or restores editable input.
   * Logic: Disable duplicate clicks; send only the decision, preserve the existing question/history, and keep conflicts visible in the dialog.
   * Constraints: Failed or lost responses never imply success and are not automatically retried; stale context responses do not update the current chat. */
  async decideApproval(answer, decision, dialog) {
    const epoch = this.epoch;
    for (const button of dialog.querySelectorAll("button")) button.disabled = true;
    try {
      await salesRequest(`chat/requests/${answer.request_id}/approvals/${answer.approval.id}/decision/`, {
        method: "POST", data: { decision },
      });
      if (epoch !== this.epoch || dialog !== this.approvalDialog) return;
      this.dismissApproval();
      if (decision === "reject" && !this.nodes.input.value.trim()) {
        this.nodes.input.value = this.questions.get(answer.user_message_id) || "";
        this.nodes.input.dispatchEvent(new Event("input"));
      }
      await this.refreshAnswers();
      if (decision === "reject" && epoch === this.epoch && this.isOpen) {
        this.nodes.input.disabled = false;
        this.nodes.input.focus();
      }
    } catch (error) {
      if (epoch === this.epoch && dialog === this.approvalDialog) {
        dialog.querySelector('[role="alert"]').textContent = error.message;
        console.warn("assistant_approval_failed");
      }
    } finally {
      for (const button of dialog.querySelectorAll("button")) button.disabled = false;
    }
  }

  /** Function: Serialize current panel operations and display errors. Inputs: task is an asynchronous callback.
   * Outputs: None. Logic: Disable saving, input, and conversation switching; active tasks continue to prevent new questions after completion, and errors appear in the status area.
   * Constraints: No retries or body logging; the panel can still be closed after changing conversations. */
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
      this.nodes.submit.disabled = this.answers.some((row) => ["pending", "processing", "awaiting_approval"].includes(row.status));
      if (this.needsLoad && this.isOpen) {
        this.needsLoad = false;
        this.run(() => this.load(this.conversation?.id));
      }
    }
  }
}
