/** 职责：解释后端明确提供的活动日期精度。
 * 实现：date 使用包含末日的 starts_on/ends_on，时间型沿用原时间；全天日历使用排除式结束日期。
 * 关联：world-news.js 的展示、筛选、倒计时和 ICS 共用；不解析 Agent 自由文本或访问网络。
 * 目录：eventDates、eventWindow、calendarBounds。
 * 变量索引：无模块变量；日期计算的中间值仅在函数内使用。
 */

/** 功能：取得用于展示的日期。输入：item 后端活动。输出：start/end 日期字符串。
 * 逻辑：仅显式 date 使用后端日期投影；时间型保持既有 ISO 日期展示。约束：date 缺少日期直接报错，不推测占位钟点。 */
export function eventDates(item) {
  if (item.time_precision === 'date') {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(item.starts_on || '') || !/^\d{4}-\d{2}-\d{2}$/.test(item.ends_on || '')) throw new Error('Date-only event is missing its calendar dates.');
    return { start: item.starts_on, end: item.ends_on };
  }
  return { start: item.starts_at.slice(0, 10), end: item.ends_at.slice(0, 10) };
}

/** 功能：取得筛选和倒计时边界。输入：item 后端活动。输出：start/end Date，结束为排除边界。
 * 逻辑：date 按访问者日历日午夜构造，末日次日午夜结束；datetime 保留真实时刻。约束：日期型不把 UTC 占位钟点展示或当作真实事件时间。 */
export function eventWindow(item) {
  if (item.time_precision !== 'date') return { start: new Date(item.starts_at), end: new Date(item.ends_at) };
  const dates = eventDates(item), start = new Date(dates.start + 'T00:00:00'), end = new Date(dates.end + 'T00:00:00');
  end.setDate(end.getDate() + 1);
  return { start, end };
}

/** 功能：生成日历起止属性。输入：item 后端活动。输出：两条 ICS 时间属性。
 * 逻辑：date 使用 VALUE=DATE，结束为来源末日次日；datetime 转换真实时刻为 UTC。约束：不输出日期占位钟点，不改变来源日期。 */
export function calendarBounds(item) {
  if (item.time_precision === 'date') {
    const dates = eventDates(item), end = new Date(dates.end + 'T00:00:00Z');
    end.setUTCDate(end.getUTCDate() + 1);
    return ['DTSTART;VALUE=DATE:' + dates.start.replaceAll('-', ''), 'DTEND;VALUE=DATE:' + end.toISOString().slice(0, 10).replaceAll('-', '')];
  }
  return [item.starts_at, item.ends_at].map((value, index) => ['DTSTART:', 'DTEND:'][index] + new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z'));
}
