"""职责：提供不要求认证的服务存活与默认数据库就绪检查。
实现：存活检查不访问数据库；就绪检查执行 SELECT 1，数据库异常或意外结果返回 503 并记录脱敏诊断。
关联：由 config.urls 暴露，依赖连接配置、响应序列化器及中间件 request_id；不验证迁移和 pgvector。

目录：
- LivenessView：提供无需数据库和身份认证的存活检查。
- LivenessView.get：返回后端进程的存活状态。
- ReadinessView：提供默认数据库连接往返检查。
- ReadinessView.get：检查默认数据库能否返回预期查询结果。

变量索引：
- logger：salesmate.health 日志记录器，仅输出脱敏数据库诊断。
- LivenessView.authentication_classes：空列表，避免存活探测触发会话认证。
- LivenessView.permission_classes：AllowAny，允许匿名存活检查。
- ReadinessView.authentication_classes：空列表，避免就绪探测触发会话认证。
- ReadinessView.permission_classes：AllowAny，允许匿名数据库探测。
"""

import logging

from django.db import DatabaseError, connections
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import LivenessSerializer, ReadinessSerializer

logger = logging.getLogger("salesmate.health")


# 功能：提供无需数据库和身份认证的存活检查。
# 逻辑：显式清空认证器并允许匿名访问，GET 返回固定服务标识。
# 约束：只证明请求能经过应用处理，不证明数据库、迁移或外部依赖就绪。
class LivenessView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    # 功能：返回后端进程的存活状态。
    # 输入：`request` 为 DRF Request，本方法不读取其业务数据。
    # 输出：返回 200 Response，status 为 ok，service 为 salesmate-backend。
    # 逻辑：构造固定字典，不进行数据库查询和会话认证。
    # 约束：没有业务写入；数据库不可用不影响该方法返回存活状态。
    @extend_schema(responses=LivenessSerializer, tags=["health"])
    def get(self, request):
        return Response({"status": "ok", "service": "salesmate-backend"})


# 功能：提供默认数据库连接往返检查。
# 逻辑：允许匿名 GET，通过连接上下文执行探测并统一报告数据库故障。
# 约束：不验证迁移完成、数据表、向量扩展或其他外部服务。
class ReadinessView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    # 功能：检查默认数据库能否返回预期查询结果。
    # 输入：`request` 为 DRF Request；故障日志使用中间件设置的 request_id。
    # 输出：成功返回 200 和 ok；DatabaseError 返回 503 和 unavailable。
    # 逻辑：在游标上下文执行 SELECT 1，并要求结果为 (1,)；异常结果也转换为 DatabaseError，故障日志仅保留异常类型与排查提示。
    # 约束：发起数据库读取但不写业务表；非 DatabaseError 不在本方法捕获。连接等待时间由 DATABASES 配置决定，游标由上下文关闭。
    @extend_schema(
        responses={200: ReadinessSerializer, 503: ReadinessSerializer},
        tags=["health"],
        description="Check the configured default database. Does not verify migrations.",
    )
    def get(self, request):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                # 连接未抛异常仍不足以判定成功；返回值不符合探测协议时走同一故障分支。
                if cursor.fetchone() != (1,):
                    raise DatabaseError("Unexpected database readiness response.")
        except DatabaseError as exc:
            # A connection error can include credentials or host details: record its type only.
            logger.error(
                "database_readiness_failed request_id=%s alias=default error_type=%s "
                "action=check_default_database_and_DATABASE_URL",
                request.request_id,
                type(exc).__name__,
            )
            return Response({"status": "unavailable", "database": "unavailable"}, status=503)
        return Response({"status": "ok", "database": "ok"})
