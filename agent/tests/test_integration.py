"""检查 Gmail 只读读取、History 增量接口和百炼客户端边界。"""

import base64
import json
import unittest
from email.message import EmailMessage
from unittest.mock import Mock, call, patch

from agent.llm.bailian import generate_json
from agent.tools.gmail import (
    GmailHistoryExpiredError,
    SCOPES,
    get_profile_address,
    get_profile_history_id,
    list_history_message_ids,
    read_email,
)


BAILIAN_CONFIG = {
    "DASHSCOPE_API_KEY": "fake-test-key",
    "BAILIAN_BASE_URL": "https://example.invalid/compatible-mode/v1",
    "BAILIAN_MODEL": "fake-model",
}


def _gmail_raw(message_id, *, subject=None, body=None, headers=None):
    message = EmailMessage()
    message["From"] = "buyer@example.com"
    message["To"] = "sales@example.com"
    message["Subject"] = subject or f"Subject {message_id}"
    for name, value in headers or ():
        message[name] = value
    message.set_content(body or f"Body {message_id}")
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")




class GmailReadOnlyIntegrationTests(unittest.TestCase):
    """使用 mocked Google service 验证 Gmail 读取边界，全程离线。"""

    def setUp(self):
        self.service = Mock(name="mock_gmail_service")
        self.users = self.service.users.return_value
        self.messages = self.users.messages.return_value
        self.threads = self.users.threads.return_value
        self.drafts = self.users.drafts.return_value
        self.labels = self.users.labels.return_value

    def assert_fully_offline_and_read_only(self):
        self.assertEqual(
            SCOPES, ["https://www.googleapis.com/auth/gmail.readonly"]
        )
        for method_name in (
            "modify",
            "batchModify",
            "trash",
            "delete",
            "batchDelete",
            "send",
            "insert",
            "import_",
        ):
            getattr(self.messages, method_name).assert_not_called()
        for method_name in ("modify", "trash", "delete"):
            getattr(self.threads, method_name).assert_not_called()
        for method_name in ("create", "update", "send", "delete"):
            getattr(self.drafts, method_name).assert_not_called()
        for method_name in ("create", "update", "patch", "delete"):
            getattr(self.labels, method_name).assert_not_called()
        self.users.threads.assert_not_called()
        self.users.drafts.assert_not_called()
        self.users.labels.assert_not_called()

    def test_profile_address_uses_mocked_authorized_profile(self):
        profile_request = Mock(name="profile_request")
        profile_request.execute.return_value = {
            "emailAddress": "sales@example.com"
        }
        self.users.getProfile.return_value = profile_request

        self.assertEqual(get_profile_address(self.service), "sales@example.com")

        self.users.getProfile.assert_called_once_with(userId="me")
        profile_request.execute.assert_called_once_with()
        self.messages.get.assert_not_called()
        self.messages.list.assert_not_called()
        self.assert_fully_offline_and_read_only()

    def test_profile_history_id_uses_read_only_profile(self):
        profile_request = Mock(name="profile_history_request")
        profile_request.execute.return_value = {"historyId": "9001"}
        self.users.getProfile.return_value = profile_request

        self.assertEqual(get_profile_history_id(self.service), "9001")

        self.users.getProfile.assert_called_once_with(userId="me")
        profile_request.execute.assert_called_once_with()
        self.assert_fully_offline_and_read_only()

    def test_history_lists_added_inbox_and_sent_ids_across_pages(self):
        first_request = Mock(name="history_page_1")
        first_request.execute.return_value = {
            "historyId": "110",
            "nextPageToken": "page-2",
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "inbox-1", "labelIds": ["INBOX"]}},
                        {"message": {"id": "draft-1", "labelIds": ["DRAFT"]}},
                    ]
                }
            ],
        }
        second_request = Mock(name="history_page_2")
        second_request.execute.return_value = {
            "historyId": "120",
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "inbox-1", "labelIds": ["INBOX"]}},
                        {"message": {"id": "sent-1", "labelIds": ["SENT"]}},
                    ]
                }
            ],
        }
        history = self.users.history.return_value
        history.list.side_effect = [first_request, second_request]

        message_ids, cursor = list_history_message_ids(self.service, "100")

        self.assertEqual(message_ids, ["inbox-1", "sent-1"])
        self.assertEqual(cursor, "120")
        self.assertEqual(
            history.list.call_args_list,
            [
                call(
                    userId="me",
                    startHistoryId="100",
                    historyTypes=["messageAdded"],
                    maxResults=100,
                ),
                call(
                    userId="me",
                    startHistoryId="100",
                    historyTypes=["messageAdded"],
                    maxResults=100,
                    pageToken="page-2",
                ),
            ],
        )
        self.assert_fully_offline_and_read_only()

    def test_expired_history_cursor_has_distinct_fallback_error(self):
        expired = RuntimeError("expired")
        expired.resp = Mock(status=404)
        request = Mock(name="expired_history_request")
        request.execute.side_effect = expired
        self.users.history.return_value.list.return_value = request

        with self.assertRaises(GmailHistoryExpiredError):
            list_history_message_ids(self.service, "old-cursor")

        self.assert_fully_offline_and_read_only()

    def test_specified_message_gets_only_target_id_as_raw(self):
        message_request = Mock(name="message_request")
        message_request.execute.return_value = {
            "id": "target-id",
            "threadId": "thread-target",
            "raw": _gmail_raw("target-id"),
        }
        self.messages.get.return_value = message_request

        email = read_email(self.service, "target-id")

        self.messages.get.assert_called_once_with(
            userId="me", id="target-id", format="raw"
        )
        message_request.execute.assert_called_once_with()
        self.messages.list.assert_not_called()
        self.assertEqual(email["gmail_message_id"], "target-id")
        self.assertEqual(email["thread_id"], "thread-target")
        self.assertIsNone(email["received_at"])
        self.assertNotIn("source", email)
        self.assertNotIn("message_id", email)
        self.assertEqual(email["body_text"], "Body target-id")
        self.assert_fully_offline_and_read_only()

class IntegrationTests(unittest.TestCase):
    @patch.dict("os.environ", BAILIAN_CONFIG, clear=True)
    @patch("agent.llm.bailian.requests.post")
    def test_llm_client_accepts_arbitrary_json_task(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"order_id": "A-1"}'}}]
        }
        result = generate_json("提取订单编号，返回 JSON。", "订单编号 A-1")
        self.assertEqual(json.loads(result), {"order_id": "A-1"})
        self.assertEqual(post.call_args.kwargs["json"]["messages"], [
            {"role": "system", "content": "提取订单编号，返回 JSON。"},
            {"role": "user", "content": "订单编号 A-1"},
        ])

if __name__ == "__main__":
    unittest.main()
