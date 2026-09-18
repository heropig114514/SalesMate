/**
 * 职责：管理页面操作提示的显示、阅读暂停与关闭生命周期。
 * 实现：只显示最新提示，替换时取消旧计时器；悬停、焦点和后台页面暂停倒计时。
 * 关联：app.js 调用 Notice.show，app.css 控制底部紧凑布局；不改变请求或业务失败状态。
 * 目录：Notice、Notice.constructor、Notice.show、Notice.dismiss、Notice.pause、Notice.resume。
 * 变量索引：DURATION 为成功/错误提示阅读时长；实例 box/message/close 保存 DOM，
 * timer/deadline/remaining 管理当前提示的剩余显示时间，单位为毫秒。
 */
const DURATION = { success: 4000, error: 8000 };

/** 功能：为单个页面提示容器管理显示周期。
 * 逻辑：事件监听只注册一次，复用文本节点与关闭按钮。约束：每个容器只创建一个实例。 */
export class Notice {
  /** 功能：初始化可读、可关闭的提示。输入：box 为页面持久容器。输出：Notice 实例。
   * 逻辑：纯文本呈现；按钮不提交表单，焦点/悬停/可见性事件管理计时。
   * 约束：监听器与页面同寿命，不抢占键盘焦点，不插入服务端 HTML。 */
  constructor(box) {
    this.box = box;
    this.timer = null;
    this.deadline = 0;
    this.remaining = 0;
    this.message = document.createElement('span');
    this.message.className = 'notice-message';
    this.close = document.createElement('button');
    this.close.type = 'button';
    this.close.textContent = '×';
    this.close.setAttribute('aria-label', '关闭提示');
    box.setAttribute('aria-atomic', 'true');
    box.replaceChildren(this.message, this.close);
    this.close.addEventListener('click', () => this.dismiss());
    box.addEventListener('pointerenter', () => this.pause());
    box.addEventListener('pointerleave', () => this.resume());
    box.addEventListener('focusin', () => this.pause());
    box.addEventListener('focusout', () => this.resume());
    document.addEventListener('visibilitychange', () => document.hidden ? this.pause() : this.resume());
  }

  /** 功能：替换当前提示并开始独立倒计时。输入：message 为文本，error 表示失败。输出：无。
   * 逻辑：先清理旧计时，再设置状态语义、文本和阅读时长。
   * 约束：成功不打断辅助阅读，错误使用 alert；保留页面原有业务失败状态。 */
  show(message, error = true) {
    this.dismiss();
    this.box.className = error ? 'notice error' : 'notice success';
    this.box.setAttribute('role', error ? 'alert' : 'status');
    this.message.textContent = message;
    this.remaining = error ? DURATION.error : DURATION.success;
    this.box.hidden = false;
    this.resume();
  }

  /** 功能：关闭当前提示并取消计时。输入：实例计时器。输出：无。
   * 逻辑：隐藏容器，释放尚未触发的回调。约束：重复调用安全，不清理业务数据。 */
  dismiss() {
    clearTimeout(this.timer);
    this.timer = null;
    this.box.hidden = true;
  }

  /** 功能：暂停阅读倒计时。输入：当前 deadline 与单调时钟。输出：无。
   * 逻辑：仅在计时中保存剩余时间，避免多重暂停重复扣减。约束：不延长为新的完整周期。 */
  pause() {
    if (this.timer === null) return;
    this.remaining = Math.max(0, this.deadline - performance.now());
    clearTimeout(this.timer);
    this.timer = null;
  }

  /** 功能：在用户未阅读且页面可见时恢复倒计时。输入：容器悬停/焦点、页面可见性。输出：无。
   * 逻辑：从剩余时间开始，只允许一个计时器；失去焦点事件中的目标仍受焦点判定保护。
   * 约束：隐藏或已有计时器时不重复安排回调。 */
  resume() {
    if (this.box.hidden || this.timer !== null || document.hidden || this.box.matches(':hover, :focus-within')) return;
    this.deadline = performance.now() + this.remaining;
    this.timer = setTimeout(() => this.dismiss(), this.remaining);
  }
}
