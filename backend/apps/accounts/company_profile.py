"""职责：提供当前账号工作空间的本公司资料接口。
实现：显式字段验证、会话权限、owner 行锁和 If-Match 防止越权及并发覆盖；读取不创建记录。
关联：CompanyProfile 独立于 CRM 客户及销售目标画像；前端 company-settings.js 调用。
目录：
- CompanyProfileSerializer：验证并输出公司资料。
- CompanyProfileSerializer.Meta：声明字段白名单。
- CompanyProfileView：读取和保存账号自己的公司资料。
- CompanyProfileView.get：返回已存资料或版本为零的空资料。
- CompanyProfileView.patch：按版本合并保存并记录非敏感审计日志。
变量索引：
- logger：仅记录账号标识、版本及修改字段名。
- CompanyProfileSerializer.Meta.model：资料模型。
- CompanyProfileSerializer.Meta.fields：可见资料及版本字段。
- CompanyProfileSerializer.Meta.read_only_fields：服务端维护的版本和时间。
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import check_version
from apps.sales.serializers import StrictModelSerializer
from .models import CompanyProfile

logger = logging.getLogger(__name__)


# 功能：验证并输出公司资料。
# 逻辑：复用严格字段拒绝规则和模型长度、邮箱与 URL 校验。
# 约束：不允许 owner、revision 或未知字段写入；公司名称必填，其他字段可清空。
class CompanyProfileSerializer(StrictModelSerializer):
    # 功能：声明字段白名单。
    # 逻辑：排除 owner，服务端版本及时间只读。
    # 约束：不暴露邮箱授权、团队信息或客户资料。
    class Meta:
        model = CompanyProfile
        fields = ["company_name", "industry", "website", "email", "phone", "address", "description", "revision", "updated_at"]
        read_only_fields = ["revision", "updated_at"]


# 功能：读取和保存账号自己的公司资料。
# 逻辑：沿用全局 SessionAuthentication/IsAuthenticated，以 request.user 定位唯一记录。
# 约束：不提供按 ID 访问其他账号的接口；写入受会话 CSRF 和版本约束。
class CompanyProfileView(APIView):
    # 功能：返回已存资料或版本为零的空资料。
    # 输入：`request` 为认证后的会话请求。
    # 输出：公司资料和 ETag；未保存时 updated_at 为 null。
    # 逻辑：未找到记录时构造未保存模型，仅供序列化。
    # 约束：不写数据库，不触发评分或邮件任务。
    @extend_schema(responses=CompanyProfileSerializer, tags=["accounts"])
    def get(self, request):
        profile = CompanyProfile.objects.filter(owner=request.user).first() or CompanyProfile(owner=request.user)
        return Response(CompanyProfileSerializer(profile).data, headers={"ETag": f'"{profile.revision}"'})

    # 功能：按版本合并保存并记录非敏感审计日志。
    # 输入：`request` 提供字段 JSON 及 If-Match 版本。
    # 输出：已保存资料；无效字段或缺失版本为 400，过期版本为 409。
    # 逻辑：先锁用户行，覆盖首次创建的竞争；校验后仅在实际变更时递增版本。
    # 约束：失败事务不写入；日志不含公司字段值；不改变客户、评分或邮箱配置。
    @extend_schema(request=CompanyProfileSerializer, responses=CompanyProfileSerializer, tags=["accounts"],
                   parameters=[OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True)])
    @transaction.atomic
    def patch(self, request):
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        current = CompanyProfile.objects.filter(owner=request.user).first()
        check_version((request.headers.get("If-Match") or "").strip('"'), current.revision if current else 0)
        serializer = CompanyProfileSerializer(current, data=request.data, partial=current is not None)
        serializer.is_valid(raise_exception=True)
        changed = sorted(key for key, value in serializer.validated_data.items() if current is None or getattr(current, key) != value)
        if current is None or changed:
            current = serializer.save(owner=request.user, revision=(current.revision if current else 0) + 1)
            logger.info("company_profile_saved owner_id=%s revision=%s fields=%s", request.user.pk, current.revision, changed)
        return Response(CompanyProfileSerializer(current).data, headers={"ETag": f'"{current.revision}"'})
