"""职责：提供销售方目标画像的 owner 隔离读写接口。
实现：严格验证规范名称、人数范围和 IANA 时区；在 owner 锁内按 If-Match 保存并更新公司评分依赖。
关联：SellerProfile 持久化人工资料，priority 模块从订单和产品补充计算字段；不开放统计值手工覆盖。
目录：
- SizeRangeSerializer：验证目标客户人数闭区间。
- SizeRangeSerializer.validate：拒绝倒置范围。
- SellerProfileSerializer：声明可缺失的销售方资料。
- SellerProfileSerializer.validate_time_zone：检查 IANA 时区。
- SellerProfileResponseSerializer：声明资料及乐观锁版本。
- SellerProfileView：维护当前用户自己的销售方画像。
- SellerProfileView.get：读取已有配置或无默认画像的空配置。
- SellerProfileView.patch：合并明确提交字段并触发依赖更新。
变量索引：
- SizeRangeSerializer.min：非负人数下界。
- SizeRangeSerializer.max：非负人数上界。
- SellerProfileSerializer.target_industries：规范目标行业，可清空为未知。
- SellerProfileSerializer.target_company_size：目标人数范围，可空。
- SellerProfileSerializer.service_regions：规范服务地区，可清空为未知。
- SellerProfileSerializer.time_zone：可空 IANA 时区，不自动补业务默认值。
- SellerProfileResponseSerializer.revision：读取与更新所需版本。
- SellerProfileResponseSerializer.profile：当前显式保存的画像。
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers as s
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import check_version
from apps.crm.serializers import StrictSerializer
from .models import SellerProfile
from .priority import refresh_owner_priority
from .services import audit


# 功能：验证目标客户人数闭区间。
# 逻辑：上下界均明确提供并保持非负整数。
# 约束：不补默认范围，不把未知人数转换为零。
class SizeRangeSerializer(StrictSerializer):
    min = s.IntegerField(min_value=0)
    max = s.IntegerField(min_value=0)

    # 功能：拒绝倒置范围。
    # 输入：`attrs` 为已验证整数边界。
    # 输出：原字典；上下界倒置抛 ValidationError。
    # 逻辑：比较闭区间两端，不变更用户填写的值。
    # 约束：无数据库或外部调用。
    def validate(self, attrs):
        if attrs["min"] > attrs["max"]:
            raise s.ValidationError("目标规模下界不得大于上界。")
        return attrs


# 功能：声明可缺失的销售方资料。
# 逻辑：所有字段均可省略，null 或空数组表示明确清除现有资料。
# 约束：均值、产品目录和相似赢单是派生字段，不能从本接口写入。
class SellerProfileSerializer(StrictSerializer):
    target_industries = s.ListField(child=s.CharField(max_length=100), required=False, allow_empty=True)
    target_company_size = SizeRangeSerializer(required=False, allow_null=True)
    service_regions = s.ListField(child=s.CharField(max_length=100), required=False, allow_empty=True)
    time_zone = s.CharField(max_length=100, required=False, allow_null=True)

    # 功能：检查 IANA 时区。
    # 输入：`value` 为时区字符串或 null。
    # 输出：验证后的原值；不可识别时抛 ValidationError。
    # 逻辑：使用运行时同一 ZoneInfo 数据库，确保 Agent 可解释仅日期的信号。
    # 约束：不替换成 UTC 或服务器时区；null 表示由评分时钟解释。
    def validate_time_zone(self, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise s.ValidationError("必须提供有效 IANA 时区。") from None
        return value


# 功能：声明资料及乐观锁版本。
# 逻辑：返回 profile 与 revision，ETag 与该版本一致。
# 约束：不返回其他员工配置或计算出的订单统计。
class SellerProfileResponseSerializer(s.Serializer):
    revision = s.IntegerField()
    profile = SellerProfileSerializer()


# 功能：维护当前用户自己的销售方画像。
# 逻辑：会话认证后直接以 request.user 定位单例，不接受客户端 owner。
# 约束：共享客户权限不授予读取或修改销售方画像的权限。
class SellerProfileView(APIView):
    # 功能：读取已有配置或无默认画像的空配置。
    # 输入：`request` 为当前认证请求。
    # 输出：profile、revision 与相同值的 ETag。
    # 逻辑：未配置时只读返回版本 0，不创建记录。
    # 约束：GET 不入队、不写数据库。
    @extend_schema(responses=SellerProfileResponseSerializer, tags=["sales"])
    def get(self, request):
        profile = SellerProfile.objects.filter(owner=request.user).first()
        revision = profile.revision if profile else 0
        return Response({"revision": revision, "profile": profile.profile if profile else {}}, headers={"ETag": f'"{revision}"'})

    # 功能：合并明确提交字段并触发依赖更新。
    # 输入：`request` 包含画像字段和 If-Match。
    # 输出：保存后的画像及新版本；旧版本、非法字段和时区返回明确错误。
    # 逻辑：先锁 owner，验证版本与完整合并值，仅实际变更时审计、递增画像版本并更新所有客户。
    # 约束：事务回滚同时撤销配置、公司版本和任务；不改变运行模式、不调用模型。
    @extend_schema(request=SellerProfileSerializer, responses=SellerProfileResponseSerializer, tags=["sales"],
                   parameters=[OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True)])
    @transaction.atomic
    def patch(self, request):
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        current = SellerProfile.objects.filter(owner=request.user).first()
        check_version((request.headers.get("If-Match") or "").strip('"'), current.revision if current else 0)
        changes = SellerProfileSerializer(data=request.data)
        changes.is_valid(raise_exception=True)
        combined = {**(current.profile if current else {}), **changes.validated_data}
        checked = SellerProfileSerializer(data=combined)
        checked.is_valid(raise_exception=True)
        if current is None and not combined:
            return Response({"revision": 0, "profile": {}}, headers={"ETag": '"0"'})
        if current is None or combined != current.profile:
            current = current or SellerProfile(owner=request.user)
            current.profile = dict(checked.validated_data)
            current.revision += 1
            current.save()
            audit(request.user, current, "seller_profile_updated", {"fields": sorted(changes.validated_data)})
            refresh_owner_priority(request.user.pk)
        return Response({"revision": current.revision, "profile": current.profile}, headers={"ETag": f'"{current.revision}"'})
