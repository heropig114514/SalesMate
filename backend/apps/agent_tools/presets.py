"""职责：提供可审阅的一次性工具授权范围，减少逐项配置错误。
实现：根据当前注册表列出明确的工具名；创建凭证时冻结列表，后续新增工具不自动扩权。
关联：CredentialView 可接收 preset；PresetView 只查询范围，不能创建或提升权限。
目录：
- permission_presets：构造当前工具名快照。
- PresetView：查询授权模板。
- PresetView.get：返回模板及明确工具列表。
变量索引：
- PresetView.authentication_classes：允许已登录用户或有效 Tool 凭证读取无业务数据的授权说明。
"""

from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiTypes
from apps.sales.views import SalesView
from .authentication import ToolAuthentication


# 功能：构造可审阅的授权集合。
# 输入：`registry` 为当前已注册工具。
# 输出：按模板名索引的说明与名称列表。
# 逻辑：read_only 收集只读工具，data_management 收集只读及已支持的直接写入工具。
# 约束：不包含 confirm 操作；外部动作只可准备，不能批准或执行；返回值不修改现有凭证。
def permission_presets(registry):
    return {
        "read_only": {"description": "当前全部只读工具；读取范围仍受本人及业务授权约束。", "allowed_tools": sorted(name for name, spec in registry.items() if spec["executionMode"] == "read")},
        "data_management": {"description": "当前只读与直接写入工具；普通资料无需逐次批准，外部动作仅准备，确认类操作不包含在内。", "allowed_tools": sorted(name for name, spec in registry.items() if spec["executionMode"] in {"read", "write"})},
    }


# 功能：展示工具授权模板。
# 逻辑：只查询注册表，不创建令牌。
# 约束：Tool 身份不能据此增加权限。
class PresetView(SalesView):
    authentication_classes = [ToolAuthentication, SessionAuthentication]

    # 功能：返回可授权范围。
    # 输入：`request` 认证身份。
    # 输出：模板和名称快照。
    # 逻辑：从实时注册表生成，说明显式重新授权才能获得新增工具。
    # 约束：不含令牌或业务数据。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="agent_tools_permission_presets")
    def get(self, request):
        from .registry import build_registry
        return Response({"presets": permission_presets(build_registry()), "expansion": "snapshot_at_issuance"})
