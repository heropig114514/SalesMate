"""职责：注册业务、会话、演示及 Agent HTTP 路由。
实现：使用 DRF SimpleRouter 生成标准尾斜线接口。
关联：由 config.urls 挂载到 /api/v1/。
目录：
- 无
变量索引：
- router：业务 ViewSet 路由注册表。
- urlpatterns：会话与业务接口列表。
"""
from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AgentViewSet, CompanyViewSet, DemoViewSet, MailboxViewSet, SessionView

router = SimpleRouter()
router.register("companies", CompanyViewSet, basename="companies")
router.register("mailboxes", MailboxViewSet, basename="mailboxes")
router.register("demo", DemoViewSet, basename="demo")
router.register("agent", AgentViewSet, basename="agent")
urlpatterns = [path("session/", SessionView.as_view(), name="session"), *router.urls]
