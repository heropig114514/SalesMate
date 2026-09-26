"""Responsibility: Define persistent entities for the email-intelligence closed loop.
Implementation: Relationship fields carry ownership and uniqueness constraints, JSON preserves protocol source, analysis snapshots are retained by revision, and synchronization and lineage models are imported.
Relationships: ingestion, jobs, and results perform transactional writes, selectors provides authorized queries, and qq_models registers QQ credentials and checkpoints independently.
Directory:
- Mailbox: Store user-owned business mailbox and synchronization cursor.
- Mailbox.Meta: Constrain mailbox addresses to one per user.
- GmailCredential: Store Gmail read-only credential granted through employee web OAuth.
- AgentCredential: Store Agent service credential digest limited to one user's business data.
- Company: Store user-isolated company grouping and authoritative CRM snapshot.
- Company.Meta: Isolate same-domain companies across users.
- Contact: Store a contact identity in a company.
- Contact.Meta: Prevent duplicate contacts in one company.
- Email: Store immutable normalized email.
- Extraction: Store versioned extraction result for one email.
- Extraction.Meta: Limit one extraction to each email, prompt version, and repair generation.
- AnalysisInput: Archive Agent-submitted analysis input and its backend revision.
- AnalysisInput.Meta: Keep input versions unique within revision while retaining snapshot history across generations.
- Analysis: Store versioned L3 analysis source.
- Analysis.Meta: Retain distinct analysis prompt versions.
- Score: Store L4 score and its rule version.
- Job: Store company-analysis job and atomic claim lease.
Variable index:
- AgentCredential.created_at: Database record creation time.
- AgentCredential.digest: SHA-256 digest of the high-entropy Agent token.
- AgentCredential.name: Credential label used to identify the service identity.
- AgentCredential.owner: Foreign key to the authenticated business user that enforces data isolation.
- Analysis.Meta.constraints: Database uniqueness constraint preventing duplicate analysis versions.
- Analysis.created_at: Database record creation time.
- Analysis.payload: Complete JSON snapshot for the corresponding protocol.
- Analysis.prompt_version: Version identifier of the analysis producer.
- Analysis.provider: Result origin, rules or agent.
- Analysis.snapshot: Immutable L2 snapshot relationship on which L3 depends.
- AnalysisInput.Meta.constraints: Database uniqueness constraint preventing duplicate inputs at one revision.
- AnalysisInput.company: Owning company foreign key whose user ownership must be validated on access.
- AnalysisInput.created_at: Database record creation time.
- AnalysisInput.input_version: Input version calculated and submitted unchanged by Agent.
- AnalysisInput.payload: Complete JSON snapshot for the corresponding protocol.
- AnalysisInput.revision: Context version incremented when email, facts, or business data changes.
- Company.Meta.constraints: Database uniqueness constraint preventing duplicate company groups per owner.
- Company.created_at: Database record creation time.
- Company.crm_status: Company record state, registered or unregistered.
- Company.customer: Authoritative backend CRM base-data snapshot.
- Company.domains: Confirmed full-domain list for the company group.
- Company.external_version: External CRM business snapshot version.
- Company.group_key: Grouping key from a full enterprise domain or public-mailbox contact address.
- Company.id: Unique entity identifier.
- Company.name: Optional company display name.
- Company.orders: Historical order array that cannot be inferred from email mentions.
- Company.owner: Foreign key to the authenticated business user that enforces data isolation.
- Company.quotes: Authoritative quote array retaining actual outbound-evidence types.
- Company.revision: Context version incremented when email, facts, or business data changes.
- Company.tickets: Authoritative ticket-record array with no current editing interface.
- Contact.Meta.constraints: Database uniqueness constraint preventing duplicate contacts per company.
- Contact.company: Owning company foreign key whose user ownership must be validated on access.
- Contact.email: Contact email address.
- Contact.name: Optional identity name sourced from evidenced email facts.
- Email.business_classification: Current effective classification: business, non_business, or needs_review.
- Email.classification_source: Decision origin: rule, llm, or human.
- Email.classification_reason: Displayable basis for the classification.
- Email.review_status: Human-review decision; an empty string means no human decision.
- Email.reviewed_by: Employee who made the human decision.
- Email.reviewed_at: Human-decision timestamp.
- Email.review_revision: Version preventing concurrent reviews from overwriting one another.
- Email.company: Owning company foreign key whose user ownership must be validated on access.
- Email.contact: Primary external-contact foreign key; messages reverse relation supports interaction queries.
- Email.dedupe_key: Natural idempotency key formed from mailbox address and Gmail message ID.
- Email.direction: Inbound, outbound, or unknown message direction.
- Email.mailbox: Backend business mailbox owning the email.
- Email.payload: Complete JSON snapshot for the corresponding protocol.
- Email.received_at: Message receipt time used for today's new-email statistics.
- Email.sent_at: Timezone-aware time when the email or business action occurred.
- Extraction.Meta.constraints: Database uniqueness constraint preventing duplicate extraction generations.
- Extraction.created_at: Database record creation time.
- Extraction.email: Foreign key to the email owning this extraction.
- Extraction.error: Explicit failure description that is not displayed as a successful result.
- Extraction.facts: Locatable fact structure, null on failure as required by protocol.
- Extraction.prompt_version: Version identifier of the extraction producer.
- Extraction.repair_generation: Zero for ordinary extraction and repair job ID for human repair, retaining old extraction history.
- Extraction.status: Current protocol payload or task state; field declaration defines allowed values.
- Job.attempt: Number of times the job has been claimed.
- Job.company: Owning company foreign key whose user ownership must be validated on access.
- Job.enqueued_at: Job queue time.
- Job.id: Unique entity identifier.
- Job.lease_token: Random claim credential that must not enter logs.
- Job.lease_until: Expiration time for this job lease.
- Job.report: Terminal output declaration and error information for the job.
- Job.revision: Context version incremented when email, facts, or business data changes.
- Job.status: Current protocol payload or task state; field declaration defines allowed values.
- Job.trigger: Business event type that created the job.
- Mailbox.Meta.constraints: Database uniqueness constraint preventing duplicate mailboxes per owner.
- Mailbox.address: Business mailbox display address, not evidence of OAuth verification.
- Mailbox.id: Unique entity identifier.
- Mailbox.owner: Foreign key to the authenticated business user that enforces data isolation.
- Mailbox.sync_state: Synchronization cursor state without authorization tokens.
- Mailbox.version: Optimistic-lock version for synchronization state.
- GmailCredential.authorized_at: Time web authorization first completed.
- GmailCredential.credentials: Google authorized-user JSON used only by backend and Agent routes.
- GmailCredential.mailbox: One-to-one relationship with the employee business mailbox.
- GmailCredential.updated_at: Timestamp for credential refresh or reauthorization.
- Score.analysis: Foreign key to the analysis being scored.
- Score.created_at: Database record creation time.
- Score.payload: Complete JSON snapshot for the corresponding protocol.
- Score.score_version: Scoring-rule version separating rules placeholders from formal Agent versions.
- Score.value: Nullable follow-up priority from 0 to 100.
"""
import uuid

