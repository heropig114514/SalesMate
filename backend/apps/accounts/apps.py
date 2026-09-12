"""职责：声明账号模块的 Django 应用配置。
实现：以稳定应用路径注册模块，并指定默认主键类型与显示名称。
关联：由 config.settings.base.INSTALLED_APPS 引用。

目录：
- AccountsConfig：提供账号应用的注册元数据。

变量索引：
- AccountsConfig.default_auto_field：该应用模型的默认主键类型。
- AccountsConfig.name：Django 应用的完整 Python 导入路径。
- AccountsConfig.verbose_name：Admin 中的中文应用名称。
"""

from django.apps import AppConfig


# 功能：提供账号应用的注册元数据。
# 逻辑：用类属性指定应用路径、默认主键类型和中文显示名称。
# 约束：当前不定义 ready 钩子或额外启动动作。
class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "账号"
