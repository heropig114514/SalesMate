"""职责：计算实验夹具的持久化行指纹，供导入、只读共享和清理共同验证。
实现：只使用实际数据库字段；二进制和向量规范化后按键排序计算 SHA-256。
关联：sales.seed_kg_lab 保存指纹；sales.experiments 在跨账号返回内容前检查指纹。
目录：
- fingerprint：计算单行持久化内容的摘要。
变量索引：
- 无
"""

import hashlib
import json


# 功能：计算数据库行内容指纹。
# 输入：`instance` 已从数据库读取的 ORM 实例。
# 输出：SHA-256 十六进制字符串。
# 逻辑：外键用 ID，二进制用 hex，向量用列表，其余非 JSON 类型按字符串规范化。
# 约束：与既有 kg-business-fixture-v1 清单兼容；摘要检测内容漂移，不证明来源真实性。
def fingerprint(instance):
    values = {}
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname)
        if isinstance(value, (bytes, memoryview)):
            value = bytes(value).hex()
        elif hasattr(value, "tolist"):
            value = value.tolist()
        values[field.attname] = value
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()
