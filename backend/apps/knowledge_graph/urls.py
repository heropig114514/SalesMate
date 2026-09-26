"""Responsibility: Declare graph-query and external-observation input routes.
Implementation: Schema and observation endpoints perform automatic association; status, entities, facts, and lineage expose the current user's graph.
Relationships: config.urls mounts /api/v1/graph/ and views enforce identity and synchronization checks.
Directory:
- None
Variable index:
- urlpatterns: Graph query and external-observation URL mappings.
"""
from django.urls import path
from .views import EntityView, FactView, GraphStatusView, LineageView
from .episode_views import SchemaView, EpisodeView, EpisodeDetailView, EpisodeRetractView

urlpatterns = [
    path("schema/", SchemaView.as_view()),
    path("episodes/", EpisodeView.as_view()),
    path("episodes/<uuid:episode_id>/", EpisodeDetailView.as_view()),
    path("episodes/<uuid:episode_id>/retract/", EpisodeRetractView.as_view()),
    path("status/", GraphStatusView.as_view()),
    path("entities/", EntityView.as_view()),
    path("facts/", FactView.as_view()),
    path("facts/<uuid:fact_id>/lineage/", LineageView.as_view()),
]
