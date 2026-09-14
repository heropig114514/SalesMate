"""职责：声明账号 API 的命名空间与相对路由。
实现：me/ 查询当前身份，register/ 提供带 CSRF 保护的匿名普通账号注册。
关联：由 config.urls 挂载到 api/v1/accounts/，分别委托 views 和 registration。

目录：
- 无

变量索引：
- app_name：反向解析使用的 accounts 命名空间。
- urlpatterns：当前用户查询与账号注册的模块内路由。
"""

from django.urls import path

from .views import CurrentUserView
from .registration import RegistrationView

app_name = "accounts"
urlpatterns = [
    path("me/", CurrentUserView.as_view(), name="me"),
    path("register/", RegistrationView.as_view(), name="register"),
]
