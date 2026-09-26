/**
 * Responsibility: Manage operation notifications, reading pauses, and dismissal lifetimes.
 * Implementation: Show the latest notification only and cancel old timers on replacement; hover, focus, and background visibility pause the countdown.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; app.js calls Notice.show and app.css supplies compact bottom layout. Request/business failure states remain unchanged.
 * Directory: Notice, Notice.constructor, Notice.show, Notice.dismiss, Notice.pause, Notice.resume.
 * Variable index: DURATION sets success/error reading durations; box/message/close hold DOM nodes;
 * timer/deadline/remaining manage remaining display time in milliseconds.
 */
import { t } from './i18n.js?v=20260921-product';

const DURATION = { success: 4000, error: 8000 };

/** Function: Manage display lifetime for one page notification container.
 * Logic: Register listeners once and reuse text nodes/the close button. Constraints: One instance per container. */
export class Notice {
  /** Function: Initialize readable, dismissible notifications. Inputs: box is a persistent page container. Outputs: A Notice instance.
   * Logic: Render plain text; the button does not submit forms; focus/hover/visibility events manage timing.
   * Constraints: Listeners live for the page lifetime; never steal keyboard focus or insert server HTML. */
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
    this.close.setAttribute('aria-label', t('关闭提示'));
    box.setAttribute('aria-atomic', 'true');
    box.replaceChildren(this.message, this.close);
    this.close.addEventListener('click', () => this.dismiss());
    box.addEventListener('pointerenter', () => this.pause());
    box.addEventListener('pointerleave', () => this.resume());
    box.addEventListener('focusin', () => this.pause());
    box.addEventListener('focusout', () => this.resume());
    document.addEventListener('visibilitychange', () => document.hidden ? this.pause() : this.resume());
  }

  /** Function: Replace the current notification and start its countdown. Inputs: message text and error flag. Outputs: None.
   * Logic: Clear old timing, then set status semantics, text, and reading duration.
   * Constraints: Success does not interrupt assistive reading; errors use alert. Preserve the page's business failure state. */
  show(message, error = true) {
    this.dismiss();
    this.box.className = error ? 'notice error' : 'notice success';
    this.box.setAttribute('role', error ? 'alert' : 'status');
    this.message.textContent = message;
    this.remaining = error ? DURATION.error : DURATION.success;
    this.box.hidden = false;
    this.resume();
  }

  /** Function: Dismiss the current notification and cancel timing. Inputs: Instance timer. Outputs: None.
   * Logic: Hide the container and release pending callbacks. Constraints: Repeated calls are safe and never clear business data. */
  dismiss() {
    clearTimeout(this.timer);
    this.timer = null;
    this.box.hidden = true;
  }

  /** Function: Pause the reading countdown. Inputs: Current deadline and monotonic clock. Outputs: None.
   * Logic: Save remaining time only while timing, avoiding duplicate deductions on repeated pauses. Constraints: Never reset to a full new period. */
  pause() {
    if (this.timer === null) return;
    this.remaining = Math.max(0, this.deadline - performance.now());
    clearTimeout(this.timer);
    this.timer = null;
  }

  /** Function: Resume timing when the page is visible and the user is not reading. Inputs: Container hover/focus and page visibility. Outputs: None.
   * Logic: Resume remaining time with one timer; focus checks also protect the target during blur events.
   * Constraints: Do not schedule duplicate callbacks while hidden or already timing. */
  resume() {
    if (this.box.hidden || this.timer !== null || document.hidden || this.box.matches(':hover, :focus-within')) return;
    this.deadline = performance.now() + this.remaining;
    this.timer = setTimeout(() => this.dismiss(), this.remaining);
  }
}
