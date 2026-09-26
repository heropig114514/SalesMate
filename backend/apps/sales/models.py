"""Responsibility: Define relational schemas for sales business, team sharing, and assistant operations.
Implementation: News stores one set of public leads, exact amounts, and evidence without automatic CRM links. Events/news share facts while retaining owner write attribution. Agent sources deduplicate by URL, plus event start time. Events explicitly store date precision; other records use UUIDs, revisions, and archival state.
Relationships: sales.services handles transactions and validation; CRM retains private email and Agent analysis contracts.
Directory:
- WorldNews: Industry news facts, one set of public sales leads, source amounts, and evidence.
- WorldEvent: Event facts and explicit opportunity links.
- WorldEvent.Meta: Enforce uniqueness of Agent events with the same source and start time.
- WorldNews.Meta: Enforce source uniqueness and monetary-field consistency.
- Record: Base class for archivable, versioned business records.
- Record.Meta: Declare abstraction or database uniqueness/numeric constraints.
- CompanyRecord: Base class for business records linked to companies and assignees.
- CompanyRecord.Meta: Declare abstraction or database uniqueness/numeric constraints.
- CompanySettings: Company lifecycle and manually selected primary-contact settings.
- CompanyAlias: Manually confirmed domain/contact grouping mappings.
- CompanyAlias.Meta: Declare abstraction or database uniqueness/numeric constraints.
- ContactProfile: Supplemental manual contact details.
- Team: Business team with an explicit manager.
- Membership: Team membership and roles.
- Membership.Meta: Declare abstraction or database uniqueness/numeric constraints.
- CompanyGrant: Company business-record sharing grants.
- CompanyGrant.Meta: Declare abstraction or database uniqueness/numeric constraints.
- Product: Product catalog and manual inventory records.
- Product.Meta: Declare abstraction or database uniqueness/numeric constraints.
- Ticket: Customer service tickets.
- Opportunity: Sales opportunities and pipeline.
- SellerProfile: Owner-isolated seller target profiles.
- Quote: Quotes with review and genuine external-send evidence.
- Quote.Meta: Declare abstraction or database uniqueness/numeric constraints.
- QuoteLine: Quote line snapshots.
- QuoteLine.Meta: Declare abstraction or database uniqueness/numeric constraints.
- SalesOrder: Customer orders and confirmation state.
- SalesOrder.Meta: Declare abstraction or database uniqueness/numeric constraints.
- OrderLine: Order line snapshots.
- OrderLine.Meta: Declare abstraction or database uniqueness/numeric constraints.
- FollowUp: Customer follow-ups and due reminders.
- Conversation: Employees' own general or company assistant conversations.
- Message: Immutable conversation messages.
- Message.Meta: Declare abstraction or database uniqueness/numeric constraints.
- Draft: Editable drafts in private conversations.
- ToolAction: Explicitly confirmed external-tool actions and execution states.
- ToolAction.Meta: Declare abstraction or database uniqueness/numeric constraints.
- Attachment: Employee-private files and company links.
- AuditEvent: Append-only business operation records.
- Notification: In-app due reminders.
- Notification.Meta: Declare abstraction or database uniqueness/numeric constraints.
- Connection: Encrypted credentials for separately authorized external services.
- Connection.Meta: Restrict each employee to one connection per provider/account.
Variable index:
- NEWS_SIGNAL_TYPES: Public news event types.
- NEWS_CURRENCIES: Currencies supported by the collaboration contract.
- NEWS_AMOUNT_TYPES: Business meaning of source amounts.
- NEWS_AMOUNT_SCOPES: Coverage of source amounts.
- WorldEvent.time_precision: datetime denotes an exact instant; date denotes UTC date boundaries with an exclusive end date.
- WorldEvent.Meta.constraints: Unique nonempty Agent source URL/start-time pairs, including archived records.
- WorldNews.Meta.constraints: Unique Agent sources; amounts are nonnegative and jointly present or absent with currency, type, scope, and evidence.
- WorldEvent.data_source: Event source label; synthetic denotes a placeholder.
- WorldNews.data_source: News source label; synthetic denotes a placeholder.
- WorldNews.title: News title.
- WorldNews.category: News category.
- WorldNews.industry: Industry explicitly supplied by the source.
- WorldNews.country: Nullable ISO country/region code.
- WorldNews.published_at: Actual publication time.
- WorldNews.source_url: Original report source.
- WorldNews.summary: Upstream-provided summary.
- WorldNews.content: Plain-text body.
- WorldNews.company_name: Original company or institution name.
- WorldNews.signal_type: Original event type, optionally empty.
- WorldNews.project_name: Original project name.
- WorldNews.demand_description: Demand explicitly disclosed by the news.
- WorldNews.potential_sales_need: Potential procurement inference separated from news facts.
- WorldNews.opportunity_reason: Explanation linking potential demand and products, not confirmed procurement.
- WorldNews.time_window: Project/procurement milestones supplied by the source.
- WorldNews.evidence: Original excerpt from the public source.
- WorldNews.amount: Source amount with up to 24 integer and 6 fractional digits; null when unknown.
- WorldNews.currency: Source currency without conversion.
- WorldNews.amount_type: Amount meaning, such as investment, budget, tender, or contract.
- WorldNews.amount_scope: Coverage such as the entire project, equipment procurement, or another scope.
- WorldNews.amount_evidence: Original amount excerpt included in evidence.
- WorldEvent.title: Event name.
- WorldEvent.event_type: Event type.
- WorldEvent.country: ISO country/region code.
- WorldEvent.city: City.
- WorldEvent.latitude: Actual latitude.
- WorldEvent.longitude: Actual longitude.
- WorldEvent.starts_at: Timezone-aware start time.
- WorldEvent.ends_at: Timezone-aware end time.
- WorldEvent.registration_deadline: Nullable registration deadline.
- WorldEvent.source_url: Verifiable source link.
- WorldEvent.description: Original event description.
- WorldEvent.onsite: Original on-site situation highlights.
- WorldEvent.suggested_actions: Upstream advice text, not generated by the backend.
- WorldEvent.opportunity_ids: Explicit links to the user's own opportunities, without inferred relevance.
- SellerProfile.owner: Unique business owner of the seller profile; statistics cannot be shared across owners.
- SellerProfile.revision: Seller profile optimistic-lock version.
- SellerProfile.profile: Optional target industry, size, region, and IANA timezone; no model guesses.
- SellerProfile.updated_at: Timestamp of the last explicit update.
- Opportunity.product_names: Explicitly entered canonical product-name list; null or an empty array denotes missing information.
- Connection.provider: Service provider: gmail, qq, or calendar.
- Connection.account: External account or calendar connection name.
- Connection.encrypted_credentials: Fernet-encrypted Google authorization JSON or QQ authorization code, inaccessible to browsers.
- Connection.Meta.constraints: Composite connection-identity uniqueness constraint.
- Record.id: Entity UUID.
- Record.owner: Authoritative business owner; clients cannot supply it.
- Record.revision: Optimistic-lock version.
- Record.archived: Soft-archive state.
- Record.created_at: Creation time.
- Record.updated_at: Last modification time.
- Record.Meta.abstract: Do not create a table for the base class.
- CompanyRecord.company: Related company.
- CompanyRecord.assigned_to: Authorized business assignee.
- CompanyRecord.Meta.abstract: Do not create a table for the base class.
- CompanySettings.company: Corresponding original company.
- CompanySettings.primary_contact: Manually selected contact from the same company.
- CompanySettings.notes: Manual company notes.
- CompanyAlias.company: Grouping target.
- CompanyAlias.group_key: Exact grouping key: domain:<domain> or contact:<email>.
- CompanyAlias.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- ContactProfile.contact: Original contact.
- ContactProfile.title: Manually confirmed job title.
- ContactProfile.phone: Contact phone number.
- ContactProfile.notes: Contact notes.
- Team.name: Team name.
- Membership.team: Parent team.
- Membership.user: Member account.
- Membership.role: Member role.
- Membership.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- CompanyGrant.company: Company covered by the grant.
- CompanyGrant.team: Authorized team.
- CompanyGrant.role: Maximum team permissions for this company.
- CompanyGrant.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- Product.sku: Product code within the employee's catalog.
- Product.name: Product name.
- Product.description: Product description.
- Product.currency: Explicit three-letter ISO currency.
- Product.unit_price: Catalog unit price.
- Product.stock_quantity: Manually recorded inventory; null when unknown.
- Product.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- Ticket.title: Ticket subject.
- Ticket.description: Issue description.
- Ticket.status: open/in_progress/resolved/closed.
- Ticket.priority: Manual handling priority.
- Ticket.due_at: Agreed deadline.
- Opportunity.title: Opportunity name.
- Opportunity.description: Opportunity description.
- Opportunity.status: new/qualified/proposal/won/lost.
- Opportunity.amount: Estimated opportunity amount; null when unknown.
- Opportunity.currency: Amount currency.
- Opportunity.expected_close: Expected closing date.
- Quote.number: Quote number within the owning employee's scope.
- Quote.status: draft/approved/sent/accepted/rejected.
- Quote.currency: Currency for the entire quote.
- Quote.valid_until: Quote validity period.
- Quote.notes: Quote terms.
- Quote.sent_at: Actual successful external-send time.
- Quote.external_message_id: Email identifier returned by the real sending service.
- Quote.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- QuoteLine.quote: Parent quote.
- QuoteLine.product: Optional catalog product.
- QuoteLine.description: Frozen product/service description.
- QuoteLine.quantity: Quoted quantity.
- QuoteLine.unit_price: Explicit unit price for this line.
- QuoteLine.discount: Total discount amount for the line.
- QuoteLine.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- SalesOrder.number: Order number within the owning employee's scope.
- SalesOrder.quote: Optional source quote.
- SalesOrder.currency: Currency for the entire order.
- SalesOrder.status: draft/confirmed/fulfilled/cancelled.
- SalesOrder.notes: Order description.
- SalesOrder.confirmed_at: Manual order-confirmation time.
- SalesOrder.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- OrderLine.order: Parent order.
- OrderLine.product: Optional source product.
- OrderLine.description: Order product description.
- OrderLine.quantity: Ordered quantity.
- OrderLine.unit_price: Confirmed unit price.
- OrderLine.discount: Total discount amount for the line.
- OrderLine.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- FollowUp.title: Follow-up item.
- FollowUp.description: Follow-up description.
- FollowUp.due_at: Due time.
- FollowUp.status: open/completed/cancelled.
- Conversation.company: Nullable company; empty denotes a general conversation.
- Conversation.title: Conversation display title.
- Message.conversation: Parent conversation.
- Message.role: Message source role.
- Message.content: Message body.
- Message.client_key: Client idempotency key.
- Message.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- Draft.conversation: Parent conversation.
- Draft.kind: Draft purpose.
- Draft.subject: Email subject.
- Draft.content: Draft body.
- Draft.recipients: Recipient email array validated by the API.
- ToolAction.company: Action company.
- ToolAction.conversation: Optional source conversation.
- ToolAction.tool: Allowed external tool.
- ToolAction.parameters: Complete parameters frozen upon confirmation.
- ToolAction.status: pending confirmation/approved/running/succeeded/failed/uncertain/cancelled.
- ToolAction.idempotency_key: Action idempotency key scoped to the employee.
- ToolAction.approved_at: Explicit confirmation time.
- ToolAction.started_at: Execution start time.
- ToolAction.finished_at: Termination time.
- ToolAction.result: Safe result returned by the external service.
- ToolAction.error: Error code and actionable diagnostics.
- ToolAction.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
- Attachment.company: Related company.
- Attachment.name: Original upload filename; downloads use a safe name.
- Attachment.storage_key: Private relative storage key.
- Attachment.content_type: Uploader-declared media type, used as metadata only.
- Attachment.size: Actual byte count.
- Attachment.sha256: Content verification digest.
- AuditEvent.id: Audit identifier.
- AuditEvent.owner: Business workspace owning the record.
- AuditEvent.actor: Actual actor.
- AuditEvent.company: Optional business company.
- AuditEvent.event: Event type.
- AuditEvent.object_type: Object model name.
- AuditEvent.object_id: Object identifier.
- AuditEvent.changes: Controlled changes to states or field names, excluding bodies.
- AuditEvent.created_at: Event timestamp.
- Notification.follow_up: Source follow-up task.
- Notification.source_revision: Follow-up version that generated the reminder.
- Notification.title: Reminder display text.
- Notification.read_at: Read timestamp.
- Notification.Meta.constraints: Enforce uniqueness or monetary bounds under database concurrency.
"""

