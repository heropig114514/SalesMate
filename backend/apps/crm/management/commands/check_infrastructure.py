"""Responsibility: Provide deployment-time checks for Redis, pgvector, and Celery consumer availability.
Implementation: Check real connections and optionally send a random non-business identifier to two queues and verify its return.
Relationships: Blue-green deployment checks infrastructure before switchover and the full message chain after consumers start.
Directory:
- Command: Infrastructure-check command.
- Command.add_arguments: Define the consumer-probe option.
- Command.handle: Execute redacted checks and report failures explicitly.
Variable index:
- Command.help: Command purpose.
"""
import secrets
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from redis import Redis


# Function: Verify that actual infrastructure is available.
# Logic: Use configured addresses without reading or printing plaintext credentials.
# Constraints: Does not treat a network probe as acceptance testing for real email delivery.
class Command(BaseCommand):
    help = "Check Redis and pgvector, and optionally round-trip messages through Celery's two queues."

    # Function: Declare check options.
    # Inputs: `parser` is the command parser.
    # Outputs: Registers the workers switch.
    # Logic: Check basic connectivity by default and accept workers only after deployment consumers start.
    # Constraints: Does not start consumers or automatically repair failures.
    def add_arguments(self, parser):
        parser.add_argument("--workers", action="store_true")

    # Function: Check the database extension, broker, and result store.
    # Inputs: `args` are positional arguments; `options` includes workers and implicitly reads Django configuration.
    # Outputs: Writes success to stdout or raises a redacted CommandError on failure.
    # Logic: Perform a vector operation and Redis PING; optionally require each queue's task result to match within 20 seconds.
    # Constraints: Does not modify business tables; removes each random probe result after reading it and does not retry exceptions automatically.
    def handle(self, *args, **options):
        try:
            if settings.TASK_EXECUTION_MODE != "celery":
                raise ValueError("This check requires explicit celery mode")
            with connection.cursor() as cursor:
                cursor.execute("SELECT '[1,0]'::vector <=> '[1,0]'::vector")
                if cursor.fetchone()[0] != 0:
                    raise ValueError("Vector distance mismatch")
            for url in (settings.CELERY_BROKER_URL, settings.CELERY_RESULT_BACKEND):
                with Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5) as client:
                    if not client.ping():
                        raise ValueError("Redis ping rejected")
            if options["workers"]:
                from common.tasks import probe
                for queue in ("crm", "sales"):
                    token = secrets.token_hex(16)
                    result = probe.apply_async(args=[token], queue=queue, retry=False, expires=30)
                    if result.get(timeout=20) != token:
                        raise ValueError("Worker probe mismatch")
                    result.forget()
            self.stdout.write("Redis, pgvector and requested worker probes: OK")
        except Exception as error:
            raise CommandError(f"Infrastructure check failed ({type(error).__name__}); inspect Redis, vector extension and Celery services. Credentials omitted.") from None
