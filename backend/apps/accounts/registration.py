"""职责：提供无需邮箱或手机验证的普通账号注册接口。
实现：错误响应按请求语言展示；验证用户名与既有密码规则，事务创建账号后建立 Session；数据库唯一约束处理并发重名。
关联：accounts.urls 注册路由；前端复用 session/ 获取 CSRF；新用户数据由既有 owner 权限隔离。
目录：
- RegistrationSerializer：限制注册可写字段并校验账号信息。
- RegistrationSerializer.to_internal_value：拒绝额外字段及非对象载荷。
- RegistrationSerializer.validate_username：规范化并验证用户名和重名。
- RegistrationSerializer.validate：使用既有 Django 密码校验器。
- RegistrationView：承载带 CSRF 保护的匿名注册入口。
- RegistrationView.post：创建普通用户并登录当前浏览器。
变量索引：
- logger：记录注册结果，不记录用户名、密码或会话令牌。
- RegistrationSerializer.username：最多 150 字符的用户名。
- RegistrationSerializer.password：最多 128 字符的只写密码，保留空格语义。
- RegistrationView.permission_classes：允许匿名发起注册，已登录请求单独拒绝。
"""

import logging

from django.contrib.auth import login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from django.utils.translation import gettext
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User

logger = logging.getLogger("salesmate.accounts")


# 功能：声明公开注册所接受的账号字段。
# 逻辑：只允许 username/password；复用模型用户名规则和项目既有密码校验配置。
# 约束：不接受角色、邮箱验证状态或 Agent 凭证，密码不进入序列化响应。
class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    # 功能：限定注册输入的字段集合。
    # 输入：`data` 为请求的原始 JSON。
    # 输出：字段转换结果，非法结构抛 ValidationError。
    # 逻辑：先检查对象及未知字段，以 non_field_errors 字典报告结构错误，再执行 DRF 字段校验。
    # 约束：不创建账号，不静默忽略权限相关字段。
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["注册只接受用户名和密码。"]})
        return super().to_internal_value(data)

    # 功能：统一用户名表示并验证可用性。
    # 输入：`value` 为去除首尾空白后的用户名。
    # 输出：规范化用户名；格式或重名错误抛 ValidationError。
    # 逻辑：先按 User 的 Unicode 规则规范化，再运行模型字段校验和精确重名查询。
    # 约束：保持现有大小写语义；并发冲突仍由数据库唯一约束裁决。
    def validate_username(self, value):
        value = User.normalize_username(value)
        try:
            User._meta.get_field("username").clean(value, None)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("用户名已被使用，请换一个。")
        return value

    # 功能：验证密码是否符合项目既有策略。
    # 输入：`attrs` 含已验证用户名和原始密码。
    # 输出：通过校验的字段字典；错误以 password 字段报告。
    # 逻辑：构造未保存的用户交给 Django validate_password，不修改全局校验器配置。
    # 约束：不要求邮箱、手机号、验证码或实名信息；不记录密码。
    def validate(self, attrs):
        try:
            validate_password(attrs["password"], user=User(username=attrs["username"]))
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": error.messages}) from error
        return attrs


# 功能：通过同源浏览器创建普通账号并登录。
# 逻辑：匿名注册保留 CSRF，已登录用户不能用注册请求替换当前身份。
# 约束：不创建管理员、不复制演示数据、不自动授权 Gmail 或启动系统进程。
@method_decorator(csrf_protect, name="dispatch")
class RegistrationView(APIView):
    permission_classes = [AllowAny]

    # 功能：提交账号注册并返回已认证会话。
    # 输入：`request` 含 username/password JSON 及有效 CSRF Cookie/请求头。
    # 输出：成功返回 201 和身份、轮换后的 CSRF；已登录返回按请求语言显示的 409，输入错误返回 400。
    # 逻辑：事务创建经哈希存储密码的普通用户，提交后登录；仅将已确认的重名冲突转换为输入错误。
    # 约束：非重名的 IntegrityError 记录错误类型并继续抛出；不泄露秘密、不重试、不生成虚构邮箱。
    @extend_schema(request=RegistrationSerializer, responses={201: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT}, tags=["accounts"])
    def post(self, request):
        if request.user.is_authenticated:
            return Response({"error": {"code": "already_authenticated", "detail": gettext("请先退出当前账号，再注册新账号。")}, "request_id": getattr(request, "request_id", None)}, status=409)
        serializer = RegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            logger.info("registration_rejected reason=validation")
            raise serializers.ValidationError(serializer.errors)
        try:
            with transaction.atomic():
                user = User.objects.create_user(**serializer.validated_data)
        except IntegrityError:
            if User.objects.filter(username=serializer.validated_data["username"]).exists():
                logger.info("registration_rejected reason=duplicate_username")
                raise serializers.ValidationError({"username": ["用户名已被使用，请换一个。"]})
            logger.error("registration_failed error_type=IntegrityError action=inspect_database_constraints")
            raise
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        logger.info("registration_completed user_id=%s", user.pk)
        return Response({"authenticated": True, "username": user.username, "csrf_token": get_token(request)}, status=201)
