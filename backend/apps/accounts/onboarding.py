"""职责：保存四步引导中的个人、产品、方案信息并提供私有附件读取。
实现：严格结构校验、owner 隔离及 If-Match 乐观锁；PDF/文本附件只经过认证接口读取。
关联：SalesSetup、SetupDocument；公司资料继续使用 company-profile 接口，不修改评分输入。
目录：
- StrictSerializer：拒绝未声明字段。
- StrictSerializer.to_internal_value：核对字段白名单。
- PersonalSerializer：个人身份及负责范围。
- ProductSerializer：参考产品字段。
- SolutionSerializer：方案附件引用。
- SetupSerializer：验证整份引导信息。
- SetupSerializer.validate：验证附件归属及产品价格区间。
- snapshot：输出当前账号的引导快照。
- SetupView：引导读取及版本化保存。
- SetupView.get：读取无副作用快照。
- SetupView.patch：事务保存信息。
- DocumentView：私有 PDF 和文本附件接口。
- DocumentView.post：校验并保存上传文件。
- DocumentView.get：认证后内联读取或下载文件。
变量索引：
- logger：仅记录账号、阶段、版本等非敏感上下文。
- MAX_BYTES：单个引导文件最多 5 MiB。
- PersonalSerializer.name：姓名。
- PersonalSerializer.title：职位。
- PersonalSerializer.email：联系邮箱，不代表 Gmail 授权。
- PersonalSerializer.phone：可选电话。
- PersonalSerializer.regions：负责区域列表。
- PersonalSerializer.industries：行业列表。
- ProductSerializer.name：产品名称。
- ProductSerializer.category：类别或型号。
- ProductSerializer.specifications：规格逐项文本。
- ProductSerializer.price_min：可空价格下界。
- ProductSerializer.price_max：可空价格上界。
- ProductSerializer.currency：明确的参考价格币种。
- ProductSerializer.scenarios：适用行业或场景列表。
- ProductSerializer.document_id：可空的本账号规格书引用。
- SolutionSerializer.name：方案名称。
- SolutionSerializer.document_id：本账号文件引用。
- SetupSerializer.personal：个人信息对象。
- SetupSerializer.products：最多 200 个参考产品。
- SetupSerializer.solutions：最多 100 个方案。
- SetupSerializer.completed：完成或跳过全部引导的状态。
- DocumentView.parser_classes：仅支持 multipart 上传。
"""

import logging
from pathlib import PurePath

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import check_version
from .models import SalesSetup, SetupDocument

logger = logging.getLogger(__name__)
MAX_BYTES = 5 * 1024 * 1024


# 功能：严格限制资料字段。
# 逻辑：先验证对象和未知键，再执行 DRF 标准字段校验。
# 约束：不静默丢弃拼写错误或客户端权限字段。
class StrictSerializer(serializers.Serializer):
    # 功能：核对输入结构。
    # 输入：`data` 为请求对象或嵌套对象。
    # 输出：已验证字段；未知字段抛带 non_field_errors 的 ValidationError。
    # 逻辑：按声明字段检查键集合。
    # 约束：不接受非对象载荷。
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["资料含未知字段或不是对象。"]})
        return super().to_internal_value(data)


# 功能：验证个人身份。
# 逻辑：允许跳过整个步骤，填写时校验邮箱与字段长度。
# 约束：邮箱仅用于联系资料，不创建授权或更改登录身份。
class PersonalSerializer(StrictSerializer):
    name = serializers.CharField(max_length=150, allow_blank=True)
    title = serializers.CharField(max_length=150, allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    phone = serializers.CharField(max_length=80, allow_blank=True)
    regions = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30)
    industries = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30)


# 功能：验证参考产品信息。
# 逻辑：限制数量、长度与非负价格，未知价格必须为 null。
# 约束：参考价格不创建业务报价或覆盖已有 Product 交易记录。
class ProductSerializer(StrictSerializer):
    name = serializers.CharField(max_length=240)
    category = serializers.CharField(max_length=150, allow_blank=True)
    specifications = serializers.ListField(child=serializers.CharField(max_length=500), max_length=50)
    price_min = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, allow_null=True)
    price_max = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, allow_null=True)
    currency = serializers.ChoiceField(choices=["SGD", "USD", "CNY", "EUR", "JPY"])
    scenarios = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30)
    document_id = serializers.UUIDField(allow_null=True)


# 功能：验证销售方案信息。
# 逻辑：方案名称与文件绑定，文件归属在外层检查。
# 约束：不解析或执行文件，不宣称 AI 已读取附件。
class SolutionSerializer(StrictSerializer):
    name = serializers.CharField(max_length=240)
    document_id = serializers.UUIDField()


# 功能：验证引导资料增量。
# 逻辑：各步骤独立提交；嵌套内容完整替换；附件只允许引用当前账号文件。
# 约束：不能修改 owner、revision；不触发算法或模型服务。
class SetupSerializer(StrictSerializer):
    personal = PersonalSerializer(required=False)
    products = ProductSerializer(many=True, max_length=200, required=False)
    solutions = SolutionSerializer(many=True, max_length=100, required=False)
    completed = serializers.BooleanField(required=False)

    # 功能：校验跨字段边界与私有文件引用。
    # 输入：`attrs` 为字段校验后的数据；context 中 user 是当前用户。
    # 输出：验证后的数据；价格倒置或他人附件抛 ValidationError。
    # 逻辑：批量核对引用集合，只接受全部属于当前账号的文件。
    # 约束：不查询或泄露其他账号文件内容。
    def validate(self, attrs):
        products = attrs.get("products", [])
        for product in products:
            low, high = product["price_min"], product["price_max"]
            if low is not None and high is not None and low > high:
                raise serializers.ValidationError("参考价格下限不能大于上限。")
        ids = {row["document_id"] for row in products + attrs.get("solutions", []) if row.get("document_id")}
        if SetupDocument.objects.filter(owner=self.context["user"], pk__in=ids).count() != len(ids):
            raise serializers.ValidationError("附件不存在或不属于当前账号。")
        return attrs


