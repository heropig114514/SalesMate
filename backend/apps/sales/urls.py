"""职责：声明销售业务 API 路由。
实现：新增全球洞察、销售方统一资料和商机上下文/评分查询；业务写接口保持原权限；browse 只读入口合并获准实验记录；固定操作优先，资源名由各视图白名单验证。
关联：config.urls 挂载于 /api/v1/sales/，视图沿用正式登录或实验公开身份。
目录：
- 无
变量索引：
- urlpatterns：记录、目录、文件、授权与审计的路径映射。
"""

from django.urls import path
from . import views
from .qq_connection import QQSendConnectionView
from .priority_views import SellerProfileView
from .browse import BrowseOverviewView, BrowseView
from .world import WorldView
from .algorithm_views import SellerContextView, OpportunityContextView, PriorityBoardView

urlpatterns = [
    path("world/", WorldView.as_view()),
    path("seller-context/", SellerContextView.as_view()),
    path("opportunity-context/<uuid:opportunity_id>/", OpportunityContextView.as_view()),
    path("priority-board/", PriorityBoardView.as_view()),
    path("seller-profile/", SellerProfileView.as_view()),
    path("catalog/", views.CatalogView.as_view()),
    path("overview/", views.OverviewView.as_view()),
    path("browse/overview/", BrowseOverviewView.as_view()),
    path("browse/<str:resource>/", BrowseView.as_view()),
    path("directory/", views.DirectoryView.as_view()),
    path("people/", views.PeopleView.as_view()),
    path("directory/<uuid:company_id>/contacts/", views.ContactView.as_view()),
    path("grouping/<str:operation>/", views.GroupingView.as_view()),
    path(
        "records/<str:resource>/",
        views.ResourceView.as_view(http_method_names=["get", "post", "options"]),
    ),
    path(
        "records/<str:resource>/<uuid:record_id>/",
        views.ResourceDetailView.as_view(http_method_names=["get", "patch", "options"]),
    ),
    path(
        "records/<str:resource>/<uuid:record_id>/commands/", views.CommandView.as_view()
    ),
    path("files/", views.FileView.as_view(http_method_names=["post", "options"])),
    path(
        "files/<uuid:record_id>/download/",
        views.FileView.as_view(http_method_names=["get", "options"]),
    ),
    path("oauth/", views.OAuthView.as_view(), name="sales-oauth"),
    path("connections/qq/", QQSendConnectionView.as_view()),
    path("calendar/<str:operation>/", views.CalendarView.as_view()),
    path("audit/", views.AuditView.as_view()),
]
