/**
 * 职责：显示同步批次进度及邮件人工复核。
 * 实现：从整体进度接口渲染计数，复核使用版本化 PATCH，重试由员工明确点击。
 * 关联：app.js 提供列表刷新和轮询入口；api.js 管理 Session/CSRF；index.html 提供对话框。
 * 目录：refreshReviewBadge、loadReviews、updateRunProgress、initProcessingUI。
 * 变量索引：reviewState 保存页码与当前复核记录；runLabels 为批次状态中文映射。
 */
import { request, escapeHtml as e } from './api.js';

const reviewState = { page: 1, records: [], changed: null, retry: null };
const runLabels = { queued: '等待处理', running: '正在处理', partial: '部分完成', completed: '邮件处理完成', failed: '处理失败' };

/** 功能：刷新待复核徽标。输入：无，读取当前员工会话。输出：无。
 * 逻辑：只取待复核数量，更新按钮文案。约束：错误传播给调用者，不把失败当作零。 */
export async function refreshReviewBadge() {
  const data = await request('email-reviews/?status=pending');
  document.getElementById('email-reviews-open').textContent = `待复核邮件 (${data.pending_count})`;
}

/** 功能：加载当前页复核邮件。输入：无，读取筛选器和 reviewState.page。输出：无。
 * 逻辑：只渲染后端原文与证据，保存每封的 revision。约束：所有不可信文本转义，正文只作文本展示。 */
async function loadReviews() {
  const status = document.getElementById('review-filter').value;
  const data = await request(`email-reviews/?status=${encodeURIComponent(status)}&page=${reviewState.page}`);
  reviewState.records = data.results;
  document.getElementById('review-items').innerHTML = data.results.map((item, index) => `<article class="review-card"><h3>${e(item.subject || '无主题')}</h3><p class="muted">${e(item.sender)} · ${e(item.reason)}</p><details><summary>查看邮件原文与证据</summary><pre>${e(item.body_text)}</pre><p>判断证据：${item.intent_evidences.map(e).join('；') || '规则判断或暂无模型证据'}</p></details><div class="review-actions"><button type="button" class="primary" data-review-index="${index}" data-decision="confirmed_business">确认业务</button><button type="button" class="secondary" data-review-index="${index}" data-decision="confirmed_non_business">确认非业务</button></div></article>`).join('') || '<p class="muted">当前没有符合条件的邮件。</p>';
  document.getElementById('review-page').textContent = `${data.page} / ${Math.max(1, Math.ceil(data.count / data.page_size))}`;
  document.getElementById('review-prev').disabled = data.page === 1;
  document.getElementById('review-next').disabled = data.page * data.page_size >= data.count;
  document.getElementById('email-reviews-open').textContent = `待复核邮件 (${data.pending_count})`;
}

/** 功能：渲染整个批次的实时计数。输入：runs 为后端批次数组。输出：无。
 * 逻辑：显示邮件与画像独立进度，以及失败邮件和显式重试按钮。约束：不根据公司分页推测完成情况。 */
export function updateRunProgress(runs) {
  const panel = document.getElementById('sync-progress');
  panel.hidden = !runs.length;
  panel.innerHTML = runs.map(run => `<article class="sync-run"><strong>${e(runLabels[run.status] || run.status)}</strong><p>${run.total_count} 封邮件：完成 ${run.completed_count} · 处理中 ${run.running_count} · 等待 ${run.pending_count} · 失败 ${run.failed_count}</p><p>客户画像：完成 ${run.analysis_completed_count} · 等待/处理中 ${run.analysis_pending_count} · 失败 ${run.analysis_failed_count}</p>${run.error ? `<p class="failure">${e(run.error.message)}</p>` : ''}${run.email_errors.length ? `<details><summary>查看失败邮件</summary>${run.email_errors.map(item => `<p>${e(item.gmail_message_id)} · ${e(item.stage)} · ${e(item.message)}</p>`).join('')}</details>` : ''}${['failed', 'partial'].includes(run.status) ? `<button type="button" class="secondary" data-retry-run="${e(run.run_id)}">重试未完成邮件</button>` : ''}</article>`).join('');
}

/** 功能：注册复核与重试交互。输入：onChanged 刷新列表，onRetry 恢复指定邮箱轮询。输出：无。
 * 逻辑：确认后刷新原文版本与徽标，冲突显示后端错误；重试创建新批次。约束：不自动确认或重试。 */
export function initProcessingUI(onChanged, onRetry) {
  reviewState.changed = onChanged;
  reviewState.retry = onRetry;
  const dialog = document.getElementById('review-dialog');
  const reportError = error => { document.getElementById('review-error').textContent = error.message; };
  document.getElementById('email-reviews-open').onclick = () => {
    dialog.showModal();
    document.getElementById('review-error').textContent = '';
    loadReviews().catch(reportError);
  };
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
