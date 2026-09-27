"""Responsibility: Provide HTTP entry points for the browser workspace and Agent pull protocol.
Implementation: Every business endpoint requires authenticated Session or Agent identity; laboratory settings cannot widen private ownership. Web validation enqueues work and runtime exposes QQ capability. CRM registration stores country and propagates industry changes; historical L1 upgrades are explicitly queued and details include compatibility state. Mailbox connections are limited to the current owner, session and Agent identities are isolated, and registered users enter onboarding from persistent state.
Relationships: sync_scope requires Gmail/QQ synchronization scope; urls registers routes; the frontend calls authorized business endpoints; sales records CRM-registration audits.
Directory:
- AgentAuthenticationSchema: Declare independent Agent-service authentication for OpenAPI.
- AgentAuthenticationSchema.get_security_definition: Return the security-scheme definition.
- LoginSerializer: Declare a login request.
- SessionView: Provide CSRF-protected browser session entry points.
- SessionView.get: Return session identity and a CSRF token.
- SessionView.post: Create an authenticated user session.
- SessionView.delete: Log out the current session.
- validated: Run a serializer and return validated data.
- versioned: Generate a response with the backend revision.
- expected: Parse the integer version representation in If-Match.
- process_if_rules: Run a rules job for the configured provider or leave work for an independent Agent.
- CompanyViewSet: Provide company list, detail, registration, and explicit reanalysis.
- CompanyViewSet.list: Query the company list and statistics.
- CompanyViewSet.retrieve: Return all displayed data for the customer workspace.
- CompanyViewSet.analyze: Explicitly request company analysis and reject incompatible historical facts.
- CompanyViewSet.extraction_upgrade: Preview or explicitly queue historical-fact upgrades for a company.
- CompanyViewSet.register: Register a company in CRM and save sourced baseline data.
- MailboxViewSet: Manage the signed-in user's business mailboxes.
- MailboxViewSet.list: List the current user's mailboxes.
- MailboxViewSet.create: Create or reuse the current user's business mailbox.
- MailboxViewSet.gmail_authorize: Generate the current employee's Google OAuth URL.
- MailboxViewSet.gmail_callback: Complete an employee's Gmail authorization and return to the workspace.
- MailboxViewSet.request_sync: Request synchronization of the current employee's authorized mailbox.
- MailboxViewSet.disconnect_gmail: Remove the current employee's Gmail authorization.
- DemoViewSet: Provide runtime capability and explicit simulated-email entry points.
- DemoViewSet.runtime: Return runtime capabilities required by the frontend.
- DemoViewSet.email: Submit one manually simulated email.
- DemoViewSet.seed: Explicitly import independent synthetic demo material.
- AgentViewSet: Host backend protocol calls initiated by the Agent in the README.
- AgentViewSet.submit_emails: Receive a batch of standard emails.
- AgentViewSet.claim_mailbox_syncs: Claim Gmail synchronizations requested by employees in the browser.
- AgentViewSet.report_mailbox_sync: Report an employee Gmail synchronization result.
- AgentViewSet.resubmit_facts: Resubmit facts for failed emails.
- AgentViewSet.failed_extractions: Return failed-extraction deduplication keys or full redo input for one email.
- AgentViewSet.grouping: Read the company-grouping object.
- AgentViewSet.context: Read email and CRM business context.
- AgentViewSet.latest_analysis_input: Query the latest AnalysisInput at the current revision with still-valid laboratory provenance.
- AgentViewSet.cached_analysis: Query analysis-cache metadata.
- AgentViewSet.save_analysis_input: Save an L2 snapshot.
- AgentViewSet.save_analysis: Save an L3 analysis.
- AgentViewSet.save_score: Save an L4 score.
- AgentViewSet.get_sync_state: Read business-mailbox synchronization state.
- AgentViewSet.save_sync_state: Write the synchronization cursor.
- AgentViewSet.claim_jobs: Claim pending jobs.
- AgentViewSet.report_job: Report a job's final state.
Variable index:
- AgentAuthenticationSchema.name: Entity name; in application configuration it identifies the module import path.
- AgentAuthenticationSchema.target_class: Class path whose OpenAPI authentication description is extended.
- AgentViewSet.authentication_classes: Authentication policy for the independent Agent service.
- CompanyViewSet.queryset: Empty queryset that lets OpenAPI determine the company UUID path type.
- LoginSerializer.password: Write-only password used only for authentication.
- LoginSerializer.username: Browser login username.
- MailboxViewSet.queryset: Empty queryset that lets OpenAPI infer the mailbox UUID path type.
- OBJECT: Generic OpenAPI object response type.
- logger: Records session creation, failed authentication, logout, and configuration errors without credentials.
- SessionView.permission_classes: Endpoint access-permission policy.
- VERSION_HEADERS: Schema definitions for If-Match, job ID, and lease credentials.
"""
import logging
from urllib.parse import urlencode

from apps.accounts.models import SalesSetup
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import transaction
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import serializers as s
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed, NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from apps.sales.models import CompanySettings
from apps.sales.services import audit

