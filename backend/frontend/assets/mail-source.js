/**
 * 职责：统一展示邮件真实来源与演示数据标签。
 * 实现：来源枚举映射为用户可读文本；未知值保留原标记，未标注时明确说明。
 * 关联：app.js 在客户列表和时间线使用；processing.js 在原文核对入口使用。
 * 目录：mailSourceLabel（返回来源文本）。
 * 变量索引：sourceLabels 为协议来源到中文标签的映射。
 */
const sourceLabels = { gmail_real: 'Gmail 邮件', qq_real: 'QQ 邮件', synthetic_sample: '演示样例', simulated: '模拟数据', research_dataset: '研究数据' };

/** 功能：取得来源标签。输入：source 为邮件载荷中的来源。输出：未转义的显示文本。
 * 逻辑：只映射已知来源，不从客户名或分析提供方推测邮件来源。约束：调用者插入 HTML 前须转义。 */
export function mailSourceLabel(source) {
  return sourceLabels[source] || source || '来源未标注';
}
