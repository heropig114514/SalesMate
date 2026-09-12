"""职责：注册自定义用户模型的 Django Admin 管理界面。
实现：导入时将 User 绑定到框架 UserAdmin；注册属于模块导入副作用。
关联：复用 accounts.models.User 和 django.contrib.auth.admin.UserAdmin。

目录：
- 无

变量索引：
- 无
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.register(User, UserAdmin)