from . import gmail_oauth, ingestion, jobs, results, rules, selectors
from .access import AgentAuthentication, InvalidState, check_version, company_for, mailbox_for
from common.laboratory import enabled, owner_scope
from .models import Company, Email, Mailbox
from .sync_scope import SyncRequestSerializer
from .response_schemas import (SubmissionResultSerializer, JobResponseSerializer, CachedAnalysisResponseSerializer,
                               GroupingResponseSerializer, CompanyContextResponseSerializer, MailboxResponseSerializer,
                               MailboxSyncClaimResponseSerializer)
from .serializers import (AnalysisInputSerializer, AnalysisSerializer, ClaimSerializer, EmailSubmissionSerializer,
                          FactsResubmissionSerializer, JobReportSerializer, MailboxSerializer, RegisterSerializer,
                          ScoreSerializer, SimulateSerializer, StrictSerializer, SyncStateSerializer,
                          MailboxSyncClaimSerializer, MailboxSyncReportSerializer)

OBJECT = OpenApiTypes.OBJECT
logger = logging.getLogger("salesmate.business")
VERSION_HEADERS = [OpenApiParameter("If-Match", int, OpenApiParameter.HEADER, required=True),
                   OpenApiParameter("X-Job-ID", str, OpenApiParameter.HEADER, required=True),
                   OpenApiParameter("X-Lease-Token", str, OpenApiParameter.HEADER, required=True)]


# Function: Declare independent Agent-service authentication for OpenAPI.
# Logic: Describe the custom Agent scheme with the Authorization header.
# Constraints: This declaration does not authenticate; AgentAuthentication performs the actual validation.
class AgentAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "apps.crm.access.AgentAuthentication"
    name = "agentCredential"

    # Function: Return the security-scheme definition.
    # Inputs: `auto_schema` is the schema-generation context.
    # Outputs: An OpenAPI apiKey scheme object.
    # Logic: Require the whole Authorization header to contain the Agent service token.
    # Constraints: Does not include actual credentials.
    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "Authorization", "description": "Agent <service-token>; never a Gmail token."}


# Function: Declare a login request.
# Logic: The password is a write-only field.
# Constraints: Django checks authentication results and the password is never logged.
class LoginSerializer(StrictSerializer):
    username = s.CharField()
    password = s.CharField(write_only=True, trim_whitespace=False)


# Function: Provide CSRF-protected browser session entry points.
# Logic: GET obtains CSRF and can establish a configured local session unless explicitly suppressed; POST validates login and DELETE preserves an anonymous logout marker.
# Constraints: Anonymous login also executes Django csrf_protect; it does not replace security validation with DRF anonymous-CSRF exemption.
@method_decorator(csrf_protect, name="dispatch")
class SessionView(APIView):
    permission_classes = [AllowAny]

    # Function: Return session identity and a CSRF token.
    # Inputs: `request` includes optional auto_login=false and an anonymous session suppression marker.
    # Outputs: Authentication state, username, CSRF token, debug_auto_login, and onboarding_required flags.
    # Logic: Require DEBUG, the configured switch, loopback, no logout marker, and no auto_login=false before establishing the configured ordinary-user session.
    # Constraints: Local auto-login neither creates users nor admits disabled or administrator accounts; laboratory settings never establish or replace authenticated identity.
    @extend_schema(responses=OBJECT, tags=["session"])
    def get(self, request):
        debug_auto_login = bool(settings.DEBUG and getattr(settings, "LOCAL_DEBUG_AUTO_LOGIN", False)
                                and request.META.get("REMOTE_ADDR") in {"127.0.0.1", "::1"}
                                and request.query_params.get("auto_login") != "false"
                                and not request.session.get("suppress_debug_auto_login", False))
        if debug_auto_login and not request.user.is_authenticated:
            user = get_user_model().objects.filter(
                username=settings.LOCAL_DEBUG_USER, is_active=True, is_staff=False, is_superuser=False,
            ).first()
            if user is None:
                logger.error("debug_session_unavailable action=check_LOCAL_DEBUG_USER_and_provision_active_non_admin_user")
                raise InvalidState("本地调试账号不可用，请检查 LOCAL_DEBUG_USER 并创建启用的普通账号。")
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            logger.info("debug_session_created user_id=%s", user.pk)
        return Response({"authenticated": request.user.is_authenticated,
                         "username": request.user.get_username() if request.user.is_authenticated else None,
                         "debug_auto_login": debug_auto_login,
                         "lab_open_access": enabled(),
                         "onboarding_required": bool(not enabled() and request.user.is_authenticated and SalesSetup.objects.filter(owner=request.user, completed=False).exists()),
                         "csrf_token": get_token(request)})

    # Function: Create an authenticated user session.
    # Inputs: `request`.data contains username and password.
    # Outputs: Username and a new CSRF token; authentication failure raises AuthenticationFailed.
    # Logic: Authenticate, rotate the session ID, clear the logout marker, and log the successful user ID; failures log no supplied identity or password.
    # Constraints: Does not create users; registration or provisioning creates accounts separately.
    @extend_schema(request=LoginSerializer, responses=OBJECT, tags=["session"])
    def post(self, request):
        data = validated(LoginSerializer, request.data)
        user = authenticate(request, username=data["username"], password=data["password"])
        if user is None:
            logger.warning("session_login_failed reason=invalid_credentials")
            raise AuthenticationFailed("用户名或密码不正确。")
        login(request, user)
        request.session.pop("suppress_debug_auto_login", None)
        logger.info("session_login_succeeded user_id=%s", user.pk)
        return Response({"authenticated": True, "username": user.get_username(), "csrf_token": get_token(request)})

    # Function: Log out the current session.
    # Inputs: `request` is a browser request; write requests require CSRF.
    # Outputs: Empty 204 response.
    # Logic: Flush the authenticated session, then retain only an anonymous marker preventing immediate debug auto-login; log the former user ID.
    # Constraints: Does not revoke independent Agent credentials or change global automatic-login settings; CSRF failure leaves identity intact.
    @extend_schema(responses={204: None}, tags=["session"])
    def delete(self, request):
        user_id = request.user.pk
        logout(request)
        request.session["suppress_debug_auto_login"] = True
        logger.info("session_logout_succeeded user_id=%s", user_id)
        return Response(status=204)


