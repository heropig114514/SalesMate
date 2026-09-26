#!/usr/bin/env python
"""Responsibility: Provide the Django management command entry point.
Implementation: Add the repository root to load the colocated agent, select default local settings, and forward command-line arguments to Django.
Relationships: Uses config.settings.local; crm_worker requires the colocated agent package, while migrations, checks, and tests load through this entry point.

Directory:
- main: Execute the Django management command requested by this process.

Variable index:
- None
"""


import os
import sys
from pathlib import Path


# Function: Execute the Django management command requested by this process.
# Inputs: No external parameters; read the process environment and sys.argv.
# Outputs: None on normal completion; commands may exit or raise according to Django behavior.
# Logic: Resolve repository_root from this file to locate the agent, add it to the import path when needed, preserve explicit settings, and forward arguments.
# Constraints: Modifies process sys.path; sets the settings environment variable only when absent, without automatic migration, retry, or database switching.
def main():
    repository_root = str(Path(__file__).resolve().parent.parent)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
