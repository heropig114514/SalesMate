"""Responsibility: Validate sales APIs and relation references and declare explicit OpenAPI fields.
Implementation: Public news validates one set of sales leads, source amounts, and evidence without generating/linking CRM. Generic resources register opportunity signals/scores without executing algorithms. Events/news validate date precision, sources, and authorized links, filtering private opportunity IDs at all read entry points. Use explicit field allowlists, read-only state protection, and authorized relation queries; opportunities accept canonical product names and amounts use Decimal.
Relationships: views selects concrete serializers; services additionally validates transactions, cross-entity relationships, and state.
Directory:
- ZonedDateTimeField: Event/news time and field validation.
- ZonedDateTimeField.to_internal_value: Event/news time and field validation.
- WorldEventSerializer: Event/news time and field validation.
- WorldEventSerializer.validate: Event/news time and field validation.
- WorldEventSerializer.to_representation: Filter inaccessible opportunity IDs and return explicit date ranges.
- WorldEventSerializer.Meta: Event/news time and field validation.
- WorldNewsSerializer: Public-news, single-lead-set, and source-amount contract.
- WorldNewsSerializer.validate: Validate amount evidence after merging updates, then validate source deduplication.
- WorldNewsSerializer.Meta: Declare base news fields and thirteen optional public lead fields.
- ConnectionSerializer: Safe connection fields.
- ConnectionSerializer.Meta: Field configuration.
- StrictModelSerializer: Reject unknown/read-only inputs and restrict relations per user.
- StrictModelSerializer.to_internal_value: Reject undeclared writes.
- StrictModelSerializer.get_fields: Restrict objects referenceable by relation fields.
- DocumentSerializer: Output amounts and line items for quotes/orders.
- DocumentSerializer.get_total: Calculate net amounts of currently active line items.
- DocumentSerializer.get_lines: Return line-item snapshots.
- CompanySettingsSerializer: Authorized field contract for company lifecycle and manual primary-contact settings.
- CompanySettingsSerializer.Meta: Declare entity fields and states not directly writable.
- CompanyAliasSerializer: Authorized field contract for manually confirmed domain/contact grouping mappings.
- CompanyAliasSerializer.Meta: Declare entity fields and states not directly writable.
- ContactProfileSerializer: Authorized field contract for supplemental manual contact details.
- ContactProfileSerializer.Meta: Declare entity fields and states not directly writable.
- TeamSerializer: Authorized field contract for business teams with explicit managers.
- TeamSerializer.Meta: Declare entity fields and states not directly writable.
- MembershipSerializer: Authorized field contract for team membership and roles.
- MembershipSerializer.Meta: Declare entity fields and states not directly writable.
- CompanyGrantSerializer: Authorized field contract for company business-record sharing grants.
- CompanyGrantSerializer.Meta: Declare entity fields and states not directly writable.
- ProductSerializer: Authorized field contract for product catalogs and manual inventory.
- ProductSerializer.Meta: Declare entity fields and states not directly writable.
- TicketSerializer: Authorized field contract for customer service tickets.
- TicketSerializer.Meta: Declare entity fields and states not directly writable.
- OpportunitySerializer: Authorized field contract for sales opportunities and pipelines.
- OpportunitySerializer.Meta: Declare entity fields and states not directly writable.
- QuoteSerializer: Authorized field contract for reviewed quotes with genuine external-send evidence.
- QuoteSerializer.Meta: Declare entity fields and states not directly writable.
- QuoteLineSerializer: Authorized field contract for quote line snapshots.
- QuoteLineSerializer.Meta: Declare entity fields and states not directly writable.
- SalesOrderSerializer: Authorized field contract for customer orders and confirmation states.
- SalesOrderSerializer.Meta: Declare entity fields and states not directly writable.
- OrderLineSerializer: Authorized field contract for order line snapshots.
- OrderLineSerializer.Meta: Declare entity fields and states not directly writable.
- FollowUpSerializer: Authorized field contract for customer follow-ups and due reminders.
- FollowUpSerializer.Meta: Declare entity fields and states not directly writable.
- ConversationSerializer: Authorized field contract for employees' own general/company assistant conversations.
- ConversationSerializer.Meta: Declare entity fields and states not directly writable.
- MessageSerializer: Authorized field contract for immutable conversation messages.
- MessageSerializer.Meta: Declare entity fields and states not directly writable.
- DraftSerializer: Authorized field contract for editable drafts in private conversations.
- DraftSerializer.Meta: Declare entity fields and states not directly writable.
- ToolActionSerializer: Authorized field contract for explicitly confirmed external-tool actions and execution states.
- ToolActionSerializer.Meta: Declare entity fields and states not directly writable.
- AttachmentSerializer: Authorized field contract for employee-private files and company links.
- AttachmentSerializer.Meta: Declare entity fields and states not directly writable.
- NotificationSerializer: Authorized field contract for in-app due reminders.
- NotificationSerializer.Meta: Declare entity fields and states not directly writable.
Variable index:
- WorldEventSerializer.starts_on: Inclusive start date of date-only events, read-only.
- WorldEventSerializer.ends_on: Inclusive end date of date-only events, read-only.
- WorldEventSerializer.source_url: Source-format validation; insights and the database handle conditional deduplication, avoiding uniqueness constraints on manual sources.
- WorldNewsSerializer.source_url: News source-format validation; insights and the database handle cross-account Agent deduplication.
- WorldEventSerializer.Meta.validators: Disable automatic DRF conditional uniqueness validation in favor of the explicit 409 contract.
- WorldNewsSerializer.Meta.validators: Disable automatic DRF conditional uniqueness validation while retaining manual conditions and database constraints.
- WorldEventSerializer.latitude: Explicit latitude type and boundary validation.
- WorldEventSerializer.longitude: Explicit longitude type and boundary validation.
- WorldEventSerializer.country: Explicit country type and boundary validation.
- WorldEventSerializer.starts_at: Explicit starts_at type and boundary validation.
- WorldEventSerializer.ends_at: Explicit ends_at type and boundary validation.
- WorldEventSerializer.registration_deadline: Explicit registration_deadline type and boundary validation.
- WorldEventSerializer.onsite: Explicit onsite type and boundary validation.
- WorldEventSerializer.suggested_actions: Explicit suggested_actions type and boundary validation.
- WorldEventSerializer.opportunity_ids: Explicit opportunity_ids type and boundary validation.
- WorldNewsSerializer.published_at: Explicit published_at type and boundary validation.
- WorldNewsSerializer.country: Explicit country type and boundary validation.
- WorldNewsSerializer.summary: Explicit summary type and boundary validation.
- WorldNewsSerializer.content: Explicit content type and boundary validation.
- WorldNewsSerializer.amount: Nonnegative decimal string with 24 integer and 6 fractional digits; return a fixed-precision string or null.
- WorldNewsSerializer.evidence: Preserve original whitespace; at most 600 characters.
- WorldNewsSerializer.amount_evidence: Preserve original amount whitespace; at most 400 characters.
- WorldEventSerializer.amount: Exact source amount, nullable and independent of CRM.
- WorldEventSerializer.evidence: Public source excerpt, whitespace preserved.
- WorldEventSerializer.amount_evidence: Verbatim monetary excerpt.
- WorldEventSerializer.Meta.model: Declare the corresponding model.
- WorldEventSerializer.Meta.fields: Declare public fields.
- WorldEventSerializer.Meta.read_only_fields: Declare server-maintained fields.
- WorldNewsSerializer.Meta.model: Declare the corresponding model.
- WorldNewsSerializer.Meta.fields: Declare public fields.
- WorldNewsSerializer.Meta.read_only_fields: Declare server-maintained fields.
- OpportunitySerializer.product_names: Explicit canonical product-name array; omission retains the existing value, while null/empty arrays denote unknown.
- ConnectionSerializer.Meta.model: Connection model.
- ConnectionSerializer.Meta.fields: Credential-free field list.
- ConnectionSerializer.Meta.read_only_fields: All fields are read-only.
- DocumentSerializer.total: Single-currency net amount returned as a string, excluding taxes.
- DocumentSerializer.lines: Currently active line-item list.
- SERIALIZERS: Allowlist mapping business route names to concrete serializers.
- CompanySettingsSerializer.Meta.model: Corresponding CompanySettings relation model.
- CompanySettingsSerializer.Meta.fields: Explicit field set permitted for output and validation.
- CompanySettingsSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- CompanyAliasSerializer.Meta.model: Corresponding CompanyAlias relation model.
- CompanyAliasSerializer.Meta.fields: Explicit field set permitted for output and validation.
- CompanyAliasSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- ContactProfileSerializer.Meta.model: Corresponding ContactProfile relation model.
- ContactProfileSerializer.Meta.fields: Explicit field set permitted for output and validation.
- ContactProfileSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- TeamSerializer.Meta.model: Corresponding Team relation model.
- TeamSerializer.Meta.fields: Explicit field set permitted for output and validation.
- TeamSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- MembershipSerializer.Meta.model: Corresponding Membership relation model.
- MembershipSerializer.Meta.fields: Explicit field set permitted for output and validation.
- MembershipSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- CompanyGrantSerializer.Meta.model: Corresponding CompanyGrant relation model.
- CompanyGrantSerializer.Meta.fields: Explicit field set permitted for output and validation.
- CompanyGrantSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- ProductSerializer.Meta.model: Corresponding Product relation model.
- ProductSerializer.Meta.fields: Explicit field set permitted for output and validation.
- ProductSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- TicketSerializer.Meta.model: Corresponding Ticket relation model.
- TicketSerializer.Meta.fields: Explicit field set permitted for output and validation.
- TicketSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- OpportunitySerializer.Meta.model: Corresponding Opportunity relation model.
- OpportunitySerializer.Meta.fields: Explicit field set permitted for output and validation.
- OpportunitySerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- QuoteSerializer.Meta.model: Corresponding Quote relation model.
- QuoteSerializer.Meta.fields: Explicit field set permitted for output and validation.
- QuoteSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- QuoteLineSerializer.Meta.model: Corresponding QuoteLine relation model.
- QuoteLineSerializer.Meta.fields: Explicit field set permitted for output and validation.
- QuoteLineSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- SalesOrderSerializer.Meta.model: Corresponding SalesOrder relation model.
- SalesOrderSerializer.Meta.fields: Explicit field set permitted for output and validation.
- SalesOrderSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- OrderLineSerializer.Meta.model: Corresponding OrderLine relation model.
- OrderLineSerializer.Meta.fields: Explicit field set permitted for output and validation.
- OrderLineSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- FollowUpSerializer.Meta.model: Corresponding FollowUp relation model.
- FollowUpSerializer.Meta.fields: Explicit field set permitted for output and validation.
- FollowUpSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- ConversationSerializer.Meta.model: Corresponding Conversation relation model.
- ConversationSerializer.Meta.fields: Explicit field set permitted for output and validation.
- ConversationSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- MessageSerializer.Meta.validators: Transactions check message idempotency keys, permitting repeated submission of identical content.
- MessageSerializer.Meta.model: Corresponding Message relation model.
- MessageSerializer.Meta.fields: Explicit field set permitted for output and validation.
- MessageSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- DraftSerializer.Meta.model: Corresponding Draft relation model.
- DraftSerializer.Meta.fields: Explicit field set permitted for output and validation.
- DraftSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- ToolActionSerializer.Meta.model: Corresponding ToolAction relation model.
- ToolActionSerializer.Meta.fields: Explicit field set permitted for output and validation.
- ToolActionSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- AttachmentSerializer.Meta.model: Corresponding Attachment relation model.
- AttachmentSerializer.Meta.fields: Explicit field set permitted for output and validation.
- AttachmentSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
- NotificationSerializer.Meta.model: Corresponding Notification relation model.
- NotificationSerializer.Meta.fields: Explicit field set permitted for output and validation.
- NotificationSerializer.Meta.read_only_fields: Service-maintained identity, version, and execution state.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers as s

from apps.crm.models import Company, Contact
from . import models
from .news_signals import NewsAmountField, validate_news_signal
from .permissions import scope, visible_company_ids


# Function: Strictly control sales API inputs and relation references.
# Logic: Reject fields outside the allowlist explicitly; query related objects through user authorization.
# Constraints: Serializers alone do not guarantee state transitions or cross-entity consistency.
class StrictModelSerializer(s.ModelSerializer):
    # Function: Reject undeclared writes.
    # Inputs: `data`: client JSON object.
    # Outputs: DRF-converted data; unknown/read-only fields raise ValidationError.
    # Logic: Check field sets before ordinary ModelSerializer validation.
    # Constraints: Never silently ignore owner, state, or misspelled parameters.
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise s.ValidationError("请求必须是 JSON 对象。")
        allowed = {name for name, field in self.fields.items() if not field.read_only}
        invalid = set(data) - allowed
        if invalid:
            raise s.ValidationError(
                {"fields": "字段不可写或未定义：" + ", ".join(sorted(invalid))}
            )
        return super().to_internal_value(data)

    # Function: Restrict objects referenceable by relation fields.
    # Inputs: No parameters; read serializer.context.request.user.
    # Outputs: Dictionary of authorization-filtered fields.
    # Logic: Companies/contacts use company business permissions; other models use scope. Assignees are limited to the current user and common team members; invitations may reference active accounts, with transaction-level authorization.
    # Constraints: Without a user during schema generation, relation querysets are empty; never enumerate business data.
    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        user = request.user if request else None
        for field in fields.values():
            if (
                not isinstance(field, s.PrimaryKeyRelatedField)
                or field.queryset is None
            ):
                continue
            model = field.queryset.model
            if user is None or not user.is_authenticated:
                field.queryset = model.objects.none()
            elif model is Company:
                field.queryset = Company.objects.filter(
                    pk__in=visible_company_ids(user)
                )
            elif model is Contact:
                field.queryset = Contact.objects.filter(
                    company_id__in=visible_company_ids(user)
                )
            elif model is get_user_model() and self.Meta.model is models.Membership:
                field.queryset = model.objects.filter(is_active=True)
            elif model is get_user_model():
                teams = scope(models.Team, user).filter(archived=False)
                field.queryset = (
                    model.objects.filter(is_active=True)
                    .filter(
                        Q(pk=user.pk)
                        | Q(
                            sales_memberships__team__in=teams,
                            sales_memberships__archived=False,
                        )
                        | Q(pk__in=teams.values("owner_id"))
                    )
                    .distinct()
                )
            else:
                field.queryset = scope(model, user).filter(archived=False)
        return fields


# Function: Output document amounts and active details.
# Logic: Preserve Decimal precision and sum after rounding each line to two fractional digits.
# Constraints: Amounts are post-discount net totals in the document currency, excluding undeclared taxes.
class DocumentSerializer(StrictModelSerializer):
    total = s.SerializerMethodField()
    lines = s.SerializerMethodField()

    # Function: Calculate net amounts of currently active line items.
    # Inputs: `obj`: Quote or SalesOrder.
    # Outputs: String with two fractional digits.
    # Logic: Quantity times unit price minus whole-line discount, using ROUND_HALF_UP per line.
    # Constraints: No cross-currency arithmetic, floats, or inferred taxes.
    def get_total(self, obj) -> str:
        return str(
            sum(
                (
                    (line.quantity * line.unit_price - line.discount).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    for line in obj.lines.filter(archived=False)
                ),
                Decimal("0.00"),
            )
        )

    # Function: Return line-item snapshots.
    # Inputs: `obj`: quote or order.
    # Outputs: Serialized active-line array.
    # Logic: Stable ordering by creation time and UUID.
    # Constraints: The parent document must be authorized; never overwrite historical prices from the catalog.
    def get_lines(self, obj) -> list[dict]:
        serializer = (
            QuoteLineSerializer
            if isinstance(obj, models.Quote)
            else OrderLineSerializer
        )
        return serializer(
            obj.lines.filter(archived=False).order_by("created_at", "id"),
            many=True,
            context=self.context,
        ).data


# Function: Declare fields for company lifecycle and manual primary-contact settings.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class CompanySettingsSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.CompanySettings
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "primary_contact",
            "notes",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for manually confirmed domain/contact grouping mappings.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class CompanyAliasSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.CompanyAlias
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "group_key",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for supplemental manual contact details.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class ContactProfileSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.ContactProfile
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "contact",
            "title",
            "phone",
            "notes",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for business teams with explicit managers.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class TeamSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Team
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "name",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for team membership and roles.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class MembershipSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Membership
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "team",
            "user",
            "role",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for company business-record sharing grants.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class CompanyGrantSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.CompanyGrant
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "team",
            "role",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for product catalogs and manual inventory.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class ProductSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Product
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "sku",
            "name",
            "description",
            "currency",
            "unit_price",
            "stock_quantity",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for customer service tickets.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class TicketSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Ticket
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "title",
            "description",
            "status",
            "priority",
            "due_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
        ]


# Function: Declare fields for sales opportunities and pipelines.
# Logic: Filter relations by current user; enter opportunity product names explicitly, and maintain state through dedicated business actions.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class OpportunitySerializer(StrictModelSerializer):
    product_names = s.ListField(child=s.CharField(max_length=240), required=False, allow_empty=True, allow_null=True)
    # Function: Bind model and API fields.
    # Logic: Explicitly expose product names and existing opportunity fields; browsers cannot change owner or state.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Opportunity
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "title",
            "description",
            "status",
            "amount",
            "currency",
            "expected_close",
            "product_names",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
        ]


# Function: Declare fields for reviewed quotes with genuine external-send evidence.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class QuoteSerializer(DocumentSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Quote
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "number",
            "status",
            "currency",
            "valid_until",
            "notes",
            "sent_at",
            "external_message_id",
            "total",
            "lines",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
            "sent_at",
            "external_message_id",
        ]


# Function: Declare fields for quote line snapshots.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class QuoteLineSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.QuoteLine
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "quote",
            "product",
            "description",
            "quantity",
            "unit_price",
            "discount",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for customer orders and confirmation states.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class SalesOrderSerializer(DocumentSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.SalesOrder
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "number",
            "quote",
            "currency",
            "status",
            "notes",
            "confirmed_at",
            "total",
            "lines",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
            "confirmed_at",
        ]


# Function: Declare fields for order line snapshots.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class OrderLineSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.OrderLine
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "order",
            "product",
            "description",
            "quantity",
            "unit_price",
            "discount",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for customer follow-ups and due reminders.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class FollowUpSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.FollowUp
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "assigned_to",
            "title",
            "description",
            "due_at",
            "status",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
        ]


# Function: Declare fields for employees' own general/company assistant conversations.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class ConversationSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Conversation
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "title",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for immutable conversation messages.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class MessageSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Message
        validators = []
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "conversation",
            "role",
            "content",
            "client_key",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "role",
        ]


# Function: Declare fields for editable drafts in private conversations.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class DraftSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Draft
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "conversation",
            "kind",
            "subject",
            "content",
            "recipients",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
        ]


# Function: Declare fields for explicitly confirmed external-tool actions and execution states.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class ToolActionSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.ToolAction
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "conversation",
            "tool",
            "parameters",
            "status",
            "idempotency_key",
            "approved_at",
            "started_at",
            "finished_at",
            "result",
            "error",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "status",
            "approved_at",
            "started_at",
            "finished_at",
            "result",
            "error",
        ]


# Function: Declare fields for employee-private files and company links.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class AttachmentSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Attachment
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "company",
            "name",
            "content_type",
            "size",
            "sha256",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "size",
            "sha256",
        ]


# Function: Declare fields for in-app due reminders.
# Logic: Filter relations by current user; dedicated business actions maintain state.
# Constraints: Reject client-supplied owner/revision or fabricated execution results.
class NotificationSerializer(StrictModelSerializer):
    # Function: Bind model and API fields.
    # Logic: Explicit field lists prevent newly added model fields from being exposed automatically.
    # Constraints: Transactional services additionally validate cross-field rules.
    class Meta:
        model = models.Notification
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "follow_up",
            "source_revision",
            "title",
            "read_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "revision",
            "created_at",
            "updated_at",
            "archived",
            "read_at",
            "follow_up",
            "source_revision",
            "title",
        ]


# Function: Expose connection identity and status.
# Logic: Exclude credential fields completely; connections are written only through OAuth callbacks.
# Constraints: Never return ciphertext to clients either.
class ConnectionSerializer(StrictModelSerializer):
    # Function: Declare a credential-free field allowlist.
    # Logic: All fields are read-only; disabling uses dedicated versioned archival.
    # Constraints: Record APIs cannot fabricate connections.
    class Meta:
        model = models.Connection
        fields = [
            "id",
            "owner",
            "revision",
            "archived",
            "created_at",
            "updated_at",
            "provider",
            "account",
        ]
        read_only_fields = fields



# Function: Validate explicitly timezone-aware timestamps.
# Logic: Reject strings without offsets to avoid silently using the server timezone.
# Constraints: Serialization follows existing DRF time conventions.
class ZonedDateTimeField(s.DateTimeField):
    # Function: Check timestamp input.
    # Inputs: `value`: raw field.
    # Outputs: Timezone-aware datetime.
    # Logic: Check ISO parsing and offset before DRF validation.
    # Constraints: Invalid or timezone-free input raises 400 without adding a default timezone.
    def to_internal_value(self, value):
        from django.utils.dateparse import parse_datetime
        from django.utils.timezone import is_aware
        parsed = parse_datetime(value) if isinstance(value, str) else value
        if parsed is None or not hasattr(parsed, 'tzinfo') or not is_aware(parsed):
            raise s.ValidationError('时间必须包含明确时区偏移。')
        return super().to_internal_value(value)


# Function: Validate shared event facts and isolate linked business information.
# Logic: Validate date precision on writes; all read entry points filter invisible opportunity IDs. Date-only output includes the final day in its date range.
# Constraints: Original event text is shared fact data; opportunities, companies, and amounts gain no permissions through events. No external site access.
class WorldEventSerializer(StrictModelSerializer):
    amount = NewsAmountField(max_digits=30, decimal_places=6, min_value=0, allow_null=True, required=False, coerce_to_string=True)
    evidence = s.CharField(max_length=600, allow_blank=True, required=False, trim_whitespace=False)
    amount_evidence = s.CharField(max_length=400, allow_blank=True, required=False, trim_whitespace=False)
    source_url = s.URLField(max_length=2000, allow_blank=True, required=False)
    starts_on = s.DateField(read_only=True, allow_null=True)
    ends_on = s.DateField(read_only=True, allow_null=True)
    latitude = s.FloatField(min_value=-85, max_value=85)
    longitude = s.FloatField(min_value=-180, max_value=180)
    country = s.RegexField(r'^[A-Z]{2}$')
    starts_at = ZonedDateTimeField()
    ends_at = ZonedDateTimeField()
    registration_deadline = ZonedDateTimeField(allow_null=True, required=False)
    onsite = s.ListField(child=s.CharField(max_length=1000), max_length=100, required=False)
    suggested_actions = s.ListField(child=s.CharField(max_length=1000), max_length=100, required=False)
    opportunity_ids = s.ListField(child=s.UUIDField(), max_length=200, required=False)

    # Function: Validate cross-field relationships.
    # Inputs: `attrs`: fields.
    # Outputs: Validated attrs.
    # Logic: Validate source amount metadata and evidence, then delegate dates, ownership and duplicate checks to insights.
    # Constraints: Preserve original text/timestamps; duplicates return 409 without overwriting records.
    def validate(self, attrs):
        from .insights import validate_insight
        return validate_insight(self, validate_news_signal(self, attrs))

    # Function: Project shared events for the current viewer.
    # Inputs: `instance`: event record; implicitly read request.user and opportunity links for the current batch.
    # Outputs: Filtered event dictionary; date-only events include starts_on/ends_on, while timestamp events return null for both.
    # Logic: Query visible opportunities once per serialization batch, shared by all APIs/Tools; derive inclusive dates from UTC boundaries for date-only events.
    # Constraints: Without request identity, links are empty. Cache only on the current serializer instance, never across requests; no database writes.
    def to_representation(self, instance):
        from .insight_dates import date_range
        data = super().to_representation(instance)
        if not hasattr(self, "_visible_insight_opportunities"):
            request = self.context.get("request")
            rows = self.parent.instance if isinstance(self.parent, s.ListSerializer) else [instance]
            identifiers = {str(pk) for row in rows for pk in row.opportunity_ids}
            self._visible_insight_opportunities = set()
            if request and request.user.is_authenticated and identifiers:
                self._visible_insight_opportunities = {str(pk) for pk in scope(models.Opportunity, request.user).filter(pk__in=identifiers).values_list("pk", flat=True)}
        data["opportunity_ids"] = [str(pk) for pk in instance.opportunity_ids if str(pk) in self._visible_insight_opportunities]
        dates = date_range(instance.starts_at, instance.ends_at) if instance.time_precision == "date" else (None, None)
        data["starts_on"], data["ends_on"] = [value.isoformat() if value else None for value in dates]
        return data

    # Function: Declare event fields.
    # Logic: Reuse Record's read-only version/account fields; expose provenance, time precision, and read-only date ranges.
    # Constraints: Reject fabricated owners/date projections; project linked IDs through viewer permissions.
    class Meta:
        model = models.WorldEvent
        validators = []
        fields = ['id', 'owner', 'revision', 'archived', 'created_at', 'updated_at', 'title', 'event_type', 'country', 'city', 'latitude', 'longitude', 'starts_at', 'ends_at', 'time_precision', 'starts_on', 'ends_on', 'registration_deadline', 'source_url', 'description', 'onsite', 'suggested_actions', 'opportunity_ids', 'data_source', 'evidence', 'amount', 'currency', 'amount_type', 'amount_scope', 'amount_evidence', 'amount_qualifier']
        read_only_fields = ['id', 'owner', 'revision', 'archived', 'created_at', 'updated_at']


# Function: Validate industry news, public sales leads, and exact source amounts.
# Logic: New fields are optional and never automatically linked to CRM; merge to validate amount evidence/combinations, with globally deduplicated Agent sources.
# Constraints: Disable DRF automatic source uniqueness checks; insights returns 409 for duplicates and the database handles concurrency. No fetching or summary generation.
class WorldNewsSerializer(StrictModelSerializer):
    source_url = s.URLField(max_length=2000, allow_blank=True, required=False)
    published_at = ZonedDateTimeField()
    country = s.RegexField(r'^[A-Z]{2}$', allow_blank=True, required=False)
    summary = s.CharField(max_length=8000, allow_blank=True, required=False)
    content = s.CharField(max_length=100000)
    amount = NewsAmountField(max_digits=30, decimal_places=6, min_value=0, allow_null=True, required=False, coerce_to_string=True, help_text="非负普通十进制字符串，最多 24 位整数和 6 位小数；超限拒绝，不舍入；未知为 null。")
    evidence = s.CharField(max_length=600, allow_blank=True, required=False, trim_whitespace=False)
    amount_evidence = s.CharField(max_length=400, allow_blank=True, required=False, trim_whitespace=False)

    # Function: Validate news sources and complete monetary-evidence combinations.
    # Inputs: `attrs`.
    # Outputs: Validated fields.
    # Logic: Merge old values to validate monetary combinations and excerpt inclusion, then reuse insights for HTTPS/source-duplicate validation.
    # Constraints: Duplicates raise 409; never access sources or overwrite original records.
    def validate(self, attrs):
        from .insights import validate_insight
        return validate_insight(self, validate_news_signal(self, attrs))

    # Function: Declare news fields.
    # Logic: Content, source, and thirteen public lead fields are writable; new fields are optional. Identity/version are read-only; insights/database constraints enforce source uniqueness.
    # Constraints: DRF automatic source uniqueness must not incorrectly reject manual records; archival uses command endpoints.
    class Meta:
        model = models.WorldNews
        validators = []
        fields = ['id', 'owner', 'revision', 'archived', 'created_at', 'updated_at', 'title', 'category', 'industry', 'country', 'published_at', 'source_url', 'summary', 'content', 'data_source', 'company_name', 'signal_type', 'project_name', 'demand_description', 'potential_sales_need', 'opportunity_reason', 'time_window', 'evidence', 'amount', 'currency', 'amount_type', 'amount_scope', 'amount_evidence', 'amount_qualifier']
        read_only_fields = ['id', 'owner', 'revision', 'archived', 'created_at', 'updated_at']


from .algorithm_serializers import OpportunitySignalSerializer, OpportunityPrioritySerializer  # noqa: E402


SERIALIZERS = {
    "opportunity-signals": OpportunitySignalSerializer,
    "opportunity-priorities": OpportunityPrioritySerializer,
    "world-events": WorldEventSerializer,
    "world-news": WorldNewsSerializer,
    "connections": ConnectionSerializer,
    "customers": CompanySettingsSerializer,
    "aliases": CompanyAliasSerializer,
    "contact-profiles": ContactProfileSerializer,
    "teams": TeamSerializer,
    "memberships": MembershipSerializer,
    "grants": CompanyGrantSerializer,
    "products": ProductSerializer,
    "tickets": TicketSerializer,
    "opportunities": OpportunitySerializer,
    "quotes": QuoteSerializer,
    "quote-lines": QuoteLineSerializer,
    "orders": SalesOrderSerializer,
    "order-lines": OrderLineSerializer,
    "follow-ups": FollowUpSerializer,
    "conversations": ConversationSerializer,
    "messages": MessageSerializer,
    "drafts": DraftSerializer,
    "actions": ToolActionSerializer,
    "files": AttachmentSerializer,
    "notifications": NotificationSerializer,
}
