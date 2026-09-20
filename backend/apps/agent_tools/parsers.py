"""职责：为文件工具提供有界 JSON 传输，不放宽全站请求限制。
实现：从请求流最多读取文件 Base64 信封上限，再交给 DRF JSON 解析；非上传工具继续受原 JSON 大小限制。
关联：仅 CallView 使用；support 的文件大小及 Schema 校验仍独立执行。
目录：
- ToolJSONParser：受限工具 JSON 解析器。
- ToolJSONParser.parse：检查载荷大小并解析结构。
变量索引：
- MAX_ENVELOPE_BYTES：5 MiB 文件的 Base64 长度加 64 KiB JSON 信封空间。
- ToolJSONParser.media_type：只匹配 application/json。
"""

import io
from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser, JSONParser
from apps.accounts.onboarding import MAX_BYTES

MAX_ENVELOPE_BYTES = 4 * ((MAX_BYTES + 2) // 3) + 64 * 1024


# 功能：解析有界文件工具信封。
# 逻辑：组合既有 JSONParser，先限制读取长度，避免把大文件请求整体缓存到 HttpRequest.body。
# 约束：不修改全局 Django 大小设置，不禁用字段、文件和身份校验。
class ToolJSONParser(BaseParser):
    media_type = "application/json"

    # 功能：读取并解析一份工具 JSON。
    # 输入：`stream` 请求流、`media_type` 媒体类型、`parser_context` DRF 编码和请求上下文。
    # 输出：JSON 对象；超限或格式错误抛 ParseError。
    # 逻辑：最多读取上限加一字节；只有明确 setup_documents.upload 可使用文件信封上限，其余沿用全局限制。
    # 约束：不回显原文；接收仍需工具授权和业务校验；不自动重试或截断成功。
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
