"""职责：声明工具、用户授权和提案确认的独立路径。
实现：固定路由，调用名称通过 JSON 白名单解析。
关联：config.urls 挂载 /api/v1/agent-tools/。
目录：
- 无
变量索引：
- urlpatterns：工具发现、调用及 Session-only 控制接口。
"""

from django.urls import path
from . import views

urlpatterns = [
    path("catalog/", views.CatalogView.as_view()),
    path("call/", views.CallView.as_view()),
    path("credentials/", views.CredentialView.as_view()),
    path("credentials/<uuid:credential_id>/", views.CredentialDetailView.as_view()),
    path("proposals/", views.ProposalView.as_view()),
    path("proposals/<uuid:proposal_id>/", views.ProposalDetailView.as_view()),
    path("proposals/<uuid:proposal_id>/decision/", views.DecisionView.as_view()),
]
