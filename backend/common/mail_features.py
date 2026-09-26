"""Responsibility: Centrally control temporary suspension of QQ mail capabilities.
Implementation: Reads QQ_MAIL_ENABLED and explicitly rejects requests before queuing, decrypting, or calling external services when disabled.
Relationships: Shared by CRM inbound mail and sales outbound mail; does not alter existing mail or authorization records.
Directory:
- QQMailDisabled: Provides the suspension status and error code.
- require_qq_enabled: Validates the QQ feature switch.
Variable index:
- logger: Logs blocked operations without credentials.
- QQMailDisabled.status_code: 503 temporarily unavailable.
- QQMailDisabled.default_code: Stable error code.
- QQMailDisabled.default_detail: Suspension and historical-retention explanation.
"""
import logging
from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger("salesmate.mail_features")


# Function: Represent suspended QQ service.
# Logic: Returns an explicit 503 and never switches providers or retries.
# Constraints: The exception carries no credentials or mail body.
class QQMailDisabled(APIException):
    status_code = 503
    default_code = "qq_temporarily_disabled"
    default_detail = "QQ 邮箱功能暂时停用，历史邮件仍保留。"


# Function: Reject QQ operations while the feature is disabled.
# Inputs: `operation` is the fixed operation name; reads server-side QQ_MAIL_ENABLED.
# Outputs: No value when enabled; raises QQMailDisabled when disabled.
# Logic: Checks the central switch and logs the rejected operation.
# Constraints: Does not modify data or log credentials or message bodies.
def require_qq_enabled(operation):
    if not settings.QQ_MAIL_ENABLED:
        logger.warning("qq_operation_disabled operation=%s", operation)
        raise QQMailDisabled()
