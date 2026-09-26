# QQ Test Mail Injector

This tool is distributed with the Gmail injector in the `salesmate-test-tools` download package. It appends synthetic mail to the `INBOX` of the QQ account that you sign in to through QQ IMAP. Use it to verify SalesMate QQ synchronization, per-message originals, company grouping, and L1–L4 analysis. It never sends mail to template sender addresses or other external addresses.

It requires only the Python 3.10+ standard library and can run independently from SalesMate. It does not require Gmail dependencies, Google OAuth configuration, or a custom callback domain. The tool uses fixed `imap.qq.com:993` and validates the TLS certificate. It does not read the main project's `.env` or saved QQ connections.

## 1. Prepare synthetic mail

After extracting the package, run this in the tools directory:

```powershell
Copy-Item .\qq_test_messages.template.json .\qq_test_messages.local.json
```

Edit `qq_test_messages.local.json` and change top-level `mailbox_address` to your complete QQ or foxmail address. The remaining format matches the Gmail template:

```json
{
  "mailbox_address": "your-account@qq.com",
  "messages": [
    {
      "from": "测试客户 <buyer@customer-test.example>",
      "subject": "设备采购咨询",
      "body": "我们计划采购 12 台设备，请提供报价与交期。"
    }
  ]
}
```

Each message permits only `from`, `subject`, and `body`, and all messages are written to the top-level mailbox. Additional `to`, server, folder, or authorization-code fields are rejected. The template contains six synthetic scenarios covering multiple contacts from one company, different companies, shared mailboxes, and non-business notices. Template content must not contain real customer data.

## 2. Offline preview

```powershell
python .\qq_test_injector.py --messages-file .\qq_test_messages.local.json --dry-run
```

This outputs target mailbox, batch marker, count, sender, and subject. It does not read an authorization code or connect to QQ. When `--messages-file` is omitted, it reads the packaged example. You must explicitly choose either `--dry-run` or `--apply`; no mode means no write.

## 3. Explicitly write to your own inbox

Enable IMAP/SMTP in QQ Mail and generate a client authorization code, then run:

```powershell
python .\qq_test_injector.py --messages-file .\qq_test_messages.local.json --apply
```

The program signs in using the mailbox address in JSON and prompts for a 16-character client authorization code. It does not echo the code or save it to a file, and it does not accept an authorization code on the command line. A controlled script environment may preset `QQ_TEST_AUTHORIZATION_CODE`; otherwise it prompts for secure input. It fails explicitly when no secure-input terminal exists and does not fall back to echoing input. Never place a real authorization code in JSON, GitHub Actions, commit history, or shared logs.

The program uses standard-library [IMAP APPEND](https://docs.python.org/3/library/imaplib.html#imaplib.IMAP4.append) and waits for server confirmation for every message. The QQ service must allow the current account to perform APPEND. If it rejects the action, the program reports failure and does not switch to SMTP. Preview and mocked tests do not prove that the real QQ service permits writes.

Output states:

- `completed`: Every mail item received an explicit success response.
- `failed`: Authentication, template, or explicit-rejection failure. `inserted_count` is the number confirmed successful in this batch; previously successful mail is not rolled back.
- `uncertain`: The current APPEND was interrupted or response was unclear, so mail may already be written. Record `uncertain_message_id`, inspect the mailbox by batch subject and Message-ID first, and do not rerun directly.

There is no automatic retry. Each run generates a different batch, so repeated `--apply` creates new test mail. There is no deletion, EXPUNGE, or automatic cleanup feature. The program appends mail only to your own inbox; template `From` values are synthetic data rather than real SMTP sender identities.

## 4. Verify in SalesMate

1. Sign in to SalesMate and connect the QQ receiving account that matches JSON.
2. Manually choose a recent-days or maximum-message-count scope, then synchronize. If only a count is set and the mailbox has newer mail, those messages may be processed first; choose scope according to actual conditions.
3. Under the QQ account, view synchronized mail. Use the output batch marker to verify subject and original text, then inspect the company and analysis corresponding to business mail.
4. To clean up, search QQ Web Mail for output `search_subject` or batch number and manually delete only after verification. Deleting QQ mail does not automatically remove SalesMate history.

This injector verifies the receiving and analysis path. It does not verify SMTP delivery, SPF/DKIM, or spam classification. To test production QQ sending, connect QQ sending under SalesMate external connections, then prepare, preview, and confirm a mail draft under external actions. The package workflow does not perform these actions for you.

## 5. Developer checks

From the complete repository root, run:

```powershell
python -m unittest discover -s test_tools/tests -v
python backend/tools/check_docs.py test_tools/qq_test_injector.py test_tools/tests/test_qq_test_injector.py
python test_tools/qq_test_injector.py --dry-run
```

Tests mock network and credential input, covering offline preview, explicit write, header validation, fixed target, partial success, uncertain result, and log redaction. GitHub Actions uses Python 3.11. The distribution directory separately rechecks both tools with `python -I` to avoid implicit dependencies on main-project modules.