# Function: Run a serializer and return validated data.
# Inputs: `serializer_class` is a protocol class; `data` is the request payload.
# Outputs: validated_data.
# Logic: Field errors raise the standard DRF ValidationError.
# Constraints: Does not write to the database.
def validated(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


# Function: Generate a response carrying the backend revision.
# Inputs: `data` is the business response; `version` is the current integer version.
# Outputs: A Response with an ETag header.
# Logic: ETag uses the HTTP representation with double quotes.
# Constraints: Remove outer quotes from If-Match before comparison.
def versioned(data, version):
    return Response(data, headers={"ETag": f'"{version}"'})


# Function: Parse the integer version representation in If-Match.
# Inputs: `request` is an HTTP request.
# Outputs: A string without HTTP quotes, or None.
# Logic: Does not accept weak ETags or wildcards; the service performs concrete integer validation.
# Constraints: There is no default version, so writes fail when it is missing.
def expected(request):
    value = request.headers.get("If-Match")
    return value.strip('"') if value is not None else None


# Function: Run a rules job for the explicit provider or leave it for an independent Agent.
# Inputs: `owner` is the user; `company_id` identifies the company.
# Outputs: A rules-job report or None.
# Logic: rules executes the placeholder; agent only retains already-enqueued work.
# Constraints: Unknown configuration fails immediately and an Agent failure does not switch to rules.
def process_if_rules(owner, company_id):
    if settings.ANALYSIS_PROVIDER == "rules":
        return rules.run_company(owner, company_id)
    if settings.ANALYSIS_PROVIDER != "agent":
        raise InvalidState("ANALYSIS_PROVIDER 只能为 rules 或 agent。")
    return None


# Function: Provide company list, detail, registration, and explicit reanalysis.
# Logic: Filter every object by the private company owner scope; historical facts use a separate version-upgrade entry point.
# Constraints: Does not expose unverified sending or Gmail synchronization capability.
class CompanyViewSet(ViewSet):
    queryset = Company.objects.none()
    # Function: Query the company list and statistics.
    # Inputs: `request`.query_params contains industry, size, signal, keywords, and pagination.
    # Outputs: Paginated company projection.
    # Logic: Call the owner-scoped list selector regardless of experiment settings.
    # Constraints: The default IsAuthenticated rejects unauthenticated requests.
    @extend_schema(operation_id="companies_list", responses=OBJECT, tags=["companies"], parameters=[OpenApiParameter(name, str) for name in ["q", "industry", "size_band", "signal", "crm_status", "page", "page_size"]])
    def list(self, request):
        return Response(selectors.list_companies(Company.objects.filter(owner_scope(request.user)), request.query_params))

    # Function: Return all displayed data for the customer workspace.
    # Inputs: `request` is the current session; `pk` is the company UUID.
    # Outputs: List summary, grouping, emails, context, analysis, score, and fact-upgrade state.
    # Logic: A company row lock keeps this multi-table read consistent; GET itself creates no job.
    # Constraints: Analysis after opening the page is triggered by a separate POST.
    @extend_schema(operation_id="companies_retrieve", responses=OBJECT, tags=["companies"])
    @transaction.atomic
    def retrieve(self, request, pk=None):
        company = company_for(request.user, pk, lock=True)
        from .extraction_upgrades import upgrade_summary
        grouping, context = selectors.context_pair(company)
        analysis, score = selectors.latest_result(company)
        return versioned({**selectors.company_row(company), "grouping": grouping, "context": context,
                          "analysis": analysis.payload if analysis else None, "score_detail": score.payload if score else None,
                          "extraction_upgrade": upgrade_summary(company)}, company.revision)

    # Function: Explicitly request company analysis.
    # Inputs: `request` is an authenticated user; `pk` is the company UUID.
    # Outputs: Job ID, provider, and current job status.
    # Logic: Preflight against the shared fact contract before enqueueing; on incompatibility, direct callers to the web and tool upgrade entries, while rules processing retains the company’s original owner.
    # Constraints: Failure never returns false success; agent mode only enqueues work.
    @extend_schema(request=None, responses=OBJECT, tags=["companies"])
    @action(detail=True, methods=["post"])
    def analyze(self, request, pk=None):
        with transaction.atomic():
            company = company_for(request.user, pk, lock=True)
            if not company.emails.filter(business_classification="business").exists():
                raise InvalidState("没有已确认业务邮件，不能生成客户画像。")
            from .extraction_upgrades import upgrade_summary
            if settings.ANALYSIS_PROVIDER == "agent" and upgrade_summary(company)["incompatible_emails"]:
                raise InvalidState("客户含不兼容的旧版邮件事实，请点击“升级邮件事实”，或调用 customers.upgrade_extractions / extraction-upgrade 接口后再分析。")
            job = jobs.enqueue(company, "customer_detail_opened")
        process_if_rules(company.owner, company.pk)
        job.refresh_from_db()
        return Response({"job_id": str(job.pk), "status": job.status, "provider": settings.ANALYSIS_PROVIDER})

    # Function: Preview or explicitly queue historical L1 upgrades for the current customer.
    # Inputs: `request` is a session request; POST requires an empty body and If-Match; `pk` is the customer UUID.
    # Outputs: Version distribution and remediation progress; POST returns 202 and a new ETag.
    # Logic: GET is read-only; POST delegates atomic queueing to the service, and the Worker re-extracts from the persisted body.
    # Constraints: Does not alter old facts or refetch mailboxes; unauthorized access returns 404 and a stale version returns 409.
    @extend_schema(request=None, responses=OBJECT, tags=["companies"], parameters=[OpenApiParameter("If-Match", str, location=OpenApiParameter.HEADER)])
    @action(detail=True, methods=["get", "post"], url_path="extraction-upgrade")
    @transaction.atomic
    def extraction_upgrade(self, request, pk=None):
        from .extraction_upgrades import queue_upgrades, upgrade_summary
        if request.method == "POST":
            if request.data:
                raise ValidationError("升级请求不接受事实或模型参数，请使用空正文。")
            data = queue_upgrades(request.user, pk, expected(request))
            return Response(data, status=202, headers={"ETag": f'"{data["revision"]}"'})
        company = company_for(request.user, pk, lock=True)
        return versioned(upgrade_summary(company), company.revision)

    # Function: Register a company in CRM and save sourced baseline data.
    # Inputs: `request` contains RegisterSerializer data and If-Match; `pk` is the company UUID.
    # Outputs: The new company projection and revision.
    # Logic: Retrieve the company under access-mode rules, save CRM fields, and audit the change; settings records and rules processing use the company’s original owner, and an industry change refreshes the matching owner’s scoring context.
    # Constraints: A non-null employee count requires a source; authoritative fields are never guessed from email, and a missing country retains its prior value.
    @extend_schema(request=RegisterSerializer, responses=OBJECT, parameters=VERSION_HEADERS[:1], tags=["companies"])
    @action(detail=True, methods=["post"])
    def register(self, request, pk=None):
        data = validated(RegisterSerializer, request.data)
        if data["employee_count"] is not None and not data["employee_count_source"]:
            raise ValidationError("人数非空时必须填写来源。")
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            company = company_for(request.user, pk, lock=True)
            check_version(expected(request), company.revision)
            previous_industry = company.customer.get("industry_from_crm")
            if CompanySettings.objects.filter(company=company, archived=True).exists():
                raise InvalidState("客户已归档，请先恢复后编辑。")
            company.name, company.crm_status = data["company_name"], "registered"
            company.customer = {**company.customer, "customer_id": str(company.pk), **{key: value for key, value in data.items() if key != "company_name"}}
            company.revision += 1
            company.external_version += 1
            company.save(update_fields=["name", "crm_status", "customer", "revision", "external_version"])
            company_settings, _ = CompanySettings.objects.get_or_create(company=company, defaults={"owner": company.owner})
            audit(request.user, company_settings, "company_registered", {"fields": sorted(data)})
            jobs.enqueue(company, "external_updated")
            if previous_industry != company.customer.get("industry_from_crm"):
                from apps.sales.priority import refresh_owner_priority
                refresh_owner_priority(company.owner_id, exclude=[company.pk])
        process_if_rules(company.owner, company.pk)
        company.refresh_from_db()
        return versioned(selectors.company_row(company), company.revision)


# Function: Manage the current signed-in employee’s Gmail connections and synchronization state.
# Logic: The OAuth callback verifies the actual account, and every read and write is isolated by request.user.
# Constraints: The browser never receives a Google access token or refresh token.
class MailboxViewSet(ViewSet):
    queryset = Mailbox.objects.none()
    # Function: List the current user’s mailboxes.
    # Inputs: `request` provides the session user.
    # Outputs: Mailbox IDs, addresses, and SyncState array.
    # Logic: Mailbox connections always query only the current owner, preventing laboratory shared mailboxes from appearing as personal connections.
    # Constraints: Returns no authorization tokens.
    @extend_schema(responses=MailboxResponseSerializer(many=True), tags=["mailboxes"])
    def list(self, request):
        mailboxes = Mailbox.objects.select_related("gmail_credential").filter(
            owner=request.user
        )
        return Response([gmail_oauth.mailbox_status(item) for item in mailboxes])

    # Function: Create or reuse the current user’s business mailbox.
    # Inputs: `request`.data contains address.
    # Outputs: Mailbox identifier and address.
    # Logic: Lowercase the address and create it uniquely per owner.
    # Constraints: Does not request Gmail or claim that synchronization occurred.
    @extend_schema(request=MailboxSerializer, responses=OBJECT, tags=["mailboxes"])
    def create(self, request):
        data = validated(MailboxSerializer, request.data)
        mailbox, created = Mailbox.objects.get_or_create(owner=request.user, address=data["address"].lower())
        return Response(gmail_oauth.mailbox_status(mailbox), status=201 if created else 200)

    # Function: Generate a Google OAuth redirect URL for the current employee.
    # Inputs: `request` is an authenticated browser session carrying a CSRF token.
    # Outputs: authorization_url.
    # Logic: Store state in that employee’s browser session; create the Mailbox binding only after the callback.
    # Constraints: Requests only gmail.readonly permission.
    @extend_schema(request=None, responses=OBJECT, tags=["mailboxes"])
    @action(detail=False, methods=["post"], url_path="gmail-authorize")
    def gmail_authorize(self, request):
        return Response({"authorization_url": gmail_oauth.begin_authorization(request)})

    # Function: Complete the current employee’s Google OAuth and await synchronization-scope selection.
    # Inputs: `request` contains Google-returned code, state, and the current employee session.
    # Outputs: Redirect to the workspace carrying the authorization result.
    # Logic: The backend exchanges credentials, reads the Gmail profile, and binds the real mailbox; after the employee chooses scope, the frontend queues work separately.
    # Constraints: Does not create an unverified mailbox connection on failure.
    @extend_schema(responses={302: None}, tags=["mailboxes"])
    @action(detail=False, methods=["get"], url_path="gmail-callback")
    def gmail_callback(self, request):
        try:
            mailbox = gmail_oauth.finish_authorization(request)
            query = urlencode({"gmail": "authorized", "address": mailbox.address})
        except Exception as error:
            # During local development, retain the full stack trace to distinguish state, token exchange, and
            # Gmail API call failures; logs do not deliberately emit authorization codes or credentials.
            logger.exception(
                "Gmail OAuth callback failed: %s: %s",
                type(error).__name__,
                error,
            )
            query_data = {"gmail": "error"}
            if settings.DEBUG:
                query_data["reason"] = (
                    request.query_params.get("error") or type(error).__name__
                )
            query = urlencode(query_data)
        return redirect(f"/?{query}")

    # Function: Let the current employee request refresh of their Gmail or QQ mail.
    # Inputs: `request` is the current employee request with required sync_options; `pk` is the mailbox_id in the URL.
    # Outputs: Latest connection and synchronization state without credentials.
    # Logic: Create a persistent batch and return HTTP 202 for an independent Worker to claim.
    # Constraints: Cannot request another employee’s or an unauthorized mailbox.
    @extend_schema(request=SyncRequestSerializer, responses={202: OBJECT}, tags=["mailboxes"])
    @action(detail=True, methods=["post"], url_path="request-sync")
    def request_sync(self, request, pk=None):
        serializer = SyncRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mailbox = gmail_oauth.request_mailbox_sync(request.user, pk, serializer.validated_data.get("sync_options"))
        return Response(mailbox, status=202)

    # Function: Remove the current employee’s local Gmail authorization.
    # Inputs: `request` is the current employee request; `pk` is the mailbox_id in the URL.
    # Outputs: authorization_required state.
    # Logic: Delete credentials while retaining synchronized emails and business analysis.
    # Constraints: Does not delete historical customers or emails.
    @extend_schema(responses=MailboxResponseSerializer, tags=["mailboxes"])
    @action(detail=True, methods=["delete"], url_path="gmail-authorization")
    def disconnect_gmail(self, request, pk=None):
        return Response(gmail_oauth.disconnect_mailbox(request.user, pk))


# Function: Provide runtime capability and explicit simulated-email entry points.
# Logic: Simulated data uses the production email-ingestion and analysis services.
# Constraints: Only rules mode may write demo data; agent mode rejects it without fallback.
class DemoViewSet(ViewSet):
    # Function: Return runtime capabilities required by the frontend.
    # Inputs: `request` is an authenticated-user request.
    # Outputs: Provider, timezone, simulation availability, QQ capability flag, and version.
    # Logic: Read explicit configuration and do not change mode automatically after probing.
    # Constraints: Count only the current signed-in employee’s authorized connections.
    @extend_schema(responses=OBJECT, tags=["demo"])
    @action(detail=False, methods=["get"])
    def runtime(self, request):
        return Response({"provider": settings.ANALYSIS_PROVIDER, "simulation_enabled": settings.ANALYSIS_PROVIDER == "rules", "gmail_connected": Mailbox.objects.filter(owner=request.user, gmail_credential__isnull=False).exists(),
                         "timezone": settings.TIME_ZONE, "qq_enabled": settings.QQ_MAIL_ENABLED,
                         "analysis_version": rules.ANALYSIS_VERSION if settings.ANALYSIS_PROVIDER == "rules" else None})

    # Function: Submit one manually simulated email.
    # Inputs: `request`.data contains business mailbox, sender, subject, and body.
    # Outputs: Email-ingestion result and processed company ID.
    # Logic: After rules extraction, use the production submission, Job, snapshot, and results services.
    # Constraints: Does not send email or read external mailboxes; email source is synthetic_sample.
    @extend_schema(request=SimulateSerializer, responses=SubmissionResultSerializer(many=True), tags=["demo"])
    @action(detail=False, methods=["post"], url_path="email")
    def email(self, request):
        if settings.ANALYSIS_PROVIDER != "rules":
            raise InvalidState("当前为 Agent 模式，模拟入口已关闭。")
        data = validated(SimulateSerializer, request.data)
        mailbox = mailbox_for(request.user, data["mailbox_id"])
        result = ingestion.submit_emails(request.user, [rules.extract_email(mailbox, data["sender"], data["subject"], data["body_text"])])
        process_if_rules(request.user, result[0]["company_id"])
        return Response(result, status=201)

    # Function: Explicitly import independent synthetic demo material.
    # Inputs: `request` is the user request made by selecting import sample.
    # Outputs: Counts of created emails and companies.
    # Logic: Fixed sample IDs ensure repeated import does not change existing emails or times; the first import generates the current time.
    # Constraints: Does not modify the research database or experiment split, and does not create real business transactions.
    @extend_schema(request=None, responses=OBJECT, tags=["demo"])
    @action(detail=False, methods=["post"])
    def seed(self, request):
        if settings.ANALYSIS_PROVIDER != "rules":
            raise InvalidState("当前为 Agent 模式，模拟入口已关闭。")
        mailbox, _ = Mailbox.objects.get_or_create(owner=request.user, address="sales@salesmate.example")
        samples = [
            ("lin@aurora.example", "新产线光学检测设备询价", "公司：曙光光学\n联系人：林悦\n职位：采购经理\n行业：光学检测\n需求：采购镜片外观检测设备\n数量：12 台\n预算：上限 30 万\n交期：希望下月交付\n决策流程：采购经理提交技术总监审批\n顾虑：需要先确认检测精度"),
            ("lin@aurora.example", "预算调整，请保留原定数量", "预算：预算调整为 26 万\n数量：12 台\n顾虑：预算尚待最终审批"),
            ("chen@precision.example", "精密量测项目初步咨询", "公司：衡准科技\n联系人：陈默\n行业：精密量测\n需求：采购精密量测设备\n顾虑：需要了解与现有产线的兼容性"),
            ("liaison@gmail.com", "后续联系", "联系人：许宁\n感谢沟通，收到资料后再联系。"),
        ]
        payloads = []
        for index, (sender, subject, body) in enumerate(samples):
            message_id = f"demo-v1-{index}"
            if not Email.objects.filter(pk=f"{mailbox.address.casefold()}:{message_id}").exists():
                payloads.append(rules.extract_email(mailbox, sender, subject, body, message_id))
        inserted = ingestion.submit_emails(request.user, payloads) if payloads else []
        companies = {item["company_id"] for item in inserted}
        for company_id in sorted(companies):
            process_if_rules(request.user, company_id)
        return Response({"created_emails": len(inserted), "affected_companies": len(companies), "source": "synthetic_sample"})


# Function: Host backend protocol calls initiated by the Agent in the README.
# Logic: Independent AgentAuthentication validates service identity in every environment; private entity reads remain owner scoped.
# Constraints: Browser sessions cannot call these routes in production mode; OAuth credential claims always require an Agent service token.
class AgentViewSet(ViewSet):
    authentication_classes = [AgentAuthentication]

    # Function: Claim Gmail synchronization work requested by the employee belonging to the current credential.
    # Inputs: `request`.data contains limit; each call handles at most ten mailboxes.
    # Outputs: Mailbox identifier, address, Google authorization information, and read limit.
    # Logic: Change sync_requested to sync_running before returning the work to a one-shot Agent.
    # Constraints: Full Google credentials are returned only through an AgentAuthentication route.
    @extend_schema(request=MailboxSyncClaimSerializer, responses=MailboxSyncClaimResponseSerializer(many=True), tags=["agent"])
    @action(detail=False, methods=["post"], url_path="mailbox-syncs/claim")
    def claim_mailbox_syncs(self, request):
        data = validated(MailboxSyncClaimSerializer, request.data)
        return Response(gmail_oauth.claim_mailbox_syncs(request.user, data["limit"]))

    # Function: Save a successful or failed employee-mailbox synchronization result.
    # Inputs: `request`.data contains mailbox_id, status, synchronization summary, and optional refreshed credentials.
    # Outputs: Browser-visible mailbox state without credentials.
    # Logic: On success write last_synced_at; on failure retain a displayable error.
    # Constraints: Does not retry automatically or poll continuously.
    @extend_schema(request=MailboxSyncReportSerializer, responses=MailboxResponseSerializer, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="mailbox-syncs/report")
    def report_mailbox_sync(self, request):
        data = validated(MailboxSyncReportSerializer, request.data)
        return Response(gmail_oauth.report_mailbox_sync(request.user, data))

    # Function: Receive a batch of standard emails.
    # Inputs: `request`.data is an EmailSubmission array.
    # Outputs: Creation or deduplication status for every email.
    # Logic: Call the atomic batch-ingestion service.
    # Constraints: Does not trigger the rules placeholder; jobs wait for the Agent to claim them.
    @extend_schema(request=EmailSubmissionSerializer(many=True), responses=SubmissionResultSerializer(many=True), tags=["agent"])
    @action(detail=False, methods=["post"], url_path="emails")
    def submit_emails(self, request):
        return Response(ingestion.submit_emails(request.user, request.data))

    # Function: Resubmit facts for failed emails.
    # Inputs: `request`.data is FactsResubmission.
    # Outputs: New company revision.
    # Logic: Delegate to the one-time failed-to-success transition service.
    # Constraints: Completed records cannot be resubmitted.
    @extend_schema(request=FactsResubmissionSerializer, responses=OBJECT, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="facts")
    def resubmit_facts(self, request):
        return Response(ingestion.resubmit_facts(request.user, request.data))

    # Function: Return deduplication keys for failed extraction or full redo input for one email.
    # Inputs: `request` queries mailbox_id and optionally dedupe_key.
    # Outputs: An array of failed keys, or an email containing current facts and the complete body.
    # Logic: Verify mailbox ownership before reading the current extraction state.
    # Constraints: Does not search for the same dedupe_key across mailboxes.
    @extend_schema(responses={200: {"oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "object"}]}}, tags=["agent"], parameters=[OpenApiParameter("mailbox_id", str, required=True), OpenApiParameter("dedupe_key", str)])
    @action(detail=False, methods=["get"], url_path="failed-extractions")
    def failed_extractions(self, request):
        mailbox = mailbox_for(request.user, request.query_params.get("mailbox_id"))
        query = mailbox.emails.prefetch_related("extractions")
        if request.query_params.get("dedupe_key"):
            email = query.filter(pk=request.query_params["dedupe_key"]).first()
            if email is None:
                raise NotFound("邮件不存在。")
            return Response(selectors.email_data(email))
        return Response([item.pk for item in query if selectors.latest_extraction(item).status == "failed"])

    # Function: Read the company-grouping object.
    # Inputs: `request`.query_params.company_id is the company UUID.
    # Outputs: Grouping and ETag revision.
    # Logic: Construct a consistent projection under the company row lock.
    # Constraints: Subsequent context must verify the same ETag.
    @extend_schema(responses=GroupingResponseSerializer, tags=["agent"], parameters=[OpenApiParameter("company_id", str, required=True)])
    @action(detail=False, methods=["get"])
    @transaction.atomic
    def grouping(self, request):
        company = company_for(request.user, request.query_params.get("company_id"), lock=True)
        return versioned(selectors.context_pair(company)[0], company.revision)

    # Function: Read email and CRM business context.
    # Inputs: `request`.query_params.company_id and optional If-Match ensure the same version as Grouping.
    # Outputs: CompanyContext and ETag.
    # Logic: Hold the company lock while reading all email and the external business snapshot.
    # Constraints: A stale version returns a conflict instead of mixing two reads.
    @extend_schema(responses=CompanyContextResponseSerializer, tags=["agent"], parameters=[OpenApiParameter("company_id", str, required=True), *VERSION_HEADERS[:1]])
    @action(detail=False, methods=["get"])
    @transaction.atomic
    def context(self, request):
        company = company_for(request.user, request.query_params.get("company_id"), lock=True)
        check_version(expected(request), company.revision)
        return versioned(selectors.context_pair(company)[1], company.revision)

    # Function: Query the latest AnalysisInput at the current revision whose laboratory provenance remains valid.
    # Inputs:`request`.query_params.company_id。
    # Outputs: Unchanged snapshot and ETag; returns 404 when no current snapshot exists.
    # Logic: Exclude snapshots whose business lineage is invalidated or whose laboratory source changed.
    # Constraints: Does not present an old snapshot as the detail page’s current input.
    @extend_schema(responses=AnalysisInputSerializer, tags=["agent"], parameters=[OpenApiParameter("company_id", str, required=True)])
    @action(detail=False, methods=["get"], url_path="latest-analysis-input")
    def latest_analysis_input(self, request):
        company = company_for(request.user, request.query_params.get("company_id"))
        snapshot = company.inputs.filter(revision=company.revision, invalidation__isnull=True).order_by("-id").first()
        from .enrichment import snapshot_current
        if snapshot is None or not snapshot_current(snapshot, company):
            raise NotFound("当前上下文尚未归并。")
        return versioned(snapshot.payload, company.revision)

    # Function: Query analysis-cache metadata.
    # Inputs: `request` contains company_id, input_version, and optional analysis_prompt_version.
    # Outputs:CachedAnalysis。
    # Logic: hit=true only when the current revision, input, and prompt version match.
    # Constraints: A cache miss does not invoke rules or a model.
    @extend_schema(responses=CachedAnalysisResponseSerializer, tags=["agent"], parameters=[OpenApiParameter(name, str, required=name != "analysis_prompt_version") for name in ["company_id", "input_version", "analysis_prompt_version"]])
    @action(detail=False, methods=["get"], url_path="cached-analysis")
    def cached_analysis(self, request):
        company = company_for(request.user, request.query_params.get("company_id"))
        if not request.query_params.get("input_version"):
            raise ValidationError("input_version 必填。")
        return Response(results.cached_analysis(company, request.query_params["input_version"], request.query_params.get("analysis_prompt_version")))

    # Function: Save an L2 snapshot.
    # Inputs: `request`.data is AnalysisInput; headers contain version and claim credentials.
    # Outputs: The payload archived unchanged.
    # Logic: The unified service validates provenance, revision, and lease.
    # Constraints: Does not allow an old job to overwrite current input.
    @extend_schema(request=AnalysisInputSerializer, responses=AnalysisInputSerializer, parameters=VERSION_HEADERS, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="analysis-inputs")
    def save_analysis_input(self, request):
        return Response(results.save_input(request.user, request.data, expected(request), request.headers.get("X-Job-ID"), request.headers.get("X-Lease-Token")))

    # Function: Save an L3 analysis.
    # Inputs: `request`.data is Analysis; headers contain version and claim credentials.
    # Outputs: Saved analysis.
    # Logic: Force provider=agent and have the service verify source provenance.
    # Constraints: The client cannot label provenance as another provider.
    @extend_schema(request=AnalysisSerializer, responses=AnalysisSerializer, parameters=VERSION_HEADERS, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="analyses")
    def save_analysis(self, request):
        return Response(results.save_analysis(request.user, request.data, expected(request), request.headers.get("X-Job-ID"), request.headers.get("X-Lease-Token")))

    # Function: Save an L4 score.
    # Inputs: `request`.data is Score; headers contain version and claim credentials.
    # Outputs: Saved Score.
    # Logic: Bind the current successful analysis and verify the contribution sum.
    # Constraints: Strictly distinguish a missing score from a zero score.
    @extend_schema(request=ScoreSerializer, responses=ScoreSerializer, parameters=VERSION_HEADERS, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="scores")
    def save_score(self, request):
        return Response(results.save_score(request.user, request.data, expected(request), request.headers.get("X-Job-ID"), request.headers.get("X-Lease-Token")))

    # Function: Read business-mailbox synchronization state.
    # Inputs:`request`.query_params.mailbox_id。
    # Outputs: SyncState and ETag version.
    # Logic: Read the business cursor after authorization.
    # Constraints: Does not read Gmail tokens.
    @extend_schema(responses=SyncStateSerializer, tags=["agent"], parameters=[OpenApiParameter("mailbox_id", str, required=True)])
    @action(detail=False, methods=["get"], url_path="sync-state")
    def get_sync_state(self, request):
        mailbox = mailbox_for(request.user, request.query_params.get("mailbox_id"))
        return versioned(ingestion.sync_state(mailbox), mailbox.version)

    # Function: Write the synchronization cursor.
    # Inputs: `request`.data is SyncState, and If-Match identifies the prior version.
    # Outputs: State after incrementing.
    # Logic: Call the mailbox row-lock and optimistic-lock service.
    # Constraints: The Agent is responsible for ensuring all preceding email submissions succeeded.
    @extend_schema(request=SyncStateSerializer, responses=SyncStateSerializer, parameters=VERSION_HEADERS[:1], tags=["agent"])
    @action(detail=False, methods=["post"], url_path="sync-state-save")
    def save_sync_state(self, request):
        data = validated(SyncStateSerializer, request.data)
        state = ingestion.save_sync_state(request.user, data, expected(request))
        return versioned(state, state["version"])

    # Function: Claim pending jobs.
    # Inputs: `request`.data contains limit and lease_seconds.
    # Outputs: Job array including claim credentials and expected_version extension.
    # Logic: Claim under a row lock; return an empty array when no work exists.
    # Constraints: Only jobs belonging to that service credential’s owner.
    @extend_schema(request=ClaimSerializer, responses=JobResponseSerializer(many=True), tags=["agent"])
    @action(detail=False, methods=["post"], url_path="jobs/claim")
    def claim_jobs(self, request):
        data = validated(ClaimSerializer, request.data)
        return Response(jobs.claim(request.user, data["limit"], data["lease_seconds"]))

    # Function: Report a job’s final state.
    # Inputs: `request`.data is JobReport and X-Lease-Token is the claim credential.
    # Outputs: Persisted state.
    # Logic: Validate the lease and successful output; retain error state on failure.
    # Constraints: Does not retry automatically or treat a report as evidence that output was saved.
    @extend_schema(request=JobReportSerializer, responses=OBJECT, parameters=VERSION_HEADERS[2:], tags=["agent"])
    @action(detail=False, methods=["post"], url_path="jobs/report")
    def report_job(self, request):
        return Response(jobs.report(request.user, validated(JobReportSerializer, request.data), request.headers.get("X-Lease-Token")))
