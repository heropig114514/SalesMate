/**
 * Responsibility: Continuously read the current customer's results and update details while preserving nodes.
 * Implementation: Serial GET polling, generation isolation, and explicit pauses on failure; reuse DOM by stable keys and restore reading anchors.
 * Relationships: app.js supplies authorized requests, rendering, and errors; this module never submits analysis, retries business operations, or saves drafts.
 * Directory: nodeKey, sameKind, patchNode, patchChildren, patchHTML, preserveReading,
 * DetailObserver, DetailObserver.constructor, DetailObserver.start, DetailObserver.stop, DetailObserver.tick.
 * Variable index: No module state; DetailObserver read/apply/fail are callbacks; interval retains the existing 3000ms period;
 * epoch isolates invalidated requests; timer stores the scheduled timer; companyId identifies the current customer.
 */

/** Function: Get a rendering node's stable identity. Inputs: node. Outputs: A key or empty string.
 * Logic: Prefer DOM id, then natural email keys or explicit presentation keys. Constraints: Never use email bodies as identity. */
function nodeKey(node) {
  return node.nodeType === Node.ELEMENT_NODE ? node.id || node.getAttribute('data-email-ref') || node.getAttribute('data-live-key') || '' : '';
}

/** Function: Determine whether a node can be updated in place. Inputs: current and next. Outputs: Boolean.
 * Logic: Compare node type, tag, and stable key. Constraints: Different emails never share identity even with matching tags. */
function sameKind(current, next) {
  return current && current.nodeType === next.nodeType && current.nodeName === next.nodeName && nodeKey(current) === nodeKey(next);
}

/** Function: Synchronize one existing node. Inputs: current and next. Outputs: None.
 * Logic: Modify changed text/attributes only, then recurse into children; independent renderers own data-live-preserve content.
 * Constraints: Trusted templates and escaped data only; never manage form input values or replace unchanged text-selection nodes. */
function patchNode(current, next) {
  if (current.nodeType !== Node.ELEMENT_NODE) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return;
  }
  for (const attribute of [...current.attributes]) {
    if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  }
  for (const attribute of next.attributes) {
    if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
  }
  if (!next.hasAttribute('data-live-preserve')) patchChildren(current, next);
}

/** Function: Synchronize children by stable identity. Inputs: current and next containers. Outputs: None.
 * Logic: Reorder keyed nodes while preserving DOM; match unkeyed nodes by position/type and remove obsolete trailing nodes.
 * Constraints: Exclude the independent assistant and edit dialogs; never execute string-based scripts. */
function patchChildren(current, next) {
  const desired = [...next.childNodes];
  desired.forEach((node, index) => {
    let previous = current.childNodes[index];
    if (nodeKey(node) && !sameKind(previous, node)) {
      const matching = [...current.childNodes].find(item => sameKind(item, node));
      if (matching) { current.insertBefore(matching, previous || null); previous = matching; }
      else { const inserted = node.cloneNode(true); current.insertBefore(inserted, previous || null); previous = inserted; }
    }
    if (sameKind(previous, node)) patchNode(previous, node);
    else if (previous) current.replaceChild(node.cloneNode(true), previous);
    else current.append(node.cloneNode(true));
  });
  while (current.childNodes.length > desired.length) current.lastChild.remove();
}

/** Function: Update a local container from a trusted template. Inputs: container and html. Outputs: None.
 * Logic: Parse in an offscreen template and reuse existing nodes. Constraints: Callers must escape every untrusted field. */
export function patchHTML(container, html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  patchChildren(container, template.content);
}

/** Function: Preserve reading position during partial updates. Inputs: root and synchronous update callback. Outputs: None.
 * Logic: Record visible anchors and scroll-container positions, then compensate for height changes; without anchors, preserve page coordinates.
 * Constraints: Never restore business-deleted text or touch assistant input/focus; browser maximum scroll limits still apply. */
export function preserveReading(root, update) {
  const x = window.scrollX, y = window.scrollY;
  const anchor = [...root.querySelectorAll('[data-email-ref], [data-live-key]')].find(node => {
    const rect = node.getBoundingClientRect();
    return rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight;
  });
  const top = anchor?.getBoundingClientRect().top;
  const scrollers = [root, ...root.querySelectorAll('.mail-panel, .analysis-panel, .context-panel')].map(node => [node, node.scrollTop, node.scrollLeft]);
  update();
  for (const [node, vertical, horizontal] of scrollers) { node.scrollTop = vertical; node.scrollLeft = horizontal; }
  window.scrollTo(x, y + (anchor?.isConnected ? anchor.getBoundingClientRect().top - top : 0));
}

/** Function: Independently observe a customer's complete results. Logic: Start the next timer only after one GET completes, preventing parallel accumulation.
 * Constraints: Independent of mailbox-batch completion; leaving, restarting, and failure invalidate old responses. */
export class DetailObserver {
  /** Function: Construct an observer. Inputs: read/apply/fail callbacks and interval in milliseconds. Outputs: An instance.
   * Logic: Store callbacks and the initial generation; default to the existing three-second page-check interval. Constraints: Construction sends no request. */
  constructor({ read, apply, fail, interval = 3000 }) {
    this.read = read; this.apply = apply; this.fail = fail; this.interval = interval;
    this.epoch = 0; this.timer = null; this.companyId = null;
  }

  /** Function: Continue observation after the initial GET. Inputs: companyId. Outputs: None.
   * Logic: Discard the old generation and read the current customer after the interval. Constraints: Schedule reads only; never automatically create/retry analysis. */
  start(companyId) {
    this.stop();
    this.companyId = companyId;
    const epoch = this.epoch;
    this.timer = setTimeout(() => this.tick(companyId, epoch), this.interval);
  }

  /** Function: Cancel subsequent checks and invalidate in-flight responses. Inputs: None; reads instance state. Outputs: None.
   * Logic: Clear timer, increment epoch, and unbind the customer. Constraints: Already-sent read-only HTTP calls may finish but cannot render. */
  stop() {
    clearTimeout(this.timer); this.timer = null; this.companyId = null; this.epoch += 1;
  }

  /** Function: Read and apply one observation cycle. Inputs: companyId and epoch. Outputs: Promise<void>.
   * Logic: Validate generations before/after the response; stop on failure and delegate error display, or restart the timer after success.
   * Constraints: Neither success nor failure from old requests may overwrite a new customer; no silent network retries. */
  async tick(companyId, epoch) {
    if (epoch !== this.epoch) return;
    try {
      const data = await this.read(companyId);
      if (epoch !== this.epoch) return;
      this.apply(data);
    } catch (error) {
      if (epoch !== this.epoch) return;
      this.stop(); this.fail(error); return;
    }
    if (epoch === this.epoch) this.timer = setTimeout(() => this.tick(companyId, epoch), this.interval);
  }
}
