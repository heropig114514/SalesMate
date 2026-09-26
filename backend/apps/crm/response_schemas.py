"""Responsibility: Declare consumable OpenAPI structures for Agent queries and batch responses.
Implementation: Independent response serializers describe actual arrays and objects; CompanyContext describes separate scoring context and mailbox-claim responses include mandatory frozen scope and retry IDs.
Relationships: views uses these as Schema declarations, and contract tests validate service fields in practice.
Directory:
- SubmissionResultSerializer: Describe submission result for one email.
- JobResponseSerializer: Describe a claimed Job and concurrency extension fields.
- CachedAnalysisResponseSerializer: Describe analysis-cache metadata.
- GroupingResponseSerializer: Describe backend company grouping result.
- CompanyContextResponseSerializer: Describe company emails and business snapshot.
- MailboxResponseSerializer: Describe a browser mailbox-list row.
- MailboxSyncClaimResponseSerializer: Describe employee mailbox synchronization request claimed by Agent.
Variable index:
- CompanyContextResponseSerializer.company_enrichment: Backend-matched experiment data, status, and source version without additional Tool authorization.
- CompanyContextResponseSerializer.priority_context: Formal company-level customer, deal, and seller scoring data; unknown fields remain absent.
- SubmissionResultSerializer.dedupe_key: Natural key of email submitted this time.
- SubmissionResultSerializer.company_id: Company UUID assigned by backend grouping.
- SubmissionResultSerializer.status: created, updated, or duplicate submission result.
- JobResponseSerializer.job_id: Backend job UUID.
- JobResponseSerializer.company_id: Backend UUID of company awaiting analysis.
- JobResponseSerializer.trigger: Business event that created the job.
- JobResponseSerializer.enqueued_at: Job queue time.
- JobResponseSerializer.attempt: Number of claims.
- JobResponseSerializer.lease_until: Claim-lease expiration time.
- JobResponseSerializer.lease_token: Random credential the claimant must submit on write.
- JobResponseSerializer.expected_version: Backend context revision frozen by job.
- CachedAnalysisResponseSerializer.company_id: UUID of the company currently queried.
- CachedAnalysisResponseSerializer.input_version: Input version of a hit or legacy analysis.
- CachedAnalysisResponseSerializer.analysis_prompt_version: Prompt version of cached analysis, null without cache.
- CachedAnalysisResponseSerializer.generated_at: Analysis generation time, null without cache.
- CachedAnalysisResponseSerializer.status: Processing state in cache metadata.
- CachedAnalysisResponseSerializer.hit: Whether current revision, input, and requested prompt match.
- CachedAnalysisResponseSerializer.analysis: Complete L3 Analysis returned on hit, null on miss.
- GroupingResponseSerializer.company_id: Backend company UUID.
- GroupingResponseSerializer.company_name: Nullable company display name.
- GroupingResponseSerializer.crm_status: Company record status.
- GroupingResponseSerializer.domains: Array of confirmed full domains.
- GroupingResponseSerializer.contacts: Contact email, name, interaction count, and primary-contact flag.
- GroupingResponseSerializer.member_dedupe_keys: Complete natural-key set of company member emails.
- CompanyContextResponseSerializer.company_id: Backend company UUID.
- CompanyContextResponseSerializer.external_snapshot_version: CRM external business snapshot version.
- CompanyContextResponseSerializer.emails: Array of current standard emails and extraction facts.
- CompanyContextResponseSerializer.customer: Authoritative CRM base-field snapshot.
- CompanyContextResponseSerializer.tickets: Authoritative ticket array.
- CompanyContextResponseSerializer.quotes: Authoritative quote array.
- CompanyContextResponseSerializer.orders: Authoritative order array.
- MailboxResponseSerializer.mailbox_id: Backend business mailbox UUID.
- MailboxResponseSerializer.address: Business mailbox display address.
- MailboxResponseSerializer.gmail_authorized: Whether current mailbox completed Google OAuth.
- MailboxResponseSerializer.qq_authorized: Whether current mailbox verified a QQ IMAP authorization code.
- MailboxResponseSerializer.sync_state: Business synchronization cursor and state.
- MailboxSyncClaimResponseSerializer.authorization: Google authorized-user JSON returned only to Agent.
- MailboxSyncClaimResponseSerializer.mailbox_address: Mailbox address verified by Gmail profile.
- MailboxSyncClaimResponseSerializer.mailbox_id: Backend employee business mailbox UUID.
- MailboxSyncClaimResponseSerializer.sync_options: User selection, server-frozen time window, and this run's over-limit approval.
- MailboxSyncClaimResponseSerializer.message_ids: Explicit failed retry IDs; empty array for ordinary scoped synchronization.
- MailboxSyncClaimResponseSerializer.max_results: Gmail single-page read limit, not total batch count.
"""
from rest_framework import serializers as s

