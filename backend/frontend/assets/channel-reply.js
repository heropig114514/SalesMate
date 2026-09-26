/** Responsibility: Provide a page-local reply draft below the mail conversation and an explicit assistant-polishing entry.
 * Implementation: Keep per-customer drafts in memory; copy through the clipboard; polishing fills the shared assistant, with explicit question submission still required.
 * Relationships: app.js mounts this after the mail list and clears drafts on account changes; actual sending uses the existing reviewed business-action flow.
 * Directory: mountReplyComposer, mountReplyComposer.text.
 * Variable index: No module state; the caller manages drafts; companyId isolates drafts; assistant is the page's single chat instance.
 */
import { language } from './i18n.js?v=20260921-product';

/** Function: Mount a reply draft for the current customer. Inputs: root container, companyId, drafts Map, and assistant instance.
 * Outputs: None. Logic: Build the form once and preserve input focus on updates; show asynchronous copy failures, wait for loading before inserting polishing input, and discard stale insertion after navigation. Never automatically call a model or send mail.
 * Constraints: Drafts live only in page memory and disappear on reload; never automatically add company source content or overwrite existing chat input. */
export function mountReplyComposer(root, companyId, drafts, assistant) {
  if (root.childElementCount) return;
  /** Function: Select static interface text. Inputs: zh/en. Outputs: Current-language text. Logic: Use the shared preference. Constraints: Never translate drafts. */
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
