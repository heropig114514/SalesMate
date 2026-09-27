"""Responsibility: Separate explicit synthetic-data experiments from account authorization.
Implementation: Laboratory settings affect synthetic fixtures only; private queries always constrain ownership.
Relationships: Business sharing uses sales.permissions; Session, Agent, and Tool authenticators prove identity independently.
Directory:
- owner_only: Read the optional personal-only business policy.
- enabled: Read the synthetic experiment switch.
- owner_scope: Build an unconditional private ownership predicate.
Variable index:
- None
"""
from django.conf import settings
from django.db.models import Q


# Function: Read the optional personal-only business policy.
# Inputs: Django settings; no explicit parameters.
# Outputs: Boolean.
# Logic: Enabling the policy also disables team sharing and synthetic experiments.
# Constraints: Does not authenticate users or query business records.
def owner_only():
    return getattr(settings, "WORKSPACE_OWNER_ONLY", False)


# Function: Read the synthetic experiment switch.
# Inputs: Django settings; no explicit parameters.
# Outputs: Boolean, disabled under personal-only business policy.
# Logic: Permit explicit fixture experiments without weakening authentication or ownership.
# Constraints: Must not be used to bypass real account authorization.
def enabled():
    return not owner_only() and getattr(settings, "LAB_OPEN_ACCESS", False)


# Function: Build a private ownership predicate.
# Inputs: Authenticated `owner` and relation `path`, defaulting to owner.
# Outputs: Django Q predicate.
# Logic: Always constrain the selected relation to the caller, regardless of experiment settings.
# Constraints: Team-shared business records use sales.permissions rather than this private scope.
def owner_scope(owner, path="owner"):
    return Q(**{path: owner})
