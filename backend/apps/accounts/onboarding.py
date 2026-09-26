"""Responsibility: Persist personal, product, and solution information from four-step onboarding and provide private attachment reads.
Implementation: Experiment mode permits cross-account attachment reads and product or attachment links; the setup singleton remains located by the publicly selected identity; entries contain stable IDs and optional transactional-product links; strict structural validation, owner isolation, and ``If-Match`` optimistic locking apply; PDF and text attachments are read only through authenticated endpoints.
Relationships: ``SalesSetup`` and ``SetupDocument``; company information continues to use the ``company-profile`` endpoint and does not modify scoring inputs.
Directory:
- StrictSerializer: Reject undeclared fields.
- StrictSerializer.to_internal_value: Check the field allowlist.
- PersonalSerializer: Personal identity and responsibility scope.
- ProductSerializer: Reference-product fields.
- SolutionSerializer: Solution attachment reference.
- SetupSerializer: Validate complete onboarding information.
- SetupSerializer.validate: Validate attachment ownership and product price range.
- snapshot: Render the current account's onboarding snapshot.
- catalog_rows: Add stable non-writing reference identifiers to legacy arrays.
- SetupView: Read and versioned save of onboarding.
- SetupView.get: Read a side-effect-free snapshot.
- SetupView.patch: Transactionally save information.
- DocumentView: Private PDF and text attachment endpoint.
- DocumentView.post: Validate and save an uploaded file.
- DocumentView.get: Authenticated inline read or file download.
Variable index:
- logger: Records only non-sensitive context such as account, stage, and version.
- MAX_BYTES: Maximum 5 MiB for one onboarding file.
- PersonalSerializer.name: Name.
- PersonalSerializer.title: Job title.
- PersonalSerializer.email: Contact mailbox; does not represent Gmail authorization.
- PersonalSerializer.phone: Optional phone number.
- PersonalSerializer.regions: List of responsible regions.
- PersonalSerializer.industries: List of industries.
- ProductSerializer.name: Product name.
- ProductSerializer.id: Setup-entry UUID, persisted after saving.
- ProductSerializer.linked_product_id: Explicit link to this account's transactional catalog that does not synchronize or alter prices.
- ProductSerializer.category: Category or model.
- ProductSerializer.specifications: Per-item specification text.
- ProductSerializer.price_min: Nullable lower price bound.
- ProductSerializer.price_max: Nullable upper price bound.
- ProductSerializer.currency: Explicit reference-price currency.
- ProductSerializer.scenarios: List of applicable industries or scenarios.
- ProductSerializer.document_id: Nullable reference to this account's specification document.
- SolutionSerializer.name: Solution name.
- SolutionSerializer.id: Solution-entry UUID, persisted after saving.
- SolutionSerializer.document_id: Reference to this account's file.
- SetupSerializer.personal: Personal-information object.
- SetupSerializer.products: At most 200 reference products.
- SetupSerializer.solutions: At most 100 solutions.
- SetupSerializer.completed: State for completing or skipping all onboarding.
- DocumentView.parser_classes: Supports only multipart upload.
"""

from common.laboratory import owner_scope

import logging
import uuid
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


# Function: Strictly limit setup-information fields.
# Logic: Validate the object and unknown keys before running standard DRF field validation.
# Constraints: Does not silently discard spelling errors or client permission fields.
class StrictSerializer(serializers.Serializer):
    # Function: Check input structure.
    # Inputs: ``data`` is a request object or nested object.
    # Outputs: Validated fields; an unknown field raises ``ValidationError`` with ``non_field_errors``.
    # Logic: Check key sets against declared fields.
    # Constraints: Does not accept non-object payloads.
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise serializers.ValidationError({"non_field_errors": ["资料含未知字段或不是对象。"]})
        return super().to_internal_value(data)


