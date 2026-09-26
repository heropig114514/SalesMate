"""Responsibility: Register the email-intelligence business application.
Implementation: Use the independent crm label to maintain business migrations.
Relationships: Loaded by INSTALLED_APPS.
Directory:
- CRMConfig: Registers email, company, analysis, and job models.
Variable index:
- CRMConfig.default_auto_field: Default primary-key type.
- CRMConfig.name: Application import path.
"""
from django.apps import AppConfig


# Function: Register email, company, analysis, and job models.
# Logic: Django discovers models from name.
# Constraints: Does not start background threads or execute database operations.
class CRMConfig(AppConfig):
    name = "apps.crm"
    default_auto_field = "django.db.models.BigAutoField"
