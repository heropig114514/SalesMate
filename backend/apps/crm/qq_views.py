"""Responsibility: Provide employee HTTP endpoints to connect and remove QQ mailboxes.
Implementation: Strict input fields, Session/CSRF, and current-employee isolation; return only safe mailbox state after writing credentials.
Relationships: sync_scope supplies shared Gmail/QQ scope validation, urls registers paths, qq_connection handles network and persistence, and response_schemas describes responses.
Directory:
- QQConnectSerializer: Restrict QQ addresses and 16-character authorization codes.
- QQConnectView: Validate and connect a QQ mailbox.
- QQConnectView.post: Save a connection and return queued state.
- QQDisconnectView: Remove a specified QQ connection.
- QQDisconnectView.delete: Validate ownership and remove ciphertext.
Variable index:
- QQConnectSerializer.address: Allows qq.com and foxmail.com addresses only.
- QQConnectSerializer.authorization_code: Write-only authorization code that is never echoed.
- QQConnectSerializer.sync_options: At least one day or message-count limit for this run.
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
from .sync_scope import MailboxSyncOptionsSerializer


# Function: Declare a connection request that allows QQ service only.
# Logic: Reject arbitrary servers, owners, and unknown fields; shared scope validation still requires one limit and authorization codes never enter read responses.
# Constraints: Format validation cannot prove authorization; the service layer must still validate a real login.
class QQConnectSerializer(StrictSerializer):
    address = serializers.RegexField(r"(?i)^[^\s@]+@(qq|foxmail)\.com$", max_length=254)
    authorization_code = serializers.RegexField(r"^[A-Za-z]{16}$", write_only=True, trim_whitespace=True)
    sync_options = MailboxSyncOptionsSerializer()


# Function: Connect the current session employee's QQ mailbox.
# Logic: Retain global SessionAuthentication and permissions.
# Constraints: Does not permit anonymous callers or Agent tokens to submit authorization codes for employees.
@method_decorator(sensitive_post_parameters("authorization_code"), name="dispatch")
class QQConnectView(APIView):
    # Function: Validate input and establish a connection.
    # Inputs: `request` contains address, authorization_code, and sync_options.
    # Outputs: HTTP 202 safe mailbox state with initial synchronization queued.
    # Logic: Call the real validation service after serialization.
    # Constraints: Does not return authorization codes or accept account passwords; debug reports hide sensitive request values.
    @extend_schema(request=QQConnectSerializer, responses={202: MailboxResponseSerializer}, tags=["mailboxes"])
    @sensitive_variables()
    def post(self, request):
        serializer = QQConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mailbox = qq_connection.connect_mailbox(request.user, data["address"].casefold(), data["authorization_code"], data["sync_options"])
        return Response(mailbox_status(mailbox), status=202)


# Function: Manage deletion of an existing QQ connection.
# Logic: Permit DELETE only for the specified employee mailbox.
# Constraints: Does not delete historical business records or Google credentials.
class QQDisconnectView(APIView):
    # Function: Remove a local QQ ciphertext connection.
    # Inputs: `request` is an employee session and `mailbox_id` is the mailbox UUID.
    # Outputs: Safe mailbox state.
    # Logic: The service layer validates owner and active batches.
    # Constraints: Absence or unauthorized access returns 404; active batches return 409.
    @extend_schema(responses=MailboxResponseSerializer, tags=["mailboxes"])
    def delete(self, request, mailbox_id):
        return Response(mailbox_status(qq_connection.disconnect_mailbox(request.user, mailbox_id)))
