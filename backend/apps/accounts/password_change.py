"""Responsibility: Change the authenticated account's password without changing business data.
Implementation: Validate explicit password fields, lock the current user before checking the old password, apply existing password validators, and refresh only the requesting session's authentication hash.
Relationships: accounts.urls exposes PasswordChangeView; the personal-profile form uses SessionAuthentication and CSRF; Django rejects other sessions when their stored password hash becomes stale.
Directory:
- PasswordChangeSerializer: Declare and validate password-change input.
- PasswordChangeSerializer.to_internal_value: Reject unknown fields and non-object input.
- PasswordChangeSerializer.validate: Require matching new-password confirmation.
- PasswordChangeView: Authenticated password-change endpoint.
- PasswordChangeView.post: Verify and replace the current account's password.
Variable index:
- logger: Records outcomes and account IDs without credentials.
- PasswordChangeSerializer.current_password: Untrimmed current password, used only for verification.
- PasswordChangeSerializer.new_password: Untrimmed replacement with the established 8–128-character limits.
- PasswordChangeSerializer.password_confirmation: Untrimmed confirmation, never persisted.
"""
import logging

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from common.serializers import ApiErrorSerializer

logger = logging.getLogger("salesmate.accounts")


# Function: Validate the explicit current/new/confirmation password contract.
# Logic: Preserve whitespace and reuse registration length limits; cross-field validation checks confirmation.
# Constraints: All fields are write-only; account selection is never accepted from the request body.
class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8, max_length=128)
    password_confirmation = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8, max_length=128)

    # Function: Reject fields outside the password-change contract.
    # Inputs: `data` is the untrusted request body.
    # Outputs: Converted fields, or a DRF validation error.
    # Logic: Check object shape and unknown keys before normal field validation.
    # Constraints: Never include submitted passwords in error messages or select another account.
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["Only current_password, new_password and password_confirmation are accepted."]})
        return super().to_internal_value(data)

    # Function: Require matching replacement passwords.
    # Inputs: `attrs` contains validated password strings.
    # Outputs: The original fields, or a field-specific validation error.
    # Logic: Compare confirmation without trimming either value.
    # Constraints: No database reads or credential logging.
    def validate(self, attrs):
        if attrs["new_password"] != attrs["password_confirmation"]:
            raise serializers.ValidationError({"password_confirmation": "New passwords do not match."})
        return attrs


# Function: Allow a logged-in user to change their own password.
# Logic: Inherit project SessionAuthentication and IsAuthenticated, including CSRF enforcement.
# Constraints: Provides neither anonymous password recovery nor administrator-selected account changes.
class PasswordChangeView(APIView):
    # Function: Verify the current password and save a validated replacement.
    # Inputs: `request` supplies authenticated identity, CSRF-protected session, and the three serializer fields.
    # Outputs: HTTP 204 on success; validation errors leave the password unchanged.
    # Logic: Lock and reread the user to serialize concurrent password changes, apply configured validators, save the hash, and rotate/update the current session.
    # Constraints: Logs only account ID and outcome; other sessions fail Django's hash check on their next request; business records and independent service credentials are unchanged.
    @extend_schema(request=PasswordChangeSerializer, responses={204: None, 400: ApiErrorSerializer, 403: ApiErrorSerializer}, tags=["accounts"])
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if not user.check_password(data["current_password"]):
                logger.warning("password_change_rejected owner_id=%s reason=current_password_incorrect", user.pk)
                raise serializers.ValidationError({"current_password": "Current password is incorrect."})
            try:
                validate_password(data["new_password"], user=user)
            except DjangoValidationError as error:
                raise serializers.ValidationError({"new_password": error.messages}) from error
            user.set_password(data["new_password"])
            user.save(update_fields=["password"])
            update_session_auth_hash(request, user)
        logger.info("password_change_completed owner_id=%s", user.pk)
        return Response(status=204)
