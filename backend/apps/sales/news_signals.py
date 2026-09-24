"""职责：校验公共新闻线索中的单组精确金额与证据一致性。
实现：使用非负十进制字符串；合并更新前后值校验金额元数据及原文包含关系，不生成线索。
关联：WorldNewsSerializer 调用金额字段与校验；不导入数据库模型、CRM 或 Agent。
目录：
- NewsAmountField：仅接收契约中的金额字符串。
- NewsAmountField.to_internal_value：拒绝浮点、指数表示和负数。
- validate_news_signal：核对完整记录中的金额组合与证据。
变量索引：
- logger：仅记录校验位置与字段，不记录证据正文。
"""

import logging
import re

from rest_framework import serializers

logger = logging.getLogger("salesmate.news_signals")


# 功能：为新闻来源金额保持精确的十进制字符串输入。
# 逻辑：继承 DRF 精度与可空校验，额外拒绝隐式数值和指数转换。
# 约束：范围由序列化器明确指定，超限报错，不截断或四舍五入输入。
class NewsAmountField(serializers.DecimalField):
    # 功能：验证金额的传输类型和普通十进制语法。
    # 输入：`data` 为非 null 原始 JSON 字段；null 由父类可空校验处理。
    # 输出：精确 Decimal；类型或范围错误抛 ValidationError。
    # 逻辑：先验证非负十进制字符串，再交由父类核对总位数和小数位数。
    # 约束：不接受 float、整数 JSON、负数、空白、NaN 或科学计数法；日志不含数值。
    def to_internal_value(self, data):
        if not isinstance(data, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", data) is None:
            logger.warning("news_signal_rejected field=amount reason=decimal_string_required")
            raise serializers.ValidationError("amount 必须是非负普通十进制字符串或 null。")
        return super().to_internal_value(data)


# 功能：验证公共新闻的金额及证据约束。
# 输入：`serializer` 含可选旧实例，`attrs` 为字段级校验后的创建或部分更新数据。
# 输出：原 attrs；组合矛盾时抛字段级 ValidationError。
# 逻辑：合并现有记录与补丁；有金额时四个元数据字段必须含非空白内容，无金额时必须全空；金额证据逐字包含于 evidence。
# 约束：不访问来源、不推断缺失信息、不关联 CRM；包含关系只能证明内部一致，不能证明外部新闻真实性。
def validate_news_signal(serializer, attrs):
    names = ("amount", "currency", "amount_type", "amount_scope", "amount_evidence", "evidence")
    record = {name: attrs.get(name, getattr(serializer.instance, name, None if name == "amount" else "")) for name in names}
    metadata = ("currency", "amount_type", "amount_scope", "amount_evidence")
    errors = {}
    for name in metadata:
        if record["amount"] is None and record[name]:
            errors[name] = "amount 为 null 时金额元数据必须为空。"
        elif record["amount"] is not None and not record[name].strip():
            errors[name] = "提供 amount 时必须同时提供币种、类型、范围和金额证据。"
    if record["amount_evidence"] and record["amount_evidence"] not in record["evidence"]:
        errors["amount_evidence"] = "金额证据必须逐字包含在同一条 evidence 中。"
    if errors:
        logger.warning("news_signal_rejected operation=%s fields=%s", "update" if serializer.instance else "create", ",".join(sorted(errors)))
        raise serializers.ValidationError(errors)
    return attrs
