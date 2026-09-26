/**
 * Responsibility: Require employees to explicitly select Gmail synchronization scope each time.
 * Implementation: Intersect day/message limits; an omitted message count defaults to 50. Warn on each oversized submission and include approval only after explicit consent.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; app.js calls this for initial authorization, individual mailbox sync, and refresh; index.html provides a separate Gmail-scope dialog.
 * Directory: readGmailScope validates scope; chooseGmailScope returns the selection or cancellation.
 * Variable index: GMAIL_MESSAGE_LIMIT is the ordinary 50-message limit; selection/approval exist only in the current dialog Promise.
 */
import { t } from './i18n.js?v=20260921-product';

const GMAIL_MESSAGE_LIMIT = 50;

/** Function: Read one explicit sync limit selection. Inputs: form is the current form. Outputs: A day/message-limit object.
 * Logic: Require at least one positive safe integer; selecting days alone sets the message count to 50. Constraints: Reject empty forms and never inherit approval from prior choices. */
function readGmailScope(form) {
  const options = Object.fromEntries(['recent_days', 'max_messages'].map(name => [name, form.elements[name].value === '' ? null : Number(form.elements[name].value)]));
  if (!Object.values(options).some(value => value !== null)) throw new Error(t('请填写最近 N 天或最近 N 封，至少一项。'));
  if (Object.values(options).some(value => value !== null && (!Number.isSafeInteger(value) || value <= 0))) throw new Error(t('同步限制必须为正整数。'));
  if (options.max_messages === null) options.max_messages = GMAIL_MESSAGE_LIMIT;
  return options;
}

/** Function: Wait for a scope selection from a blank form before syncing. Inputs: address is the displayed mailbox address. Outputs: Selected scope, or null on cancellation.
 * Logic: Reset the form each time; submitting over 50 messages shows a warning with the current count and records approval after confirmation. Cancelling the warning retains the form.
 * Constraints: Closing the form sends no request; never reuse approval across submissions/count changes. Day-only selections still have a 50-message limit. */
export function chooseGmailScope(address) {
  const dialog = document.getElementById('gmail-scope-dialog');
  const form = document.getElementById('gmail-scope-form');
  const errorBox = document.getElementById('gmail-scope-error');
  form.reset();
  errorBox.textContent = '';
  document.getElementById('gmail-scope-address').textContent = address;
  return new Promise(resolve => {
    let selected = null;
    form.onsubmit = event => {
      event.preventDefault();
      try {
        const options = readGmailScope(form);
        if (options.max_messages > GMAIL_MESSAGE_LIMIT) {
          const approved = window.confirm(t`本次选择最多 ${options.max_messages} 封邮件，超过默认的 50 封上限，可能长时间占用处理进程并增加分析费用。是否批准本次超量同步？取消后可修改数量。`);
          if (!approved) return;
          options.allow_large_sync = true;
        }
        selected = options;
        dialog.close();
      }
      catch (error) { errorBox.textContent = error.message; }
    };
    dialog.addEventListener('close', () => { form.onsubmit = null; resolve(selected); }, { once: true });
    dialog.showModal();
  });
}

