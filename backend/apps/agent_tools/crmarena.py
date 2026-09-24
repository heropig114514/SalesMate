"""职责：将 CRMArena 公开图谱与模型发布为明确授权的 MCP 工具。
实现：四个固定只读工具，复用 HTTP 服务与严格数据命名空间。
关联：registry 发布 Schema，dispatch 分派，已有 stdio 桥动态读取目录。
目录：
- crmarena_specs：建立工具声明。
- execute_crmarena_tool：执行获准操作。
变量索引：
- 无
"""
from rest_framework.response import Response
from .schemas import object_schema


# 功能：声明四个独立研究工具。
# 输入：`tool` 为现有注册表构造函数。
# 输出：info、evidence、evaluation、predict 的只读工具列表。
# 逻辑：要求显式公开数据空间；实时推理在描述中声明 GPU 条件与质量限制。
# 约束：不扩大已有凭证的 allowed_tools；没有写回或任意模型调用能力。
def crmarena_specs(tool):
    props = {"dataset_id": {"enum": ["crmarena-pro-b2b-v3"]},
             "lead_id": {"type": "string", "pattern": "^[A-Za-z0-9]{15,18}$", "maxLength": 18}}
    descriptions = {
        "info": "读取公开 CRMArena 图谱、模型版本、13.3%回归成绩与运行状态；不加载模型。",
        "evidence": "读取公开合成 Lead 的通话、产品价格、政策及来源；不生成判断、不查询私有 CRM。",
        "evaluation": "回放75题中一题的历史 Kaggle 结果；明确 recorded_evaluation，不是实时预测。",
        "predict": "对75道冻结公开题执行实时 BANT 推理；需要已配置固定权重和双T4，耗时较长；实验正确率13.3%，不自动决定客户去留，不写库。",
    }
    return [tool("crmarena." + operation, description, "crmarena",
                 object_schema({}) if operation == "info" else object_schema(props, ["dataset_id", "lead_id"]),
                 operation=operation) for operation, description in descriptions.items()]


# 功能：调用同一后端服务并保留 HTTP 错误语义。
# 输入：`spec` 为注册表声明，`args` 为已获准的参数。
# 输出：Response；底层异常直接传播给现有工具错误边界。
# 逻辑：只使用注册表固定 operation，不接受 URL 或函数名。
# 约束：认证与逐工具授权由 services.invoke 执行。
def execute_crmarena_tool(spec, args):
    from apps.knowledge_graph.crmarena_views import execute_crmarena
    return Response(execute_crmarena(spec["operation"], args))
