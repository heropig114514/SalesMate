/**
 * Responsibility: Consistently label real email sources and demo data.
 * Implementation: Map source enums to readable text; preserve unknown markers and explicitly indicate missing source labels.
 * Internationalization: i18n.js translates explicitly marked static text only; dynamic business content and API values remain unchanged.
 * Relationships: The 0919 interface and shared language/API resources use coordinated cache versions; app.js uses labels in customer lists/timelines, and processing.js uses them in source-review entry points.
 * Directory: mailSourceLabel returns source text.
 * Variable index: sourceLabels maps protocol sources to current-language labels.
 */
import { t } from './i18n.js?v=20260921-product';

const sourceLabels = { gmail_real: t('Gmail 邮件'), qq_real: t('QQ 邮件'), synthetic_sample: t('演示样例'), simulated: t('模拟数据'), research_dataset: t('研究数据') };

/** Function: Get a source label. Inputs: source is the email payload's provenance. Outputs: Unescaped display text.
 * Logic: Map known sources only; never infer provenance from customer names or analysis providers. Constraints: Callers must escape before HTML insertion. */
export function mailSourceLabel(source) {
  return sourceLabels[source] || source || t('来源未标注');
}
