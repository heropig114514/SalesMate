/**
 * Responsibility: Reuse product headers and cross-page section navigation without reading or fabricating account/sync state.
 * Implementation: A native Web Component renders branding and three sections: global insights, social intelligence, and sales business. hashchange updates the active section; the header has no experiment-data entry.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; workspace.js registers the component, entries declare salesmate-header, and design-system.css defines styling.
 * Directory: SalesMateHeader, SalesMateHeader.connectedCallback, SalesMateHeader.disconnectedCallback, SalesMateHeader.updateSection.
 * Variable index: SalesMateHeader.onRoute is a removable route listener; no other module variables or configuration.
 */
import { h } from './i18n.js?v=20260921-product';


/** Function: Render the product header. Logic: Use light DOM for shared design variables and native keyboard navigation.
 * Constraints: No account reads, external runtimes, or business writes. */
class SalesMateHeader extends HTMLElement {
  /** Function: Mount fixed structure and subscribe to routes. Inputs: Browser location. Outputs: None.
   * Logic: Static HTML exposes only three business sections and marks the current one immediately. Constraints: Remove prior listeners before reconnecting; internal experiment pages stay outside product navigation. */
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
  /** Function: Release the route listener. Inputs: Instance onRoute. Outputs: None.
   * Logic: Clean up on removal or reconnection. Constraints: No business side effects. */
  disconnectedCallback() {
    if (this.onRoute) window.removeEventListener('hashchange', this.onRoute);
  }
  /** Function: Update active-section semantics. Inputs: location.pathname/hash. Outputs: None.
   * Logic: Match global/sales sections by path and mail/customer analysis by hash; home, chat, and internal experiment pages are not marked as product sections.
 * Constraints: Set aria-current only; never rebuild pages or alter browser history. */
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
