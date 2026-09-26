"""Responsibility: Register the sales application.
Implementation: Use an independent sales label to separate added business functionality from the email-analysis protocol.
Relationships: Registered in config.settings.base; models and migrations belong to this application.
Directory:
- SalesConfig: Configure application name and primary-key type.
Variable index:
- SalesConfig.default_auto_field: Use BigAutoField when no primary key is explicit.
- SalesConfig.name: Sales application import path.
"""

from django.apps import AppConfig


# Function: Configure sales application metadata.
# Logic: Django's application registry reads class attributes.
# Constraints: Do not start jobs or access the database during application loading.
class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sales"
