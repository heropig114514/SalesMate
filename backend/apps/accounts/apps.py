"""Responsibility: Declare Django application configuration for the accounts module.
Implementation: Register the module under a stable application path and specify its default primary-key type and display name.
Relationships: Referenced by ``config.settings.base.INSTALLED_APPS``.

Directory:
- AccountsConfig: Registration metadata for the accounts application.

Variable index:
- AccountsConfig.default_auto_field: Default primary-key type for application models.
- AccountsConfig.name: Full Python import path for the Django application.
- AccountsConfig.verbose_name: Chinese application display name in Admin.
"""

from django.apps import AppConfig


# Function: Provide registration metadata for the accounts application.
# Logic: Class attributes specify the application path, default primary-key type, and Chinese display name.
# Constraints: No ``ready`` hook or additional startup action is currently defined.
class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "账号"
