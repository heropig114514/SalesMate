"""职责：独立保存 QQ 授权和 IMAP 扫描状态。
实现：每邮箱一份加密授权码和文件夹检查点，不修改 Gmail 凭证或 History 检查点。
关联：models 注册实体，qq_connection 验证授权，qq_sync 事务登记消息及最大 UID。
目录：
- QQCredential：QQ 客户端授权码的认证密文。
- QQSyncCheckpoint：每文件夹 UIDVALIDITY 与最后已登记 UID。
变量索引：
- QQCredential.mailbox：员工邮箱的一对一归属。
- QQCredential.encrypted_code：使用 SALESMATE_VAULT_KEY 加密的授权码。
- QQCredential.authorized_at：首次验证时间。
- QQCredential.updated_at：重新验证时间。
- QQSyncCheckpoint.mailbox：邮箱的一对一检查点。
- QQSyncCheckpoint.folders：wire 文件夹名到 uidvalidity/last_uid 的字典。
"""
from django.db import models


# 功能：保存已通过 IMAP 登录验证的 QQ 授权码。
# 逻辑：密文独立存储，邮箱列表只返回授权布尔值。
# 约束：不暴露给 Agent HTTP 领取接口或浏览器，解密仅发生在 Worker。
class QQCredential(models.Model):
    mailbox = models.OneToOneField("crm.Mailbox", on_delete=models.CASCADE, related_name="qq_credential")
    encrypted_code = models.TextField()
    authorized_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


# 功能：持久保存 QQ 文件夹身份代次与最大已登记 UID。
# 逻辑：登记消息后更新 last_uid 统计；有界扫描不使用它跳过旧邮件，失败原文等待显式重试。
# 约束：UIDVALIDITY 改变时停止同步，不静默重置或复用旧 UID。
class QQSyncCheckpoint(models.Model):
    mailbox = models.OneToOneField("crm.Mailbox", primary_key=True, on_delete=models.CASCADE, related_name="qq_checkpoint")
    folders = models.JSONField(default=dict)