# Function: Validate personal identity.
# Logic: Allows the entire step to be skipped and validates mailbox and field lengths when supplied.
# Constraints: The mailbox is only contact information; it neither creates authorization nor changes login identity.
class PersonalSerializer(StrictSerializer):
    name = serializers.CharField(max_length=150, allow_blank=True)
    title = serializers.CharField(max_length=150, allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    phone = serializers.CharField(max_length=80, allow_blank=True)
    regions = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30)
    industries = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30)


# Function: Validate reference-product information.
# Logic: Generate UUIDs for new entries by default; explicit ``linked_product_id`` only creates a reference; limit lengths and prices, with unknown prices as null.
# Constraints: Reference prices do not create business quotes or overwrite existing ``Product`` transaction records.
class ProductSerializer(StrictSerializer):
    id = serializers.UUIDField(required=False, default=uuid.uuid4)
    linked_product_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    name = serializers.CharField(max_length=240)
    category = serializers.CharField(max_length=150, allow_blank=True)
    specifications = serializers.ListField(child=serializers.CharField(max_length=500), max_length=50)
    price_min = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, allow_null=True)
    price_max = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0, allow_null=True)
    currency = serializers.ChoiceField(choices=["SGD", "USD", "CNY", "EUR", "JPY"])
    scenarios = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30)
    document_id = serializers.UUIDField(allow_null=True)


# Function: Validate sales-solution information.
# Logic: Generate a new entry UUID by default, bind solution name to a file, and check file ownership in the outer layer.
# Constraints: Does not parse or execute files or claim that AI read an attachment.
class SolutionSerializer(StrictSerializer):
    id = serializers.UUIDField(required=False, default=uuid.uuid4)
    name = serializers.CharField(max_length=240)
    document_id = serializers.UUIDField()


# Function: Validate onboarding-information increments.
# Logic: Steps submit independently, nested content is fully replaced, and attachments may reference only current-account files.
# Constraints: Cannot modify ``owner`` or ``revision`` and does not trigger algorithm or model services.
class SetupSerializer(StrictSerializer):
    personal = PersonalSerializer(required=False)
    products = ProductSerializer(many=True, max_length=200, required=False)
    solutions = SolutionSerializer(many=True, max_length=100, required=False)
    completed = serializers.BooleanField(required=False)

    # Function: Validate cross-field bounds and private-file references.
    # Inputs: ``attrs`` is field-validated data; the context's user is the current user.
    # Outputs: Validated data; inverted prices or another account's attachment raise ``ValidationError``.
    # Logic: Validate unique entry UUIDs and unarchived products; production mode restricts product and attachment ownership while experiment mode permits cross-account links.
    # Constraints: Only validates references here and does not read file content; actual downloads use the corresponding file endpoint.
    def validate(self, attrs):
        from apps.sales.models import Product

        products = attrs.get("products", [])
        for key in ("products", "solutions"):
            rows = attrs.get(key, [])
            if len({row["id"] for row in rows}) != len(rows):
                raise serializers.ValidationError("资料条目 id 不得重复。")
        linked = {row["linked_product_id"] for row in products if row.get("linked_product_id")}
        if Product.objects.filter(owner_scope(self.context["user"]), archived=False, pk__in=linked).count() != len(linked):
            raise serializers.ValidationError("关联产品不存在、已归档或不属于当前账号。")
        for product in products:
            low, high = product["price_min"], product["price_max"]
            if low is not None and high is not None and low > high:
                raise serializers.ValidationError("参考价格下限不能大于上限。")
        ids = {row["document_id"] for row in products + attrs.get("solutions", []) if row.get("document_id")}
        if SetupDocument.objects.filter(owner_scope(self.context["user"]), pk__in=ids).count() != len(ids):
            raise serializers.ValidationError("附件不存在或不属于当前账号。")
        return attrs


