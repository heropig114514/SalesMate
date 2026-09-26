"""Responsibility: Provide company-information endpoints for the current account workspace.
Implementation: Company-size and explicit-field validation, session permissions, owner row locks, and ``If-Match`` prevent unauthorized access and concurrent overwrites; reads do not create records.
Relationships: ``CompanyProfile`` is independent of CRM customer and sales-target profiles; ``company-settings.js`` calls these endpoints.
Directory:
- CompanyProfileSerializer: Validate and render company information.
- CompanyProfileSerializer.Meta: Declare the field allowlist.
- CompanyProfileView: Read and save the account's own company information.
- CompanyProfileView.get: Return persisted information or an empty profile at version zero.
- CompanyProfileView.patch: Merge and save by version while recording a non-sensitive audit log.
Variable index:
- logger: Records only account identifier, version, and changed field names.
- CompanyProfileSerializer.updated_at: Read-only save time; null when no profile was first created.
- CompanyProfileSerializer.Meta.model: Profile model.
- CompanyProfileSerializer.Meta.fields: Visible profile and version fields.
- CompanyProfileSerializer.Meta.read_only_fields: Server-maintained version and time.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.crm.access import check_version
from apps.sales.serializers import StrictModelSerializer
from .models import CompanyProfile

logger = logging.getLogger(__name__)


# Function: Validate and render company information.
# Logic: Reuse strict field-rejection rules and model length, mailbox, and URL validation; a profile not yet created explicitly permits a null timestamp.
# Constraints: ``owner``, ``revision``, and unknown fields cannot be written; company name is required, while ``size_band`` and other fields may be blank.
class CompanyProfileSerializer(StrictModelSerializer):
    updated_at = serializers.DateTimeField(read_only=True, allow_null=True)

    # Function: Declare the field allowlist.
    # Logic: Includes company size, excludes owner, and makes server-managed version and time read-only.
    # Constraints: Does not expose mailbox authorization, team information, or customer data.
    class Meta:
        model = CompanyProfile
        fields = ["company_name", "industry", "size_band", "website", "email", "phone", "address", "description", "revision", "updated_at"]
        read_only_fields = ["revision", "updated_at"]


# Function: Read and save the account's own company information.
# Logic: Reuse global ``SessionAuthentication`` and ``IsAuthenticated`` and locate the sole record through ``request.user``.
# Constraints: Provides no ID-based endpoint for other accounts; writes are subject to session CSRF and version constraints.
class CompanyProfileView(APIView):
    # Function: Return persisted information or an empty profile at version zero.
    # Inputs: ``request`` is an authenticated session request.
    # Outputs: Company information and ETag; ``updated_at`` is null before saving.
    # Logic: Construct an unsaved model only for serialization when no record is found.
    # Constraints: Does not write the database or trigger scoring or email jobs.
    @extend_schema(responses=CompanyProfileSerializer, tags=["accounts"])
    def get(self, request):
        profile = CompanyProfile.objects.filter(owner=request.user).first() or CompanyProfile(owner=request.user)
        return Response(CompanyProfileSerializer(profile).data, headers={"ETag": f'"{profile.revision}"'})

    # Function: Merge and save by version while recording a non-sensitive audit log.
    # Inputs: ``request`` supplies field JSON and the ``If-Match`` version.
    # Outputs: Persisted information; invalid fields or a missing version produce 400, and a stale version produces 409.
    # Logic: Lock the user row first to cover the race on initial creation; after validation, increment the version only for an actual change.
    # Constraints: Failed transactions do not write; logs exclude company field values and do not alter customers, scoring, or mailbox configuration.
    @extend_schema(request=CompanyProfileSerializer, responses=CompanyProfileSerializer, tags=["accounts"],
                   parameters=[OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True)])
    @transaction.atomic
    def patch(self, request):
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        current = CompanyProfile.objects.filter(owner=request.user).first()
        check_version((request.headers.get("If-Match") or "").strip('"'), current.revision if current else 0)
        serializer = CompanyProfileSerializer(current, data=request.data, partial=current is not None)
        serializer.is_valid(raise_exception=True)
        changed = sorted(key for key, value in serializer.validated_data.items() if current is None or getattr(current, key) != value)
        if current is None or changed:
            current = serializer.save(owner=request.user, revision=(current.revision if current else 0) + 1)
            logger.info("company_profile_saved owner_id=%s revision=%s fields=%s", request.user.pk, current.revision, changed)
        return Response(CompanyProfileSerializer(current).data, headers={"ETag": f'"{current.revision}"'})
