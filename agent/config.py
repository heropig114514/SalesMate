"""Responsibility: Manage agent paths centrally and read shared environment settings from the project root.
Implementation: Resolve paths relative to this module and explicitly load the shared .env without overriding existing environment values.
Relationships: Called by agent entry points before constructing provider or backend clients.

Directory:
- load_environment: Called by program entry points; importing other modules does not automatically read local secrets.

Variable index:
- AGENT_DIR: Resolved agent package directory.
- PROJECT_DIR: Project root used to locate shared environment configuration.
"""

from pathlib import Path

from dotenv import load_dotenv

AGENT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = AGENT_DIR.parent


def load_environment() -> None:
    """Called by program entry points; importing other modules does not automatically read local secrets."""
    load_dotenv(PROJECT_DIR / ".env")
