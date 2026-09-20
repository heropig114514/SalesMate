/**
 * 职责：统一展示邮件真实来源与演示数据标签。
 * 实现：来源枚举映射为用户可读文本；未知值保留原标记，未标注时明确说明。
 * 国际化：i18n.js 仅翻译显式标记的静态文案；动态业务正文和接口值保持原样。
 * 关联：共享语言/API 资源随需求界面统一版本；app.js 在客户列表和时间线使用；processing.js 在原文核对入口使用。
 * 目录：mailSourceLabel（返回来源文本）。
 * 变量索引：sourceLabels 为协议来源到当前语言标签的映射。
 */
import { t } from './i18n.js?v=20260920-requirements';

const sourceLabels = { gmail_real: t('Gmail 邮件'), qq_real: t('QQ 邮件'), synthetic_sample: t('演示样例'), simulated: t('模拟数据'), research_dataset: t('研究数据') };

/** 功能：取得来源标签。输入：source 为邮件载荷中的来源。输出：未转义的显示文本。
 * 逻辑：只映射已知来源，不从客户名或分析提供方推测邮件来源。约束：调用者插入 HTML 前须转义。 */
export function mailSourceLabel(source) {
  return sourceLabels[source] || source || t('来源未标注');
}
