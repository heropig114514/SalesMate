/**
 * 职责：持续读取当前客户结果，并以保留节点的方式更新详情。
 * 实现：单通道 GET 轮询、代次隔离及显式失败暂停；按稳定键复用 DOM，恢复阅读锚点。
 * 关联：app.js 提供授权请求、渲染与错误展示；不提交分析、重试业务或保存草稿。
 * 目录：nodeKey、sameKind、patchNode、patchChildren、patchHTML、preserveReading、
 * DetailObserver、DetailObserver.constructor、DetailObserver.start、DetailObserver.stop、DetailObserver.tick。
 * 变量索引：无模块状态；DetailObserver 的 read/apply/fail 为回调，interval 为既有 3000ms 间隔，
 * epoch 隔离失效请求，timer 保存待执行定时器，companyId 标识当前客户。
 */

/** 功能：取得渲染节点的稳定身份。输入：node。输出：键字符串或空字符串。
 * 逻辑：优先 DOM id，再取邮件天然键或显式展示键。约束：不使用邮件正文作为身份。 */
function nodeKey(node) {
  return node.nodeType === Node.ELEMENT_NODE ? node.id || node.getAttribute('data-email-ref') || node.getAttribute('data-live-key') || '' : '';
}

/** 功能：判断是否可原位更新节点。输入：current、next。输出：布尔值。
 * 逻辑：比较节点类型、标签及稳定键。约束：不同邮件不共享身份，即使标签相同。 */
function sameKind(current, next) {
  return current && current.nodeType === next.nodeType && current.nodeName === next.nodeName && nodeKey(current) === nodeKey(next);
}

/** 功能：同步一个已有节点。输入：current、next。输出：无。
 * 逻辑：只改动变化的文本与属性，再递归子节点；data-live-preserve 的内容由独立渲染器维护。
 * 约束：仅用于可信模板、已转义数据；不处理表单控件的输入值，不替换未变化文本选择节点。 */
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

/** 功能：按稳定身份同步子节点。输入：current、next 容器。输出：无。
 * 逻辑：键控节点可重排并保留原 DOM；无键节点按位置和类型匹配，删除失效尾部。
 * 约束：作用范围不包括独立助手与编辑对话框，禁止以字符串执行脚本。 */
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

/** 功能：以可信模板更新局部容器。输入：container、html。输出：无。
 * 逻辑：在离屏 template 解析后复用已有节点。约束：调用方必须转义所有不可信字段。 */
export function patchHTML(container, html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  patchChildren(container, template.content);
}

/** 功能：在局部刷新期间保留阅读位置。输入：root、同步 update 回调。输出：无。
 * 逻辑：记录可见内容锚点与滚动容器位置，更新后校正高度变化；无锚点保留页面坐标。
 * 约束：不恢复已被业务删除的文本，不触碰助手输入及焦点；浏览器最大滚动范围仍生效。 */
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

/** 功能：独立观察当前客户的完整结果。逻辑：一次 GET 完成后再计时，无并行累积。
 * 约束：不依赖邮箱批次完成；离开、重新请求及失败都会使旧响应失效。 */
export class DetailObserver {
  /** 功能：建立观察器。输入：read/apply/fail 回调及 interval 毫秒间隔。输出：实例。
   * 逻辑：保存回调与初始代次，默认沿用页面 3 秒检查周期。约束：构造不发请求。 */
  constructor({ read, apply, fail, interval = 3000 }) {
    this.read = read; this.apply = apply; this.fail = fail; this.interval = interval;
    this.epoch = 0; this.timer = null; this.companyId = null;
  }

  /** 功能：在初次 GET 后持续观察。输入：companyId。输出：无。
   * 逻辑：废弃旧代次，间隔后读取当前客户。约束：只安排读取，不自动创建或重试分析任务。 */
  start(companyId) {
    this.stop();
    this.companyId = companyId;
    const epoch = this.epoch;
    this.timer = setTimeout(() => this.tick(companyId, epoch), this.interval);
  }

  /** 功能：取消后续检查并使在途响应失效。输入：无，读取实例状态。输出：无。
   * 逻辑：清理 timer、递增 epoch、解除客户绑定。约束：已经发送的只读 HTTP 可以结束但不得渲染。 */
  stop() {
    clearTimeout(this.timer); this.timer = null; this.companyId = null; this.epoch += 1;
  }

  /** 功能：读取并应用一个观察周期。输入：companyId、epoch。输出：Promise<void>。
   * 逻辑：响应前后校验代次；失败停止并交给界面显示，成功后继续计时。
   * 约束：旧请求的成功和失败均不能覆盖新客户；没有静默网络重试。 */
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
