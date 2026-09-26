"""Responsibility: Verify QQ IMAP parsing and read-only boundaries.
Implementation: Mock IMAP transport to verify folder discovery, UID deduplication, generations, and MIME without a real mailbox.
Relationships: qq_mail adapter; all test emails and authorization codes are fictional.
Directory:
- QQMailTests: QQ transport tests.
- QQMailTests.test_connect_uses_tls_and_hides_provider_error: TLS and sanitized errors.
- QQMailTests.test_folders_requires_sent_and_supports_flags: Sent-folder discovery.
- QQMailTests.test_uid_search_filters_reverse_star_range: Incremental lower-bound filtering.
- QQMailTests.test_message_ids_distinguish_folder_and_generation: Message identity boundaries.
- QQMailTests.test_read_is_peek_and_preserves_dates: Read-only MIME retrieval.
- QQMailTests.test_changed_validity_stops_before_fetch: Reject reads after a generation change.
- QQMailTests.test_date_search_and_metadata_are_body_free: Date filtering and metadata-only reads.
- QQMailTests.test_metadata_rejects_missing_or_invalid_responses: Reject missing, duplicate, or invalid metadata.
Variable index:
- None
"""
import imaplib
from datetime import datetime, timezone
import ssl
import unittest
from unittest.mock import Mock, patch

from agent.tools import qq_mail


