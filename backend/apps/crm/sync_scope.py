"""Responsibility: Validate explicit Gmail/QQ scope for each synchronization and freeze its time window.
Implementation: Require at least one of days and message count, both positive integers; Gmail defaults to at most 50 messages and expands only after explicit approval.
Relationships: qq_views and views validate HTTP input, processing freezes batch scope, and durable_sync and qq_sync apply filtering.
Directory:
- MailboxSyncOptionsSerializer: Declare synchronization limits with no prefilled values.
- MailboxSyncOptionsSerializer.validate: Reject an empty scope.
- SyncRequestSerializer: Declare required mailbox scope for generic synchronization requests.
- snapshot: Convert a user selection to a fixed UTC time window.
Variable index:
- MailboxSyncOptionsSerializer.recent_days: Optional number of recent days.
- MailboxSyncOptionsSerializer.max_messages: Optional number of messages to process in this batch.
- MailboxSyncOptionsSerializer.allow_large_sync: Optional approval for this explicit over-limit message count.
- SyncRequestSerializer.sync_options: Scope object shared by Gmail and QQ.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from agent.tools.gmail_scope import gmail_message_limit

from .serializers import StrictSerializer


# Function: Limit the processing scope of each mailbox synchronization.
# Logic: Permit days only, message count only, or both; over-limit approval cannot itself replace scope, and the service layer applies provider limits.
# Constraints: Fields must be positive integers; StrictSerializer rejects unknown fields.
class MailboxSyncOptionsSerializer(StrictSerializer):
    recent_days = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    max_messages = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    allow_large_sync = serializers.BooleanField(required=False)

    # Function: Ensure the user explicitly selects at least one limit.
    # Inputs: `attrs` is the scope object after field validation.
    # Outputs: Retains scope and explicitly supplied approval fields, using None for omitted scope items.
    # Logic: Return 400 when no positive scope integer is present; an approval flag is not valid scope.
    # Constraints: Does not interpret an empty selection as full synchronization.
    def validate(self, attrs):
        if not (attrs.get("recent_days") or attrs.get("max_messages")):
            raise serializers.ValidationError("请填写最近 N 天或最多 N 封，至少一项。")
        return {"recent_days": attrs.get("recent_days"), "max_messages": attrs.get("max_messages"),
                **({"allow_large_sync": attrs["allow_large_sync"]} if "allow_large_sync" in attrs else {})}


# Function: Declare required scope for shared mailbox synchronization requests.
# Logic: Gmail and QQ must both provide sync_options.
# Constraints: An empty request cannot start full synchronization.
class SyncRequestSerializer(StrictSerializer):
    sync_options = MailboxSyncOptionsSerializer()


# Function: Freeze scope for one ordinary mailbox synchronization.
# Inputs: `options` is the user selection; `gmail` selects Gmail's second-level window and 50-message policy; server UTC current time is read implicitly.
# Outputs: JSON object containing recent_days, max_messages, since, until, and explicit approval.
# Logic: Validate and use queue time as the upper bound; Gmail truncates to whole seconds and freezes the effective message count; compute lower bound as days times 24 hours.
# Constraints: Explicitly reject time overflow; retries use the original snapshot without calling this function to advance time.
def snapshot(options, *, gmail=False):
    serializer = MailboxSyncOptionsSerializer(data=options or {})
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    until = timezone.now()
    if gmail:
        try:
            values["max_messages"] = gmail_message_limit(values)
        except ValueError as error:
            raise serializers.ValidationError({"max_messages": str(error)}) from None
        until = until.replace(microsecond=0)
    try:
        since = until - timedelta(days=values["recent_days"]) if values["recent_days"] else None
    except (OverflowError, ValueError):
        raise serializers.ValidationError({"recent_days": "天数超出支持的日期范围。"}) from None
    return {**values, "since": since.isoformat() if since else None, "until": until.isoformat()}
