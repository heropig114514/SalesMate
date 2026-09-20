"""职责：增加账号清理协调状态，不更改登录身份。
实现：创建版本、幂等键及待清理附件清单。
关联：accounts.reset_models；依赖引导资料迁移。
目录：
- Migration：声明协调状态表。
变量索引：
- Migration.dependencies：引导模型迁移。
- Migration.operations：AccountReset 表结构。
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


# 功能：创建账号重置协调状态。
# 逻辑：仅增加表，不初始化用户业务数据。
# 约束：反向迁移删除协调记录，不恢复已清理的业务数据。
class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_salessetup_companyprofile_size_band_setupdocument")]
    operations = [migrations.CreateModel(name="AccountReset", fields=[
        ("owner", models.OneToOneField(primary_key=True, serialize=False, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ("generation", models.PositiveBigIntegerField(default=0)),
        ("key", models.UUIDField(null=True)),
        ("keys", models.JSONField(default=list)),
        ("pending_files", models.JSONField(default=list)),
        ("cleaning", models.BooleanField(default=False)),
    ])]
