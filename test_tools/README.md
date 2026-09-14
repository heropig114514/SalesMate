# SalesMate 测试工具

下载包同时提供 Gmail 和 QQ 测试邮件注入器。两者用于验证「收件箱同步 → 邮件抽取 → 客户分析」，不会向外部地址发信。

| 邮箱 | 脚本 | 使用说明 |
|---|---|---|
| Gmail | `gmail_test_injector.py` | 本文后续章节；需要独立 Google Desktop OAuth |
| QQ / foxmail | `qq_test_injector.py` | [QQ 测试工具说明](QQ_README.md)；只需 Python 标准库和 QQ 客户端授权码 |

本目录提供 Gmail 测试邮件注入器，供开发和测试人员反复验证 SalesMate 全流程。注入器可以脱离 SalesMate 主项目独立安装和运行，只依赖本目录的 `requirements.txt`。它通过 Gmail API 将合成邮件直接插入测试人员自己的 Gmail 收件箱，随后可以在 SalesMate 网页中执行正常的 Gmail 同步，让邮件依次经过 L1 事实抽取、后端公司归组以及 L2–L4 客户分析。

工具不会修改 SalesMate 后端，也不会把邮件发送到外部地址。它使用 Gmail `messages.insert`，因此只验证 Gmail 读取和 SalesMate 处理链路，不验证 SMTP 投递、SPF、DKIM 或垃圾邮件分类。

每次 `test_tools` 或该流水线配置更新并推送到 `main` 后，GitHub Actions 会自动验证并生成包含两种工具的 ZIP。相关 PR 和手动执行也会触发检查。在 GitHub 仓库的 **Actions → Package test tools → 最新成功运行 → Artifacts** 中下载 `salesmate-test-tools`，解压后即可按照本文操作。Artifact 保留 30 天，也可以从该工作流页面手动重新打包。

流水线运行 QQ 离线单元测试及 Gmail/QQ 的 `--dry-run`，并在实际分发目录复验。它不使用邮箱凭证，不写入邮箱、不测试 SMTP 投递；下载包只包含脚本、模板、依赖清单和说明，不含个人凭证或 `*.local.json`。

## 1. 独立安装 Gmail 注入器

安装 Python 3.10 或更高版本。只下载或解压 `test_tools` 文件夹即可，不需要下载 `agent`、`backend` 或安装主项目依赖。

在 PowerShell 中进入下载后的 `test_tools` 目录，为工具创建独立虚拟环境：

```powershell
Set-Location .\test_tools
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

后续章节中的 Gmail 注入命令都默认在 `test_tools` 目录中执行。再次打开终端时，只需要进入该目录并运行 `.\.venv\Scripts\Activate.ps1`。

## 2. 创建测试工具专用 Google OAuth 客户端

1. 打开 Google Cloud Console，进入当前项目的 **APIs & Services → Credentials**。
2. 确认项目已经启用 Gmail API。
3. 创建 OAuth Client ID，Application type 必须选择 **Desktop app**。
4. 下载 JSON，将文件重命名为 `gmail_inject_credentials.json`。
5. 把文件放到下载后的工具目录：

```text
test_tools/
├── .gitignore
├── README.md
├── requirements.txt
├── gmail_test_injector.py
├── gmail_test_messages.template.json
└── gmail_inject_credentials.json
```

不要使用网页 Gmail 授权所用的 **Web application** OAuth Client。Web Client 只接受登记过的 Django 回调地址，而该工具会启动一个随机 localhost 端口，因此会产生 `redirect_uri_mismatch`。

如果 OAuth consent screen 处于测试状态，还需要把准备授权的 Gmail 地址加入 **Test users**，否则可能出现 `403 access_denied`。

`gmail_inject_credentials.json` 包含客户端密钥，已经被 Git 忽略，不要提交或发到聊天、Issue、提交记录中。每名测试人员应使用自己的凭据文件。

## 3. 编写测试邮件 JSON

复制模板后修改，不建议直接改模板文件：

```powershell
Copy-Item .\gmail_test_messages.template.json .\gmail_test_messages.local.json
```

`*.local.json` 已被 `test_tools/.gitignore` 忽略，适合保存每名测试人员自己的邮箱和测试内容。需要作为团队公共案例时，可以另外创建不带 `.local` 的 JSON，并确认其中没有真实客户数据或凭据后提交。

JSON 格式如下：

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

字段要求：

| 字段 | 要求 |
|---|---|
| `mailbox_address` | 接收测试邮件并在 SalesMate 网页授权的完整 Gmail 地址 |
| `messages` | 至少包含一封邮件的数组 |
| `messages[].from` | 纯邮箱或 `名称 <邮箱>` 格式；同一企业域名会用于测试公司归组 |
| `messages[].subject` | 非空字符串，不能包含换行 |
| `messages[].body` | 非空邮件正文，也是 L1 事实抽取的主要输入 |

收件邮箱统一由顶层 `mailbox_address` 指定，每封邮件不能单独设置 `to`。这样可以避免同一批测试数据误插入不同账号。

## 4. 预览测试邮件

在 `test_tools` 目录执行：

```powershell
python .\gmail_test_injector.py --dry-run
```

上面的命令默认读取同目录的 `gmail_test_messages.template.json`。预览自定义文件：

```powershell
python .\gmail_test_injector.py `
  --messages-file .\gmail_test_messages.local.json `
  --dry-run
