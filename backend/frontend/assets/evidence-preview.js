/** Responsibility: Preview the current customer's actual email sources beside analysis-evidence buttons.
 * Implementation: Hover, keyboard focus, and touch clicks open a dismissible overlay; resolve citations only from authorized details and display bodies as plain text.
 * Relationships: The 0919 interface and shared language resources use coordinated cache versions; app.js supplies current details and a source-location callback; app.css supplies overlay styles. No additional API requests.
 * Directory: mountEvidencePreview, mountEvidencePreview.text, mountEvidencePreview.close, mountEvidencePreview.position, mountEvidencePreview.show, mountEvidencePreview.scheduleClose.
 * Variable index: No module state; popup is the preview node, anchor is the source button, timer manages delayed closing, and pinned records click-pinned state.
 */
import { language, locale } from './i18n.js?v=20260921-product';
import { escapeHtml as e } from './api.js?v=20260921-product';

/** Function: Register citation previews within one page. Inputs: root analysis container, getDetail reader, and openSource location callback.
 * Outputs: An overlay-close function for route cleanup. Logic: Match context.emails exactly by dedupe_key, never inventing quoted source sentences from fact summaries.
 * Constraints: Explicitly report unavailable previews for missing emails; never write business data to caches, logs, or external requests. */
export function mountEvidencePreview(root, getDetail, openSource) {
  /** Function: Select interface language. Inputs: zh/en text. Outputs: Current-language text. Logic: Use the shared preference. Constraints: Never translate emails. */
  const text = (zh, en) => language === 'en' ? en : zh;
  const popup = document.createElement('aside');
  popup.id = 'evidence-preview';
  popup.className = 'evidence-preview';
  popup.setAttribute('role', 'dialog');
  popup.setAttribute('aria-label', text('引用来源', 'Evidence source'));
  popup.hidden = true;
  document.body.append(popup);
  let anchor = null, timer = null, pinned = false;
  /** Function: End a preview. Inputs: restoreFocus controls return to the source button. Outputs: None.
   * Logic: Clear timers/expanded state and remove private body text. Constraints: Route changes do not steal focus. */
  function close(restoreFocus = false) {
    clearTimeout(timer);
    const previous = anchor;
    previous?.setAttribute('aria-expanded', 'false');
    popup.hidden = true;
    popup.replaceChildren();
    if (restoreFocus && previous?.isConnected) previous.focus();
    anchor = null; pinned = false;
  }
  /** Function: Position the overlay. Inputs: Implicit anchor, viewport, and overlay dimensions. Outputs: None.
   * Logic: Prefer below the button, move above when space is insufficient, and retain horizontal margins. Constraints: Reposition on scrolling without changing page scroll. */
  function position() {
    if (!anchor?.isConnected || !anchor.getClientRects().length) { close(); return; }
    const box = anchor.getBoundingClientRect();
    popup.style.left = Math.max(12, Math.min(box.left, innerWidth - popup.offsetWidth - 12)) + 'px';
    popup.style.top = Math.max(12, Math.min(box.bottom + 8, innerHeight - popup.offsetHeight - 12)) + 'px';
  }
  /** Function: Display a source from authorized details. Inputs: button is the source button; pin controls pinning. Outputs: None.
   * Logic: Avoid rebuilding the same source to preserve selection; show sender, time, subject, and verbatim text. Constraints: The protocol lacks citation character offsets, so explicitly label this as full text rather than an exact excerpt. */
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
  /** Function: Let the pointer cross the button-overlay gap. Inputs: None; reads pinned and focus. Outputs: None.
   * Logic: Close unpinned previews 180ms after leaving, retaining previews in keyboard use. Constraints: Never delay network or business actions. */
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