import uuid

from django.conf import settings
from django.db import models

NEWS_SIGNAL_TYPES = [(value, value) for value in ("expansion", "new_factory", "tender", "equipment_upgrade", "procurement", "other")]
NEWS_CURRENCIES = [(value, value) for value in ("CNY", "USD", "EUR", "GBP", "JPY", "KRW", "SGD", "TWD", "HKD", "INR", "CAD", "AUD", "CHF")]
NEWS_AMOUNT_TYPES = [(value, value) for value in ("total_investment", "procurement_budget", "tender_amount", "contract_amount", "other")]
NEWS_AMOUNT_SCOPES = [(value, value) for value in ("whole_project", "equipment_procurement", "other")]


# Function: Base class for archivable, versioned business records.
# Logic: Abstract fields unify identity, ownership, versions, and timestamps; transactional services validate state transitions.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    revision = models.PositiveIntegerField(default=0)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        abstract = True


# Function: Base class for business records linked to companies and assignees.
# Logic: The employee owner comes from the company owner; assignees require company business-edit permissions.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class CompanyRecord(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="%(class)s_records"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        abstract = True


# Function: Company lifecycle and manually selected primary-contact settings.
# Logic: Retain original companies and emails; archival does not delete evidence.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class CompanySettings(Record):
    company = models.OneToOneField(
        "crm.Company", on_delete=models.PROTECT, related_name="business_settings"
    )
    primary_contact = models.ForeignKey(
        "crm.Contact",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.TextField(blank=True)


# Function: Manually confirmed domain/contact grouping mappings.
# Logic: Match original owner and group_key exactly without inferring corporate-group relations.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class CompanyAlias(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="group_aliases"
    )
    group_key = models.CharField(max_length=320)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "group_key"], name="sales_owner_alias"
            )
        ]


