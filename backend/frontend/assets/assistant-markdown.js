/**
 * 职责：将助手回答转换为受限、安全的 Markdown HTML。
 * 实现：复用 markdown-it 的 CommonMark、表格和删除线规则；禁用原始 HTML，限制链接协议，图片改为显式访问的链接。
 * 关联：assistant.js 仅对助手消息调用；assistant-widget.css 提供排版；markdown-it.vendor.js 为固定版本的官方浏览器发行包。
 * 目录：isSafeLink、renderLinkOpen、renderImageLink、renderTableOpen、renderTableClose、renderAssistantMarkdown。
 * 变量索引：markdown 为唯一解析器，固定 html=false、breaks=true、linkify=true、typographer=false；不改变回答文本或服务端存储。
 */
import MarkdownIt from './markdown-it.vendor.js?v=15.0.2';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true, typographer: false });

/** 功能：检查 Markdown 链接协议。输入：url 为解析器规范化后的目标。输出：是否允许。
 * 逻辑：相对地址按 HTTPS 基准解析，只接受 HTTP、HTTPS、mailto；无效 URL 返回 false。
 * 约束：不发起请求，禁止 data、javascript、file 等协议；基准仅用于协议判断，不替换实际链接。 */
function isSafeLink(url) {
  try {
    return ['http:', 'https:', 'mailto:'].includes(new URL(url, 'https://markdown.invalid/').protocol);
  } catch {
    return false;
  }
}

/** 功能：生成安全的链接起始标签。输入：tokens、index、options、env、renderer 为 markdown-it 渲染参数。
 * 输出：转义属性后的 HTML。逻辑：经协议校验的链接在新标签打开并隔离 opener/referrer。
 * 约束：不自行拼接不可信属性，不导航或触发外部调用。 */
function renderLinkOpen(tokens, index, options, env, renderer) {
  tokens[index].attrSet('target', '_blank');
  tokens[index].attrSet('rel', 'noopener noreferrer nofollow');
  return renderer.renderToken(tokens, index, options);
}

/** 功能：将图片表示为可访问的文本链接。输入：tokens、index、options、env、renderer 为渲染参数。
 * 输出：转义后的链接或替代文字。逻辑：保留图片描述和安全地址，由用户点击访问。
 * 约束：不自动加载模型提供的远程图片；再次检查目标协议，替代文字不执行 HTML。 */
function renderImageLink(tokens, index, options, env, renderer) {
  const token = tokens[index], source = token.attrGet('src') || '';
  const label = markdown.utils.escapeHtml(renderer.renderInlineAsText(token.children || [], options, env) || source);
  return isSafeLink(source)
    ? `<a href="${markdown.utils.escapeHtml(source)}" target="_blank" rel="noopener noreferrer nofollow">${label}</a>`
    : label;
}

/** 功能：创建表格的独立滚动容器。输入：无。输出：静态起始标签。
 * 逻辑：保留原生 table 语义，容器可聚焦以便键盘横向滚动。约束：宽表格不撑开聊天面板。 */
function renderTableOpen() {
  return '<div class="assistant-markdown-table" tabindex="0"><table>\n';
}

/** 功能：闭合表格及滚动容器。输入：无。输出：静态结束标签。
 * 逻辑：与 renderTableOpen 成对。约束：只处理解析器产生的表格 token。 */
function renderTableClose() {
  return '</table></div>\n';
}

markdown.validateLink = isSafeLink;
markdown.renderer.rules.link_open = renderLinkOpen;
markdown.renderer.rules.image = renderImageLink;
markdown.renderer.rules.table_open = renderTableOpen;
markdown.renderer.rules.table_close = renderTableClose;

/** 功能：渲染一条助手回答。输入：content 为 API 持久化的字符串。输出：可插入消息容器的 HTML。
 * 逻辑：标准 Markdown 解析统一处理转义、代码、列表、引用与表格；保留普通换行。
 * 约束：只用于展示，不修改原文、不执行代码；未闭合代码围栏按 CommonMark 显示为代码。 */
export function renderAssistantMarkdown(content) {
  return markdown.render(content);
}
