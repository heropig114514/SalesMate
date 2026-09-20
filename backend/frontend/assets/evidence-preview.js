/** 职责：在分析依据按钮旁预览当前客户的真实邮件来源。
 * 实现：悬停、键盘聚焦及触摸点击打开可关闭浮层；仅从已授权详情解析引用，正文作为纯文本显示。
 * 关联：0919 界面及共享语言资源统一缓存版本；app.js 提供当前详情及原文定位回调；app.css 提供弹层样式；不新增 API 请求。
 * 目录：mountEvidencePreview、mountEvidencePreview.text、mountEvidencePreview.close、mountEvidencePreview.position、mountEvidencePreview.show、mountEvidencePreview.scheduleClose。
 * 变量索引：无模块状态；实例 popup 为预览节点，anchor 为来源按钮，timer 为离开延迟，pinned 为点击固定状态。
 */
import { language, locale } from './i18n.js?v=20260921-product';
import { escapeHtml as e } from './api.js?v=20260921-product';

/** 功能：注册一个页面内的引用预览。输入：root 分析容器、getDetail 当前详情读取器、openSource 原文定位回调。
 * 输出：关闭浮层函数，供路由切换清理。逻辑：从 context.emails 按 dedupe_key 精确匹配，不从事实概括臆造引用原句。
 * 约束：缺失邮件明确说明无法预览；不将业务资料写入缓存、日志或外部请求。 */
export function mountEvidencePreview(root, getDetail, openSource) {
  /** 功能：选择界面语言。输入：zh/en 文案。输出：当前语言文本。逻辑：沿用共享偏好。约束：不翻译邮件。 */
  const text = (zh, en) => language === 'en' ? en : zh;
  const popup = document.createElement('aside');
  popup.id = 'evidence-preview';
  popup.className = 'evidence-preview';
  popup.setAttribute('role', 'dialog');
  popup.setAttribute('aria-label', text('引用来源', 'Evidence source'));
  popup.hidden = true;
  document.body.append(popup);
  let anchor = null, timer = null, pinned = false;
  /** 功能：结束预览。输入：restoreFocus 是否返回来源按钮。输出：无。
   * 逻辑：清理延迟和展开状态、删除私有正文。约束：路由切换不抢焦点。 */
  function close(restoreFocus = false) {
    clearTimeout(timer);
    const previous = anchor;
    previous?.setAttribute('aria-expanded', 'false');
    popup.hidden = true;
    popup.replaceChildren();
    if (restoreFocus && previous?.isConnected) previous.focus();
    anchor = null; pinned = false;
  }
  /** 功能：定位浮层。输入：隐式 anchor、视口及弹层尺寸。输出：无。
   * 逻辑：优先放按钮下方，空间不足时向上；左右保留边距。约束：滚动时更新位置，不改变页面滚动。 */
  function position() {
    if (!anchor?.isConnected || !anchor.getClientRects().length) { close(); return; }
    const box = anchor.getBoundingClientRect();
    popup.style.left = Math.max(12, Math.min(box.left, innerWidth - popup.offsetWidth - 12)) + 'px';
    popup.style.top = Math.max(12, Math.min(box.bottom + 8, innerHeight - popup.offsetHeight - 12)) + 'px';
  }
  /** 功能：显示授权详情中的来源。输入：button 来源按钮、pin 是否固定。输出：无。
   * 逻辑：同一来源不重建以保留选区；显示发件人、时间、主题和逐字原文。约束：原协议没有引用字符偏移，因此明确标记全文而非精确摘录。 */
  function show(button, pin = false) {
    clearTimeout(timer);
    if (button === anchor) { pinned ||= pin; return; }
    close(); anchor = button; pinned = pin;
    const ref = button.dataset.ref;
    const mail = getDetail()?.context?.emails?.find(item => item.dedupe_key === ref);
    const timestamp = mail?.sent_at || mail?.received_at;
    const date = timestamp && !Number.isNaN(Date.parse(timestamp)) ? new Date(timestamp).toLocaleString(locale, { hour12: false }) : text('时间未知', 'Time unavailable');
    popup.innerHTML = `<header><strong>${text('引用来源', 'Evidence source')}</strong><button type="button" data-evidence-close aria-label="${text('关闭引用预览', 'Close evidence preview')}">×</button></header>${mail ? `<dl><dt>${text('发件人', 'Sender')}</dt><dd>${e(mail.from || mail.sender || mail.mailbox_address || text('未知发件人', 'Unknown sender'))}</dd><dt>${mail.sent_at ? text('发送时间', 'Sent at') : text('接收时间', 'Received at')}</dt><dd>${e(date)}</dd><dt>${text('主题', 'Subject')}</dt><dd>${e(mail.subject || text('无主题', 'No subject'))}</dd></dl><p class="fine">${text('邮件原文 · 当前引用未提供精确段落位置', 'Original email · Exact excerpt location is not provided')}</p><pre>${e(mail.body_text || text('没有可显示的正文。', 'No email body available.'))}</pre><button type="button" class="secondary" data-evidence-open>${text('在邮件往来中查看', 'Show in email history')}</button>` : `<p>${text('当前详情未包含这条引用的邮件原文，可能来自其他业务记录。', 'This reference has no email body in the current detail and may refer to another business record.')}</p><code>${e(ref)}</code>`}`;
    popup.querySelector('[data-evidence-close]').onclick = () => close(true);
    const open = popup.querySelector('[data-evidence-open]');
    if (open) open.onclick = () => { close(); openSource(ref); };
    button.setAttribute('aria-expanded', 'true');
    button.setAttribute('aria-controls', popup.id);
    popup.hidden = false; position();
  }
  /** 功能：允许指针跨越按钮与浮层间隙。输入：无，读取 pinned 与焦点。输出：无。
   * 逻辑：非固定预览在离开 180ms 后关闭；保留键盘正在操作的预览。约束：不延迟网络或业务动作。 */
  function scheduleClose() {
    clearTimeout(timer);
    if (!pinned) timer = setTimeout(() => {
      if (!popup.contains(document.activeElement) && document.activeElement !== anchor) close();
    }, 180);
  }
  root.addEventListener('pointerover', event => { const button = event.target.closest('[data-ref]'); if (button) show(button); });
  root.addEventListener('focusin', event => { const button = event.target.closest('[data-ref]'); if (button) show(button); });
  root.addEventListener('pointerout', event => { if (event.target.closest('[data-ref]')) scheduleClose(); });
  root.addEventListener('focusout', scheduleClose);
  root.addEventListener('click', event => { const button = event.target.closest('[data-ref]'); if (button) { event.preventDefault(); show(button, true); popup.querySelector('button').focus(); } });
  popup.addEventListener('pointerenter', () => clearTimeout(timer));
  popup.addEventListener('pointerleave', scheduleClose);
  popup.addEventListener('focusout', scheduleClose);
  document.addEventListener('pointerdown', event => { if (!popup.contains(event.target) && !event.target.closest('[data-ref]')) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !popup.hidden) { event.preventDefault(); close(true); } });
  window.addEventListener('resize', () => { if (!popup.hidden) position(); });
  document.addEventListener('scroll', () => { if (!popup.hidden) position(); }, true);
  return close;
}