# Function: Supplemental manual contact details.
# Logic: Store supplemental details separately from email-extracted facts; explicit contact editing manages the original name.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class ContactProfile(Record):
    contact = models.OneToOneField(
        "crm.Contact", on_delete=models.PROTECT, related_name="business_profile"
    )
    title = models.CharField(max_length=240, blank=True)
    phone = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)


# Function: Business team with an explicit manager.
# Logic: The creator is owner; teams receive no mailbox permissions automatically.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Team(Record):
    name = models.CharField(max_length=160)


# Function: Team membership and roles.
# Logic: Managers manage members, editors edit authorized business records, and viewers have read-only access.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Membership(Record):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=[("viewer", "只读"), ("editor", "编辑"), ("manager", "管理")],
    )

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="sales_team_member")
        ]


# Function: Company business-record sharing grants.
# Logic: Company owners explicitly grant team access; grants cover neither private email nor Agent APIs.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class CompanyGrant(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="business_grants"
    )
    team = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="company_grants"
    )
    role = models.CharField(
        max_length=16, choices=[("viewer", "只读"), ("editor", "编辑")]
    )

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "team"], name="sales_company_team"
            )
        ]


# Function: Product catalog and manual inventory records.
# Logic: Prices have explicit currencies and historical documents retain independent snapshots; inventory does not imply reservation or fulfillment.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Product(Record):
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    currency = models.CharField(max_length=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    stock_quantity = models.DecimalField(
        max_digits=18, decimal_places=3, null=True, blank=True
    )

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "sku"], name="sales_owner_sku"),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="sales_product_price"
            ),
        ]


