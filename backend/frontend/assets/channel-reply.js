/** 职责：在邮件会话底部提供本页回复草稿及显式助手润色入口。
 * 实现：按客户保存内存草稿；复制使用剪贴板；润色仅填入共享助手，用户仍需明确发送问题。
 * 关联：app.js 在邮件列表后挂载，切换账号清空 drafts；实际发信使用已有业务动作审阅流程。
 * 目录：mountReplyComposer、mountReplyComposer.text。
 * 变量索引：无模块状态；drafts 由调用方管理，companyId 为草稿隔离键；assistant 为当前页面唯一聊天实例。
 */
import { language } from './i18n.js?v=20260921-product';

/** 功能：挂载当前客户的回复草稿。输入：root 容器、companyId 客户标识、drafts Map、assistant 实例。
 * 输出：无。逻辑：首次建表单、后续保留输入焦点；异步复制失败明确显示，润色等待读取完成后填入，路由切换舍弃过期填入；不会自动调用模型或发送邮件。
 * 约束：草稿仅在本页内存中，刷新后丢失；公司原文不自动加入助手，不覆盖现有聊天输入。 */
export function mountReplyComposer(root, companyId, drafts, assistant) {
  if (root.childElementCount) return;
  /** 功能：选择静态界面文案。输入：zh/en。输出：当前语言文本。逻辑：共享偏好。约束：不翻译草稿。 */
  const text = (zh, en) => language === 'en' ? en : zh;
  root.innerHTML = `<label for="channel-reply">${text('回复草稿', 'Reply draft')}</label><textarea id="channel-reply" rows="5" maxlength="2000" placeholder="${text('写下回复，或交给 AI 助手润色…', 'Write a reply, or refine it with the AI assistant…')}"></textarea><div class="channel-reply-actions"><small id="channel-reply-count"></small><button type="button" data-reply-copy>${text('复制草稿', 'Copy draft')}</button><button type="button" data-reply-refine>${text('AI 润色', 'Refine with AI')}</button></div><p class="fine" role="status">${text('草稿仅保留在本页；发送邮件请使用下方“准备沟通动作”。', 'Draft stays on this page. To send an email, use Prepare communication below.')}</p>`;
  const input = root.querySelector('textarea'), count = root.querySelector('small'), status = root.querySelector('[role="status"]');
  input.value = drafts.get(companyId) || '';
  count.textContent = `${input.value.length} / 2000`;
  input.oninput = () => { drafts.set(companyId, input.value); count.textContent = `${input.value.length} / 2000`; };
  root.querySelector('[data-reply-copy]').onclick = async () => {
    if (!input.value.trim()) { status.textContent = text('请先填写回复。', 'Write a reply first.'); return; }
    try { await navigator.clipboard.writeText(input.value); status.textContent = text('草稿已复制，可在邮件草稿中使用。', 'Draft copied for use in your email draft.'); }
    catch (error) { console.error('channel_reply_copy_failed', { name: error.name }); status.textContent = text('无法访问剪贴板，请手动选择并复制。', 'Clipboard access failed. Select and copy the text manually.'); }
  };
  root.querySelector('[data-reply-refine]').onclick = async () => {
    if (!input.value.trim()) { status.textContent = text('请先填写回复。', 'Write a reply first.'); return; }
    if (assistant.busy) { status.textContent = text('助手正在读取或保存，请稍后再试。', 'The assistant is loading or saving. Try again when it finishes.'); return; }
    if (!assistant.isOpen) await assistant.open(root.querySelector('[data-reply-refine]'));
    if (!root.isConnected || !assistant.isOpen) return;
    const target = assistant.nodes.input;
    target.value += (target.value ? '\n\n' : '') + text('请润色下面的邮件草稿，不添加未经确认的信息：\n', 'Please refine this email draft without adding unconfirmed information:\n') + input.value;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.focus();
    status.textContent = text('已填入助手，确认后点击“发送问题”。', 'Added to the assistant. Review it and choose Send question.');
  };
}
