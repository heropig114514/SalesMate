"""Responsibility: Provide bounded JSON transport for file tools without relaxing site-wide request limits.
Implementation: Read at most the Base64 file-envelope bound from the request stream, then hand it to DRF JSON parsing; non-upload tools remain subject to the original JSON size limit.
Relationships: Used only by ``CallView``; ``support`` continues to independently enforce file size and Schema validation.
Directory:
- ToolJSONParser: Restricted tool JSON parser.
- ToolJSONParser.parse: Check payload size and parse structure.
Variable index:
- MAX_ENVELOPE_BYTES: Base64 length for a 5 MiB file plus 64 KiB JSON envelope space.
- ToolJSONParser.media_type: Matches only ``application/json``.
"""

import io
from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser, JSONParser
from apps.accounts.onboarding import MAX_BYTES

MAX_ENVELOPE_BYTES = 4 * ((MAX_BYTES + 2) // 3) + 64 * 1024


# Function: Parse a bounded file-tool envelope.
# Logic: Compose the existing ``JSONParser`` and bound read length first to avoid caching a large file request in full in ``HttpRequest.body``.
# Constraints: Does not change global Django size settings or disable field, file, or identity validation.
class ToolJSONParser(BaseParser):
    media_type = "application/json"

    # Function: Read and parse one tool JSON payload.
    # Inputs: Request stream ``stream``, media type ``media_type``, and DRF encoding and request context ``parser_context``.
    # Outputs: JSON object; an over-limit or malformed payload raises ``ParseError``.
    # Logic: Read at most the bound plus one byte; only explicit ``setup_documents.upload`` can use the file-envelope bound, while others retain the global limit.
    # Constraints: Does not echo original content; acceptance still requires tool authorization and business validation; does not retry automatically or treat truncation as success.
    def parse(self, stream, media_type=None, parser_context=None):
        content = stream.read(MAX_ENVELOPE_BYTES + 1)
        if len(content) > MAX_ENVELOPE_BYTES:
            raise ParseError("工具请求超过文件上传信封上限。")
        try:
            data = JSONParser().parse(io.BytesIO(content), media_type, parser_context)
        except ParseError:
            raise ParseError("工具请求不是合法 JSON。") from None
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        upload = isinstance(data, dict) and data.get("name") == "setup_documents.upload"
        if not upload and limit is not None and len(content) > limit:
            raise ParseError("普通工具请求超过 JSON 大小限制。")
        return data
