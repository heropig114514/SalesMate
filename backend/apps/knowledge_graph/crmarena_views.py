"""职责：公开 CRMArena 研究图谱和模型的认证 HTTP 边界。
实现：固定数据空间和严格输入；证据、历史回放、实时推理使用不同路由及执行类型。
关联：SalesView 保留现有身份和 Session CSRF；agent_tools 复用 execute_crmarena。
目录：
- LeadRequest：严格公开 Lead 请求。
- LeadRequest.validate：拒绝未知字段和隐式类型转换。
- execute_crmarena：固定操作服务分派。
- CRMArenaInfoView：元数据视图。
- CRMArenaInfoView.get：读取版本与观察到的运行状态。
- CRMArenaLeadView：Lead 操作视图。
- CRMArenaLeadView.post：执行取证、回放或推理。
变量索引：
- LeadRequest.dataset_id：必填公开数据空间。
- LeadRequest.lead_id：15至18位公开字母数字标识。
- CRMArenaLeadView.operation：由 URL 固定的操作，不由请求选择。
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.sales.views import SalesView
from .crmarena import DATASET_ID, get_store
from .crmarena_runtime import runtime


# 功能：限定公开样本命名空间与精确 Lead ID。
# 逻辑：不接受任意文本、路径、客户 UUID、模型参数或自定义问题。
# 约束：此请求不表示私有客户授权；这里只处理已发布合成样本。
class LeadRequest(serializers.Serializer):
    dataset_id = serializers.ChoiceField(choices=[DATASET_ID])
    lead_id = serializers.RegexField(r"^[A-Za-z0-9]{15,18}$", trim_whitespace=False, max_length=18)

    # 功能：阻止未定义字段和类型转换。
    # 输入：`attrs` 为字段初步校验后的字典，initial_data 为原 JSON。
    # 输出：合法 attrs；非法请求抛 400。
    # 逻辑：要求字段精确匹配且原输入都是字符串。
    # 约束：不会清洗或猜测 ID。
    def validate(self, attrs):
        if set(self.initial_data) != set(self.fields) or any(type(v) is not str for v in self.initial_data.values()):
            raise ValidationError("只接受字符串字段 dataset_id 和 lead_id。")
        return attrs


# 功能：为 HTTP 与 MCP 执行同一固定业务入口。
# 输入：`operation` 为内部白名单，`data` 为请求对象。
# 输出：JSON 数据；未知操作抛 ValueError，非法输入由 serializer 拒绝。
# 逻辑：先验证输入再加载制品；info 不接受业务参数，predict 显式调用运行器。
# 约束：调用者负责认证与工具授权；不会把实时失败转为历史回放。
def execute_crmarena(operation, data):
    if operation == "info":
        if data:
            raise ValidationError("info 不接受参数。")
        return {**get_store().info(), "runtime": runtime.status()}
    if operation not in {"evidence", "evaluation", "predict"}:
        raise ValueError("Unregistered CRMArena operation")
    serializer = LeadRequest(data=data)
    serializer.is_valid(raise_exception=True)
    lead_id = serializer.validated_data["lead_id"]
    store = get_store()
    if operation == "predict":
        return runtime.predict(store, lead_id)
    if operation == "evidence":
        return store.evidence(lead_id)
    return store.evaluation(lead_id)


# 功能：展示公开制品状态及研究限制。
# 逻辑：复用已认证的 SalesView。
# 约束：读取状态不会尝试加载 GPU 模型。
class CRMArenaInfoView(SalesView):
    # 功能：读取元数据。
    # 输入：`request` 为认证请求，不接受查询参数。
    # 输出：200 元数据；未安装 503；非法参数 400。
    # 逻辑：使用与 MCP 相同的 info 操作。
    # 约束：正式模式仍要求登录。
    @extend_schema(responses=OpenApiTypes.OBJECT, operation_id="crmarena_info")
    def get(self, request):
        return Response(execute_crmarena("info", request.query_params))


# 功能：提供三个语义独立的公开 Lead 操作。
# 逻辑：URL 在 as_view 中绑定 operation。
# 约束：结果不写入客户、商机、订单或现有实时业务图。
class CRMArenaLeadView(SalesView):
    operation = None

    # 功能：执行明确选定的 Lead 操作。
    # 输入：`request` 为 LeadRequest 的 JSON。
    # 输出：200 证据/回放/实时结果；400/404/429/502/503 保留明确失败语义。
    # 逻辑：不进行自动重试或降级，沿用 Session CSRF。
    # 约束：仅 predict 会消耗 GPU，且保持原实验参数。
    @extend_schema(request=LeadRequest, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        return Response(execute_crmarena(self.operation, request.data))