# Function: Customer service tickets.
# Logic: Explicit transition APIs advance state; email mentions do not imply resolution.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Ticket(CompanyRecord):
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="open", db_index=True)
    priority = models.CharField(
        max_length=16,
        choices=[("low", "低"), ("normal", "普通"), ("high", "高")],
        default="normal",
    )
    due_at = models.DateTimeField(null=True, blank=True)


# Function: Sales opportunities and pipeline.
# Logic: Aggregate only explicit amounts; users enter product names and explicitly declare won/lost states.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Opportunity(CompanyRecord):
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="new", db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3)
    expected_close = models.DateField(null=True, blank=True)
    product_names = models.JSONField(null=True, blank=True)


# Function: Owner-isolated seller target profiles.
# Logic: One versioned configuration per business owner; JSON accepts only target criteria validated by the dedicated API.
# Constraints: No default business profile; averages, product catalogs, and similar wins derive from authoritative data rather than manual entries in this table.
class SellerProfile(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField(default=0)
    profile = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


# Function: Quotes with review and genuine external-send evidence.
# Logic: Drafts are editable and reviewed contents are frozen; only genuine sending results may set sent.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Quote(CompanyRecord):
    number = models.CharField(max_length=100)
    status = models.CharField(max_length=20, default="draft", db_index=True)
    currency = models.CharField(max_length=3)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    external_message_id = models.CharField(max_length=200, blank=True)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "number"], name="sales_quote_number"
            )
        ]


