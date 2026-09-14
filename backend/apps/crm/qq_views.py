"""职责：提供 QQ 邮箱连接与移除的员工 HTTP 入口。
实现：严格输入字段、Session/CSRF 和当前员工隔离；凭证写入后只返回安全邮箱状态。
关联：urls 注册路径，qq_connection 处理网络与持久化，response_schemas 描述响应。
目录：
- QQConnectSerializer：限制 QQ 地址和 16 位授权码。
- QQConnectView：验证并连接 QQ 邮箱。
- QQConnectView.post：保存连接并返回已排队状态。
- QQDisconnectView：移除指定 QQ 连接。
- QQDisconnectView.delete：验证所有权后移除密文。
变量索引：
- QQConnectSerializer.address：仅允许 qq.com/foxmail.com 地址。
- QQConnectSerializer.authorization_code：只写授权码，不回显。
- QQConnectSerializer.sync_options：本次至少一项的天数或封数限制。
"""
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from . import qq_connection
from .gmail_oauth import mailbox_status
from .response_schemas import MailboxResponseSerializer
from .serializers import StrictSerializer
from .qq_scope import QQSyncOptionsSerializer


# 功能：声明只允许 QQ 服务的连接请求。
# 逻辑：拒绝任意服务器、owner 和未知字段；授权码不进入读响应。
# 约束：格式检查不能证明已授权，服务层仍须真实登录验证。
class QQConnectSerializer(StrictSerializer):
    address = serializers.RegexField(r"(?i)^[^\s@]+@(qq|foxmail)\.com$", max_length=254)
    authorization_code = serializers.RegexField(r"^[A-Za-z]{16}$", write_only=True, trim_whitespace=True)
    sync_options = QQSyncOptionsSerializer()


# 功能：连接当前会话员工的 QQ 邮箱。
# 逻辑：沿用全局 SessionAuthentication 与权限。
# 约束：不允许匿名或 Agent token 替员工提交授权码。
@method_decorator(sensitive_post_parameters("authorization_code"), name="dispatch")
class QQConnectView(APIView):
    # 功能：验证输入并建立连接。
    # 输入：`request` 含 address、authorization_code 和 sync_options。
    # 输出：HTTP 202 安全邮箱状态，首次同步已排队。
    # 逻辑：序列化后调用真实验证服务。
    # 约束：不返回授权码，不接受账号密码；debug 报告隐藏请求中的敏感值。
    @extend_schema(request=QQConnectSerializer, responses={202: MailboxResponseSerializer}, tags=["mailboxes"])
    @sensitive_variables()
    def post(self, request):
        serializer = QQConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mailbox = qq_connection.connect_mailbox(request.user, data["address"].casefold(), data["authorization_code"], data["sync_options"])
        return Response(mailbox_status(mailbox), status=202)


# 功能：管理已有 QQ 连接的删除。
# 逻辑：仅 DELETE 到指定员工邮箱。
# 约束：不会删除历史业务记录或 Google 凭证。
class QQDisconnectView(APIView):
    # 功能：移除本地 QQ 密文连接。
    # 输入：`request` 为员工会话；`mailbox_id` 为邮箱 UUID。
    # 输出：安全邮箱状态。
    # 逻辑：服务层执行 owner 和活动批次校验。
    # 约束：不存在或越权返回 404，活动批次返回 409。
    @extend_schema(responses=MailboxResponseSerializer, tags=["mailboxes"])
    def delete(self, request, mailbox_id):
        return Response(mailbox_status(qq_connection.disconnect_mailbox(request.user, mailbox_id)))
