"""职责：从首次迁移起声明项目拥有的用户模型。
实现：继承 AbstractUser 的字段和行为，当前不增加团队或邮箱权限字段。
关联：由 AUTH_USER_MODEL、Admin、用户序列化器及初始迁移共同引用。

目录：
- User：声明项目自定义用户类型。

变量索引：
- 无
"""

from django.contrib.auth.models import AbstractUser


# 功能：声明项目自定义用户类型。
# 逻辑：完整继承 AbstractUser，使后续身份扩展不需要更换 AUTH_USER_MODEL。
# 约束：当前没有团队、邮箱绑定和 Agent 服务身份能力；数据库结构以迁移为准。
class User(AbstractUser):
    """Own the user model from the first migration so identity can evolve safely."""
