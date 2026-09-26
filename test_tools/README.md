# SalesMate Test Tools

The download package provides Gmail and QQ test-mail injectors. Both verify inbox synchronization, mail extraction, and customer analysis without sending mail to external addresses.

| Mailbox | Script | Usage |
|---|---|---|
| Gmail | `gmail_test_injector.py` | Later sections of this document; requires separate Google Desktop OAuth |
| QQ / foxmail | `qq_test_injector.py` | [QQ test-tool guide](QQ_README.md); requires only Python's standard library and a QQ client authorization code |

This directory provides the Gmail test-mail injector for developers and testers to repeatedly verify the complete SalesMate flow. It can be installed and run independently from the main project using only this directory's requirements.txt. It uses the Gmail API to insert synthetic mail directly into the tester's own Gmail inbox. Ordinary Gmail synchronization in SalesMate then runs L1 fact extraction, backend company grouping, and L2–L4 customer analysis.

The tool does not modify the SalesMate backend or send mail externally. Gmail messages.insert verifies Gmail reading and SalesMate processing only, not SMTP delivery, SPF, DKIM, or spam classification.

Updates to test_tools or its workflow pushed to main trigger GitHub Actions validation and a ZIP containing both injectors. Relevant pull requests and manual runs also trigger checks. Download salesmate-test-tools from **Actions → Package test tools → latest successful run → Artifacts**, extract it, and follow this guide. Artifacts are retained for 30 days and can be rebuilt manually from the workflow page.

The workflow runs QQ offline unit tests and Gmail/QQ --dry-run, then revalidates the actual distribution directory. It uses no mailbox credentials, writes no mailbox data, and does not test SMTP delivery. Packages contain only scripts, templates, dependency manifests, and documentation, never personal credentials or *.local.json.

## 1. Install the Gmail injector independently

Install Python 3.10 or newer. Downloading or extracting only test_tools is sufficient; agent, backend, and main-project dependencies are unnecessary.

In PowerShell, enter the downloaded directory and create an isolated environment:

```powershell
Set-Location .\test_tools
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

All Gmail commands below run from test_tools. After reopening a terminal, enter that directory and run `.\.venv\Scripts\Activate.ps1`.

## 2. Create a dedicated Google OAuth client

1. Open Google Cloud Console and enter **APIs & Services → Credentials** for the current project.
2. Confirm Gmail API is enabled.
3. Create an OAuth Client ID with application type **Desktop app**.
4. Download the JSON and rename it gmail_inject_credentials.json.
5. Place it in the downloaded tools directory:

```text
test_tools/
├── .gitignore
├── README.md
├── requirements.txt
├── gmail_test_injector.py
├── gmail_test_messages.template.json
└── gmail_inject_credentials.json
```

Do not use the Web application OAuth client for browser Gmail authorization. A Web client accepts only registered Django callback addresses, while this tool starts a random localhost port and would produce redirect_uri_mismatch.

If the consent screen is in testing status, add the Gmail account under Test users to avoid 403 access_denied.

gmail_inject_credentials.json contains a client secret and is Git-ignored. Do not commit it or send it through chat, issues, or commit history. Each tester should use a separate credential file.

## 3. Write test-mail JSON

Copy and modify the template rather than editing it directly:

```powershell
Copy-Item .\gmail_test_messages.template.json .\gmail_test_messages.local.json
```

*.local.json is ignored by test_tools/.gitignore and can contain each tester's mailbox and test content. For shared team cases, create another JSON without .local and commit only after confirming it has no real customer data or credentials.

JSON format:

```json
{
  "mailbox_address": "your-account@gmail.com",
  "messages": [
    {
      "from": "客户姓名 <customer@example.com>",
      "subject": "采购咨询",
      "body": "我们希望采购 10 台检测设备，请提供报价和交付周期。"
    }
  ]
}
```

| Field | Requirement |
|---|---|
| mailbox_address | Full receiving Gmail address, also authorized in SalesMate |
| messages | Array containing at least one message |
| messages[].from | Plain email or Name <email>; same company domains test grouping |
| messages[].subject | Nonempty string without line breaks |
| messages[].body | Nonempty body, the primary L1 fact-extraction input |

The recipient is always top-level mailbox_address. Individual messages cannot set to, preventing a batch from being inserted into different accounts.

## 4. Preview test mail

From test_tools:

```powershell
python .\gmail_test_injector.py --dry-run
```

By default this reads same-directory gmail_test_messages.template.json. To preview custom data:

```powershell
python .\gmail_test_injector.py `
  --messages-file .\gmail_test_messages.local.json `
  --dry-run
```

--dry-run validates the complete JSON and prints the inbox, sender, and subject without accessing Gmail or opening authorization.

## 5. Insert test mail

```powershell
python .\gmail_test_injector.py
```

For custom JSON:

```powershell
python .\gmail_test_injector.py `
  --messages-file .\gmail_test_messages.local.json
```

The first run opens Google sign-in and requests gmail.insert and gmail.readonly. After authorization, gmail_inject_token.json is created in the same directory and reused later.

JSON mailbox_address must match the account actually authorized in the browser. The tool reads the Gmail profile before writing to prevent insertion into the wrong account.

Successful output includes:

- run_id for this execution.
- Gmail message IDs for six messages.
- gmail_search for searching and cleanup.

The six samples cover grouping multiple same-domain contacts into one company, different company domains, public Gmail contacts, and no-reply non-business mail.

## 6. Run the complete SalesMate flow

After injection, test mail exists in the specified Gmail inbox. Stop here if testing only injection. For the complete flow, use the full SalesMate repository root:

1. Start Django and the web UI:

   ```powershell
   python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
   ```

2. Open http://127.0.0.1:8000/.
3. Confirm the left-side connection uses the receiving Gmail account.
4. Select Gmail synchronization.
5. Wait for synchronization and analysis, then inspect companies, original mail, facts, profiles, analysis, and follow-up scores.

Mail enters through the production Gmail read-only synchronization path, executing normal deduplication and Gmail History incremental reads.

## 7. Reauthorize or change accounts

Delete the local token and rerun insertion:

```powershell
Remove-Item -LiteralPath .\gmail_inject_token.json
python .\gmail_test_injector.py `
  --messages-file .\another_account_messages.local.json
```

Also delete the old token and reauthorize after changing scopes.

## 8. Delete test mail from Gmail

Copy gmail_search from successful output, search in Gmail, verify results, and delete in bulk. Subject format:

```text
[SalesMate测试:<run_id>] Mail subject
```

Deleting Gmail mail does not automatically remove records already synchronized into SalesMate.

## 9. Common errors

| Error | Cause | Resolution |
|---|---|---|
| redirect_uri_mismatch | Web application client used | Create a Desktop app client and replace test_tools/gmail_inject_credentials.json |
| 403 access_denied | Current account is not an OAuth test user | Add it under Test users in the consent screen |
| Authorized account mismatch | Token account differs from JSON mailbox_address | Delete gmail_inject_token.json and reauthorize, or correct JSON |
| JSON validation failure | Missing/wrong-type field or empty mail array | Correct custom JSON against gmail_test_messages.template.json |
| Insufficient token scope | Old token lacks gmail.insert | Delete the token and reauthorize |
| Credential file not found | Incorrect name/location | Confirm test_tools/gmail_inject_credentials.json exists |
