/**
 * Responsibility: Convert assistant replies into restricted, safe Markdown HTML.
 * Implementation: Use markdown-it CommonMark, table, and strikethrough rules; disable raw HTML, restrict link protocols, and convert images to links requiring explicit access.
 * Relationships: assistant.js calls this only for assistant messages; assistant-widget.css supplies typography; markdown-it.vendor.js is the pinned official browser distribution.
 * Directory: isSafeLink, renderLinkOpen, renderImageLink, renderTableOpen, renderTableClose, renderAssistantMarkdown.
 * Variable index: markdown is the sole parser, with html=false, breaks=true, linkify=true, typographer=false; response text and server storage remain unchanged.
 */
import MarkdownIt from './markdown-it.vendor.js?v=15.0.2';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true, typographer: false });

/** Function: Check Markdown link protocols. Inputs: url is the parser-normalized target. Outputs: Whether it is allowed.
 * Logic: Resolve relative addresses against an HTTPS base and accept only HTTP, HTTPS, and mailto; invalid URLs return false.
 * Constraints: Make no requests; reject data, javascript, file, and other protocols. The base is used only for protocol checks and does not replace the actual link. */
function isSafeLink(url) {
  try {
    return ['http:', 'https:', 'mailto:'].includes(new URL(url, 'https://markdown.invalid/').protocol);
  } catch {
    return false;
  }
}

/** Function: Generate a safe opening link tag. Inputs: tokens, index, options, env, renderer are markdown-it rendering arguments.
 * Outputs: HTML with escaped attributes. Logic: Open validated links in a new tab with opener/referrer isolation.
 * Constraints: Never concatenate untrusted attributes manually, navigate, or trigger external calls. */
function renderLinkOpen(tokens, index, options, env, renderer) {
  tokens[index].attrSet('target', '_blank');
  tokens[index].attrSet('rel', 'noopener noreferrer nofollow');
  return renderer.renderToken(tokens, index, options);
}

/** Function: Represent an image as an accessible text link. Inputs: tokens, index, options, env, renderer are rendering arguments.
 * Outputs: An escaped link or alternative text. Logic: Retain the description and safe address for the user to open explicitly.
 * Constraints: Never automatically load remote model-provided images; recheck the target protocol and do not execute alternative text as HTML. */
function renderImageLink(tokens, index, options, env, renderer) {
  const token = tokens[index], source = token.attrGet('src') || '';
  const label = markdown.utils.escapeHtml(renderer.renderInlineAsText(token.children || [], options, env) || source);
  return isSafeLink(source)
    ? `<a href="${markdown.utils.escapeHtml(source)}" target="_blank" rel="noopener noreferrer nofollow">${label}</a>`
    : label;
}

/** Function: Create a separate table scroll container. Inputs: None. Outputs: Static opening tags.
 * Logic: Preserve native table semantics and make the container focusable for keyboard horizontal scrolling. Constraints: Wide tables must not stretch the chat panel. */
function renderTableOpen() {
  return '<div class="assistant-markdown-table" tabindex="0"><table>\n';
}

/** Function: Close the table and scroll container. Inputs: None. Outputs: Static closing tags.
 * Logic: Pair with renderTableOpen. Constraints: Handle only table tokens emitted by the parser. */
function renderTableClose() {
  return '</table></div>\n';
}

markdown.validateLink = isSafeLink;
markdown.renderer.rules.link_open = renderLinkOpen;
markdown.renderer.rules.image = renderImageLink;
markdown.renderer.rules.table_open = renderTableOpen;
markdown.renderer.rules.table_close = renderTableClose;

/** Function: Render one assistant reply. Inputs: content is the API-persisted string. Outputs: HTML for the message container.
 * Logic: Standard Markdown parsing handles escaping, code, lists, quotes, and tables while retaining ordinary line breaks.
 * Constraints: Display only; do not modify source text or execute code. Unclosed code fences render as code under CommonMark. */
export function renderAssistantMarkdown(content) {
  return markdown.render(content);
}
