"""职责：保存账户重置的最小协调状态，不保存业务内容。
实现：版本隔离旧页面；文件清单支持数据库提交后显式恢复附件清理。
关联：reset、reset_middleware 和后台工作锁；由 accounts.models 导入注册。
目录：
- AccountReset：账号数据版本及附件清理进度。
变量索引：
- AccountReset.owner：保留的登录身份。
- AccountReset.generation：每次数据库清理递增的数据版本。
- AccountReset.key：最近一次成功提交的幂等键。
- AccountReset.keys：已提交操作键清单，阻止较早请求重放再次删除新数据。
- AccountReset.pending_files：尚需处理的私有存储键，不包含文件正文。
- AccountReset.cleaning：数据库已清空但文件或会话仍需清理。
"""
from django.conf import settings
from django.db import models


# 功能：协调账号数据清理及旧请求隔离。
# 逻辑：一账号一条元数据，文件成功清理后清空清单；不改变 User 或密码。
# 约束：状态不作为身份授权；仅在账号独占锁下修改。
class AccountReset(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    generation = models.PositiveBigIntegerField(default=0)
    key = models.UUIDField(null=True)
    keys = models.JSONField(default=list)
    pending_files = models.JSONField(default=list)
    cleaning = models.BooleanField(default=False)
