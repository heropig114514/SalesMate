"""职责：定义当前用户接口的只读身份字段集合。
实现：ModelSerializer 仅暴露显式列出的四个字段，密码、邮箱和权限标志不进入该响应。
关联：使用 accounts.models.User，供 CurrentUserView 输出与 Schema 生成使用。

目录：
- CurrentUserSerializer：输出当前用户允许公开的身份字段。
- CurrentUserSerializer.Meta：配置模型映射及只读字段白名单。

变量索引：
- CurrentUserSerializer.Meta.model：提供序列化字段的 User 模型。
- CurrentUserSerializer.Meta.fields：允许返回的四个身份字段。
- CurrentUserSerializer.Meta.read_only_fields：与 fields 相同，禁止通过此序列化器写入。
"""

from rest_framework import serializers

from .models import User


# 功能：输出当前用户允许公开的身份字段。
# 逻辑：通过 Meta 的显式字段白名单限制序列化结果，四个字段均只读。
# 约束：不承担身份验证，也不返回邮箱、密码或权限标志。
class CurrentUserSerializer(serializers.ModelSerializer):
    # 功能：配置模型映射及只读字段白名单。
    # 逻辑：model 指向 User，fields 和 read_only_fields 使用同一字段集合。
    # 约束：变更字段将影响接口契约，需同步核对调用方及 Schema。
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name"]
        read_only_fields = fields
