"""Responsibility: Expose the Django application to ASGI servers.
Implementation: Selects local settings only when the environment variable is unset and initializes application on import; startup errors propagate.
Relationships: Loaded by servers such as Uvicorn; routes and middleware come from config.settings.

Directory:
- None

Variable index:
- application: Django application object invoked by ASGI servers.
"""


import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
application = get_asgi_application()