# Function: Quote line snapshots.
# Logic: Calculate quantity times unit price minus the whole-line discount; do not infer taxes or exchange rates.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class QuoteLine(Record):
    quote = models.ForeignKey(Quote, on_delete=models.PROTECT, related_name="lines")
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0)
                & models.Q(unit_price__gte=0)
                & models.Q(discount__gte=0),
                name="sales_quote_line_values",
            )
        ]


# Function: Customer orders and confirmation state.
# Logic: Drafts are excluded from Agent historical-order projections; confirmation freezes transaction contents.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class SalesOrder(CompanyRecord):
    number = models.CharField(max_length=100)
    quote = models.ForeignKey(
        Quote, null=True, blank=True, on_delete=models.PROTECT, related_name="orders"
    )
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, default="draft", db_index=True)
    notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "number"], name="sales_order_number"
            )
        ]


# Function: Order line snapshots.
# Logic: Confirmed records are immutable; catalog changes never retroactively modify documents.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class OrderLine(Record):
    order = models.ForeignKey(
        SalesOrder, on_delete=models.PROTECT, related_name="lines"
    )
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0)
                & models.Q(unit_price__gte=0)
                & models.Q(discount__gte=0),
                name="sales_order_line_values",
            )
        ]


# Function: Customer follow-ups and due reminders.
# Logic: Background processing creates only in-app reminders, never automatic external messages.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class FollowUp(CompanyRecord):
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, default="open", db_index=True)


# Function: Employees' own general or company assistant conversations.
# Logic: A null company denotes general chat; company conversations are not business-shared and messages cannot impersonate Agent output.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Conversation(Record):
    company = models.ForeignKey(
        "crm.Company",
        on_delete=models.PROTECT,
        related_name="conversations",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=240, default="新对话")


# Function: Immutable conversation messages.
# Logic: The current browser may submit only user messages; future protected integration supplies model replies.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Message(Record):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.PROTECT, related_name="messages"
    )
    role = models.CharField(max_length=20, default="user")
    content = models.TextField()
    client_key = models.UUIDField()

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_key"], name="sales_message_key"
            )
        ]


