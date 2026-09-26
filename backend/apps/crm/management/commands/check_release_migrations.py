"""Responsibility: Provide a manually runnable conservative database-migration review.
Implementation: Inspect migrations actually pending and allow only new tables, indexes, nullable non-unique added fields, and vector-extension creation.
Relationships: Automated deployment no longer calls this command; operations can run it manually as needed. It does not replace business-level old/new version compatibility review, and PGOPTIONS separately limits DDL lock waits.
Directory:
- compatible: Determine whether one migration operation meets conservative online-migration guidance.
- Command: Inspect migrations pending on the actual database.
- Command.handle: Reject reverse operations or operations outside the allowed set.
Variable index:
- Command.help: Command purpose.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from pgvector.django import VectorExtension


# Function: Identify the conservative operation set used for manual review.
# Inputs: `operation` is a Django migration operation.
# Outputs: A boolean.
# Logic: Accept exact types only so custom subclasses cannot hide arbitrary SQL; added fields must be nullable and have no unique constraint.
# Constraints: Alter, Remove, Rename, RunPython, and RunSQL are rejected by default. This affects only this explicitly run diagnostic's exit code and never blocks automated deployment.
def compatible(operation):
    if type(operation) in {migrations.CreateModel, migrations.AddIndex, VectorExtension}:
        return True
    return type(operation) is migrations.AddField and operation.field.null and not operation.field.unique and not operation.field.primary_key


# Function: Let operations explicitly check whether pending migrations meet conservative guidance.
# Logic: Read the migration graph and database records only when manually invoked; neither execute migrations nor participate in automated release.
# Constraints: Depends on the real target-database state.
class Command(BaseCommand):
    help = "Manually check whether migrations meet conservative online-release guidance; it does not participate in automated deployment."

    # Function: Check pending migrations and list incompatible items.
    # Inputs: `args` and `options` are command arguments with no custom options; reads the default database.
    # Outputs: A success message or CommandError; does not write to the database.
    # Logic: Calculate the plan to every leaf node and call compatible for each operation.
    # Constraints: Does not bypass compatibility review by modifying already applied migrations.
    def handle(self, *args, **options):
        executor = MigrationExecutor(connection)
        blocked = []
        for migration, backwards in executor.migration_plan(executor.loader.graph.leaf_nodes()):
            for operation in migration.operations:
                if backwards or not compatible(operation):
                    blocked.append(f"{migration.app_label}.{migration.name}:{type(operation).__name__}")
        if blocked:
            raise CommandError("Online deployment blocked; explicit migration review required: " + ", ".join(blocked))
        self.stdout.write("Pending migrations satisfy the conservative online release gate.")
