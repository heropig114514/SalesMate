"""Responsibility: Provide information, catalog, and file data tools for algorithm callers without running models or scoring.
Implementation: Experiment mode opens cross-account information-file queries while the information singleton remains located by selected experiment identity; reuse accounts information APIs; catalog changes retain account lock and revision; ordinary files require a TXT suffix while experiment entry may explicitly accept validated ``text/plain``.
Relationships: ``registry`` calls ``support_specs`` and ``dispatch`` calls ``execute_support``; ``experiments`` reuses chunk encoding; ``services`` provides idempotency and call logging.
Directory:
- support_specs: Declare information, catalog, and file tools.
- catalog_operation: Paginate reads or versioned modifications of product and solution entries.
- document_operation: Read, upload, or delete the caller's onboarding file.
- read_content: Generate a bounded text or binary chunk.
- execute_support: Dispatch an information tool.
Variable index:
- logger: File-change logger that records only internal identifiers and byte count.
- CHUNK_BYTES: Maximum bytes in one binary read.
- CHUNK_TEXT: Maximum characters in one text read.
"""

from common.laboratory import owner_scope

import base64
import binascii
import hashlib
import logging
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from apps.accounts.company_profile import CompanyProfileSerializer, CompanyProfileView
from apps.accounts.onboarding import (
    DocumentView, MAX_BYTES, ProductSerializer, SetupSerializer, SetupView,
    SolutionSerializer, snapshot,
)
from apps.accounts.models import SetupDocument, SalesSetup
from apps.crm.access import check_version
from apps.sales import files, models
from apps.sales.priority_views import SellerProfileSerializer, SellerProfileView
from apps.sales.views import paged
from .schemas import PAGE, REVISION, UUID, object_schema, record_schema

logger = logging.getLogger("salesmate.support_tools")
CHUNK_BYTES = 256 * 1024
CHUNK_TEXT = 16000


