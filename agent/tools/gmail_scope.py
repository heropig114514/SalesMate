"""职责：按用户冻结的天数或封数范围分页读取 Gmail 消息标识。
实现：服务器端时间筛选、最新优先分页，默认最多 50 封；超过 50 封须携带对明确封数的批准。
关联：Django durable_sync 与一次性 Agent 共用此选择器，不依赖 Django 或邮箱凭证存储。
目录：
- gmail_message_limit：验证批准状态并返回本次允许的邮件上限。
- scoped_message_pages：逐页产生本次允许处理的消息 ID。
变量索引：
- GMAIL_MESSAGE_LIMIT：无需超量批准的单批上限 50 封。
"""
from datetime import datetime

GMAIL_MESSAGE_LIMIT = 50


# 功能：确定本次 Gmail 同步或重试允许处理的最大封数。
# 输入：`options` 为请求或冻结范围，包含可选 max_messages 和 allow_large_sync。
# 输出：正整数上限；非法数量或未批准的超量请求抛 ValueError。
# 逻辑：未填封数使用 50；批准只适用于明确提供的封数，不允许批准无限量。
# 约束：不静默截断用户明确要求的超量请求；批准随批次保存，不是账号永久开关。
def gmail_message_limit(options):
    limit = options.get("max_messages")
    approved = options.get("allow_large_sync", False)
    if type(approved) is not bool:
        raise ValueError("Large-sync approval must be a boolean.")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("Sync message count must be a positive integer.")
    if approved and limit is None:
        raise ValueError("Specify an explicit message count when approving a large sync.")
    if limit is not None and limit > GMAIL_MESSAGE_LIMIT and not approved:
        raise ValueError("A sync defaults to at most 50 messages. Larger batches may occupy workers for a long time; approve the exact count before submitting.")
    return limit if limit is not None else GMAIL_MESSAGE_LIMIT


# 功能：枚举明确范围中的 Gmail ID，不读取正文。
# 输入：`service` 为已授权 Gmail SDK；`options` 为冻结的天数、封数和 UTC 窗口；`page_size` 为调用方既定单页大小。
# 输出：按 Gmail 最新优先顺序产生去重 ID 页；非法范围、响应和分页循环抛 ValueError/RuntimeError。
# 逻辑：范围包含收件箱和已发送；纯天数也最多 50 封，超量须批准；先限量再由调用方去重。
# 约束：时间使用 Gmail 秒级 after/before 条件；不使用 History 失效回退，不隐式重试网络错误；只保存已见 ID/页标识。
def scoped_message_pages(service, options, page_size=20):
    if not isinstance(options, dict):
        raise ValueError("Gmail sync must select the most recent N days or N messages.")
    days, limit = options.get("recent_days"), options.get("max_messages")
    if not (days or limit) or any(value is not None and (type(value) is not int or value <= 0) for value in (days, limit)):
        raise ValueError("Gmail sync scope must include a positive number of days or messages.")
    limit = gmail_message_limit(options)
    until = datetime.fromisoformat(options.get("until") or "")
    since = datetime.fromisoformat(options["since"]) if options.get("since") else None
    if until.tzinfo is None or (days and since is None) or (since and (since.tzinfo is None or since >= until)):
        raise ValueError("Invalid Gmail sync time window.")
    query = f"{{in:inbox in:sent}} before:{int(until.timestamp())}"
    if since:
        query += f" after:{int(since.timestamp())}"
    token, seen_tokens, seen_ids = "", set(), set()
    while True:
        arguments = {"userId": "me", "maxResults": min(page_size, limit - len(seen_ids)), "q": query}
        if token:
            arguments["pageToken"] = token
        response = service.users().messages().list(**arguments).execute()
        if not isinstance(response, dict) or not isinstance(response.get("messages", []), list):
            raise RuntimeError("Invalid Gmail scope list response.")
        ids = [item.get("id") if isinstance(item, dict) else None for item in response.get("messages", [])]
        next_token = response.get("nextPageToken", "")
        if any(not isinstance(value, str) or not value.strip() for value in ids) or not isinstance(next_token, str):
            raise RuntimeError("Invalid Gmail scope pagination.")
        if next_token and (next_token == token or next_token in seen_tokens):
            raise RuntimeError("Gmail scope pagination loop detected.")
        page = [value for value in dict.fromkeys(ids) if value not in seen_ids]
        page = page[:limit - len(seen_ids)]
        seen_ids.update(page)
        yield page
        if not next_token or len(seen_ids) >= limit:
            return
        seen_tokens.add(next_token)
        token = next_token