# Function: Editable drafts in private conversations.
# Logic: Separate browser input from email drafts; only explicitly confirmed actions may send externally.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Draft(Record):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.PROTECT, related_name="drafts"
    )
    kind = models.CharField(
        max_length=16, choices=[("chat", "聊天输入"), ("email", "邮件草稿")]
    )
    subject = models.CharField(max_length=1000, blank=True)
    content = models.TextField(blank=True)
    recipients = models.JSONField(default=list, blank=True)


# Function: Explicitly confirmed external-tool actions and execution states.
# Logic: Support Gmail/QQ sending and calendar creation. Freeze parameters during preparation; workers execute only explicitly approved records and never automatically retry unknown outcomes.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class ToolAction(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="tool_actions"
    )
    conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="actions",
    )
    tool = models.CharField(
        max_length=40,
        choices=[
            ("gmail.send", "Gmail 发信"),
            ("qq.send", "QQ 发信"),
            ("calendar.create", "创建日历会议"),
        ],
    )
    parameters = models.JSONField()
    status = models.CharField(
        max_length=32, default="pending_confirmation", db_index=True
    )
    idempotency_key = models.UUIDField()
    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "idempotency_key"], name="sales_action_key"
            )
        ]


# Function: Employee-private files and company links.
# Logic: Download through authenticated APIs without exposing local paths or executing uploaded content.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Attachment(Record):
    company = models.ForeignKey(
        "crm.Company", on_delete=models.PROTECT, related_name="attachments"
    )
    name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    content_type = models.CharField(max_length=150)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)


# Function: Append-only business operation records.
# Logic: Record actor, object identifier, and state changes without copying secrets or bodies.
# Constraints: Append only within transactions; APIs expose no modification/deletion, and database administrators must follow audit retention policy.
class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    company = models.ForeignKey(
        "crm.Company", null=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    event = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=400)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


# Function: In-app due reminders.
# Logic: Create one reminder per assignee/follow-up version; background polling does not duplicate reminders.
# Constraints: Modify through authorized transactional services; field values do not imply completion of external actions.
class Notification(Record):
    follow_up = models.ForeignKey(
        FollowUp, on_delete=models.PROTECT, related_name="notifications"
    )
    source_revision = models.PositiveIntegerField()
    title = models.CharField(max_length=240)
    read_at = models.DateTimeField(null=True, blank=True)

    # Function: Declare model database constraints.
    # Logic: Django migrations generate consistent table structures.
    # Constraints: Not a replacement for service-layer permissions or cross-entity validation.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "follow_up", "source_revision"],
                name="sales_reminder_unique",
            )
        ]


# Function: Encrypted credentials for separately authorized external services.
# Logic: Encrypt Google OAuth or QQ authorization payloads with Fernet; existing read-only synchronization connections gain no permissions automatically.
# Constraints: Browsers may view connection status only; missing keys fail explicitly without plaintext fallback.
class Connection(Record):
    provider = models.CharField(
        max_length=16,
        choices=[
            ("gmail", "Gmail 发信"),
            ("qq", "QQ 发信"),
            ("calendar", "Google 日历"),
        ],
    )
    account = models.CharField(max_length=320)
    encrypted_credentials = models.TextField()

    # Function: Declare external connection identity uniqueness.
    # Logic: Employee, provider, and account form a unique combination.
    # Constraints: Renewed authorization replaces the same-identity connection without creating another access principal.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "provider", "account"],
                name="sales_connection_identity",
            )
        ]