from .serializers import EmailSubmissionSerializer, SyncStateSerializer


# Function: Describe submission result for one email.
# Logic: Each item in the same response array carries its natural key, company ID, and creation state.
# Constraints: This declaration does not persist data; ingestion performs actual deduplication.
class SubmissionResultSerializer(s.Serializer):
    dedupe_key = s.CharField()
    company_id = s.UUIDField()
    status = s.ChoiceField(choices=["created", "updated", "duplicate"])


# Function: Describe a claimed Job and concurrency extension fields.
# Logic: Retain original README fields and add lease_token and expected_version.
# Constraints: Return credentials only to successfully authenticated claimants.
class JobResponseSerializer(s.Serializer):
    job_id = s.UUIDField()
    trigger = s.CharField()
    company_id = s.UUIDField()
    enqueued_at = s.DateTimeField()
    attempt = s.IntegerField(min_value=1)
    lease_until = s.DateTimeField()
    lease_token = s.UUIDField()
    expected_version = s.IntegerField(min_value=0)


# Function: Describe analysis-cache metadata.
# Logic: Retain hit state and nullable legacy analysis information.
# Constraints: results service determines actual hit state.
class CachedAnalysisResponseSerializer(s.Serializer):
    company_id = s.UUIDField()
    input_version = s.CharField()
    analysis_prompt_version = s.CharField(allow_null=True)
    generated_at = s.DateTimeField(allow_null=True)
    status = s.CharField()
    hit = s.BooleanField()
    analysis = s.DictField(allow_null=True)


# Function: Describe backend company grouping result.
# Logic: Contact and email-member scopes use the same ETag as CompanyContext.
# Constraints: Does not allow Agent to decide grouping again from this structure.
class GroupingResponseSerializer(s.Serializer):
    company_id = s.UUIDField()
    company_name = s.CharField(allow_null=True)
    crm_status = s.ChoiceField(choices=["registered", "unregistered"])
    domains = s.ListField(child=s.CharField())
    contacts = s.ListField(child=s.DictField())
    member_dedupe_keys = s.ListField(child=s.CharField())


# Function: Describe company emails and business snapshot.
# Logic: Emails echo L1 standard payload, priority_context supplies authoritative company opportunity and seller data, and company_enrichment independently identifies experiment origin.
# Constraints: Scoring context neither repeats emails nor mixes in persisted L2 payloads and does not invent unknown data.
class CompanyContextResponseSerializer(s.Serializer):
    company_id = s.UUIDField()
    external_snapshot_version = s.CharField()
    emails = EmailSubmissionSerializer(many=True)
    customer = s.DictField()
    tickets = s.ListField(child=s.DictField())
    quotes = s.ListField(child=s.DictField())
    orders = s.ListField(child=s.DictField())
    priority_context = s.DictField()
    company_enrichment = s.DictField(required=False)


# Function: Describe a browser mailbox-list row.
# Logic: Display business identity, independent Gmail/QQ authorization flags, and synchronization state without credentials.
# Constraints: Returned address does not mean Gmail OAuth is complete.
class MailboxResponseSerializer(s.Serializer):
    mailbox_id = s.UUIDField()
    address = s.EmailField()
    gmail_authorized = s.BooleanField()
    qq_authorized = s.BooleanField()
    sync_state = s.DictField()


# Function: Describe employee mailbox synchronization request claimed by Agent.
# Logic: Complete authorization credentials appear only in AgentAuthentication-protected responses; consumers must apply sync_options or explicit message_ids.
# Constraints: Browser mailbox interfaces must not use this structure.
class MailboxSyncClaimResponseSerializer(s.Serializer):
    mailbox_id = s.UUIDField()
    mailbox_address = s.EmailField()
    authorization = s.DictField()
    max_results = s.IntegerField(min_value=1, max_value=20)
    sync_options = s.DictField()
    message_ids = s.ListField(child=s.CharField(max_length=200))
