"""职责：声明聊天适配的浏览器及固定 Agent 路由。
实现：相对 /api/v1/ 挂载，增加请求绑定工具目录、读取和状态核对，不改变既有 Router。
关联：chat.views 与 tool_views 实施两种独立认证。
目录：
- 无
变量索引：
- urlpatterns：浏览器操作、Agent 领取/上下文/回报及工具目录/读取/状态映射。
"""

from django.urls import path
from . import tool_views, views

urlpatterns = [
    path("sales/chat/messages/", views.SubmitView.as_view()),
    path("sales/chat/requests/", views.RequestListView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/", views.RequestView.as_view()),
    path("sales/chat/requests/<uuid:request_id>/retry/", views.RetryView.as_view()),
    path("agent/chat/requests/claim/", views.ClaimView.as_view()),
    path("agent/chat/context/", views.ContextView.as_view()),
    path("agent/chat/answers/", views.AnswerView.as_view()),
    path("agent/chat/tools/", tool_views.ToolCatalogView.as_view()),
    path("agent/chat/tool-reads/", tool_views.ToolReadView.as_view()),
    path(
        "agent/chat/requests/<uuid:request_id>/", tool_views.AgentRequestView.as_view()
    ),
]
