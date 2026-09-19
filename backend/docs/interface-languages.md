# 中英文界面

三个页面和登录页顶部均提供“跟随系统 / 简体中文 / English”。首次访问使用浏览器语言偏好（通常跟随操作系统，但浏览器可以单独设置），按偏好顺序选择支持的中英文；没有匹配语言时使用项目原有中文默认值。

手动选择保存在当前浏览器的 `django_language` cookie，跨页面及刷新有效，有效期一年。选择“跟随系统”会删除手动覆盖。此偏好不写入用户资料，因此不同设备可以使用不同界面语言。

切换语言会重新加载页面。页面检测到输入编辑后会提示确认；取消可保留当前输入。聊天快捷提示也纳入检测。检测采用保守策略：输入过的控件即使随后保存，仍可能提示；关闭或重新加载表单后重新建立检测。密码、邮件草稿和其他输入不会为语言切换复制到浏览器存储。

## 翻译边界

- 覆盖登录/注册、客户工作台、邮件连接与复核、聊天界面、销售业务表单、世界消息导航、日期格式及常用 HTTP 错误。
- 邮件原文、客户名称、会话历史、已保存的 AI 分析、引用证据和世界消息演示正文保持原样。切换语言不会更新数据库中的这些内容。
- 行业等选项只翻译显示标签，提交值仍是原来的协议枚举；字段名、状态代码、金额币种、权限和版本控制保持原值。
- 新选择的聊天快捷提示使用当前界面语言，只有用户显式发送才提交。系统提示词、模型、抽取/评分规则没有修改；本功能不保证模型回答语言。
- 未收录的内部诊断及外部服务原始错误保留原文，不通过模型翻译或改写失败含义。
- 时间显示使用选定语言；客户列表继续采用后端配置时区，世界消息继续采用上海时区，业务表单/聊天/复核仍使用原来的设备时区。

## 维护方式

前端使用现有 ES modules，不新增运行时框架或构建依赖：

- `frontend/assets/i18n.js`：语言解析、`t` 文本模板、`h` 静态 HTML 模板、标记节点初始化及切换保护。
- `frontend/assets/translations.js`：802 条源文案键。普通文案使用 `t('源文案')`，带参数文案使用 `t` 标记模板，译文通过 `{0}`、`{1}` 调整语序。
- `h` 仅处理模板的静态片段，然后拼接原有动态值，不扫描拼接后的 DOM。调用方仍须对动态值使用 `escapeHtml`。片段译文不得引入 HTML 标签或属性引号；需要复杂语序时改用完整 `t` 文本模板。
- HTML 的 `data-i18n` 和 `data-i18n-aria-label/placeholder/title` 只标记界面源文案。给 `<option>` 加翻译时必须保留显式 `value`，尤其是原先使用选项文字作为值的中文行业选项。
- 日期使用导出的 `locale`；JSON 和附件请求使用导出的 `language` 设置 `Accept-Language`。

后端使用 Django 5.2 自带的 `LocaleMiddleware`，位置在 Session 之后、Common 之前。`LANGUAGES` 限定 `zh-hans/en`，`LOCALE_PATHS` 指向 `backend/locale`。`common.exceptions.translate_detail` 仅在 HTTP 错误边界翻译已登记的错误值，保留字段键、错误代码、状态码和请求 ID；销售目录只翻译 `label`。

`locale/en/LC_MESSAGES/django.po` 收录 219 条常用界面/错误文案。错误边界中存在动态 gettext 键，因此目录需要结合源代码引用手动维护，不能直接用一次 `makemessages` 全量替换。更新源文案时同步更新 `.po`，再使用 GNU gettext 编译（在 `backend` 目录、Django 虚拟环境内运行）：

```console
python manage.py compilemessages --locale en
```

Windows 需要把 GNU gettext 工具目录加入 PATH。开发机可使用 Git for Windows 自带的 `usr/bin/msgfmt.exe`。`.po` 与编译得到的 `.mo` 应一起提交；服务器可直接读取已编译目录，无需启动时编译。CI 使用 `msgfmt --check` 并比较 `.mo`，防止只更新源目录。

## 发布前验证记录

- 后端新增 5 项语言测试，验证 cookie/请求头优先级、登录和注册错误、目录契约、中文数据往返及账户隔离。完整启用范围后端测试共 **199 项通过**，沿用现有配置排除已禁用的 QQ 专用集成测试。
- 新增 `tools/browser_i18n.cjs`：英文登录、三个入口、跨页/刷新、自动与手动选择、草稿取消保护、静态/动态正文隔离、行业值、API 语言头及手机布局。
- 原有 `browser_workspace.cjs`、`browser_chat.cjs`、`browser_processing.cjs`、`browser_world_news.cjs` 全部通过；旧中文测试显式使用 `zh-CN`。
- GNU gettext 目录检查、前端语法/翻译占位符核对、`python tools/check_docs.py` 与 `python tools/check_doc_changes.py --base HEAD --fail-on-review` 通过；已人工核对新增变量目录、语言边界和日期时区说明。
- 本地真实 Django 已重启：英文页面读取原有 7 个客户，目录 API 返回 200、`Content-Language: en` 和英文标签。浏览器模拟测试不代表真实邮箱或模型服务已验证。
- 以上记录为发布前本地验证。代码、对应说明及翻译源文件/编译文件纳入同一版本；远程 CI 与生产发布结果以该提交的 GitHub Actions “Verify and deploy” 运行记录为准。