# Function: Store event facts and explicit opportunity links.
# Logic: Retain creator, public event facts, and date precision; keep one Agent record per URL/start time.
# Constraints: Filter linked opportunities by viewer; date-only end boundaries exclude the day after the final included date and do not denote an actual clock time.
class WorldEvent(Record):
    time_precision = models.CharField(max_length=8, choices=[("datetime", "确切时间"), ("date", "仅日期")], default="datetime")
    data_source = models.CharField(max_length=30, default="manual")
    title = models.CharField(max_length=240)
    event_type = models.CharField(max_length=20, choices=[("exhibition", "展会"), ("sales", "销售活动")])
    country = models.CharField(max_length=2)
    city = models.CharField(max_length=120)
    latitude = models.FloatField()
    longitude = models.FloatField()
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    registration_deadline = models.DateTimeField(null=True, blank=True)
    source_url = models.URLField(max_length=2000, blank=True)
    description = models.TextField(blank=True)
    onsite = models.JSONField(default=list, blank=True)
    suggested_actions = models.JSONField(default=list, blank=True)
    opportunity_ids = models.JSONField(default=list, blank=True)

    # Function: Prevent duplicate cross-account collection of the same event.
    # Logic: Constrain URL/start time only for Agent records with sources; archival does not release uniqueness.
    # Constraints: Allow different editions at the same URL; manual records are exempt from collection deduplication.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["source_url", "starts_at"], condition=models.Q(data_source="agent") & ~models.Q(source_url=""), name="world_event_agent_source_start")]


# Function: Store industry news, one set of public sales leads, source amounts, and evidence.
# Logic: New text fields on old records remain empty and amounts null; preserve monetary meaning separately without adding amounts to CRM opportunities.
# Constraints: Archived records still block recollection; do not create/link private records by company name, fetch external sites, or generate inferences.
class WorldNews(Record):
    data_source = models.CharField(max_length=30, default="manual")
    title = models.CharField(max_length=240)
    category = models.CharField(max_length=20, choices=[("regulation", "监管"), ("industry", "产业"), ("competition", "竞争"), ("price", "价格")])
    industry = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, blank=True)
    published_at = models.DateTimeField(db_index=True)
    source_url = models.URLField(max_length=2000, blank=True)
    summary = models.TextField(blank=True)
    content = models.TextField()
    company_name = models.CharField(max_length=240, blank=True, default="")
    signal_type = models.CharField(max_length=30, choices=NEWS_SIGNAL_TYPES, blank=True, default="")
    project_name = models.CharField(max_length=240, blank=True, default="")
    demand_description = models.CharField(max_length=500, blank=True, default="")
    potential_sales_need = models.CharField(max_length=500, blank=True, default="")
    opportunity_reason = models.CharField(max_length=500, blank=True, default="")
    time_window = models.CharField(max_length=240, blank=True, default="")
    evidence = models.CharField(max_length=600, blank=True, default="")
    amount = models.DecimalField(max_digits=30, decimal_places=6, null=True, blank=True)
    currency = models.CharField(max_length=3, choices=NEWS_CURRENCIES, blank=True, default="")
    amount_type = models.CharField(max_length=30, choices=NEWS_AMOUNT_TYPES, blank=True, default="")
    amount_scope = models.CharField(max_length=30, choices=NEWS_AMOUNT_SCOPES, blank=True, default="")
    amount_evidence = models.CharField(max_length=400, blank=True, default="")

    # Function: Constrain collected news identity and monetary storage consistency.
    # Logic: Nonempty Agent sources are globally unique; amounts are nonnegative with all metadata present, or absent with all metadata empty.
    # Constraints: Do not merge manual records or overwrite existing bodies.
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_url"], condition=models.Q(data_source="agent") & ~models.Q(source_url=""), name="world_news_agent_source"),
            models.CheckConstraint(condition=(
                models.Q(amount__isnull=True, currency="", amount_type="", amount_scope="", amount_evidence="")
                | (models.Q(amount__isnull=False, amount__gte=0, currency__in=[value for value, _ in NEWS_CURRENCIES], amount_type__in=[value for value, _ in NEWS_AMOUNT_TYPES], amount_scope__in=[value for value, _ in NEWS_AMOUNT_SCOPES]) & ~models.Q(amount_evidence=""))
            ), name="world_news_amount_consistent"),
        ]


from .algorithm_models import OpportunitySignal, OpportunityPriority  # noqa: E402,F401
