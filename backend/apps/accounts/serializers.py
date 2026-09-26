"""Responsibility: Define the read-only identity-field set for the current-user endpoint.
Implementation: ``ModelSerializer`` exposes only four explicitly listed fields; password, mailbox, and permission flags never enter this response.
Relationships: Uses ``accounts.models.User`` for ``CurrentUserView`` output and schema generation.

Directory:
- CurrentUserSerializer: Render identity fields permitted for the current user.
- CurrentUserSerializer.Meta: Configure model mapping and the read-only field allowlist.

Variable index:
- CurrentUserSerializer.Meta.model: ``User`` model that provides serialization fields.
- CurrentUserSerializer.Meta.fields: Four identity fields allowed in responses.
- CurrentUserSerializer.Meta.read_only_fields: Same as ``fields``; prohibits writes through this serializer.
"""

from rest_framework import serializers

from .models import User


# Function: Render identity fields permitted for the current user.
# Logic: Restrict serialized output through Meta's explicit field allowlist; all four fields are read-only.
# Constraints: Does not authenticate identity or return mailbox, password, or permission flags.
class CurrentUserSerializer(serializers.ModelSerializer):
    # Function: Configure model mapping and the read-only field allowlist.
    # Logic: ``model`` points to ``User`` and ``fields`` and ``read_only_fields`` use the same field set.
    # Constraints: Field changes affect the endpoint contract and require synchronized caller and schema review.
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name"]
        read_only_fields = fields
