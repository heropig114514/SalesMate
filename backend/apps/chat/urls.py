"""职责：声明聊天适配的浏览器及固定 Agent 路由。
实现：相对 /api/v1/ 挂载，避免改变既有 sales 和 crm Router。
关联：chat.views 实施两种独立认证。
目录：
- 无
变量索引：
- urlpatterns：提交、查询、重试和 Agent 三接口映射。
"""

from django.urls import path
from . import views

urlpatterns = [
    path("sales/chat/messages/", views.SubmitView.as_view()),
    path("sales/chat/requests/", views.RequestListView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/", views.RequestView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/retry/", views.RetryView.as_view()),
    path("agent/chat/requests/claim/", views.ClaimView.as_view()),
    path("agent/chat/context/", views.ContextView.as_view()),
    path("agent/chat/answers/", views.AnswerView.as_view()),
]