# Function: Verify deterministic behavior of the QQ protocol adapter.
# Logic: Mock network responses with realistic IMAP structures.
# Constraints: Does not verify real QQ account authorization or external connectivity.
class QQMailTests(unittest.TestCase):
    # Function: Verify connection to the fixed host and certificate validation.
    # Inputs: No external parameters; mock the SSL client and authentication failure.
    # Outputs: TLS validation is enabled and errors exclude the mock authorization code.
    # Logic: Inspect constructor arguments and failure cleanup.
    # Constraints: Do not connect to QQ or log credentials.
    def test_connect_uses_tls_and_hides_provider_error(self):
        with patch.object(qq_mail.imaplib, "IMAP4_SSL") as factory:
            client = factory.return_value
            client.login.return_value = ("OK", [])
            self.assertIs(qq_mail.connect("demo@qq.com", "abcdefghijklmnop"), client)
            self.assertEqual(factory.call_args.args, ("imap.qq.com", 993))
            self.assertEqual(factory.call_args.kwargs["ssl_context"].verify_mode, ssl.CERT_REQUIRED)
            client.login.side_effect = imaplib.IMAP4.error("raw-secret-provider-error")
            with self.assertRaises(qq_mail.QQMailError) as caught:
                qq_mail.connect("demo@qq.com", "abcdefghijklmnop")
            self.assertNotIn("raw-secret", str(caught.exception))
            client.logout.assert_called_once()

    # Function: Verify sent-folder identification and failure for incomplete scope.
    # Inputs: No external parameters; three LIST responses.
    # Outputs: Recognize flags/QQ names and fail if the sent folder is absent.
    # Logic: Test special-use flags and fixed service names.
    # Constraints: Do not treat a missing folder as permission to synchronize only the inbox.
    def test_folders_requires_sent_and_supports_flags(self):
        client = Mock()
        client.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent Messages"'])
        self.assertEqual(qq_mail.folders(client), ["INBOX", "Sent Messages"])
        client.list.return_value = ("OK", [b'() "/" "&XfJT0ZAB-"'])
        self.assertEqual(qq_mail.folders(client)[1], "&XfJT0ZAB-")
        client.list.return_value = ("OK", [b'() "/" "INBOX"'])
        with self.assertRaises(qq_mail.QQMailError):
            qq_mail.folders(client)

    # Function: Verify that IMAP asterisk reverse ranges do not reimport old UIDs.
    # Inputs: No external parameters; the server returns UIDs below and above the lower bound.
    # Outputs: Retain only values strictly greater than the saved cursor.
    # Logic: Sort and filter explicitly.
    # Constraints: Do not automatically rescan old emails.
    def test_uid_search_filters_reverse_star_range(self):
        client = Mock()
        client.uid.return_value = ("OK", [b"5 7 6 7"])
        self.assertEqual(qq_mail.list_uids(client, 5), [6, 7])
        client.uid.return_value = ("OK", [b"5"])
        self.assertEqual(qq_mail.list_uids(client, 5), [])

    # Function: Verify persistent message identifier uniqueness and safe parsing.
    # Inputs: No external parameters; the same UID appears in different folders or generations.
    # Outputs: Distinct reversible identifiers; reject invalid encodings.
    # Logic: Test encoding round trips and injection inputs.
    # Constraints: Do not replace IMAP identity with a potentially repeated RFC Message-ID.
    def test_message_ids_distinguish_folder_and_generation(self):
        values = {qq_mail.message_id("INBOX", 10, 1), qq_mail.message_id("Sent Messages", 10, 1), qq_mail.message_id("INBOX", 11, 1)}
        self.assertEqual(len(values), 3)
        self.assertEqual(qq_mail.split_message_id(qq_mail.message_id("Sent Messages", 10, 1)), ("Sent Messages", 10, 1))
        with self.assertRaises(qq_mail.QQMailError):
            qq_mail.split_message_id("qq:SU5CT1g:10:1\r\nLOGOUT")

    # Function: Verify MIME parsing and side-effect-free reads.
    # Inputs: No external parameters; mock INTERNALDATE and one fictional email.
    # Outputs: Preserve source ID, reception time, and body.
    # Logic: Verify readonly and BODY.PEEK[] commands.
    # Constraints: Do not substitute current runtime time for actual reception time.
    def test_read_is_peek_and_preserves_dates(self):
        client = Mock()
        client.select.return_value = ("OK", [b"1"])
        client.response.return_value = ("UIDVALIDITY", [b"20"])
        raw = b"From: buyer@example.com\r\nTo: demo@qq.com\r\nDate: Mon, 14 Sep 2026 09:00:00 +0800\r\nSubject: Inquiry\r\n\r\nNeed 2 units."
        client.uid.return_value = ("OK", [(b'1 (UID 3 INTERNALDATE "14-Sep-2026 10:00:00 +0800" BODY[] {100}', raw), b')'])
        value = qq_mail.message_id("INBOX", 20, 3)
        result = qq_mail.read_email(client, value)
        self.assertEqual(result["gmail_message_id"], value)
        self.assertEqual(result["received_at"], "2026-09-14T02:00:00+00:00")
        self.assertIsNone(result["thread_id"])
        self.assertEqual(result["body_text"], "Need 2 units.")
        client.select.assert_called_once_with('"INBOX"', readonly=True)
        client.uid.assert_called_once_with("fetch", "3", "(UID INTERNALDATE BODY.PEEK[])")

    # Function: Prevent incorrect reads after folder generation changes.
    # Inputs: No external parameters; persisted and server generations differ.
    # Outputs: Failure without FETCH.
    # Logic: Check identity before reading raw content.
    # Constraints: Do not implicitly reset the cursor.
    def test_changed_validity_stops_before_fetch(self):
        client = Mock()
        client.select.return_value = ("OK", [b"1"])
        client.response.return_value = ("UIDVALIDITY", [b"21"])
        with self.assertRaises(qq_mail.QQMailError):
            qq_mail.read_email(client, qq_mail.message_id("INBOX", 20, 3))
        client.uid.assert_not_called()

    # Function: Verify coarse date filtering covers timezone boundaries without requesting bodies.
    # Inputs: No external parameters; a UTC date lower bound and two timezone-aware IMAP date responses.
    # Outputs: Move SINCE back one day, convert internal dates to UTC, and request only metadata through FETCH.
    # Logic: Inspect actual command arguments and returned dates.
    # Constraints: No network; do not substitute MIME Date for INTERNALDATE.
    def test_date_search_and_metadata_are_body_free(self):
        client = Mock()
        client.uid.return_value = ("OK", [b"1 2"])
        qq_mail.list_uids(client, 0, since=datetime(2026, 9, 7, 3, tzinfo=timezone.utc))
        client.uid.assert_called_once_with("search", None, "UID", "1:*", "SINCE", "06-Sep-2026")
        client.uid.reset_mock()
        client.uid.return_value = ("OK", [b'1 (UID 1 INTERNALDATE "06-Sep-2026 23:00:00 -0800")', b'2 (INTERNALDATE "07-Sep-2026 15:00:00 +0800" UID 2)'])
        dates = qq_mail.message_dates(client, [1, 2])
        self.assertEqual(dates[1], datetime(2026, 9, 7, 7, tzinfo=timezone.utc))
        self.assertEqual(dates[1], dates[2])
        client.uid.assert_called_once_with("fetch", "1,2", "(UID INTERNALDATE)")

    # Function: Verify date queries cannot report success for incomplete scope.
    # Inputs: No external parameters; mocked responses contain missing, duplicate, extra UIDs, and invalid dates.
    # Outputs: All cases raise QQMailError.
    # Logic: Compare requested sets, parsed results, and service status.
    # Constraints: Do not automatically retry or read bodies to fill missing metadata.
    def test_metadata_rejects_missing_or_invalid_responses(self):
        row = b'1 (UID 1 INTERNALDATE "14-Sep-2026 10:00:00 +0800")'
        for response in [("NO", []), ("OK", []), ("OK", [row, row]), ("OK", [row.replace(b'UID 1', b'UID 2')]), ("OK", [b'1 (UID 1 INTERNALDATE "invalid")'])]:
            client = Mock()
            client.uid.return_value = response
            with self.subTest(response=response), self.assertRaises(qq_mail.QQMailError):
                qq_mail.message_dates(client, [1])
