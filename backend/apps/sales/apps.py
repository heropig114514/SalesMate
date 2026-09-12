"""职责：注册销售业务应用。
实现：使用独立 sales 标签隔离新增业务与邮件分析协议。
关联：由 config.settings.base 注册，模型和迁移属于本应用。
目录：
- SalesConfig：配置应用名称与主键类型。
变量索引：
- SalesConfig.default_auto_field：无显式主键时使用 BigAutoField。
- SalesConfig.name：销售应用的导入路径。
"""

from django.apps import AppConfig


# 功能：配置销售应用元数据。
# 逻辑：由 Django 应用注册机制读取类属性。
# 约束：不在应用加载时启动任务或访问数据库。
class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
