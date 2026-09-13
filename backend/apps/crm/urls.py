"""职责：注册业务、会话、演示及 Agent HTTP 路由。
实现：DRF Router 提供既有接口，显式路径提供同步进度、重试和人工复核。
关联：由 config.urls 挂载到 /api/v1/。
目录：
- 无
变量索引：
- router：业务 ViewSet 路由注册表。
- urlpatterns：会话与业务接口列表。
"""
from django.urls import path
from rest_framework.routers import SimpleRouter

from .processing_views import SyncRunView, EmailReviewsView, EmailReviewView
from .views import AgentViewSet, CompanyViewSet, DemoViewSet, MailboxViewSet, SessionView

router = SimpleRouter()
router.register("companies", CompanyViewSet, basename="companies")
router.register("mailboxes", MailboxViewSet, basename="mailboxes")
router.register("demo", DemoViewSet, basename="demo")
router.register("agent", AgentViewSet, basename="agent")
urlpatterns = [
    path("mailbox-sync-runs/<uuid:run_id>/", SyncRunView.as_view()),
    path("email-reviews/", EmailReviewsView.as_view()),
    path("email-reviews/<path:email_id>/", EmailReviewView.as_view()),
    path("mailboxes/<uuid:mailbox_id>/email-reviews/", EmailReviewsView.as_view()),
    path("session/", SessionView.as_view(), name="session"), *router.urls]
