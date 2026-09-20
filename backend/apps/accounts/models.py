"""职责：声明项目用户身份及账号隔离的本公司资料模型。
实现：继承 AbstractUser；独立 CompanyProfile 保存当前账号工作空间的本公司资料，不参与客户评分。
关联：由 AUTH_USER_MODEL、Admin、身份和公司资料接口及 accounts 迁移共同引用。

目录：
- User：声明项目自定义用户类型。
- CompanyProfile：账号隔离的本公司资料及编辑版本。

变量索引：
- CompanyProfile.owner：唯一所属账号，客户端不可修改。
- CompanyProfile.company_name：本公司名称。
- CompanyProfile.industry：公司行业。
- CompanyProfile.website：公开网站地址。
- CompanyProfile.email：业务联系邮箱，不含邮箱授权。
- CompanyProfile.phone：业务联系电话。
- CompanyProfile.address：业务地址。
- CompanyProfile.description：公司简介。
- CompanyProfile.revision：乐观锁版本。
- CompanyProfile.updated_at：最近保存时间。
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


# 功能：声明项目自定义用户类型。
# 逻辑：完整继承 AbstractUser，使后续身份扩展不需要更换 AUTH_USER_MODEL。
# 约束：当前没有团队、邮箱绑定和 Agent 服务身份能力；数据库结构以迁移为准。
class User(AbstractUser):
    """Own the user model from the first migration so identity can evolve safely."""


# 功能：账号隔离的本公司资料及编辑版本。
# 逻辑：一名用户一份资料；与 CRM 客户和销售目标画像分别存储。
# 约束：不是跨账号组织或团队权限模型；仅经认证接口显式保存，未知资料保持空白。
class CompanyProfile(models.Model):
    owner = models.OneToOneField(User, primary_key=True, on_delete=models.CASCADE)
    company_name = models.CharField(max_length=240)
    industry = models.CharField(max_length=100, blank=True)
    website = models.URLField(max_length=500, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=500, blank=True)
    description = models.TextField(max_length=5000, blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
