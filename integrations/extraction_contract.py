"""职责：区分 L1 事实结构版本和合成数据生成来源。
实现：普通抽取使用提示词版本；明确合成封装使用独立结构版本，兼容已审计的历史批次。
关联：后端升级预检和 Agent L2 共用；事实字段仍由 Agent 原有校验器验证。
目录：
- compatible_extraction：判断封装是否声明当前事实结构。
变量索引：
- LEGACY_FIXTURE_SCHEMAS：已核验历史合成版本与实际结构版本的精确映射。
"""

LEGACY_FIXTURE_SCHEMAS = {"KGSEED_20260921_01:fixture-extract-v1": "extract-v7"}


# 功能：判断普通或合成抽取是否使用指定结构。
# 输入：`email` 为邮件协议字典；`target` 为消费者支持的事实版本。
# 输出：布尔值；不改变原提示词版本或事实。
# 逻辑：普通提示词直接比较；合成版本须与批次匹配，使用显式结构声明或已审计历史映射。
# 约束：不是授权检查或事实校验；未知旧版本不能仅靠添加结构声明冒充当前版本。
def compatible_extraction(email, target):
    version = email.get("extract_prompt_version")
    if version == target:
        return True
    batch = email.get("synthetic_batch")
    if not isinstance(batch, str) or not batch or version != f"{batch}:fixture-extract-v1":
        return False
    schema = email.get("extract_schema_version", LEGACY_FIXTURE_SCHEMAS.get(version))
    return schema == target
