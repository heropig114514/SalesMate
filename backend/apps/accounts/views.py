"""职责：提供当前已认证用户的身份查询接口。
实现：继承全局 SessionAuthentication 和 IsAuthenticated，序列化 request.user；不实现登录或邮箱授权。
关联：调用 CurrentUserSerializer，错误结构由 common.exceptions 统一包装。

目录：
- CurrentUserView：查询已认证用户的公开身份信息。
- CurrentUserView.get：返回当前通过权限检查的用户身份。

变量索引：
- 无
"""

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import ApiErrorSerializer

from .serializers import CurrentUserSerializer


# 功能：查询已认证用户的公开身份信息。
# 逻辑：使用项目默认认证和权限配置，GET 委托白名单序列化器。
# 约束：不提供登录流程或团队、邮箱权限判断；未认证请求由 DRF 在进入 get 前拒绝。
class CurrentUserView(APIView):
    # 功能：返回当前通过权限检查的用户身份。
    # 输入：`request` 为 DRF Request，其 user 已由认证及权限流程处理。
    # 输出：返回状态 200 的 Response，包含 id、username、first_name、last_name。
    # 逻辑：直接序列化 request.user，避免另行查询或暴露模型全部字段。
    # 约束：不写数据库、不变更会话；依赖全局 IsAuthenticated，序列化异常保持框架处理方式。
    @extend_schema(
        responses={200: CurrentUserSerializer, 403: ApiErrorSerializer},
        tags=["accounts"],
        description="Return the current session user. Team and mailbox access rules are not implemented yet.",
    )
    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)
