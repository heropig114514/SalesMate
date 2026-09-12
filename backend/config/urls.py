"""职责：集中声明工作台、业务、管理、健康检查和 API 文档路由。
实现：按 urlpatterns 分派请求；鉴权由被分派的视图和框架配置执行。
关联：组合 common.views、apps.accounts、apps.crm 和 backend/frontend；本地静态资源仅在 DEBUG 下提供。

目录：
- 无

变量索引：
- urlpatterns：项目 URL 路径与视图映射的有序列表。
"""

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from common.views import LivenessView, ReadinessView

urlpatterns = [
    path("", TemplateView.as_view(template_name="index.html"), name="workspace"),
    path("admin/", admin.site.urls),
    path("api/v1/health/live/", LivenessView.as_view(), name="health-live"),
    path("api/v1/health/ready/", ReadinessView.as_view(), name="health-ready"),
    path("api/v1/accounts/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.crm.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "frontend" / "assets")
