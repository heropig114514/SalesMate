/**
 * 职责：显示同步批次进度、按邮箱核对原文及邮件人工复核。
 * 实现：按邮箱查询全部已保存邮件并标明来源、时间与分类；请求代次隔离旧响应；复核保持人工确认与版本约束。
 * 关联：app.js 提供列表刷新和轮询入口；api.js 管理 Session/CSRF；index.html 提供对话框。
 * 目录：refreshReviewBadge、openMailboxEmails、loadReviews、updateRunProgress、initProcessingUI。
 * 变量索引：reviewState 保存邮箱范围、请求代次、页码与当前记录；runLabels 为批次状态中文映射；classificationLabels 为分类展示说明。
 */
import { request, escapeHtml as e } from './api.js';
import { mailSourceLabel } from './mail-source.js';

const reviewState = { page: 1, records: [], changed: null, retry: null, mailboxId: null, sequence: 0 };
const runLabels = { queued: '等待处理', running: '正在处理', partial: '部分完成', completed: '邮件处理完成', failed: '处理失败' };
const classificationLabels = { business: '业务邮件', non_business: '非业务邮件 · 客户页隐藏', needs_review: '待复核 · 客户页未展示' };

/** 功能：刷新待复核徽标。输入：无，读取当前员工会话。输出：无。
 * 逻辑：只取待复核数量，更新按钮文案。约束：错误传播给调用者，不把失败当作零。 */
export async function refreshReviewBadge() {
  const data = await request('email-reviews/?status=pending');
  document.getElementById('email-reviews-open').textContent = `待复核邮件 (${data.pending_count})`;
}

/** 功能：打开邮箱原文或全局人工复核。输入：mailboxId 为指定邮箱或 null，address 为该邮箱显示地址。输出：完成首屏读取的 Promise。
 * 逻辑：指定邮箱默认查看全部已保存邮件，全局默认待复核；清空旧记录后加载。约束：仅 GET，不同步、不重新分类或调用模型。 */
export async function openMailboxEmails(mailboxId = null, address = '') {
  reviewState.mailboxId = mailboxId;
  reviewState.page = 1;
  reviewState.records = [];
  document.getElementById('review-filter').value = mailboxId ? 'saved' : 'pending';
  document.getElementById('review-title').textContent = mailboxId ? '已同步邮件' : '邮件人工复核';
  document.getElementById('review-scope').textContent = mailboxId ? `${address} · 包含业务、非业务与待复核邮件，按接收时间从新到旧显示。` : '全部邮箱的复核记录。';
  document.getElementById('review-error').textContent = '';
  document.getElementById('review-items').textContent = '正在读取已保存的邮件…';
  document.getElementById('review-dialog').showModal();
  try { await loadReviews(); }
  catch (error) { document.getElementById('review-error').textContent = error.message; }
}

/** 功能：加载当前页复核邮件。输入：无，读取筛选器和 reviewState.page。输出：无。
 * 逻辑：按 reviewState.mailboxId 查询，渲染来源/接收时间/分类/原文，保存 revision；刷新全局待复核徽标。约束：不可信文本转义，过时响应不替换当前邮箱内容。 */
async function loadReviews() {
  const sequence = ++reviewState.sequence;
  const status = document.getElementById('review-filter').value;
  const prefix = reviewState.mailboxId ? `mailboxes/${encodeURIComponent(reviewState.mailboxId)}/` : '';
  const data = await request(`${prefix}email-reviews/?status=${encodeURIComponent(status)}&page=${reviewState.page}`);
  if (sequence !== reviewState.sequence) return;
  reviewState.records = data.results;
  document.getElementById('review-items').innerHTML = data.results.map((item, index) => `<article class="review-card"><h3>${e(item.subject || '无主题')}</h3><p>${e(mailSourceLabel(item.source))} · ${e(classificationLabels[item.classification] || item.classification || "分类未知")}</p><p class="muted">接收时间：${e(item.received_at ? new Date(item.received_at).toLocaleString("zh-CN", { hour12: false }) : "暂无记录")}</p><p class="muted">${e(item.sender)} · ${e(item.reason)}</p>${item.repair_status ? `<p>事实补抽取：${e(({pending: '等待处理', running: '正在处理', completed: '已完成，画像将自动更新', failed: '失败，可再次点击确认业务重试', skipped: '已取消或已被后续任务替代'})[item.repair_status] || item.repair_status)}</p>` : ''}<details><summary>查看邮件原文与证据</summary><pre>${e(item.body_text)}</pre><p>判断证据：${item.intent_evidences.map(e).join('；') || '规则判断或暂无模型证据'}</p></details><div class="review-actions"><button type="button" class="primary" data-review-index="${index}" data-decision="confirmed_business">确认业务</button><button type="button" class="secondary" data-review-index="${index}" data-decision="confirmed_non_business">确认非业务</button></div></article>`).join('') || '<p class="muted">当前没有符合条件的邮件。</p>';
  document.getElementById('review-page').textContent = `${data.page} / ${Math.max(1, Math.ceil(data.count / data.page_size))} · 共 ${data.count} 封`;
  document.getElementById('review-prev').disabled = data.page === 1;
  document.getElementById('review-next').disabled = data.page * data.page_size >= data.count;
  await refreshReviewBadge();
}

