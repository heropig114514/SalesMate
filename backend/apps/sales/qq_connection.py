"""职责：建立销售动作专用的 QQ 发信连接。
实现：验证 SMTP 登录后独立加密保存授权码，不自动发信或请求同步。
关联：urls 提供 Session/CSRF 入口；qq_smtp 验证服务；actions 仅执行已批准动作。
目录：
- QQSendConnectionSerializer：输入白名单。
- connect_account：验证并保存员工发信账号。
- QQSendConnectionView：独立发信连接入口。
- QQSendConnectionView.post：返回安全连接信息。
变量索引：
- QQSendConnectionSerializer.address：QQ/foxmail 地址。
- QQSendConnectionSerializer.authorization_code：只写授权码。
- logger：安全连接生命周期日志。
"""
import logging
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from apps.crm.access import Conflict, InvalidState
from apps.crm.serializers import StrictSerializer
from . import models, qq_smtp
from .integrations import encrypt_credentials
from .serializers import ConnectionSerializer
from .services import audit
from .views import SalesView

logger = logging.getLogger("salesmate.qq_send_connection")


# 功能：验证发信连接的显式输入。
# 逻辑：仅接受地址和授权码，不允许指定服务器或 owner。
# 约束：格式检查后仍须真实 SMTP 认证；授权码不回显。
class QQSendConnectionSerializer(StrictSerializer):
    address = serializers.RegexField(r"(?i)^[A-Za-z0-9_.+-]+@(qq|foxmail)\.com$", max_length=254)
    authorization_code = serializers.RegexField(r"^[A-Za-z]{16}$", write_only=True, trim_whitespace=True)


# 功能：验证账号并保存独立 QQ 发信密文。
# 输入：`owner` 为当前员工；`address` 为 QQ 地址；`code` 为客户端授权码。
# 输出：Connection，不创建发信或同步任务。
# 逻辑：预先加密并验证 SMTP，锁员工串行保存；更新连接递增 revision。
# 约束：关联未终结动作时拒绝换凭证，防止冻结动作身份被替换。
@sensitive_variables("code", "encrypted", "client")
def connect_account(owner, address, code):
    qq_smtp.validate_credentials(address, code)
    encrypted = encrypt_credentials({"authorization_code": code})
    try:
        client = qq_smtp.connect(address, code)
    except qq_smtp.QQSMTPError as error:
        raise InvalidState(str(error)) from None
    qq_smtp.close(client)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=owner.pk)
        existing = models.Connection.objects.filter(owner=owner, provider="qq", account=address).first()
        if existing and models.ToolAction.objects.filter(owner=owner, parameters__connection_id=str(existing.pk), status__in=["pending_confirmation", "approved", "running", "uncertain"]).exists():
            raise Conflict("该 QQ 发信连接仍有关联动作，请先处理或取消动作后更换授权。")
        connection, created = models.Connection.objects.update_or_create(owner=owner, provider="qq", account=address, defaults={"encrypted_credentials": encrypted, "archived": False})
        if not created:
            connection.revision += 1
            connection.save(update_fields=["revision", "updated_at"])
        audit(owner, connection, "qq_send_connection_authorized")
        logger.info("qq_send_connection_saved connection_id=%s", connection.pk)
        return connection


# 功能：提供独立 QQ 发信连接创建入口。
# 逻辑：继承会话和错误边界，隐藏调试报告中的授权码。
# 约束：连接成功不会批准或执行任何邮件。
@method_decorator(sensitive_post_parameters("authorization_code"), name="dispatch")
class QQSendConnectionView(SalesView):
    # 功能：验证并返回安全发信连接。
    # 输入：`request` 含 address、authorization_code。
    # 输出：201 和无凭证的 ConnectionSerializer 结果。
    # 逻辑：校验后执行 SMTP 认证并保存密文。
    # 约束：不创建 ToolAction，不回显授权码或密文。
    @extend_schema(request=QQSendConnectionSerializer, responses={201: ConnectionSerializer})
    @sensitive_variables()
    def post(self, request):
        serializer = QQSendConnectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        connection = connect_account(request.user, values["address"].casefold(), values["authorization_code"])
        return Response(ConnectionSerializer(connection).data, status=201)