from django.conf import settings
from django.db import models


# Function: Store a user-owned business mailbox and synchronization cursor.
# Logic: Backend creates mailbox ID and derives user identity from session or Agent credential.
# Constraints: OAuth credentials reside in a separate GmailCredential record; address is display-only.
class Mailbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    address = models.EmailField()
    sync_state = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=0)

    # Function: Constrain mailbox addresses to one per user.
    # Logic: Use a database uniqueness constraint for duplicate creation.
    # Constraints: Service layer normalizes addresses to lowercase.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "address"], name="crm_owner_mailbox")]


# Function: Store Gmail read-only credentials granted by employee web OAuth.
# Logic: Retain one current credential per business mailbox and let Agent service claim synchronization through protected interfaces.
# Constraints: Credentials never appear in browser mailbox lists; current MVP stores plaintext JSON in the database.
class GmailCredential(models.Model):
    mailbox = models.OneToOneField(
        Mailbox, related_name="gmail_credential", on_delete=models.CASCADE
    )
    credentials = models.JSONField(default=dict)
    authorized_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


# Function: Store Agent service credential digest limited to one user's business data.
# Logic: Authentication compares SHA-256 digest of a high-entropy token whose raw value is delivered only at creation.
# Constraints: Grants neither browser login nor cross-user access; deleting record revokes it.
class AgentCredential(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Store user-isolated company grouping and authoritative CRM snapshot.
# Logic: group_key uses a full company domain or public-mailbox contact address; revision identifies all context changes.
# Constraints: Does not infer subdomain or corporate multi-domain relationships automatically; external_version changes only with CRM snapshot.
class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    group_key = models.CharField(max_length=320)
    name = models.CharField(max_length=240, null=True, blank=True)
    domains = models.JSONField(default=list)
    crm_status = models.CharField(max_length=20, default="unregistered")
    customer = models.JSONField(default=dict)
    tickets = models.JSONField(default=list)
    quotes = models.JSONField(default=list)
    orders = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=0)
    external_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Isolate same-domain companies across users.
    # Logic: owner and group_key are jointly unique.
    # Constraints: Company merge requires a separately designed explicit service and does not directly modify group_key.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "group_key"], name="crm_owner_group")]


# Function: Store a contact identity in a company.
# Logic: Compute contact statistics from email queries to avoid independent counter drift.
# Constraints: Email cannot merge repeatedly across companies; names originate from evidenced email facts.
class Contact(models.Model):
    company = models.ForeignKey(Company, related_name="contacts", on_delete=models.CASCADE)
    email = models.EmailField()
    name = models.CharField(max_length=240, null=True, blank=True)

    # Function: Prevent duplicate contacts in one company.
    # Logic: Database constrains company and email.
    # Constraints: Grouping changes must handle contacts and messages together.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "email"], name="crm_company_contact")]


