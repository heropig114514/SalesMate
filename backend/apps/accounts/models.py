"""职责：声明项目用户身份及账号隔离的本公司资料模型。
实现：继承 AbstractUser；CompanyProfile、SalesSetup 和 SetupDocument 按账号存储公司资料、引导信息及私有文件，不参与客户评分。
关联：由 AUTH_USER_MODEL、Admin、身份和公司资料接口及 accounts 迁移共同引用；导入 reset_models 注册保留身份的重置协调状态。

目录：
- User：声明项目自定义用户类型。
- CompanyProfile：账号隔离的本公司资料及编辑版本。
- SalesSetup：个人、产品和方案资料及引导进度。
- SetupDocument：需要登录才能读取的引导附件。

变量索引：
- CompanyProfile.owner：唯一所属账号，客户端不可修改。
- CompanyProfile.company_name：本公司名称。
- CompanyProfile.industry：公司行业。
- CompanyProfile.size_band：公司规模，可留空。
- CompanyProfile.website：公开网站地址。
- CompanyProfile.email：业务联系邮箱，不含邮箱授权。
- CompanyProfile.phone：业务联系电话。
- CompanyProfile.address：业务地址。
- CompanyProfile.description：公司简介。
- CompanyProfile.revision：乐观锁版本。
- CompanyProfile.updated_at：最近保存时间。
- SalesSetup.owner：资料所属账号。
- SalesSetup.personal：个人身份与负责区域行业。
- SalesSetup.products：参考产品目录，不代替成交报价。
- SalesSetup.solutions：方案及私有附件引用。
- SalesSetup.completed：是否已完成或跳过引导。
- SalesSetup.revision：并发编辑版本。
- SetupDocument.id：不透明文件标识。
- SetupDocument.owner：唯一有权读取文件的账号。
- SetupDocument.name：显示文件名。
- SetupDocument.content_type：经校验的 PDF 或纯文本类型。
- SetupDocument.content：最大 5 MiB 文件内容，随数据库备份。
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid


# 功能：声明项目自定义用户类型。
# 逻辑：完整继承 AbstractUser，使后续身份扩展不需要更换 AUTH_USER_MODEL。
# 约束：当前没有团队、邮箱绑定和 Agent 服务身份能力；数据库结构以迁移为准。
class User(AbstractUser):
    """Own the user model from the first migration so identity can evolve safely."""


# 功能：账号隔离的本公司资料及编辑版本。
# 逻辑：一名用户一份资料，包含可选公司规模；与 CRM 客户和销售目标画像分别存储。
# 约束：不是跨账号组织或团队权限模型；仅经认证接口显式保存，未知资料保持空白。
class CompanyProfile(models.Model):
    owner = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)
    company_name = models.CharField(max_length=240)
    industry = models.CharField(max_length=100, blank=True)
    size_band = models.CharField(max_length=30, blank=True)
    website = models.URLField(max_length=500, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=500, blank=True)
    description = models.TextField(max_length=5000, blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


# 功能：保存销售代表的引导资料。
# 逻辑：一账号一份，JSON 结构由 onboarding 序列化器严格验证。
# 约束：读取不自动建档；仅显式保存递增版本，不触发评分或外部调用。
class SalesSetup(models.Model):
    owner = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)
    personal = models.JSONField(default=dict)
    products = models.JSONField(default=list)
    solutions = models.JSONField(default=list)
    completed = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)


# 功能：保存本账号的产品规格书与销售方案。
# 逻辑：内容与元数据在同一数据库事务中保存，避免文件落盘成功但记录失败。
# 约束：API 限制 5 MiB、PDF/UTF-8 文本；不公开静态 URL，不执行文件内容。
class SetupDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=240)
    content_type = models.CharField(max_length=80)
    content = models.BinaryField()


from .reset_models import AccountReset  # noqa: E402,F401
