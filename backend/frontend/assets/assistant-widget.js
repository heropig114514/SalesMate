/**
 * 职责：在共享工作空间挂载唯一的悬浮聊天入口与面板。
 * 实现：静态模板复用 AssistantPanel；按钮展开/收起，旧聊天链接只打开浮窗，不占用页面。
 * 关联：workspace.js 挂载时启用，业务权限由 API 校验；app.js 处理会话退出与旧链接；assistant-widget.css 提供跨页样式。
 * 目录：getAssistant、enableAssistant、openAssistantLink、cancelAssistantLink。
 * 变量索引：panel 为当前页面单例；controller/generation 隔离旧客户链接读取。
 */
import { h } from './i18n.js?v=20260920-i18n';
import { request } from './api.js';
import { AssistantPanel } from './assistant.js?v=20260920-floating';

let panel = null, controller = null, generation = 0;

/** 功能：获取或创建悬浮助手。输入：当前页面 DOM。输出：唯一 AssistantPanel。
 * 逻辑：仅挂载可信静态模板和按钮，初始隐藏；页面离开时取消观察与链接读取。
 * 约束：不创建会话、不请求模型，不把业务正文存入浏览器持久化存储。 */
export function getAssistant() {
  if (panel) return panel;
  document.body.insertAdjacentHTML('beforeend', h`<aside aria-labelledby="assistant-title" class="assistant-panel" hidden="" id="assistant-panel" role="complementary">
<header class="assistant-header">
<div class="assistant-heading"><span aria-hidden="true" class="assistant-mark">✧</span><div><h2 id="assistant-title"><span data-i18n="AI 助手">AI 助手</span></h2><span class="assistant-development"><span data-i18n="通用助手">通用助手</span></span></div></div>
<button aria-label="收起 AI 助手" class="icon-btn" data-i18n-aria-label="收起 AI 助手" data-i18n-title="收起（Esc）" id="assistant-close" title="收起（Esc）" type="button">−</button>
</header>
<div class="assistant-context"><span aria-hidden="true" class="assistant-context-icon">▤</span><div><span id="assistant-context-label"><span data-i18n="当前会话">当前会话</span></span><strong id="assistant-company"><span data-i18n="通用聊天">通用聊天</span></strong></div><button id="assistant-general" type="button" hidden>通用聊天</button></div>
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
  panel.setContext(null);
  document.getElementById('assistant-launcher').onclick = event => {
    cancelAssistantLink();
    panel.open(event.currentTarget);
  };
  window.addEventListener('pagehide', () => { cancelAssistantLink(); panel.close(false); });
  return panel;
}

/** 功能：控制登录态下的悬浮入口。输入：enabled 是否可用，默认 true。输出：无。
 * 逻辑：启用不打开聊天；停用清理当前账号的内存状态并隐藏入口。
 * 约束：权限始终由 API 验证；不同页面不共享未保存草稿。 */
export function enableAssistant(enabled = true) {
  const current = getAssistant();
  document.getElementById('assistant-launcher').hidden = !enabled;
  if (!enabled) { cancelAssistantLink(); current.reset(); }
}

/** 功能：取消已过期的旧链接读取。输入：模块请求状态。输出：无。
 * 逻辑：递增代次并中止请求，防止后到响应切换当前会话。
 * 约束：不取消后端回答、不清除草稿。 */
export function cancelAssistantLink() {
  ++generation;
  controller?.abort(); controller = null;
}

/** 功能：将已有聊天链接转换为浮窗。输入：companyId 可空授权客户标识。输出：Promise。
 * 逻辑：通用模式只读会话；客户链接先读取授权资料，读取失败交由页面显示。
 * 约束：失败不切为通用聊天，过期响应不覆盖用户新操作。 */
export async function openAssistantLink(companyId = null) {
  cancelAssistantLink();
  const currentGeneration = generation;
  let context = null;
  if (companyId) {
    controller = new AbortController();
    try {
      const company = await request(`companies/${encodeURIComponent(companyId)}/`, { signal: controller.signal });
      context = { id: company.company_id, name: company.company_name || company.domains[0] };
    } catch (error) {
      if (error.name === 'AbortError' || currentGeneration !== generation) return;
      console.error('assistant_link_failed', { errorType: error.name });
      throw error;
    }
  }
  if (currentGeneration !== generation) return;
  const current = getAssistant();
  current.setContext(context);
  if (!current.isOpen) current.open(document.getElementById('assistant-launcher'));
}