# Function: Define authorizable software-support tools.
# Inputs: ``tool`` is the registry declaration factory.
# Outputs: List of tool declarations.
# Logic: Writes reuse unified invocation; production-mode information changes require the read revision, while chunked files do not require a browser session.
# Constraints: Does not provide scoring, arbitrary paths, external fetching, or automatic authorization; deletion applies only to the caller's unreferenced onboarding attachments.
def support_specs(tool):
    entries = []
    for prefix, serializer in (("company_profile", CompanyProfileSerializer), ("sales_setup", SetupSerializer), ("seller_profile", SellerProfileSerializer)):
        entries.extend([
            tool(prefix + ".get", "读取本人资料及 revision；不自动补充或推断字段。", "support_profile", object_schema({}), profile=prefix, operation="get"),
            tool(prefix + ".update", "按 revision 保存显式资料；seller_profile 沿用原有依赖更新，其余不触发评分。", "support_profile",
                 object_schema({"revision": REVISION, "data": record_schema(serializer, True)}, ["revision", "data"]), "write", profile=prefix, operation="update"),
        ])
    for prefix, collection, serializer in (("setup_products", "products", ProductSerializer), ("solutions", "solutions", SolutionSerializer)):
        entries.extend([
            tool(prefix + ".list", "按名称关键词查询本人资料目录；返回 setup_revision 和分页，不查询交易目录。", "support_catalog",
                 object_schema({**PAGE, "q": {"type": "string", "maxLength": 500}}), collection=collection, operation="list"),
            tool(prefix + ".get", "读取本人目录条目和 setup_revision；产品可显式关联交易目录 linked_product_id。", "support_catalog",
                 object_schema({"id": UUID}, ["id"]), collection=collection, operation="get"),
            tool(prefix + ".create", "创建资料条目；不创建正式报价或自动关联交易产品。", "support_catalog",
                 object_schema({"revision": REVISION, "data": record_schema(serializer)}, ["revision", "data"]), "write", collection=collection, operation="create"),
            tool(prefix + ".update", "按 setup_revision 合并指定条目字段；不影响其他条目。", "support_catalog",
                 object_schema({"id": UUID, "revision": REVISION, "data": record_schema(serializer, True)}, ["id", "revision", "data"]), "write", collection=collection, operation="update"),
            tool(prefix + ".delete", "按 setup_revision 从资料目录移除一条记录；附件和交易产品保留。", "support_catalog",
                 object_schema({"id": UUID, "revision": REVISION}, ["id", "revision"]), "write", collection=collection, operation="delete"),
        ])
    entries.extend([
        tool("setup_documents.list", "分页查询本人规格书和方案附件元数据。", "support_document", object_schema(PAGE), operation="list"),
        tool("setup_documents.get", "读取附件元数据、摘要和引用；无须浏览器登录。", "support_document", object_schema({"id": UUID}, ["id"]), operation="get"),
        tool("setup_documents.upload", "上传不超过 5 MiB 的 PDF/UTF-8 TXT，base64 为文件原始字节；不会自动创建知识或关联产品。", "support_document",
             object_schema({"name": {"type": "string", "minLength": 1, "maxLength": 240}, "content_base64": {"type": "string", "minLength": 1, "maxLength": 4 * ((MAX_BYTES + 2) // 3)}}, ["name", "content_base64"]), "write", operation="upload"),
        tool("setup_documents.delete", "删除本人未被任何产品或方案引用的附件；有引用返回冲突，不删除引用。", "support_document", object_schema({"id": UUID}, ["id"]), "write", operation="delete"),
    ])
    for prefix, kind in (("setup_documents", "support_document"), ("files", "support_file")):
        entries.append(tool(prefix + ".read", "使用当前工具授权分块读取文件；base64 按字节偏移，text 仅支持 UTF-8 TXT 且按字符偏移。返回 next_offset，不自动读取全文件。", kind,
            object_schema({"id": UUID, "format": {"enum": ["base64", "text"]}, "offset": {"type": "integer", "minimum": 0},
                           "limit": {"type": "integer", "minimum": 1, "maximum": CHUNK_BYTES}}, ["id", "format", "offset", "limit"]), operation="read"))
    return entries


# Function: Read or modify an information catalog.
# Inputs: ``request`` is restricted account context, ``spec`` is the tool declaration, and ``args`` are validated parameters.
# Outputs: Pagination or item, ``setup_revision``, or deletion receipt.
# Logic: Read the catalog by selected identity and apply operation; unified version policy decides when revision can be omitted, while production mode still requires the current version.
# Constraints: Does not modify transactional ``Product``; updates cannot replace entry id; failures roll back wholly with no implicit retry.
@transaction.atomic
def catalog_operation(request, spec, args):
    operation, collection = spec["operation"], spec["collection"]
    if operation not in {"list", "get"}:
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
    current = snapshot(request.user)
    rows = current[collection]
    if operation == "list":
        query = args.get("q", "").casefold()
        selected = [row for row in rows if query in row["name"].casefold()]
        page, size = args.get("page", 1), args.get("page_size", 30)
        return Response({"count": len(selected), "page": page, "page_size": size,
                         "results": selected[(page - 1) * size:page * size], "setup_revision": current["revision"]})
    row = next((row for row in rows if row["id"] == args.get("id")), None)
    if operation != "create" and row is None:
        raise NotFound("资料条目不存在。")
    if operation == "get":
        return Response({"item": row, "setup_revision": current["revision"]})
    check_version(args.get("revision"), current["revision"])
    if operation == "create":
        rows.append(args["data"])
        index = len(rows) - 1
    elif operation == "update":
        if "id" in args["data"] and args["data"]["id"] != args["id"]:
            raise ValidationError("条目 id 不可修改。")
        index = rows.index(row)
        rows[index] = {**row, **args["data"]}
    else:
        rows.remove(row)
    request.data = {collection: rows}
    saved = SetupView().patch(request).data
    return Response({"setup_revision": saved["revision"], **({"deleted_id": args["id"]} if operation == "delete" else {"item": saved[collection][index]})})


# Function: Read or maintain private onboarding files.
# Inputs: ``request``, ``operation``, and Schema-validated ``args``.
# Outputs: Metadata, pagination, chunk, or deletion or upload receipt.
# Logic: Upload reuses original PDF and TXT validation; production mode checks the caller's information references and experiment mode checks references for all accounts.
# Constraints: Production mode limits files to current account; experiment-mode cross-account reads and writes still refuse deleting referenced files; does not parse PDFs or automatically read all chunks.
@transaction.atomic
def document_operation(request, operation, args):
    if operation in {"upload", "delete"}:
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
    query = SetupDocument.objects.filter(owner_scope(request.user)).order_by("id")
    if operation == "upload":
        try:
            content = base64.b64decode(args["content_base64"], validate=True)
        except (ValueError, binascii.Error):
            raise ValidationError("content_base64 必须是合法 Base64。") from None
        if len(content) > MAX_BYTES:
            raise ValidationError("文件超过 5 MiB。")
        upload = SimpleUploadedFile(args["name"], content)
        incoming = SimpleNamespace(user=request.user, FILES={"file": upload}, data={"file": upload})
        return DocumentView().post(incoming)
    if operation == "list":
        rows, pagination = paged(query.values("id", "name", "content_type"), request)
        return Response({**pagination, "results": rows})
    record = get_object_or_404(query, pk=args["id"])
    if operation == "read":
        return Response(read_content(record, bytes(record.content), args))
    setups = SalesSetup.objects.filter(owner_scope(request.user))
    references = [{"collection": key, "id": row.get("id"), "owner": setup.owner_id}
                  for setup in setups for key in ("products", "solutions")
                  for row in getattr(setup, key) if row.get("document_id") == str(record.pk)]
    if operation == "delete":
        if references:
            from apps.crm.access import Conflict
            raise Conflict("文件仍被资料引用，请先显式移除引用。")
        record.delete()
        logger.info("setup_document_deleted owner_id=%s document_id=%s", request.user.pk, args["id"])
        return Response({"deleted_id": args["id"]})
    return Response({"id": str(record.pk), "name": record.name, "content_type": record.content_type,
                     "size": len(record.content), "sha256": hashlib.sha256(bytes(record.content)).hexdigest(), "references": references})


# Function: Map file content to bounded data.
# Inputs: Metadata ``record``, raw bytes ``content``, ``args`` format, offset, and limit, and whether ``allow_plain_text`` permits validated ``text/plain`` experiment files.
# Outputs: Content, unit, total length, and ``next_offset``.
# Logic: Decode binary as Base64 and TXT as UTF-8; experiment entry can explicitly accept ``text/plain`` metadata while ordinary files still require a ``.txt`` suffix by default.
# Constraints: Text does not guess encodings for PDFs or arbitrary binary; out-of-bound offsets error and the end produces an empty chunk; does not execute files or log content.
def read_content(record, content, args, *, allow_plain_text=False):
    offset, limit, mode = args["offset"], args["limit"], args["format"]
    if mode == "text":
        text_file = record.name.lower().endswith(".txt") or (allow_plain_text and record.content_type == "text/plain")
        if not text_file or limit > CHUNK_TEXT:
            raise ValidationError("text 只支持 UTF-8 TXT，每次最多 16000 字符；其他文件使用 base64。")
        try:
            value = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError("文件不是 UTF-8 TXT。") from None
    else:
        value = content
    if offset > len(value):
        raise ValidationError("offset 超过文件长度。")
    end = min(offset + limit, len(value))
    chunk = value[offset:end]
    return {"id": str(record.pk), "name": record.name, "content_type": record.content_type,
            "sha256": hashlib.sha256(content).hexdigest(), "format": mode, "unit": "characters" if mode == "text" else "bytes",
            "offset": offset, "total": len(value), "next_offset": end if end < len(value) else None,
            "content": chunk if mode == "text" else base64.b64encode(chunk).decode("ascii")}


# Function: Execute an authorized software-support tool.
# Inputs: ``request``, fixed declaration ``spec``, and validated parameters ``args``.
# Outputs: Business ``Response``.
# Logic: Information tools dispatch to their corresponding operation; attachments use unified ownership policy, restricted to the caller in production mode and cross-account in experiment mode.
# Constraints: Cannot bypass the invocation-layer allowlist; does not execute models, arbitrary URLs, or paths; file handles close after reading.
def execute_support(request, spec, args):
    if spec["kind"] == "support_profile":
        view = {"company_profile": CompanyProfileView, "sales_setup": SetupView, "seller_profile": SellerProfileView}[spec["profile"]]()
        return view.get(request) if spec["operation"] == "get" else view.patch(request)
    if spec["kind"] == "support_catalog":
        return catalog_operation(request, spec, args)
    if spec["kind"] == "support_document":
        return document_operation(request, spec["operation"], args)
    record = get_object_or_404(models.Attachment.objects.filter(owner_scope(request.user)), pk=args["id"], archived=False)
    with files.open_file(request.user, record) as source:
        content = source.read(files.MAX_BYTES + 1)
    if len(content) > files.MAX_BYTES or len(content) != record.size or hashlib.sha256(content).hexdigest() != record.sha256:
        logger.error("attachment_integrity_failed owner_id=%s file_id=%s", request.user.pk, record.pk)
        raise ValidationError("附件内容与保存的元数据不一致，请检查存储。")
    return Response(read_content(record, content, args))
