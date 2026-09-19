/**
 * 职责：将一级聊天入口直接挂载为通用会话页面。
 * 实现：默认不读取客户；旧的显式客户链接仍授权读取后打开相应会话。
 * 关联：app.js 提供路由，AssistantPanel 复用会话、草稿和状态观察。
 * 目录：AssistantEntry、AssistantEntry.constructor、AssistantEntry.show、AssistantEntry.hide。
 * 变量索引：无模块变量；panel 为助手；nodes 为页面节点；generation/active 隔离路由；controller 取消客户读取。
 */
import { request } from './api.js';

/** 功能：管理直接聊天入口。逻辑：复用面板并在离开时恢复原挂载位置。
 * 约束：进入页面不创建会话、不自动提问。 */
export class AssistantEntry {
  /** 功能：连接入口节点。输入：panel 助手实例。输出：实例。
   * 逻辑：仅保存依赖。约束：不请求网络。 */
  constructor(panel) {
    this.panel = panel;
    this.nodes = Object.fromEntries(['page', 'host', 'error'].map(name => [name, document.getElementById(`assistant-entry-${name}`)]));
    this.generation = 0; this.active = false; this.controller = null;
  }
  /** 功能：直接展示通用聊天，或恢复显式客户链接。输入：preferredId 可空客户标识。
   * 输出：Promise。逻辑：通用模式不查客户，旧链接先校验访问权限，过期响应不挂载。
   * 约束：读取失败明确显示；不将失败的客户读取转换成通用会话。 */
  async show(preferredId = null) {
    this.active = true; const generation = ++this.generation;
    this.nodes.page.hidden = false; this.nodes.error.hidden = true;
    let context = null;
    if (preferredId) {
      this.controller = new AbortController();
      try {
        const company = await request(`companies/${encodeURIComponent(preferredId)}/`, { signal: this.controller.signal });
        context = { id: company.company_id, name: company.company_name || company.domains[0] || '待确认客户' };
      } catch (error) {
        if (error.name !== 'AbortError' && this.active && generation === this.generation) {
          this.nodes.error.textContent = error.message; this.nodes.error.hidden = false;
          console.error('assistant_entry_failed', { errorType: error.name });
        }
        return;
      }
    }
    if (!this.active || generation !== this.generation) return;
    this.panel.setContext(context);
    this.panel.mount(this.nodes.host);
    this.panel.open(document.querySelector('#workspace-nav a[data-assistant-entry]'));
  }
  /** 功能：离开聊天页面。输入：当前挂载和请求状态。输出：无。
   * 逻辑：取消读取及观察，恢复客户侧栏的挂载位置。
   * 约束：不删除服务器会话或当前页草稿。 */
  hide() {
    this.active = false; ++this.generation; this.controller?.abort();
    this.panel.close(false); this.panel.mount(); this.nodes.page.hidden = true;
  }
}
