# QQ 测试邮件注入器

此工具与 Gmail 注入器一起发布在 `salesmate-test-tools` 下载包中。通过 QQ IMAP 将合成邮件追加到自己登录账号的 `INBOX`，用于验证 SalesMate 的 QQ 同步、逐封原文、公司归组与 L1–L4 分析。它不会向模板中的发件地址或其他外部地址发送邮件。

只需要 Python 3.10 或以上版本的标准库，可脱离 SalesMate 独立运行，不需要安装 Gmail 依赖、Google OAuth 配置或自有回调域名。工具使用固定 `imap.qq.com:993` 并验证 TLS 证书，不读取主项目的 `.env` 或已保存的 QQ 连接。

## 1. 准备合成邮件

解压后在工具目录执行：

```powershell
Copy-Item .\qq_test_messages.template.json .\qq_test_messages.local.json
```

编辑 `qq_test_messages.local.json`，把顶层 `mailbox_address` 改为自己的完整 QQ 或 foxmail 邮箱。其余格式与 Gmail 模板一致：

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

每封邮件仅允许 `from`、`subject`、`body`，统一写入顶层邮箱；不接受额外 `to`、服务器、文件夹或授权码字段。模板自带六封合成场景，涵盖同公司多联系人、不同公司、公共邮箱与非业务通知。模板内容应避免真实客户资料。

## 2. 离线预览

```powershell
python .\qq_test_injector.py --messages-file .\qq_test_messages.local.json --dry-run
```

输出目标邮箱、批次标记、数量、发件人和主题；不读取授权码、不连接 QQ。省略 `--messages-file` 时读取随包示例。必须明确选择 `--dry-run` 或 `--apply`，不指定模式不会写入。

## 3. 明确写入自己的收件箱

在 QQ 邮箱中开启 IMAP/SMTP 并生成客户端授权码，然后执行：

```powershell
python .\qq_test_injector.py --messages-file .\qq_test_messages.local.json --apply
```

程序以 JSON 中的邮箱地址登录，提示输入 16 位客户端授权码，不回显且不保存到文件。它不接受命令行授权码。受控脚本环境也可预先设置 `QQ_TEST_AUTHORIZATION_CODE`；未设置才提示安全输入，没有安全输入终端时明确失败，不降级为回显。不要把真实授权码放入 JSON、GitHub Actions、提交记录或共享日志。

程序使用标准库的 [IMAP APPEND](https://docs.python.org/3/library/imaplib.html#imaplib.IMAP4.append)，逐封等待服务端确认。QQ 服务必须允许当前账号执行 APPEND；如果拒绝，程序报告失败，不切换为 SMTP。预览与模拟测试不能证明真实 QQ 服务已允许写入。

输出说明：

- `completed`：所有邮件收到明确成功响应。
- `failed`：认证、模板或明确拒绝等失败；`inserted_count` 是本批已确认成功数量，先前成功的邮件不会被回滚。
- `uncertain`：当前 APPEND 中断或响应不明确，邮件可能已经写入。记录 `uncertain_message_id`，先按批次主题和 Message-ID 核对邮箱，不要直接重跑。

没有自动重试。每次执行生成不同批次，重复执行 `--apply` 会创建新的测试邮件。没有删除、EXPUNGE 或自动清理功能。程序只向自己的收件箱追加邮件，模板 `From` 是合成数据，不是实际 SMTP 发件身份。

## 4. 在 SalesMate 验证

1. 登录 SalesMate，连接与 JSON 一致的 QQ 收信账号。
2. 手动选择「最近 N 天」或「最多 N 封」范围，再同步；若只设封数且邮箱有更新邮件，可能先处理那些邮件，应据实选择范围。
3. 在 QQ 账号下点击「查看已同步邮件」，按输出中的批次标记核对主题和原文，再检查业务邮件对应的公司与分析。
4. 需要清理时，在 QQ 网页端搜索输出中的 `search_subject` 或批次编号，核实后手动删除。删除 QQ 邮件不会自动清除 SalesMate 中的历史记录。

此注入器验证收信与分析链路，不验证 SMTP 投递、SPF/DKIM 或垃圾邮件分类。要测试正式 QQ 发信，请在 SalesMate「外部连接」中连接 QQ 发信，在「外部动作」中准备邮件草稿并预览、确认；打包流水线不会替你执行这些操作。

## 5. 开发者检查

在完整仓库根目录运行：

```powershell
python -m unittest discover -s test_tools/tests -v
python backend/tools/check_docs.py test_tools/qq_test_injector.py test_tools/tests/test_qq_test_injector.py
python test_tools/qq_test_injector.py --dry-run
```

测试模拟网络和凭证输入，覆盖离线预览、显式写入、头部校验、固定目标、部分成功、未知结果与日志脱敏。GitHub Actions 使用 Python 3.11，分发目录另以 `python -I` 复验两种工具，避免隐式依赖主项目模块。
