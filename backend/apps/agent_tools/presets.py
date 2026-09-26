"""Responsibility: Provide reviewable one-time tool authorization scopes and reduce item-by-item configuration errors.
Implementation: Explicit experiment mode requires no login or separate token; production mode retains original authorization. List explicit tool names from the current registry; credential creation freezes the list so later tools do not expand permission automatically.
Relationships: ``CredentialView`` can accept a preset; ``PresetView`` only queries scope and cannot create or elevate permission.
Directory:
- permission_presets: Construct a snapshot of current tool names.
- PresetView: Query authorization presets.
- PresetView.get: Return presets and explicit tool lists.
Variable index:
- PresetView.authentication_classes: Permit a logged-in user or valid Tool credential to read authorization descriptions containing no business data.
"""

from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiTypes
from apps.sales.views import SalesView
from .authentication import ToolAuthentication


# Function: Construct reviewable authorization sets.
# Inputs: ``registry`` contains currently registered tools.
# Outputs: Descriptions and name lists indexed by preset name.
# Logic: ``read_only`` collects read tools; ``data_management`` collects read tools and supported direct-write tools.
# Constraints: Excludes confirm operations; external actions may only be prepared, not approved or executed; return value does not modify existing credentials.
def permission_presets(registry):
    return {
        "read_only": {"description": "当前全部只读工具；读取范围仍受本人及业务授权约束。", "allowed_tools": sorted(name for name, spec in registry.items() if spec["executionMode"] == "read")},
        "data_management": {"description": "当前只读与直接写入工具；普通资料无需逐次批准，外部动作仅准备，确认类操作不包含在内。", "allowed_tools": sorted(name for name, spec in registry.items() if spec["executionMode"] in {"read", "write"})},
    }


# Function: Present tool-authorization presets.
# Logic: Queries only the registry and creates no token.
# Constraints: Tool identity cannot use this to increase permissions.
class PresetView(SalesView):
    authentication_classes = [ToolAuthentication, SessionAuthentication]

    # Function: Return authorizable scope.
    # Inputs: Authenticated identity from ``request``.
    # Outputs: Preset and name snapshots.
    # Logic: Generate from the live registry and indicate that new tools require explicit reauthorization.
    # Constraints: Contains no tokens or business data.
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="agent_tools_permission_presets")
    def get(self, request):
        from .registry import build_registry
        return Response({"presets": permission_presets(build_registry()), "expansion": "snapshot_at_issuance"})
