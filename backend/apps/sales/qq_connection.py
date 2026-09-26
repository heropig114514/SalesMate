"""Responsibility: Create QQ sending connections dedicated to sales actions.
Implementation: Validate SMTP login, then separately encrypt/store the authorization code without automatically sending or requesting synchronization.
Relationships: urls provides Session/CSRF endpoints; qq_smtp validates service access; actions executes approved actions only.
Directory:
- QQSendConnectionSerializer: Input allowlist.
- connect_account: Validate and save the employee's sending account.
- QQSendConnectionView: Independent sending-connection endpoint.
- QQSendConnectionView.post: Return safe connection information.
Variable index:
- QQSendConnectionSerializer.address: QQ/foxmail address.
- QQSendConnectionSerializer.authorization_code: Write-only authorization code.
- logger: Safe connection lifecycle logs.
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


# Function: Validate explicit sending-connection inputs.
# Logic: Accept only address and authorization code, never server or owner selection.
# Constraints: Real SMTP authentication is still required after format checks; never echo the authorization code.
class QQSendConnectionSerializer(StrictSerializer):
    address = serializers.RegexField(r"(?i)^[A-Za-z0-9_.+-]+@(qq|foxmail)\.com$", max_length=254)
    authorization_code = serializers.RegexField(r"^[A-Za-z]{16}$", write_only=True, trim_whitespace=True)


# Function: Validate the account and save separately encrypted QQ sending credentials.
# Inputs: `owner`: current employee; `address`: QQ address; `code`: client authorization code.
# Outputs: Connection; no sending or synchronization tasks are created.
# Logic: Encrypt first and validate SMTP, then serialize saves under the employee lock; connection updates increment revision.
# Constraints: Reject credential replacement while nonterminal actions reference the connection, preserving frozen action identity.
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


# Function: Provide independent QQ sending-connection creation.
# Logic: Inherit session/error boundaries and hide authorization codes in debug reports.
# Constraints: Successful connection never approves or executes email actions.
@method_decorator(sensitive_post_parameters("authorization_code"), name="dispatch")
class QQSendConnectionView(SalesView):
    # Function: Validate and return a safe sending connection.
    # Inputs: `request`: address and authorization_code.
    # Outputs: 201 with credential-free ConnectionSerializer output.
    # Logic: After validation, authenticate SMTP and save ciphertext.
    # Constraints: Do not create ToolAction or echo authorization codes/ciphertext.
    @extend_schema(request=QQSendConnectionSerializer, responses={201: ConnectionSerializer})
    @sensitive_variables()
    def post(self, request):
        serializer = QQSendConnectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        connection = connect_account(request.user, values["address"].casefold(), values["authorization_code"])
        return Response(ConnectionSerializer(connection).data, status=201)
