/**
 * 职责：让员工每次明确选择 Gmail 同步范围。
 * 实现：天数与封数取交集，未填封数默认 50；超量提交逐次警告，明确批准后才携带批准字段。
 * 关联：app.js 在首次授权、单邮箱同步和刷新入口调用；index.html 提供独立 Gmail 范围弹窗。
 * 目录：readGmailScope 校验范围；chooseGmailScope 返回选择或取消结果。
 * 变量索引：GMAIL_MESSAGE_LIMIT 为普通同步的 50 封上限；选择和批准只保存在本次弹窗 Promise 中。
 */
import { t } from './i18n.js?v=20260920-i18n';

const GMAIL_MESSAGE_LIMIT = 50;

/** 功能：读取一次明确的同步限制。输入：form 为本次表单。输出：天数和封数对象。
 * 逻辑：至少选择一项正安全整数；只选天数时封数设为 50。约束：空表单仍拒绝，不从历史选择继承批准。 */
function readGmailScope(form) {
  const options = Object.fromEntries(['recent_days', 'max_messages'].map(name => [name, form.elements[name].value === '' ? null : Number(form.elements[name].value)]));
  if (!Object.values(options).some(value => value !== null)) throw new Error(t('请填写最近 N 天或最近 N 封，至少一项。'));
  if (Object.values(options).some(value => value !== null && (!Number.isSafeInteger(value) || value <= 0))) throw new Error(t('同步限制必须为正整数。'));
  if (options.max_messages === null) options.max_messages = GMAIL_MESSAGE_LIMIT;
  return options;
}

/** 功能：同步前等待用户选择空白范围。输入：address 为展示用邮箱地址。输出：所选范围，取消返回 null。
 * 逻辑：每次重置表单；提交超过 50 封时弹出含本次数量的警告，确认后记录批准；取消警告保留表单。
 * 约束：关闭表单不发请求，批准不跨提交或数量变化复用；只选天数仍受 50 封限制。 */
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