# 功能：构造引导快照。
# 输入：`user` 为已认证用户。
# 输出：资料、版本及本账号附件元数据字典。
# 逻辑：没有记录时使用未保存实例，避免读取产生业务写入。
# 约束：不输出文件内容，也不查询其他账号。
def snapshot(user):
    record = SalesSetup.objects.filter(owner=user).first() or SalesSetup(owner=user)
    return {"personal": record.personal, "products": record.products, "solutions": record.solutions,
            "completed": record.completed, "revision": record.revision,
            "documents": list(SetupDocument.objects.filter(owner=user).values("id", "name", "content_type"))}


# 功能：维护当前账号的引导资料。
# 逻辑：使用默认会话认证与 CSRF，写入时锁定用户行。
# 约束：API 不提供其他账号 ID 参数；公司资料由独立版本接口维护。
class SetupView(APIView):
    # 功能：读取引导快照。
    # 输入：`request` 的认证身份。
    # 输出：200 JSON，空账号 revision 为零。
    # 逻辑：委托 snapshot。
    # 约束：无数据库写入。
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=["accounts"])
    def get(self, request):
        return Response(snapshot(request.user))

    # 功能：保存某一步或完成状态。
    # 输入：`request` 含资料 JSON 与 If-Match。
    # 输出：新快照；校验失败 400，版本冲突 409。
    # 逻辑：用户行锁防止首次创建竞争，序列化表示将 Decimal/UUID 规范为 JSON 字符串。
    # 约束：失败不提交；无自动重试；日志不记录资料正文。
    @extend_schema(request=SetupSerializer, responses=OpenApiTypes.OBJECT, tags=["accounts"], parameters=[OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True)])
    @transaction.atomic
    def patch(self, request):
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        record = SalesSetup.objects.filter(owner=request.user).first() or SalesSetup(owner=request.user)
        check_version((request.headers.get("If-Match") or "").strip('"'), record.revision)
        serializer = SetupSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.data.items():
            setattr(record, key, value)
        record.revision += 1
        record.save()
        logger.info("sales_setup_saved owner_id=%s revision=%s fields=%s", request.user.pk, record.revision, sorted(serializer.data))
        return Response(snapshot(request.user))


# 功能：提供私有规格书和方案文件。
# 逻辑：限制大小与格式，所有读取显式 owner 查询。
# 约束：无公共链接；PDF 用沙盒响应，文本不作为 HTML 执行。
class DocumentView(APIView):
    parser_classes = [MultiPartParser]

    # 功能：校验并保存一份文件。
    # 输入：`request` multipart 的 file；`document_id` 创建时为空。
    # 输出：201 文件元数据；格式或大小不合约返回 400。
    # 逻辑：文件最多 5 MiB，PDF 检查签名，TXT 必须为 UTF-8；数据库保存内容与元数据。
    # 约束：不执行文件、不调用外部解析；输入失败无写入，日志不含文件名或内容。
    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT}, tags=["accounts"])
    def post(self, request, document_id=None):
        upload = request.FILES.get("file")
        if document_id or set(request.data) != {"file"} or not upload or not 0 < upload.size <= MAX_BYTES:
            raise serializers.ValidationError("请选择不超过 5 MiB 的 PDF 或 UTF-8 TXT 文件。")
        data = upload.read(MAX_BYTES + 1)
        extension = PurePath(upload.name).suffix.lower()
        if len(data) > MAX_BYTES:
            raise serializers.ValidationError("文件超过 5 MiB。")
        if extension == ".pdf" and data.startswith(b"%PDF-"):
            content_type = "application/pdf"
        elif extension == ".txt":
            try:
                data.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise serializers.ValidationError("TXT 文件必须使用 UTF-8 编码。") from error
            content_type = "text/plain; charset=utf-8"
        else:
            raise serializers.ValidationError("文件格式不符，请上传 PDF 或 UTF-8 TXT。")
        record = SetupDocument.objects.create(owner=request.user, name=PurePath(upload.name).name[:240], content_type=content_type, content=data)
        logger.info("setup_document_saved owner_id=%s document_id=%s bytes=%s", request.user.pk, record.pk, len(data))
        return Response({"id": record.pk, "name": record.name, "content_type": content_type}, status=201)

    # 功能：读取当前账号的附件。
    # 输入：`request` 的身份及 download 查询项；`document_id` 为 UUID。
    # 输出：PDF/文本响应；无权访问统一为 404。
    # 逻辑：owner 限制查询，设置内联或下载、nosniff、沙盒与禁止缓存。
    # 约束：不允许跨账号访问；浏览器是否具备 PDF 阅读器由客户端决定。
    @extend_schema(responses=OpenApiTypes.BINARY, tags=["accounts"])
    def get(self, request, document_id=None):
        record = get_object_or_404(SetupDocument, pk=document_id, owner=request.user)
        response = HttpResponse(bytes(record.content), content_type=record.content_type)
        response["Content-Disposition"] = content_disposition_header("download" in request.query_params, record.name)
        response["Content-Security-Policy"] = "sandbox; default-src 'none'"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response
