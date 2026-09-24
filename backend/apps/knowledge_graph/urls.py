"""职责：声明图谱查询及外部观察输入路由。
实现：schema 与观察接口执行自动关联；状态、实体和血缘保持独立；crmarena 提供冻结公开研究接口。
关联：config.urls 挂载 /api/v1/graph/，views 负责身份与同步检查。
目录：
- 无
变量索引：
- urlpatterns：图谱查询、外部观察和公开研究 URL 映射。
"""
from django.urls import path
from .views import EntityView, FactView, GraphStatusView, LineageView
from .crmarena_views import CRMArenaInfoView, CRMArenaLeadView
from .episode_views import SchemaView, EpisodeView, EpisodeDetailView, EpisodeRetractView

urlpatterns = [
    path("schema/", SchemaView.as_view()),
    path("episodes/", EpisodeView.as_view()),
    path("episodes/<uuid:episode_id>/", EpisodeDetailView.as_view()),
    path("episodes/<uuid:episode_id>/retract/", EpisodeRetractView.as_view()),
    path("crmarena/", CRMArenaInfoView.as_view()),
    path("crmarena/evidence/", CRMArenaLeadView.as_view(operation="evidence")),
    path("crmarena/evaluation/", CRMArenaLeadView.as_view(operation="evaluation")),
    path("crmarena/predict/", CRMArenaLeadView.as_view(operation="predict")),
    path("status/", GraphStatusView.as_view()),
    path("entities/", EntityView.as_view()),
    path("facts/", FactView.as_view()),
    path("facts/<uuid:fact_id>/lineage/", LineageView.as_view()),
]