# Function: Store immutable normalized email with independent mutable classification and human-review metadata.
# Logic: payload retains source message; time and direction support queries, classification controls visibility, and review_revision protects concurrent decisions.
# Constraints: dedupe_key is globally unique and matches authorized mailbox and message ID; review cannot rewrite source text and facts version in a separate table.
class Email(models.Model):
    business_classification = models.CharField(max_length=24, default="business")
    classification_source = models.CharField(max_length=12, default="rule")
    classification_reason = models.TextField(blank=True, default="")
    review_status = models.CharField(max_length=32, blank=True, default="")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="reviewed_customer_emails")
    reviewed_at = models.DateTimeField(null=True)
    review_revision = models.PositiveIntegerField(default=0)
    dedupe_key = models.CharField(max_length=400, primary_key=True)
    mailbox = models.ForeignKey(Mailbox, related_name="emails", on_delete=models.CASCADE)
    company = models.ForeignKey(Company, related_name="emails", on_delete=models.CASCADE)
    contact = models.ForeignKey(Contact, related_name="messages", on_delete=models.PROTECT, null=True)
    payload = models.JSONField()
    sent_at = models.DateTimeField()
    received_at = models.DateTimeField()
    direction = models.CharField(max_length=12)


# Function: Store versioned extraction result for one email.
# Logic: Prompt version and repair generation are jointly unique; ordinary generation still allows only failed-to-completed resubmission.
# Constraints: Human repair uses a new generation to retain old records and does not fabricate prompt version; current extraction is selected by creation ID.
class Extraction(models.Model):
    email = models.ForeignKey(Email, related_name="extractions", on_delete=models.CASCADE)
    prompt_version = models.CharField(max_length=100)
    repair_generation = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=30)
    facts = models.JSONField(null=True)
    error = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Limit extraction to one per email, prompt version, and repair generation.
    # Logic: Ordinary submissions use generation zero and human repair uses durable job ID.
    # Constraints: Does not determine newer records by lexical version-string order.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["email", "prompt_version", "repair_generation"], name="crm_extract_generation")]


# Function: Archive Agent-submitted analysis input and its backend revision.
# Logic: Save payload unchanged and let revision identify each business context; retain independent snapshots for identical content at different revisions.
# Constraints: Agent calculates input_version and backend does not redefine its hashing algorithm.
class AnalysisInput(models.Model):
    company = models.ForeignKey(Company, related_name="inputs", on_delete=models.CASCADE)
    input_version = models.CharField(max_length=160)
    revision = models.PositiveIntegerField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Ensure input-version snapshots cannot repeat within the same revision.
    # Logic: Company, input version, and context revision are jointly unique, supporting restoration of identical facts after human revocation.
    # Constraints: Same key with different payload at one revision returns conflict; old snapshots and invalidation records are not overwritten.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "input_version", "revision"], name="crm_input_revision")]


# Function: Store versioned L3 analysis source.
# Logic: Bind source through snapshot and use provider to explicitly distinguish rules placeholders from Agent.
# Constraints: Lists and detail project from the same payload and do not present placeholders as model conclusions.
class Analysis(models.Model):
    snapshot = models.ForeignKey(AnalysisInput, related_name="analyses", on_delete=models.CASCADE)
    prompt_version = models.CharField(max_length=100)
    payload = models.JSONField()
    provider = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    # Function: Retain distinct analysis prompt versions.
    # Logic: Snapshot and prompt version are jointly unique.
    # Constraints: Existing successful results cannot be overwritten by failures.
    class Meta:
        constraints = [models.UniqueConstraint(fields=["snapshot", "prompt_version"], name="crm_analysis_version")]


# Function: Store L4 score and its rule version.
# Logic: Bind a concrete analysis to prevent reuse of old scores after analysis prompt changes.
# Constraints: Score may be null; serializers perform contribution and validation.
class Score(models.Model):
    analysis = models.ForeignKey(Analysis, related_name="scores", on_delete=models.CASCADE)
    payload = models.JSONField()
    score_version = models.CharField(max_length=100)
    value = models.PositiveSmallIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


# Function: Store company-analysis job and atomic claim lease.
# Logic: Company revision changes can create successor jobs; running jobs bind claim credential and input revision.
# Constraints: Lease expiration fails explicitly without implicit retry; users can request analysis again.
class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, related_name="jobs", on_delete=models.CASCADE)
    trigger = models.CharField(max_length=40)
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="pending")
    enqueued_at = models.DateTimeField(auto_now_add=True)
    attempt = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    report = models.JSONField(null=True)


from .processing_models import EmailProcessingJob, MailboxSyncRun  # noqa: E402,F401
from .durable_models import ExtractionRepair, SnapshotInvalidation, SnapshotSource, StoredMessage, SyncCheckpoint  # noqa: E402,F401
from .qq_models import QQCredential, QQSyncCheckpoint  # noqa: E402,F401
