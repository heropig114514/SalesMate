"""Responsibility: Declare workspace, business, company settings, world, experiment, administration, health-check, and API-documentation routes.
Implementation: The read-only graph API exposes current facts and version lineage; the opportunity-priority page reads separately stored results; urlpatterns dispatches requests; experiment pages expose approved synthetic batches; dispatched views perform business authorization.
Relationships: Combines common.views, apps.accounts, apps.crm, apps.sales, apps.chat, apps.agent_tools, and backend/frontend; local static assets are served only when DEBUG is enabled.

Directory:
- None

Variable index:
- urlpatterns: Ordered mapping of project URL paths to views.
"""

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from common.views import LivenessView, ReadinessView
from apps.sales.experiments import ExperimentView, ExperimentCatalogView, ExperimentExportView, ExperimentFileView

urlpatterns = [
    path("priorities/", TemplateView.as_view(template_name="priorities.html"), name="opportunity-priorities"),
    path("experiments/", TemplateView.as_view(template_name="experiments.html"), name="experiments"),
    path("api/v1/experiments/", ExperimentCatalogView.as_view(), name="experiment-catalog"),
    path("api/v1/experiments/<str:batch>/export/", ExperimentExportView.as_view(), name="experiment-export"),
    path("api/v1/experiments/<str:batch>/<str:label>/", ExperimentView.as_view(), name="experiment-table"),
    path("api/v1/experiments/<str:batch>/<str:label>/<str:pk>/download/", ExperimentFileView.as_view(), name="experiment-file"),
    path("", TemplateView.as_view(template_name="index.html"), name="workspace"),
    path("business/", TemplateView.as_view(template_name="business.html"), name="business-workspace"),
    path("settings/company/", TemplateView.as_view(template_name="company-settings.html"), name="company-settings"),
    path("world/", TemplateView.as_view(template_name="world.html"), name="world-news"),
    path("world/news/<slug:news_id>/", TemplateView.as_view(template_name="world.html"), name="world-news-detail"),
    path("admin/", admin.site.urls),
    path("api/v1/health/live/", LivenessView.as_view(), name="health-live"),
    path("api/v1/health/ready/", ReadinessView.as_view(), name="health-ready"),
    path("api/v1/accounts/", include("apps.accounts.urls")),
    path("api/v1/sales/", include("apps.sales.urls")),
    path("api/v1/graph/", include("apps.knowledge_graph.urls")),
    path("api/v1/agent-tools/", include("apps.agent_tools.urls")),
    path("api/v1/", include("apps.chat.urls")),
    path("api/v1/", include("apps.crm.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "frontend" / "assets")
