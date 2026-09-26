"""Responsibility: Expose the Django application to WSGI servers.
Implementation: Selects local settings only when the environment variable is unset and initializes application on import; startup errors propagate to the caller.
Relationships: Uses routes, middleware, and application configuration from config.settings.

Directory:
- None

Variable index:
- application: Django application object invoked by WSGI servers.
"""


import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
application = get_wsgi_application()
