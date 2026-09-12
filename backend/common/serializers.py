"""职责：声明错误和健康检查响应的 OpenAPI 字段结构。
实现：使用 DRF 字段表达枚举、任意 JSON 详情及可空请求 ID；不负责数据库探测或异常捕获。
关联：供 common.views、accounts.views 的 extend_schema 使用；运行时视图直接构造对应字典。

目录：
- ErrorDetailSerializer：声明标准错误的代码和详情字段。
- ApiErrorSerializer：声明带请求 ID 的统一错误响应。
- LivenessSerializer：声明服务存活响应。
- ReadinessSerializer：声明数据库就绪响应。

变量索引：
- ErrorDetailSerializer.code：机器可读错误代码。
- ErrorDetailSerializer.detail：可包含嵌套结构的原始错误详情。
- ApiErrorSerializer.error：统一错误的嵌套结构。
- ApiErrorSerializer.request_id：用于关联日志的请求 ID，允许为空。
- LivenessSerializer.status：限定为 ok 的存活标志。
- LivenessSerializer.service：服务标识字符串。
- ReadinessSerializer.status：就绪检查总体状态枚举。
- ReadinessSerializer.database：数据库状态枚举。
"""

from rest_framework import serializers


# 功能：声明标准错误的代码和详情字段。
# 逻辑：code 使用字符串，detail 使用 JSONField 以保留 DRF 多种错误形态。
# 约束：不负责判定异常类型或生成 HTTP 状态码。
class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.JSONField()


# 功能：声明带请求 ID 的统一错误响应。
# 逻辑：嵌套 ErrorDetailSerializer，request_id 允许为 None。
# 约束：用于描述接口结构；实际包装逻辑位于 api_exception_handler。
class ApiErrorSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()
    request_id = serializers.CharField(allow_null=True)


# 功能：声明服务存活响应。
# 逻辑：status 限定为 ok，service 保存服务标识字符串。
# 约束：不涉及数据库或迁移状态；仅表达存活视图输出。
class LivenessSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])
    service = serializers.CharField()


# 功能：声明数据库就绪响应。
# 逻辑：status 与 database 各自限定为 ok 或 unavailable。
# 约束：序列化器不实施两个字段的一致性验证；对应视图负责构造匹配值。
class ReadinessSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "unavailable"])
    database = serializers.ChoiceField(choices=["ok", "unavailable"])
