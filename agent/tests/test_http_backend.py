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

    def test_metadata_only_cache_hit_and_http_error_fail_explicitly(self):
        cache_session = _Session(
            _Response({"hit": True, "status": "completed", "input_version": "v1"})
        )
        with self.assertRaisesRegex(BackendContractError, "完整 Analysis"):
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


if __name__ == "__main__":
    unittest.main()
