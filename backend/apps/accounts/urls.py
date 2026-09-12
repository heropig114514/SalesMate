"""职责：声明账号 API 的命名空间与相对路由。
实现：将 me/ 映射到当前用户视图，实际认证由 DRF 默认配置执行。
关联：由 config.urls 挂载到 api/v1/accounts/。

目录：
- 无

变量索引：
- app_name：反向解析使用的 accounts 命名空间。
- urlpatterns：当前用户查询的模块内路由。
"""

from django.urls import path

from .views import CurrentUserView

app_name = "accounts"
urlpatterns = [path("me/", CurrentUserView.as_view(), name="me")]
