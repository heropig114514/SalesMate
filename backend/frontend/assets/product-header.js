/**
 * 职责：复用产品顶栏及跨页面分区导航，不获取或伪造账户与同步状态。
 * 实现：原生 Web Component 输出品牌及全球洞察、社媒情报、销售业务三个分区；监听 hashchange 更新当前分区，产品顶栏不展示实验数据入口。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：0919 界面及共享语言资源统一缓存版本；共享语言/API 资源随需求界面统一版本；workspace.js 注册组件，各页面入口声明 salesmate-header，design-system.css 定义样式。
 * 目录：SalesMateHeader、SalesMateHeader.connectedCallback、SalesMateHeader.disconnectedCallback、SalesMateHeader.updateSection。
 * 变量索引：SalesMateHeader.onRoute 为可移除的路由监听器；其余无模块变量或配置。
 */
import { h } from './i18n.js?v=20260921-product';


/** 功能：呈现产品顶栏。逻辑：采用 light DOM 共享设计变量及原生键盘导航。
 * 约束：不读取账户数据，不引入外部运行时，不执行任何业务写入。 */
class SalesMateHeader extends HTMLElement {
  /** 功能：挂载固定结构并订阅路由。输入：浏览器 location。输出：无。
   * 逻辑：静态 HTML 仅提供三个业务分区；连接后立即标记分区。约束：重连前移除旧监听器，内部实验页面不进入产品导航。 */
  connectedCallback() {
    this.disconnectedCallback();
    this.innerHTML = h`<header class="product-header">
      <a class="product-brand" href="/#home" aria-label="SalesMate 首页">SalesMate</a>
      <nav class="product-tabs" aria-label="产品分区">
        <a href="/world/" data-section="world">全球洞察</a>
        <a href="/#inbox" data-section="inbox">社媒情报</a>
        <a href="/business/#directory" data-section="business">销售业务</a>
      </nav>
    </header>`;
    this.onRoute = () => this.updateSection();
    window.addEventListener('hashchange', this.onRoute);
    this.updateSection();
  }
  /** 功能：释放路由监听器。输入：实例 onRoute。输出：无。
   * 逻辑：元素移除或重连时清理。约束：无业务副作用。 */
  disconnectedCallback() {
    if (this.onRoute) window.removeEventListener('hashchange', this.onRoute);
  }
  /** 功能：更新当前分区语义。输入：location.pathname/hash。输出：无。
   * 逻辑：全球、销售按路径匹配，邮件及客户分析按 hash 匹配；首页、聊天及内部实验页面不标为产品分区。
   * 约束：仅设置 aria-current，不重建页面、不改变浏览历史。 */
  updateSection() {
    const section = location.pathname.startsWith('/world/') ? 'world'
      : location.pathname.startsWith('/business/') ? 'business'
      : /^#(?:inbox|company\/)/.test(location.hash) ? 'inbox' : null;
    for (const link of this.querySelectorAll('[data-section]')) {
      if (link.dataset.section === section) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
  }
}
customElements.define('salesmate-header', SalesMateHeader);
