/**
 * Responsibility: Mount one floating chat entry and panel in the shared workspace.
 * Implementation: A static template reuses AssistantPanel; buttons expand/collapse it, and legacy chat links open the widget without taking over the page.
 * Relationships: Chat Markdown, 0919 interface, and shared language/API resources use coordinated cache versions. workspace.js enables the widget on mounting, with business permissions enforced by the API; app.js handles logout and legacy links; assistant-widget.css supplies cross-page styles.
 * Directory: getAssistant, enableAssistant, openAssistantLink.
 * Variable index: panel is the singleton for the current page.
 */
import { h } from './i18n.js?v=20260921-product';
import { AssistantPanel } from './assistant.js?v=20260921-markdown';

let panel = null;

/** Function: Get or create the floating assistant. Inputs: Current page DOM. Outputs: The unique AssistantPanel.
 * Logic: Mount only a trusted static template and buttons, initially hidden; cancel status observation when leaving the page.
 * Constraints: Do not create conversations, request model inference, or persist business content in browser storage. */
export function getAssistant() {
  if (panel) return panel;
  document.body.insertAdjacentHTML('beforeend', h`<aside aria-labelledby="assistant-title" class="assistant-panel" hidden="" id="assistant-panel" role="complementary">
<header class="assistant-header">
<div class="assistant-heading"><span aria-hidden="true" class="assistant-mark">✧</span><div><h2 id="assistant-title"><span data-i18n="AI 助手">AI 助手</span></h2><span class="assistant-development"><span data-i18n="通用助手">通用助手</span></span></div></div>
<button aria-label="收起 AI 助手" class="icon-btn" data-i18n-aria-label="收起 AI 助手" data-i18n-title="收起（Esc）" id="assistant-close" title="收起（Esc）" type="button">−</button>
</header>
<div class="assistant-context"><span aria-hidden="true" class="assistant-context-icon">▤</span><div><span id="assistant-context-label"><span data-i18n="当前会话">当前会话</span></span><strong id="assistant-company"><span data-i18n="通用聊天">通用聊天</span></strong></div></div>
<div class="assistant-body"><div class="assistant-session-controls"><label class="sr-only" for="assistant-sessions"><span data-i18n="选择历史会话">选择历史会话</span></label><select id="assistant-sessions"><option data-i18n="尚未选择会话" value="">尚未选择会话</option></select><button class="text-btn" id="assistant-new" type="button"><span data-i18n="新建会话">新建会话</span></button></div><div aria-live="polite" id="assistant-history"></div>
<section aria-label="助手介绍" class="assistant-welcome" data-i18n-aria-label="助手介绍"><span aria-hidden="true" class="assistant-welcome-mark">✧</span><p class="eyebrow">YOUR NEXT CONVERSATION</p><h3 id="assistant-welcome-title"><span data-i18n="有什么想聊的？">有什么想聊的？</span></h3><p id="assistant-welcome-copy"><span data-i18n="讨论问题、起草邮件、翻译文字，或一起梳理工作计划。无需选择客户。">讨论问题、起草邮件、翻译文字，或一起梳理工作计划。无需选择客户。</span></p></section>
<div class="assistant-shortcut-heading"><span><span data-i18n="从一个问题开始">从一个问题开始</span></span><small><span data-i18n="点击填入草稿">点击填入草稿</span></small></div>
<div class="assistant-shortcuts" id="assistant-shortcuts"></div>
<div class="assistant-availability"><span aria-hidden="true">◷</span><p><span id="assistant-availability-copy"><span data-i18n="可协助讨论与起草。">可协助讨论与起草。</span></span><span data-i18n="发送邮件等操作请前往"> 发送邮件等操作请前往 </span><a href="/business/#actions"><span data-i18n="业务管理 → 外部动作">业务管理 → 外部动作</span></a><span data-i18n="审阅并确认。"> 审阅并确认。</span></p></div>
</div>
<form class="assistant-composer" id="assistant-form">
<div class="assistant-composer-heading"><label for="assistant-input"><span data-i18n="消息草稿">消息草稿</span></label><button class="text-btn" id="assistant-clear" type="button"><span data-i18n="清空草稿">清空草稿</span></button></div>
<div class="assistant-input-box"><textarea aria-describedby="assistant-unavailable assistant-draft-note" data-i18n-placeholder="输入问题，或告诉我你想完成什么…" id="assistant-input" placeholder="输入问题，或告诉我你想完成什么…" rows="3"></textarea><div class="assistant-send-row"><span id="assistant-unavailable"><span data-i18n="通用问答 · 写作 · 计划">通用问答 · 写作 · 计划</span></span><button class="secondary" id="assistant-save" type="button"><span data-i18n="保存草稿">保存草稿</span></button><button class="primary" id="assistant-submit" type="submit"><span data-i18n="发送问题">发送问题</span></button></div></div>
<p class="assistant-draft-note" id="assistant-draft-note" role="status"><span data-i18n="保存后可跨刷新恢复；未保存修改仅在当前页面保留。">保存后可跨刷新恢复；未保存修改仅在当前页面保留。</span></p>
</form>
</aside>
<button id="assistant-launcher" class="assistant-launcher" type="button" aria-controls="assistant-panel" aria-expanded="false" hidden><span aria-hidden="true">✧</span><span>聊天助手</span></button>`);
  panel = new AssistantPanel();
  panel.initializeView();
  document.getElementById('assistant-launcher').onclick = event => {
    panel.open(event.currentTarget);
  };
  window.addEventListener('pagehide', () => { panel.close(false); });
  return panel;
}

/** Function: Control floating-entry availability for the login state. Inputs: enabled defaults to true. Outputs: None.
 * Logic: Enabling does not open chat; disabling clears current-account memory state and hides the entry.
 * Constraints: The API always enforces permissions; unsaved drafts are not shared across pages. */
export function enableAssistant(enabled = true) {
  const current = getAssistant();
  document.getElementById('assistant-launcher').hidden = !enabled;
  if (!enabled) { current.reset(); }
}

/** Function: Open workspace chat from a legacy chat link. Inputs: None. Outputs: None.
 * Logic: Always show the workspace conversation; company identifiers in legacy URLs neither enter requests nor determine context.
 * Constraints: Never read customer details, create tasks, or redispatch old questions. */
export function openAssistantLink() {
  const current = getAssistant();
  if (!current.isOpen) current.open(document.getElementById('assistant-launcher'));
}