# Function: Construct an onboarding snapshot.
# Inputs: ``user`` is an authenticated user.
# Outputs: Dictionary of information, version, and this account's attachment metadata.
# Logic: Use an unsaved instance when no record exists; ``catalog_rows`` completes legacy-entry references and GET does not persist them.
# Constraints: Does not emit file content or query another account.
def snapshot(user):
    record = SalesSetup.objects.filter(owner=user).first() or SalesSetup(owner=user)
    return {"personal": record.personal, "products": catalog_rows(user, "products", record.products), "solutions": catalog_rows(user, "solutions", record.solutions),
            "completed": record.completed, "revision": record.revision,
            "documents": list(SetupDocument.objects.filter(owner=user).values("id", "name", "content_type"))}


# Function: Generate addressable identifiers for legacy information.
# Inputs: ``user``, setup-information category ``kind``, and persisted array ``rows``.
# Outputs: New array containing id; products also explicitly return ``linked_product_id``.
# Logic: Derive a UUID for legacy entries without IDs from account, category, and position; the next save persists it and it then does not change with sorting.
# Constraints: Reads do not write the database; modifications must include the complete setup revision and do not automatically link transactional products.
def catalog_rows(user, kind, rows):
    return [
        {**({"linked_product_id": None} if kind == "products" else {}), **row,
         "id": row.get("id") or str(uuid.uuid5(uuid.NAMESPACE_URL, f"salesmate:setup:{user.pk}:{kind}:{index}"))}
        for index, row in enumerate(rows)
    ]


# Function: Maintain onboarding information for the current account.
# Logic: Use default session authentication and CSRF and lock the user row on writes.
# Constraints: The API exposes no other-account ID parameter; a separate versioned endpoint maintains company information.
class SetupView(APIView):
    # Function: Read the onboarding snapshot.
    # Inputs: Authenticated identity from ``request``.
    # Outputs: HTTP-200 JSON; an empty account has revision zero.
    # Logic: Delegates to ``snapshot``.
    # Constraints: No database write.
    @extend_schema(responses=OpenApiTypes.OBJECT, tags=["accounts"])
    def get(self, request):
        return Response(snapshot(request.user))

    # Function: Save one step or completion state.
    # Inputs: ``request`` contains setup-information JSON and ``If-Match``.
    # Outputs: New snapshot; validation failure is 400 and version conflict is 409.
    # Logic: The user row lock prevents an initial-creation race and the serializer representation normalizes Decimal and UUID values to JSON strings.
    # Constraints: Failures do not commit; there is no automatic retry; logs do not record setup-information content.
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


# Function: Provide private specification and solution files.
# Logic: Limit size and format, and explicitly query owner for every read.
# Constraints: No public links; PDF uses a sandboxed response and text is not executed as HTML.
class DocumentView(APIView):
    parser_classes = [MultiPartParser]

    # Function: Validate and save one file.
    # Inputs: ``request`` carries one multipart file field; ``document_id`` is absent when creating.
    # Outputs: HTTP-201 file metadata; a format or size violation returns 400.
    # Logic: Files are at most 5 MiB, PDFs check their signature, TXT must be UTF-8, and the database stores content and metadata.
    # Constraints: Does not execute files or call external parsers; invalid input writes nothing and logs omit filename and content.
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

    # Function: Read an attachment for the current account.
    # Inputs: ``request`` provides identity and the optional download query field; ``document_id`` is a UUID.
    # Outputs: PDF or text response; unauthorized access consistently returns 404.
    # Logic: Production mode restricts by owner and experiment mode exposes business files; sets inline-or-download, nosniff, sandbox, and no-cache headers.
    # Constraints: Production mode rejects cross-account access; the client decides whether the browser has a PDF reader.
    @extend_schema(responses=OpenApiTypes.BINARY, tags=["accounts"])
    def get(self, request, document_id=None):
        record = get_object_or_404(SetupDocument.objects.filter(owner_scope(request.user)), pk=document_id)
        response = HttpResponse(bytes(record.content), content_type=record.content_type)
        response["Content-Disposition"] = content_disposition_header("download" in request.query_params, record.name)
        response["Content-Security-Policy"] = "sandbox; default-src 'none'"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response
