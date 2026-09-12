"""职责：注册邮件理解业务应用。
实现：使用独立 crm 标签维护业务迁移。
关联：由 INSTALLED_APPS 加载。
目录：
- CRMConfig：注册邮件、公司、分析和任务模型。
变量索引：
- CRMConfig.default_auto_field：默认主键类型。
- CRMConfig.name：应用导入路径。
"""
from django.apps import AppConfig


# 功能：注册邮件、公司、分析和任务模型。
# 逻辑：由 Django 根据 name 发现模型。
# 约束：不启动后台线程或执行数据库操作。
class CRMConfig(AppConfig):
    name = "apps.crm"
    default_auto_field = "django.db.models.BigAutoField"
