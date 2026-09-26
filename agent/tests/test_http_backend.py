"""DjangoBackendClient 的离线传输映射测试。"""

import unittest

from agent.clients.backend_api import (
    BackendContractError,
    BackendRequestError,
    DjangoBackendClient,
)


class _Response:
    def __init__(self, payload, *, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.content = b"" if payload is None else b"json"

    def json(self):
        return self._payload


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("测试未配置后端响应。")
        return self.responses.pop(0)


class DjangoBackendClientTests(unittest.TestCase):
    def client(self, session, *, mailbox_id="mailbox-1"):
        return DjangoBackendClient(
            "http://backend.test/api/v1/agent/",
            "service-secret",
            mailbox_id=mailbox_id,
            session=session,
        )

    def test_request_bound_chat_read_uses_agent_identity_and_checks_evidence(self):
        session = _Session(_Response({
            "request_id": "request-1", "tool": "customers.search",
            "status": "completed",
            "data": {"count": 0, "page": 1, "page_size": 20, "results": []},
            "evidence_items": [],
        }))
        backend = self.client(session)
        result = backend.read_chat_tool("request-1", "customers.search", {"q": "盛微"})
        self.assertEqual(result["data"]["count"], 0)
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/chat/tool-reads/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Agent service-secret")
        self.assertEqual(kwargs["json"], {
            "request_id": "request-1", "name": "customers.search",
            "arguments": {"q": "盛微"},
        })
        with self.assertRaises(BackendContractError):
            backend.read_chat_tool("request-1", "customers.create", {"name": "假客户"})

    def test_chat_tool_catalog_is_bound_to_request_and_agent_identity(self):
        session = _Session(_Response({
            "contract_version": "chat-tools-v1", "request_id": "request-1",
            "count": 1, "page": 1, "page_size": 30,
            "tools": [{"name": "customers.search", "executionMode": "read", "inputSchema": {}}],
        }))
        result = self.client(session).get_chat_tools("request-1")
        self.assertEqual(result["tools"][0]["name"], "customers.search")
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertIn("/chat/tools/?request_id=request-1", url)
        self.assertEqual(kwargs["headers"]["Authorization"], "Agent service-secret")

    def test_chat_tool_error_preserves_backend_scope(self):
        session = _Session(_Response({
            "error": {"scope": "tool", "code": "not_found", "detail": "不可访问"}
        }, status=404))
        with self.assertRaises(BackendRequestError) as caught:
            self.client(session).read_chat_tool(
                "request-1", "customers.context", {"company_id": "company-1"}
            )
        self.assertEqual(caught.exception.scope, "tool")

    def test_chat_request_status_uses_agent_identity(self):
        session = _Session(_Response({
            "request_id": "request-1", "status": "completed",
            "assistant_message_id": "assistant-1", "citations": [],
        }))
        result = self.client(session).get_chat_request_status("request-1")
        self.assertEqual(result["status"], "completed")
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/chat/requests/request-1/"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Agent service-secret")

    def test_chat_read_rejects_wrong_request_identity(self):
        session = _Session(_Response({
            "request_id": "other", "tool": "customers.context", "status": "completed",
            "data": {}, "evidence_items": [],
        }))
        with self.assertRaises(BackendContractError):
            self.client(session).read_chat_tool(
                "request-1", "customers.context", {"company_id": "company-1"}
            )

    def test_submit_adds_transport_fields_and_aggregates_response(self):
        session = _Session(
            _Response(
                [
                    {"dedupe_key": "one", "company_id": "company-1", "status": "created"},
                    {"dedupe_key": "two", "company_id": "company-1", "status": "updated"},
                    {"dedupe_key": "three", "company_id": "company-2", "status": "duplicate"},
                ]
            )
        )
        result = self.client(session).submit_emails([{"dedupe_key": "source-key"}])

        sent = session.calls[0][2]["json"][0]
        self.assertEqual(sent["mailbox_id"], "mailbox-1")
        self.assertEqual(sent["source"], "gmail_real")
        self.assertEqual(sent["dedupe_key"], "source-key")
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["affected_company_ids"], ["company-1", "company-2"])

    def test_stored_email_lookup_maps_existing_record_and_not_found(self):
        dedupe_key = "sales@example.com:message-1"
        session = _Session(
            _Response(
                {
                    "dedupe_key": dedupe_key,
                    "extract_prompt_version": "extract-v7",
                    "extract_status": "completed",
                }
            ),
            _Response(
                {"error": {"code": "not_found", "detail": "邮件不存在。"}},
                status=404,
            ),
        )
        backend = self.client(session)

        stored = backend.get_stored_email("mailbox-1", dedupe_key)
        missing = backend.get_stored_email("mailbox-1", "sales@example.com:missing")

        self.assertEqual(stored["extract_status"], "completed")
        self.assertIsNone(missing)
        self.assertIn("mailbox_id=mailbox-1", session.calls[0][1])
        self.assertIn("dedupe_key=sales%40example.com%3Amessage-1", session.calls[0][1])

    def test_grouping_context_and_job_extensions_are_hidden_from_workflow(self):
        session = _Session(
            _Response({"company_id": "company-1"}, headers={"ETag": '"7"'}),
            _Response({"company_id": "company-1", "emails": []}, headers={"ETag": '"7"'}),
            _Response(
                [
                    {
                        "job_id": "job-1",
                        "trigger": "email_ingested",
                        "payload": {"company_id": "company-1"},
                        "enqueued_at": "2026-09-12T00:00:00+00:00",
                        "lease_token": "lease-1",
                        "expected_version": 7,
                    }
                ]
            ),
            _Response({"saved": True}),
            _Response({"job_id": "job-1", "status": "completed"}),
        )
        backend = self.client(session, mailbox_id=None)

        self.assertEqual(backend.get_company_grouping("company-1")["company_id"], "company-1")
        backend.get_company_context("company-1")
        jobs = backend.claim_jobs(1)
        self.assertEqual(jobs[0]["company_id"], "company-1")
        self.assertNotIn("lease_token", jobs[0])
        backend.save_analysis_input({"company_id": "company-1", "input_version": "v1"})
        backend.report_job(
            {
                "job_id": "job-1",
                "status": "completed",
                "input_version": "v1",
                "produced": {},
                "error": None,
                "duration_ms": 1,
            }
        )

        context_headers = session.calls[1][2]["headers"]
        save_headers = session.calls[3][2]["headers"]
        report_headers = session.calls[4][2]["headers"]
        self.assertEqual(context_headers["If-Match"], "7")
        self.assertEqual(save_headers["X-Job-ID"], "job-1")
        self.assertEqual(save_headers["X-Lease-Token"], "lease-1")
        self.assertEqual(report_headers["X-Lease-Token"], "lease-1")
        self.assertEqual(session.calls[2][2]["json"]["lease_seconds"], 120)

    def test_cache_miss_and_complete_cache_are_mapped(self):
        analysis = {
            "company_id": "company-1",
            "input_version": "v1",
            "status": "completed",
            "list_view": {},
            "detail_view": {},
        }
        session = _Session(
            _Response({"hit": False, "status": "pending"}),
            _Response({"hit": True, "status": "completed", "analysis": analysis}),
        )
        backend = self.client(session, mailbox_id=None)
        self.assertIsNone(backend.get_cached_analysis("company-1", "v1"))
        self.assertEqual(backend.get_cached_analysis("company-1", "v1"), analysis)

    def test_employee_mailbox_sync_claim_and_report_are_mapped(self):
        authorization = {"token": "test-token", "refresh_token": "test-refresh"}
        session = _Session(
            _Response(
                [
                    {
                        "mailbox_id": "mailbox-1",
                        "mailbox_address": "sales@example.com",
                        "authorization": authorization,
                        "max_results": 20,
                    }
                ]
            ),
            _Response(
                {
                    "mailbox_id": "mailbox-1",
                    "address": "sales@example.com",
                    "gmail_authorized": True,
                    "sync_state": {"status": "completed"},
                }
            ),
        )
        backend = self.client(session, mailbox_id=None)

        claims = backend.claim_mailbox_syncs(5)
        self.assertEqual(claims[0]["authorization"], authorization)
        reported = backend.report_mailbox_sync(
            {
                "mailbox_id": "mailbox-1",
                "status": "completed",
                "sync_result": {"fetched_count": 2},
                "error": None,
                "authorization": authorization,
            }
        )
        self.assertEqual(reported["sync_state"]["status"], "completed")
        self.assertTrue(
            session.calls[0][1].endswith("agent/mailbox-syncs/claim/")
        )
        self.assertTrue(
            session.calls[1][1].endswith("agent/mailbox-syncs/report/")
        )

    def test_sync_state_uses_etag_for_incremental_cursor_save(self):
        session = _Session(
            _Response(
                {
                    "mailbox_id": "mailbox-1",
                    "cursor": "100",
                    "scope": {},
                    "last_synced_at": None,
                    "status": "ok",
                    "version": 4,
                },
                headers={"ETag": '"4"'},
            ),
            _Response(
                {
                    "mailbox_id": "mailbox-1",
                    "cursor": "120",
                    "scope": {"mode": "gmail_history"},
                    "last_synced_at": "2026-09-13T00:00:00+00:00",
                    "status": "ok",
                    "version": 5,
                },
                headers={"ETag": '"5"'},
            ),
        )
        backend = self.client(session)

        state = backend.get_sync_state("mailbox-1")
        state.update(
            cursor="120",
            scope={"mode": "gmail_history"},
            last_synced_at="2026-09-13T00:00:00+00:00",
        )
        saved = backend.save_sync_state(state)

        self.assertEqual(saved["cursor"], "120")
        self.assertTrue(session.calls[0][1].endswith("agent/sync-state/?mailbox_id=mailbox-1"))
        self.assertTrue(session.calls[1][1].endswith("agent/sync-state-save/"))
        self.assertEqual(session.calls[1][2]["headers"]["If-Match"], "4")

    def test_metadata_only_cache_hit_and_http_error_fail_explicitly(self):
        cache_session = _Session(
            _Response({"hit": True, "status": "completed", "input_version": "v1"})
        )
        with self.assertRaisesRegex(BackendContractError, "full Analysis"):
            self.client(cache_session, mailbox_id=None).get_cached_analysis(
                "company-1", "v1"
            )

        error_session = _Session(
            _Response(
                {"error": {"code": "conflict", "detail": "数据已变化"}},
                status=409,
            )
        )
        with self.assertRaises(BackendRequestError) as raised:
            self.client(error_session, mailbox_id=None).claim_jobs(1)
        self.assertEqual(raised.exception.code, "conflict")
        self.assertNotIn("service-secret", str(raised.exception))


    def test_chat_claim_maps_zero_and_single_work_with_authenticated_transport(self):
        claimed_request = {
            "request_id": "request-1",
            "conversation_id": "conversation-1",
            "user_message_id": "message-1",
            "question": "这个客户最近最关心什么？",
            "recent_history": [
                {"role": "user", "content": "先看最近往来。"},
                {"role": "assistant", "content": "请问关注需求还是风险？"},
            ],
        }
        session = _Session(
            _Response({"request": None}),
            _Response({"request": claimed_request}),
            _Response({"request": {**claimed_request, "company_id": None}}),
        )
        backend = self.client(session, mailbox_id=None)

        self.assertIsNone(backend.claim_answer_request())
        claimed = backend.claim_answer_request()

        self.assertEqual(claimed, claimed_request)
        self.assertEqual(backend.claim_answer_request(), claimed_request)
        self.assertIsNot(claimed, claimed_request)
        claimed["recent_history"][0]["content"] = "changed"
        self.assertEqual(
            claimed_request["recent_history"][0]["content"], "先看最近往来。"
        )
        for method, url, kwargs in session.calls:
            with self.subTest(url=url):
                self.assertEqual(method, "POST")
                self.assertTrue(url.endswith("agent/chat/requests/claim/"))
                self.assertEqual(kwargs["json"], {})
                self.assertEqual(
                    kwargs["headers"]["Authorization"], "Agent service-secret"
                )
                self.assertEqual(kwargs["timeout"], 30.0)

    def test_chat_context_maps_scopes_and_independent_retrieval_statuses(self):
        customer_item = {
            "source_id": "mail:1",
            "source_type": "customer_email",
            "title_or_label": "采购咨询",
            "content": "需要正式报价。",
        }
        internal_gap = {
            "scope": "internal_knowledge",
            "code": "temporarily_unavailable",
            "message": "内部知识暂时不可用。",
        }
        external_gap = {
            "scope": "external_knowledge",
            "code": "temporarily_unavailable",
            "message": "外部知识暂时不可用。",
        }
        internal_partial = {
            "request_id": "request-1",
            "scope": "internal",
            "customer_context": [customer_item],
            "context_items": [],
            "customer_context_status": "completed",
            "knowledge_status": "failed",
            "retrieval_gaps": [internal_gap],
            "external_available": True,
        }
        internal_customer_failure = {
            "request_id": "request-1",
            "scope": "internal",
            "customer_context": [],
            "context_items": [
                {
                    "source_id": "kb:1",
                    "source_type": "internal_knowledge",
                    "title_or_label": "报价规则",
                    "content": "报价需要审批。",
                }
            ],
            "customer_context_status": "failed",
            "knowledge_status": "completed",
            "retrieval_gaps": [],
            "external_available": False,
        }
        external_failure = {
            "request_id": "request-1",
            "scope": "external",
            "customer_context": [],
            "context_items": [],
            "customer_context_status": "not_applicable",
            "knowledge_status": "failed",
            "retrieval_gaps": [external_gap],
            "external_available": True,
        }
        session = _Session(
            _Response(internal_partial),
            _Response(internal_customer_failure),
            _Response(external_failure),
        )
        backend = self.client(session, mailbox_id=None)

        first_internal = backend.get_answer_context("request-1", "internal")
        second_internal = backend.get_answer_context("request-1", "internal")
        external = backend.get_answer_context("request-1", "external")

        self.assertEqual(first_internal["customer_context_status"], "completed")
        self.assertEqual(first_internal["knowledge_status"], "failed")
        self.assertEqual(first_internal["retrieval_gaps"], [internal_gap])
        self.assertEqual(second_internal["customer_context_status"], "failed")
        self.assertEqual(second_internal["knowledge_status"], "completed")
        self.assertEqual(external["customer_context_status"], "not_applicable")
        self.assertEqual(external["knowledge_status"], "failed")
        self.assertEqual(external["retrieval_gaps"], [external_gap])
        first_internal["customer_context"][0]["content"] = "changed"
        self.assertEqual(customer_item["content"], "需要正式报价。")

        expected_scopes = ["internal", "internal", "external"]
        for call, expected_scope in zip(session.calls, expected_scopes):
            with self.subTest(scope=expected_scope):
                method, url, kwargs = call
                self.assertEqual(method, "POST")
                self.assertTrue(url.endswith("agent/chat/context/"))
                self.assertEqual(
                    kwargs["json"],
                    {"request_id": "request-1", "scope": expected_scope},
                )
                self.assertEqual(
                    kwargs["headers"]["Authorization"], "Agent service-secret"
                )

    def test_chat_report_maps_completed_failed_and_duplicate_results(self):
        completed = {
            "request_id": "request-1",
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "客户关注正式报价。[1]",
            "citations": [
                {
                    "source_id": "mail:1",
                    "source_type": "customer_email",
                    "title_or_label": "采购咨询",
                }
            ],
            "status": "completed",
            "error": None,
        }
        failed = {
            "request_id": "request-2",
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "",
            "citations": [],
            "status": "failed",
            "error": {
                "code": "model_unavailable",
                "message": "回答模型暂时不可用，请稍后重试。",
            },
        }
        session = _Session(
            _Response(
                {
                    "request_id": "request-1",
                    "saved": True,
                    "duplicate": False,
                    "assistant_message_id": "assistant-1",
                }
            ),
            _Response(
                {
                    "request_id": "request-2",
                    "saved": True,
                    "duplicate": False,
                    "assistant_message_id": None,
                }
            ),
            _Response(
                {
                    "request_id": "request-1",
                    "saved": True,
                    "duplicate": True,
                    "assistant_message_id": "assistant-1",
                }
            ),
        )
        backend = self.client(session, mailbox_id=None)

        saved = backend.report_answer(completed)
        failure_saved = backend.report_answer(failed)
        duplicate = backend.report_answer(completed)

        self.assertFalse(saved["duplicate"])
        self.assertIsNone(failure_saved["assistant_message_id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["assistant_message_id"], "assistant-1")
        self.assertIsNot(saved, session.responses)
        for call, expected_payload in zip(
            session.calls, (completed, failed, completed)
        ):
            with self.subTest(request_id=expected_payload["request_id"]):
                method, url, kwargs = call
                self.assertEqual(method, "POST")
                self.assertTrue(url.endswith("agent/chat/answers/"))
                self.assertEqual(kwargs["json"], expected_payload)
                self.assertIsNot(kwargs["json"], expected_payload)
                self.assertEqual(
                    kwargs["headers"]["Authorization"], "Agent service-secret"
                )

    def test_chat_scope_and_report_input_validation_happens_before_transport(self):
        session = _Session()
        backend = self.client(session, mailbox_id=None)

        invalid_context_calls = (
            ("", "internal"),
            ("request-1", "partner"),
            ("request-1", "INTERNAL"),
        )
        for request_id, scope in invalid_context_calls:
            with self.subTest(request_id=request_id, scope=scope):
                with self.assertRaises(BackendContractError):
                    backend.get_answer_context(request_id, scope)
        with self.assertRaises(BackendContractError):
            backend.report_answer([])
        with self.assertRaises(BackendContractError):
            backend.report_answer({"request_id": "request-1", "status": "pending"})
        with self.assertRaises(BackendContractError):
            backend.report_answer(
                {"request_id": "request-1", "status": "completed"}
            )
        with self.assertRaises(BackendContractError):
            backend.report_answer(
                {
                    "request_id": "request-1",
                    "chat_prompt_version": " ",
                    "status": "completed",
                }
            )

        self.assertEqual(session.calls, [])

    def test_chat_claim_rejects_malformed_response_objects(self):
        malformed_payloads = (
            [],
            {},
            {"request": []},
            {"request": {
                "request_id": "request-1", "conversation_id": "conversation-1",
                "company_id": "company-1", "user_message_id": "message-1",
            }},
            {
                "request": {
                    "request_id": "",
                    "conversation_id": "conversation-1",
                    "company_id": "company-1",
                    "user_message_id": "message-1",
                }
            },
            {
                "request": {
                    "request_id": "request-1",
                    "conversation_id": "",
                    "company_id": "company-1",
                    "user_message_id": "message-1",
                }
            },
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                session = _Session(_Response(payload))
                with self.assertRaises(BackendContractError):
                    self.client(session, mailbox_id=None).claim_answer_request()

    def test_chat_context_rejects_mismatches_and_malformed_status_objects(self):
        valid = {
            "request_id": "request-1",
            "scope": "internal",
            "customer_context": [],
            "context_items": [],
            "customer_context_status": "completed",
            "knowledge_status": "completed",
            "retrieval_gaps": [],
            "external_available": True,
        }
        malformed_payloads = (
            [],
            dict(valid, request_id="request-2"),
            dict(valid, scope="external"),
            dict(valid, customer_context="not-a-list"),
            dict(valid, context_items=["not-an-object"]),
            dict(valid, customer_context_status="not_applicable"),
            dict(valid, knowledge_status="partial"),
            dict(valid, retrieval_gaps="not-a-list"),
            dict(
                valid,
                retrieval_gaps=[
                    {
                        "scope": "internal_knowledge",
                        "code": "unavailable",
                        "message": "暂时不可用。",
                        "raw_detail": "must-not-be-accepted",
                    }
                ],
            ),
            dict(valid, external_available="true"),
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                session = _Session(_Response(payload))
                with self.assertRaises(BackendContractError):
                    self.client(session, mailbox_id=None).get_answer_context(
                        "request-1", "internal"
                    )

        invalid_external = dict(
            valid,
            scope="external",
            customer_context=[{"unexpected": "customer-data"}],
            customer_context_status="not_applicable",
        )
        session = _Session(_Response(invalid_external))
        with self.assertRaises(BackendContractError):
            self.client(session, mailbox_id=None).get_answer_context(
                "request-1", "external"
            )

        unavailable_external = dict(
            valid,
            scope="external",
            customer_context_status="not_applicable",
            external_available=False,
        )
        session = _Session(_Response(unavailable_external))
        with self.assertRaises(BackendContractError):
            self.client(session, mailbox_id=None).get_answer_context(
                "request-1", "external"
            )

    def test_chat_report_rejects_mismatched_and_malformed_response_objects(self):
        result = {
            "request_id": "request-1",
            "chat_prompt_version": "workspace-chat-v1",
            "assistant_text": "有依据的回答。[1]",
            "citations": [],
            "status": "completed",
            "error": None,
        }
        malformed_payloads = (
            [],
            {
                "request_id": "request-2",
                "saved": True,
                "duplicate": False,
                "assistant_message_id": "assistant-1",
            },
            {
                "request_id": "request-1",
                "saved": False,
                "duplicate": False,
                "assistant_message_id": "assistant-1",
            },
            {
                "request_id": "request-1",
                "saved": True,
                "duplicate": "false",
                "assistant_message_id": "assistant-1",
            },
            {
                "request_id": "request-1",
                "saved": True,
                "duplicate": False,
            },
            {
                "request_id": "request-1",
                "saved": True,
                "duplicate": False,
                "assistant_message_id": None,
            },
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                session = _Session(_Response(payload))
                with self.assertRaises(BackendContractError):
                    self.client(session, mailbox_id=None).report_answer(result)

    def test_chat_non_json_and_http_failures_are_safe(self):
        import io
        import logging

        class _NonJsonResponse(_Response):
            def json(self):
                raise ValueError("raw response body")

        sensitive_values = (
            "service-secret",
            "SENSITIVE QUESTION BODY",
            "SENSITIVE CONTEXT BODY",
            "SENSITIVE ANSWER BODY",
            "raw response body",
        )
        captured_logs = io.StringIO()
        logger = logging.getLogger("agent.clients.backend_api")
        handler = logging.StreamHandler(captured_logs)
        logger.addHandler(handler)
        errors = []
        try:
            non_json_session = _Session(
                _NonJsonResponse({"untrusted": "raw response body"})
            )
            with self.assertRaises(BackendContractError) as non_json_error:
                self.client(
                    non_json_session, mailbox_id=None
                ).claim_answer_request()
            errors.append(str(non_json_error.exception))

            http_session = _Session(
                _Response(
                    {
                        "question": "SENSITIVE QUESTION BODY",
                        "context": "SENSITIVE CONTEXT BODY",
                        "assistant_text": "SENSITIVE ANSWER BODY",
                        "error": {
                            "code": "temporarily_unavailable",
                            "detail": "聊天后端暂时不可用。",
                        },
                    },
                    status=503,
                )
            )
            with self.assertRaises(BackendRequestError) as http_error:
                self.client(http_session, mailbox_id=None).get_answer_context(
                    "request-1", "internal"
                )
            errors.append(str(http_error.exception))
            self.assertEqual(http_error.exception.status_code, 503)
            self.assertEqual(http_error.exception.code, "temporarily_unavailable")
        finally:
            logger.removeHandler(handler)

        observable_failure_output = "\n".join(errors) + captured_logs.getvalue()
        for sensitive in sensitive_values:
            with self.subTest(sensitive=sensitive):
                self.assertNotIn(sensitive, observable_failure_output)


if __name__ == "__main__":
    unittest.main()
