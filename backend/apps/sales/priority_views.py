"""Responsibility: Provide owner-isolated seller target-profile reads/writes.
Implementation: Strictly validate canonical names, headcount bounds, and IANA timezones; save against If-Match under the owner lock and update company scoring dependencies.
Relationships: SellerProfile persists manual profiles; priority derives additional fields from orders/products. Derived statistics cannot be manually overwritten.
Directory:
- SizeRangeSerializer: Validate inclusive target-company headcount bounds.
- SizeRangeSerializer.validate: Reject inverted ranges.
- SellerProfileSerializer: Declare optional seller-profile fields.
- SellerProfileSerializer.validate_time_zone: Validate IANA timezones.
- SellerProfileResponseSerializer: Declare profile and optimistic-lock version.
- SellerProfileView: Maintain the current user's own seller profile.
- SellerProfileView.get: Read existing configuration or an empty configuration without default profile data.
- SellerProfileView.patch: Merge explicitly submitted fields and trigger dependency updates.
Variable index:
- SizeRangeSerializer.min: Nonnegative headcount lower bound.
- SizeRangeSerializer.max: Nonnegative headcount upper bound.
- SellerProfileSerializer.target_industries: Canonical target industries; clearable to unknown.
- SellerProfileSerializer.target_company_size: Nullable target headcount range.
- SellerProfileSerializer.service_regions: Canonical service regions; clearable to unknown.
- SellerProfileSerializer.time_zone: Nullable IANA timezone without automatic business defaults.
- SellerProfileResponseSerializer.revision: Version required for reads/updates.
- SellerProfileResponseSerializer.profile: Currently explicitly saved profile.
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


# Function: Validate inclusive target-company headcount bounds.
# Logic: Both bounds must be explicitly provided as nonnegative integers.
# Constraints: No default ranges or conversion of unknown headcount to zero.
class SizeRangeSerializer(StrictSerializer):
    min = s.IntegerField(min_value=0)
    max = s.IntegerField(min_value=0)

    # Function: Reject inverted ranges.
    # Inputs: `attrs`: validated integer bounds.
    # Outputs: Original dictionary; inverted bounds raise ValidationError.
    # Logic: Compare the inclusive endpoints without altering user values.
    # Constraints: No database or external calls.
    def validate(self, attrs):
        if attrs["min"] > attrs["max"]:
            raise s.ValidationError("目标规模下界不得大于上界。")
        return attrs


# Function: Declare optional seller-profile fields.
# Logic: Every field is optional; null or empty arrays explicitly clear existing information.
# Constraints: Averages, product catalogs, and similar historical wins are derived fields and cannot be written through this API.
class SellerProfileSerializer(StrictSerializer):
    target_industries = s.ListField(child=s.CharField(max_length=100), required=False, allow_empty=True)
    target_company_size = SizeRangeSerializer(required=False, allow_null=True)
    service_regions = s.ListField(child=s.CharField(max_length=100), required=False, allow_empty=True)
    time_zone = s.CharField(max_length=100, required=False, allow_null=True)

    # Function: Validate IANA timezones.
    # Inputs: `value`: timezone string or null.
    # Outputs: Validated original value; unknown zones raise ValidationError.
    # Logic: Use the runtime's ZoneInfo database so Agent can interpret date-only signals.
    # Constraints: Never substitute UTC or the server timezone; null delegates interpretation to the scoring clock.
    def validate_time_zone(self, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise s.ValidationError("必须提供有效 IANA 时区。") from None
        return value


# Function: Declare profile and optimistic-lock version.
# Logic: Return profile and revision, with an ETag matching the revision.
# Constraints: Do not return other employees' configurations or computed order statistics.
class SellerProfileResponseSerializer(s.Serializer):
    revision = s.IntegerField()
    profile = SellerProfileSerializer()


# Function: Maintain the current user's own seller profile.
# Logic: After session authentication, locate the singleton directly through request.user; reject client-supplied owners.
# Constraints: Shared-company permissions do not grant access to seller profiles.
class SellerProfileView(APIView):
    # Function: Read existing configuration or an empty configuration without default profile data.
    # Inputs: `request`: current authenticated request.
    # Outputs: profile, revision, and an ETag with the same value.
    # Logic: For missing configuration, return version 0 without creating a record.
    # Constraints: GET performs no enqueueing or database writes.
    @extend_schema(responses=SellerProfileResponseSerializer, tags=["sales"])
    def get(self, request):
        profile = SellerProfile.objects.filter(owner=request.user).first()
        revision = profile.revision if profile else 0
        return Response({"revision": revision, "profile": profile.profile if profile else {}}, headers={"ETag": f'"{revision}"'})

    # Function: Merge explicitly submitted fields and trigger dependency updates.
    # Inputs: `request`: profile fields and If-Match.
    # Outputs: Saved profile and new version; stale versions, invalid fields, and timezones produce explicit errors.
    # Logic: Lock owner first, validate version and complete merged values, and only on actual changes audit, increment profile version, and update all companies.
    # Constraints: Rollback reverses configuration, company versions, and tasks together; no runtime-mode changes or model calls.
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
