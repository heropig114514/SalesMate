"""职责：统一控制 QQ 邮箱能力的临时停用。
实现：读取 QQ_MAIL_ENABLED；禁用时在排队、解密或外部调用前显式拒绝。
关联：CRM 收信及销售发信共用；不修改已有邮件或授权记录。
目录：
- QQMailDisabled：提供暂停状态及错误码。
- require_qq_enabled：验证 QQ 功能开关。
变量索引：
- logger：受阻操作日志，不包含凭证。
- QQMailDisabled.status_code：503 暂不可用。
- QQMailDisabled.default_code：稳定错误码。
- QQMailDisabled.default_detail：暂停及历史保留说明。
"""
import logging
from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger("salesmate.mail_features")


# 功能：表示 QQ 服务暂停。
# 逻辑：返回明确 503，不切换提供方或重试。
# 约束：异常不携带凭证或邮件正文。
class QQMailDisabled(APIException):
    status_code = 503
    default_code = "qq_temporarily_disabled"
    default_detail = "QQ 邮箱功能暂时停用，历史邮件仍保留。"


# 功能：拒绝禁用期间的 QQ 操作。
# 输入：`operation` 为固定操作名；读取服务端 QQ_MAIL_ENABLED。
# 输出：启用时无返回值，禁用时抛 QQMailDisabled。
# 逻辑：检查统一开关并记录被拒绝的操作。
# 约束：不修改数据，不记录凭证或正文。
def require_qq_enabled(operation):
    if not settings.QQ_MAIL_ENABLED:
        logger.warning("qq_operation_disabled operation=%s", operation)
        raise QQMailDisabled()
