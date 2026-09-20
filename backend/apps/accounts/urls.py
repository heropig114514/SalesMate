"""职责：声明账号 API 的命名空间与相对路由。
实现：onboarding/ 维护个人产品方案与私有文件；company-profile/ 维护本公司资料，me/ 查询当前身份，me/reset/ 清空内部数据并保留登录；register/ 提供带 CSRF 保护的匿名普通账号注册。
关联：由 config.urls 挂载到 api/v1/accounts/，分别委托 views、company_profile 和 registration。

目录：
- 无

变量索引：
- app_name：反向解析使用的 accounts 命名空间。
- urlpatterns：当前用户、内部数据清空、公司资料、引导与账号注册的模块内路由。
"""

from django.urls import path

from .onboarding import SetupView, DocumentView
from .views import CurrentUserView
from .company_profile import CompanyProfileView
from .registration import RegistrationView
from .reset import ResetView

app_name = "accounts"
urlpatterns = [
    path("me/reset/", ResetView.as_view(), name="reset"),
    path("onboarding/", SetupView.as_view(), name="onboarding"),
    path("onboarding/documents/", DocumentView.as_view(http_method_names=["post", "options"]), name="setup-document-upload"),
    path("onboarding/documents/<uuid:document_id>/", DocumentView.as_view(http_method_names=["get", "head", "options"]), name="setup-document"),
    path("company-profile/", CompanyProfileView.as_view(), name="company-profile"),
    path("me/", CurrentUserView.as_view(), name="me"),
    path("register/", RegistrationView.as_view(), name="register"),
]
