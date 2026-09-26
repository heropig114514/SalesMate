/**
 * Responsibility: Display sync-batch progress, per-mailbox source review, and manual email review.
 * Implementation: Query all saved mail per mailbox and label source, time, and classification; request generations isolate stale responses/errors; reviews retain manual confirmation/version constraints.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; app.js supplies list refresh/polling, api.js handles Session/CSRF, and index.html supplies dialogs.
 * Directory: refreshReviewBadge, openMailboxEmails, loadReviews, updateRunProgress, initProcessingUI.
 * Variable index: reviewState stores recent batches, mailbox scope, request generation, page, and current records; runLabels maps batch states to current-language labels; classificationLabels explains classifications.
 */
import { t, h, locale } from './i18n.js?v=20260921-product';

import { request, escapeHtml as e } from './api.js?v=20260921-product';
import { mailSourceLabel } from './mail-source.js?v=20260921-product';

const reviewState = { page: 1, records: [], changed: null, retry: null, mailboxId: null, sequence: 0, runs: [] };
const runLabels = { queued: t('等待处理'), running: t('正在处理'), partial: t('部分完成'), completed: t('邮件处理完成'), failed: t('处理失败') };
const classificationLabels = { business: t('业务邮件'), non_business: t('非业务邮件 · 客户页隐藏'), needs_review: t('待复核 · 客户页未展示') };

/** Function: Refresh the pending-review badge. Inputs: None; reads the current employee session. Outputs: None.
 * Logic: Read only the pending count and update button text. Constraints: Propagate errors rather than treating failure as zero. */
export async function refreshReviewBadge() {
  const data = await request('email-reviews/?status=pending');
  document.getElementById('email-reviews-open').textContent = t`待复核邮件 (${data.pending_count})`;
}

/** Function: Open mailbox source text or global manual review. Inputs: mailboxId selects a mailbox or null; address is its display address. Outputs: A Promise completing the initial read.
 * Logic: A specific mailbox defaults to all saved mail; global mode defaults to pending review. Clear old records before loading. Constraints: GET only, without syncing, reclassifying, or model calls. */
export async function openMailboxEmails(mailboxId = null, address = '') {
  reviewState.mailboxId = mailboxId;
  reviewState.page = 1;
  reviewState.records = [];
  document.getElementById('review-filter').value = mailboxId ? 'saved' : 'pending';
  document.getElementById('review-title').textContent = mailboxId ? t('已同步邮件') : t('邮件人工复核');
  document.getElementById('review-scope').textContent = mailboxId ? t`${address} · 包含业务、非业务与待复核邮件，按接收时间从新到旧显示。` : t('全部邮箱的复核记录。');
  document.getElementById('review-error').textContent = '';
  document.getElementById('review-items').textContent = t('正在读取已保存的邮件…');
  document.getElementById('review-dialog').showModal();
  try { await loadReviews(); }
  catch (error) { document.getElementById('review-error').textContent = error.message; }
}

/** Function: Load the current page of review emails. Inputs: None; reads filters and reviewState.page. Outputs: None.
 * Logic: Query by reviewState.mailboxId, format received time in the current language, render source/classification/body, retain revision, and refresh the global badge. Constraints: Escape untrusted text; stale responses/errors cannot replace current mailbox content. Current failures still throw; success clears old errors. No automatic retries. */