```

`--dry-run` 会先校验整个 JSON，再输出收件箱、发件人和主题；它不访问 Gmail，也不会打开授权页面。

## 5. 插入测试邮件

```powershell
python .\gmail_test_injector.py
```

插入自定义 JSON：

```powershell
python .\gmail_test_injector.py `
  --messages-file .\gmail_test_messages.local.json
```

首次执行时浏览器会要求登录 Google 并授权 `gmail.insert` 和 `gmail.readonly`。授权成功后，工具会在同一目录生成 `gmail_inject_token.json`，以后运行会自动复用该 token。

JSON 中的 `mailbox_address` 必须与浏览器实际授权的 Gmail 账号相同。工具会在写入前读取 Gmail profile 进行核对，避免把测试邮件插入错误账号。

成功结果包含：

- 本次运行的 `run_id`。
- 六封邮件的 Gmail message ID。
- 可用于 Gmail 搜索和清理的 `gmail_search`。

六封样例覆盖同域名多联系人归入同一公司、不同企业域名、公共 Gmail 联系人和 no-reply 非业务邮件。

## 6. 运行 SalesMate 全流程

注入器执行完成后，测试邮件已经存在于指定 Gmail 收件箱中。只测试邮件注入工具时，到这里即可结束。

需要继续验证 SalesMate 全流程时，在完整 SalesMate 项目的仓库根目录启动 Django 和网页：

1. 启动 Django 和网页：

   ```powershell
   python -m uvicorn --app-dir backend config.asgi:application --host 127.0.0.1 --port 8000 --reload
   ```

2. 打开 `http://127.0.0.1:8000/`。
3. 确认网页左侧连接的是刚才注入邮件的同一个 Gmail 账号。
4. 点击“同步 Gmail”。
5. 等待同步和公司分析完成，检查公司列表、邮件原文、事实、画像、分析和跟进分数。

测试邮件仍通过正式 Gmail 只读同步进入系统，因此会执行正常的去重和 Gmail History 增量读取逻辑。

## 7. 重新授权或更换账号

删除本地 token 后再次执行插入命令：

```powershell
Remove-Item -LiteralPath .\gmail_inject_token.json
python .\gmail_test_injector.py `
  --messages-file .\another_account_messages.local.json
```

如果修改过授权 scope，也需要删除旧 token 后重新授权。

## 8. 删除 Gmail 中的测试邮件

复制成功输出中的 `gmail_search`，在 Gmail 搜索框中搜索本次测试邮件，确认结果后批量删除。主题格式为：

```text
[SalesMate测试:<run_id>] 邮件主题
```

删除 Gmail 中的邮件不会自动删除已经同步到 SalesMate 数据库的记录。

## 9. 常见错误

| 错误 | 原因 | 处理方式 |
|---|---|---|
| `redirect_uri_mismatch` | 使用了 Web application Client | 创建 Desktop app Client，并替换 `test_tools/gmail_inject_credentials.json` |
| `403 access_denied` | 当前账号不在 OAuth 测试用户中 | 在 OAuth consent screen 中加入 Test user |
| 授权账号不一致 | token 所属账号和 JSON 的 `mailbox_address` 不同 | 删除 `gmail_inject_token.json` 后重新授权，或修正 JSON |
| JSON 校验失败 | 缺少字段、字段类型错误或邮件数组为空 | 对照 `gmail_test_messages.template.json` 修改自定义文件 |
| token scope 不足 | 旧 token 没有 `gmail.insert` 权限 | 删除 token 后重新授权 |
| 找不到凭据文件 | 文件名或放置目录不正确 | 确认文件位于 `test_tools/gmail_inject_credentials.json` |
