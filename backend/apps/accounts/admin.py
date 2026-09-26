"""Responsibility: Register the Django Admin interface for the custom user model.
Implementation: Bind ``User`` to the framework's ``UserAdmin`` on import; registration is an import side effect.
Relationships: Reuses ``accounts.models.User`` and ``django.contrib.auth.admin.UserAdmin``.

Directory:
- None

Variable index:
- None
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.register(User, UserAdmin)