async function loadReviews() {
  const sequence = ++reviewState.sequence;
  try {
    const status = document.getElementById('review-filter').value;
    const prefix = reviewState.mailboxId ? `mailboxes/${encodeURIComponent(reviewState.mailboxId)}/` : '';
    const data = await request(`${prefix}email-reviews/?status=${encodeURIComponent(status)}&page=${reviewState.page}`);
    if (sequence !== reviewState.sequence) return;
    document.getElementById('review-error').textContent = '';
    reviewState.records = data.results;
    document.getElementById('review-items').innerHTML = data.results.map((item, index) => h`<article class="review-card"><h3>${e(item.subject || t('无主题'))}</h3><p>${e(mailSourceLabel(item.source))} · ${e(classificationLabels[item.classification] || item.classification || t("分类未知"))}</p><p class="muted">接收时间：${e(item.received_at ? new Date(item.received_at).toLocaleString(locale, { hour12: false }) : t("暂无记录"))}</p><p class="muted">${e(item.sender)} · ${e(item.reason)}</p>${item.repair_status ? h`<p>事实补抽取：${e(({pending: t('等待处理'), running: t('正在处理'), completed: t('已完成，画像将自动更新'), failed: t('失败，可再次点击确认业务重试'), skipped: t('已取消或已被后续任务替代')})[item.repair_status] || item.repair_status)}</p>` : ''}<details><summary>查看邮件原文与证据</summary><pre>${e(item.body_text)}</pre><p>判断证据：${item.intent_evidences.map(e).join('；') || t('规则判断或暂无模型证据')}</p></details><div class="review-actions"><button type="button" class="primary" data-review-index="${index}" data-decision="confirmed_business">确认业务</button><button type="button" class="secondary" data-review-index="${index}" data-decision="confirmed_non_business">确认非业务</button></div></article>`).join('') || h('<p class="muted">当前没有符合条件的邮件。</p>');
    document.getElementById('review-page').textContent = t`${data.page} / ${Math.max(1, Math.ceil(data.count / data.page_size))} · 共 ${data.count} 封`;
    document.getElementById('review-prev').disabled = data.page === 1;
    document.getElementById('review-next').disabled = data.page * data.page_size >= data.count;
    await refreshReviewBadge();
  } catch (error) {
    if (sequence === reviewState.sequence) throw error;
  }
}

/** Function: Render live counts for complete batches. Inputs: runs is a backend batch array; omission redraws recent batches. Outputs: None.
 * Logic: Ordinary lists retain active/failed batches; explicit #processing also shows completed batches. Display separate mail/profile progress, frozen Gmail/QQ scope, failed messages, and explicit retry buttons. Constraints: Never infer completion from company pagination. */
export function updateRunProgress(runs = reviewState.runs) {
  reviewState.runs = runs;
  const visible = location.hash === "#processing" ? runs : runs.filter(run => run.status !== "completed" || run.analysis_pending_count > 0 || run.analysis_failed_count > 0 || run.failed_count > 0 || run.error || run.email_errors.length);
  const panel = document.getElementById('sync-progress');
  panel.hidden = !visible.length;
  panel.innerHTML = visible.map(run => h`<article class="sync-run"><strong>${e(runLabels[run.status] || run.status)}</strong>${run.sync_options?.until ? h`<p>本次范围：${run.sync_options.recent_days ? t`最近 ${e(run.sync_options.recent_days)} 天` : t("不限天数")} · ${run.sync_options.max_messages ? t`最多 ${e(run.sync_options.max_messages)} 封` : t("不限封数")}（收件箱与已发送合计）</p>` : ""}<p>${run.total_count} 封邮件：完成 ${run.completed_count} · 处理中 ${run.running_count} · 等待 ${run.pending_count} · 失败 ${run.failed_count}</p><p>客户画像：完成 ${run.analysis_completed_count} · 等待/处理中 ${run.analysis_pending_count} · 失败 ${run.analysis_failed_count}</p>${run.error ? `<p class="failure">${e(run.error.message)}</p>` : ''}${run.email_errors.length ? h`<details><summary>查看失败邮件</summary>${run.email_errors.map(item => `<p>${e(item.gmail_message_id)} · ${e(item.stage)} · ${e(item.message)}</p>`).join('')}</details>` : ''}${['failed', 'partial'].includes(run.status) ? h`<button type="button" class="secondary" data-retry-run="${e(run.run_id)}">重试未完成邮件</button>` : ''}</article>`).join('');
}

/** Function: Register review/retry interactions. Inputs: onChanged refreshes lists; onRetry resumes polling for a mailbox. Outputs: None.
 * Logic: Refresh source versions/badges after confirmation, display backend conflicts, and create new batches for retries. Constraints: No automatic confirmation or retry. */
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