/** 功能：渲染整个批次的实时计数。输入：runs 为后端批次数组。输出：无。
 * 逻辑：显示邮件与画像独立进度、QQ 范围、失败邮件及显式重试按钮。约束：不根据公司分页推测完成情况。 */
export function updateRunProgress(runs) {
  const panel = document.getElementById('sync-progress');
  panel.hidden = !runs.length;
  panel.innerHTML = runs.map(run => `<article class="sync-run"><strong>${e(runLabels[run.status] || run.status)}</strong>${run.sync_options?.until ? `<p>本次范围：${run.sync_options.recent_days ? `最近 ${e(run.sync_options.recent_days)} 天` : "不限天数"} · ${run.sync_options.max_messages ? `最多 ${e(run.sync_options.max_messages)} 封` : "不限封数"}（收件箱与已发送合计）</p>` : ""}<p>${run.total_count} 封邮件：完成 ${run.completed_count} · 处理中 ${run.running_count} · 等待 ${run.pending_count} · 失败 ${run.failed_count}</p><p>客户画像：完成 ${run.analysis_completed_count} · 等待/处理中 ${run.analysis_pending_count} · 失败 ${run.analysis_failed_count}</p>${run.error ? `<p class="failure">${e(run.error.message)}</p>` : ''}${run.email_errors.length ? `<details><summary>查看失败邮件</summary>${run.email_errors.map(item => `<p>${e(item.gmail_message_id)} · ${e(item.stage)} · ${e(item.message)}</p>`).join('')}</details>` : ''}${['failed', 'partial'].includes(run.status) ? `<button type="button" class="secondary" data-retry-run="${e(run.run_id)}">重试未完成邮件</button>` : ''}</article>`).join('');
}

/** 功能：注册复核与重试交互。输入：onChanged 刷新列表，onRetry 恢复指定邮箱轮询。输出：无。
 * 逻辑：确认后刷新原文版本与徽标，冲突显示后端错误；重试创建新批次。约束：不自动确认或重试。 */
export function initProcessingUI(onChanged, onRetry) {
  reviewState.changed = onChanged;
  reviewState.retry = onRetry;
  const reportError = error => { document.getElementById('review-error').textContent = error.message; };
  document.getElementById('review-dialog').addEventListener('close', () => { reviewState.sequence += 1; });
  document.getElementById('email-reviews-open').onclick = () => { openMailboxEmails().catch(reportError); };
  document.getElementById('review-filter').onchange = () => { reviewState.page = 1; loadReviews().catch(reportError); };
  document.getElementById('review-prev').onclick = () => { reviewState.page -= 1; loadReviews().catch(reportError); };
  document.getElementById('review-next').onclick = () => { reviewState.page += 1; loadReviews().catch(reportError); };
  document.getElementById('review-items').onclick = async event => {
    const button = event.target.closest('[data-review-index]');
    if (!button) return;
    const item = reviewState.records[Number(button.dataset.reviewIndex)];
    button.disabled = true;
    try {
      await request(`email-reviews/${encodeURIComponent(item.email_id)}/`, { method: 'PATCH', version: item.revision, data: { review_status: button.dataset.decision } });
      document.getElementById('review-error').textContent = '';
      await loadReviews();
      await reviewState.changed();
    } catch (error) { reportError(error); button.disabled = false; }
  };
  document.getElementById('sync-progress').onclick = async event => {
    const button = event.target.closest('[data-retry-run]');
    if (!button) return;
    button.disabled = true;
    try {
      const run = await request(`mailbox-sync-runs/${button.dataset.retryRun}/`, { method: 'POST' });
      updateRunProgress([run]);
      await reviewState.retry(run.mailbox_id);
    } catch (error) {
      button.disabled = false;
      document.getElementById('processing-error').textContent = error.message;
    }
  };
}
