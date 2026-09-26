"""Responsibility: Provide standard-account registration without mailbox or phone verification.
Implementation: Error responses follow the request language; validate username and the minimum eight-character password rule, create the account transactionally, then establish a Session; the database unique constraint handles concurrent duplicate names.
Relationships: ``accounts.urls`` registers the route; the frontend reuses ``session/`` to obtain CSRF; existing owner permissions isolate new-user data.
Directory:
- RegistrationSerializer: Restrict writable registration fields and validate account information.
- RegistrationSerializer.to_internal_value: Reject extra fields and non-object payloads.
- RegistrationSerializer.validate_username: Normalize and validate username and duplicates.
- RegistrationSerializer.validate: Use the Django password validator that requires only minimum length.
- RegistrationView: Host the CSRF-protected anonymous registration entry point.
- RegistrationView.post: Create a standard user and log in the current browser.
Variable index:
- logger: Records registration results without usernames, passwords, or session tokens.
- RegistrationSerializer.username: Username up to 150 characters.
- RegistrationSerializer.password: Write-only password up to 128 characters that preserves whitespace semantics.
- RegistrationView.permission_classes: Allows anonymous registration and separately rejects authenticated requests.
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

from .models import User, SalesSetup

logger = logging.getLogger("salesmate.accounts")


# Function: Declare account fields accepted by public registration.
# Logic: Allow only ``username`` and ``password``; reuse the model username rules and the project's existing password-validation configuration.
# Constraints: Does not accept roles, mailbox-verification status, or Agent credentials; passwords never enter serialized responses.
class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    # Function: Restrict the set of registration-input fields.
    # Inputs: ``data`` is raw JSON from the request.
    # Outputs: Field-conversion result; invalid structure raises ``ValidationError``.
    # Logic: Check the object and unknown fields first, report structural errors through ``non_field_errors``, then run DRF field validation.
    # Constraints: Does not create an account or silently ignore permission-related fields.
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["注册只接受用户名和密码。"]})
        return super().to_internal_value(data)

    # Function: Normalize username representation and validate availability.
    # Inputs: ``value`` is the username after leading and trailing whitespace removal.
    # Outputs: Normalized username; invalid format or duplicate name raises ``ValidationError``.
    # Logic: Normalize by ``User`` Unicode rules, then run model-field validation and an exact duplicate-name query.
    # Constraints: Preserves current case semantics; the database unique constraint still decides concurrent conflicts.
    def validate_username(self, value):
        value = User.normalize_username(value)
        try:
            User._meta.get_field("username").clean(value, None)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("用户名已被使用，请换一个。")
        return value

    # Function: Validate that the password meets the project's length requirement.
    # Inputs: ``attrs`` contains validated username and raw password.
    # Outputs: Validated field dictionary; errors are reported on the ``password`` field.
    # Logic: Pass an unsaved user to Django ``validate_password``; global validators retain only the eight-character minimum and do not restrict character combinations.
    # Constraints: Does not require mailbox, phone, verification code, or legal identity information; does not log passwords.
    def validate(self, attrs):
        try:
            validate_password(attrs["password"], user=User(username=attrs["username"]))
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": error.messages}) from error
        return attrs


# Function: Create a standard account and log in through the same-origin browser.
# Logic: Anonymous registration retains CSRF protection, and an authenticated user cannot replace their current identity with a registration request.
# Constraints: Does not create administrators, copy demo data, automatically authorize Gmail, or start system processes.
@method_decorator(csrf_protect, name="dispatch")
class RegistrationView(APIView):
    permission_classes = [AllowAny]

    # Function: Submit account registration and return an authenticated session.
    # Inputs: ``request`` contains username/password JSON and a valid CSRF cookie or header.
    # Outputs: On success, 201 with identity and rotated CSRF; authenticated callers receive language-specific 409, and invalid input receives 400.
    # Logic: Transactionally create a standard user with a hashed password and an incomplete-onboarding record, then log in after commit; convert only confirmed duplicate-name conflicts into input errors.
    # Constraints: Log the error type and re-raise non-duplicate ``IntegrityError``; do not expose secrets, retry, or generate fictional mailboxes.
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
                SalesSetup.objects.create(owner=user)
        except IntegrityError:
            if User.objects.filter(username=serializer.validated_data["username"]).exists():
                logger.info("registration_rejected reason=duplicate_username")
                raise serializers.ValidationError({"username": ["用户名已被使用，请换一个。"]})
            logger.error("registration_failed error_type=IntegrityError action=inspect_database_constraints")
            raise
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        logger.info("registration_completed user_id=%s", user.pk)
        return Response({"authenticated": True, "username": user.username, "csrf_token": get_token(request)}, status=201)
