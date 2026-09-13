"""职责：提供浏览器工作台和 Agent Pull 协议的 HTTP 入口。
实现：Web 只排队，独立 Worker 执行；会话路由与 Agent 凭证路由隔离；本机调试可自动建立普通用户会话；校验后交给事务服务。
关联：urls 注册路由，frontend 调用授权业务入口；sales 记录客户建档审计。
目录：
- AgentAuthenticationSchema：为 OpenAPI 声明独立 Agent 服务认证。
- AgentAuthenticationSchema.get_security_definition：返回安全方案定义。
- LoginSerializer：声明登录请求。
- SessionView：提供受 CSRF 保护的浏览器会话入口。
- SessionView.get：返回会话身份和 CSRF token。
- SessionView.post：创建已认证用户会话。
- SessionView.delete：注销当前会话。
- validated：运行序列化器并返回验证后的数据。
- versioned：生成附后端 revision 的响应。
- expected：解析 If-Match 的整数版本表示。
- process_if_rules：按显式 provider 执行规则任务或留给独立 Agent。
- CompanyViewSet：提供公司列表、详情、建档和显式重分析。
- CompanyViewSet.list：查询公司列表及统计。
- CompanyViewSet.retrieve：返回客户工作区全部展示数据。
- CompanyViewSet.analyze：显式请求公司分析。
- CompanyViewSet.register：为公司建立 CRM 档案并保存带来源的基础资料。
- MailboxViewSet：管理登录用户的业务邮箱。
- MailboxViewSet.list：列出当前用户邮箱。
- MailboxViewSet.create：创建或复用当前用户的业务邮箱。
- MailboxViewSet.gmail_authorize：生成当前员工 Google OAuth 地址。
- MailboxViewSet.gmail_callback：完成员工 Gmail 授权并返回工作台。
- MailboxViewSet.request_sync：请求同步当前员工的已授权邮箱。
- MailboxViewSet.disconnect_gmail：移除当前员工 Gmail 授权。
- DemoViewSet：提供运行能力和显式模拟邮件入口。
- DemoViewSet.runtime：返回前端需要的运行能力。
- DemoViewSet.email：提交一封人工模拟邮件。
- DemoViewSet.seed：显式导入独立合成演示材料。
- AgentViewSet：承载 README 中 Agent 主动调用的后端协议。
- AgentViewSet.submit_emails：接收整批标准邮件。
- AgentViewSet.claim_mailbox_syncs：领取员工在网页请求的 Gmail 同步。
- AgentViewSet.report_mailbox_sync：回报员工 Gmail 同步结果。
- AgentViewSet.resubmit_facts：补交失败邮件事实。
- AgentViewSet.failed_extractions：返回失败抽取的去重键或单封邮件完整重做输入。
- AgentViewSet.grouping：读取公司归组对象。
- AgentViewSet.context：读取邮件与 CRM 业务上下文。
- AgentViewSet.latest_analysis_input：查询当前 revision 最新 AnalysisInput。
- AgentViewSet.cached_analysis：查询分析缓存元数据。
- AgentViewSet.save_analysis_input：保存 L2 快照。
- AgentViewSet.save_analysis：保存 L3 分析。
- AgentViewSet.save_score：保存 L4 评分。
- AgentViewSet.get_sync_state：读取业务邮箱同步状态。
- AgentViewSet.save_sync_state：写入同步游标。
- AgentViewSet.claim_jobs：领取待处理任务。
- AgentViewSet.report_job：回报任务最终状态。
变量索引：
- AgentAuthenticationSchema.name：实体名称；应用配置中表示模块导入路径
- AgentAuthenticationSchema.target_class：需要扩展 OpenAPI 认证描述的类路径
- AgentViewSet.authentication_classes：独立 Agent 服务认证策略
- CompanyViewSet.queryset：供 OpenAPI 确定公司 UUID 路径类型的空查询集
- LoginSerializer.password：仅用于身份认证的只写密码
- LoginSerializer.username：浏览器登录用户名
- MailboxViewSet.queryset：供 OpenAPI 推导邮箱 UUID 路径类型的空查询集
- OBJECT：OpenAPI 通用对象响应类型
- logger：记录本地开发会话创建和配置错误，不包含凭证。
- SessionView.permission_classes：接口访问权限策略
- VERSION_HEADERS：If-Match、任务 ID 与租约凭证的 Schema 定义
"""
import logging
from urllib.parse import urlencode

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
from .models import Company, Email, Mailbox
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


# 功能：为 OpenAPI 声明独立 Agent 服务认证。
# 逻辑：使用 Authorization 头描述自定义 Agent 方案。
# 约束：该声明不执行鉴权，实际校验在 AgentAuthentication。
class AgentAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "apps.crm.access.AgentAuthentication"
    name = "agentCredential"

    # 功能：返回安全方案定义。
    # 输入：`auto_schema` 为 Schema 生成上下文。
    # 输出：OpenAPI apiKey 方案对象。
    # 逻辑：要求整个 Authorization 头填写 Agent 加服务令牌。
    # 约束：不包含实际凭证。
    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "Authorization", "description": "Agent <service-token>; never a Gmail token."}


# 功能：声明登录请求。
# 逻辑：密码为只写字段。
# 约束：认证结果由 Django 检查，不记录密码。
class LoginSerializer(StrictSerializer):
    username = s.CharField()
    password = s.CharField(write_only=True, trim_whitespace=False)


# 功能：提供受 CSRF 保护的浏览器会话入口。
# 逻辑：GET 可按本机调试配置建立会话并获取 CSRF；POST 验证登录，DELETE 注销。
# 约束：匿名登录也执行 Django csrf_protect，不以 DRF 匿名 CSRF 豁免代替安全验证。
@method_decorator(csrf_protect, name="dispatch")
class SessionView(APIView):
    permission_classes = [AllowAny]

    # 功能：返回会话身份和 CSRF token。
    # 输入：`request` 为浏览器请求。
    # 输出：登录状态、用户名、CSRF 令牌和 debug_auto_login 标志。
    # 逻辑：DEBUG 与显式开关开启且直连来自回环地址时，为匿名请求建立指定普通用户会话。
    # 约束：保留已有身份；不创建用户，不接受停用或管理员账号；配置错误返回 409 并记录诊断。
    @extend_schema(responses=OBJECT, tags=["session"])
    def get(self, request):
        debug_auto_login = bool(settings.DEBUG and getattr(settings, "LOCAL_DEBUG_AUTO_LOGIN", False)
                                and request.META.get("REMOTE_ADDR") in {"127.0.0.1", "::1"})
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
                         "csrf_token": get_token(request)})

    # 功能：创建已认证用户会话。
    # 输入：`request`.data 包含 username 和 password。
    # 输出：用户名与新的 CSRF 令牌；认证失败抛 AuthenticationFailed。
    # 逻辑：使用 Django authenticate 与 login 轮换会话 ID。
    # 约束：不自动创建用户，用户通过管理命令 provision。
    @extend_schema(request=LoginSerializer, responses=OBJECT, tags=["session"])
    def post(self, request):
        data = validated(LoginSerializer, request.data)
        user = authenticate(request, username=data["username"], password=data["password"])
        if user is None:
            raise AuthenticationFailed("用户名或密码不正确。")
        login(request, user)
        return Response({"authenticated": True, "username": user.get_username(), "csrf_token": get_token(request)})

    # 功能：注销当前会话。
    # 输入：`request` 为浏览器请求，写请求须有 CSRF。
    # 输出：204 空响应。
    # 逻辑：清除 Django 会话。
    # 约束：不撤销独立 Agent 凭证。
    @extend_schema(responses={204: None}, tags=["session"])
    def delete(self, request):
        logout(request)
        return Response(status=204)


# 功能：运行序列化器并返回验证后的数据。
# 输入：`serializer_class` 为协议类；`data` 为请求载荷。
# 输出：validated_data。
# 逻辑：字段错误抛标准 DRF ValidationError。
# 约束：不写数据库。
def validated(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


# 功能：生成附后端 revision 的响应。
# 输入：`data` 为业务响应；`version` 为当前版本整数。
# 输出：含 ETag 头的 Response。
# 逻辑：ETag 使用带双引号的 HTTP 表示。
# 约束：If-Match 接收时去除外层引号再比较。
def versioned(data, version):
    return Response(data, headers={"ETag": f'"{version}"'})


# 功能：解析 If-Match 的整数版本表示。
# 输入：`request` 为 HTTP 请求。
# 输出：去除 HTTP 引号的字符串或 None。
# 逻辑：不接受弱 ETag 或通配符，具体整数验证由服务执行。
# 约束：无默认版本，缺失时写入失败。
def expected(request):
    value = request.headers.get("If-Match")
    return value.strip('"') if value is not None else None


# 功能：按显式 provider 执行规则任务或留给独立 Agent。
# 输入：`owner` 为用户；`company_id` 为公司标识。
# 输出：规则任务回报或 None。
# 逻辑：rules 执行占位，agent 只保留已入队工作。
# 约束：未知配置直接报错，Agent 故障不切换为规则。
def process_if_rules(owner, company_id):
    if settings.ANALYSIS_PROVIDER == "rules":
        return rules.run_company(owner, company_id)
    if settings.ANALYSIS_PROVIDER != "agent":
        raise InvalidState("ANALYSIS_PROVIDER 只能为 rules 或 agent。")
    return None


# 功能：提供公司列表、详情、建档和显式重分析。
# 逻辑：所有对象先按 Session 用户归属筛选。
# 约束：不开放未验证的发送和 Gmail 同步能力。
class CompanyViewSet(ViewSet):
    queryset = Company.objects.none()
    # 功能：查询公司列表及统计。
    # 输入：`request`.query_params 为行业、规模、信号、关键词和分页。
    # 输出：分页公司投影。
    # 逻辑：调用授权集合上的列表 selector。
    # 约束：未登录由默认 IsAuthenticated 拒绝。
    @extend_schema(operation_id="companies_list", responses=OBJECT, tags=["companies"], parameters=[OpenApiParameter(name, str) for name in ["q", "industry", "size_band", "signal", "crm_status", "page", "page_size"]])
    def list(self, request):
        return Response(selectors.list_companies(Company.objects.filter(owner=request.user), request.query_params))

    # 功能：返回客户工作区全部展示数据。
    # 输入：`request` 为当前会话；`pk` 为公司 UUID。
    # 输出：列表摘要、分组、邮件、上下文、分析和评分。
    # 逻辑：公司行锁保证本次多表读取的一致性，GET 本身不创建任务。
    # 约束：页面打开后的分析触发通过独立 POST 执行。
    @extend_schema(operation_id="companies_retrieve", responses=OBJECT, tags=["companies"])
    @transaction.atomic
    def retrieve(self, request, pk=None):
        company = company_for(request.user, pk, lock=True)
        grouping, context = selectors.context_pair(company)
        analysis, score = selectors.latest_result(company)
        return versioned({**selectors.company_row(company), "grouping": grouping, "context": context,
                          "analysis": analysis.payload if analysis else None, "score_detail": score.payload if score else None}, company.revision)

    # 功能：显式请求公司分析。
    # 输入：`request` 为已登录用户；`pk` 为公司 UUID。
    # 输出：任务 ID、provider 和当前任务状态。
    # 逻辑：拒绝无业务邮件公司，在事务中合并任务；agent 模式由独立 Worker 消费。
    # 约束：失败不会返回伪成功；agent 模式只入队。
    @extend_schema(request=None, responses=OBJECT, tags=["companies"])
    @action(detail=True, methods=["post"])
    def analyze(self, request, pk=None):
        with transaction.atomic():
            company = company_for(request.user, pk, lock=True)
            if not company.emails.filter(business_classification="business").exists():
                raise InvalidState("没有已确认业务邮件，不能生成客户画像。")
            job = jobs.enqueue(company, "customer_detail_opened")
        process_if_rules(request.user, company.pk)
        job.refresh_from_db()
        return Response({"job_id": str(job.pk), "status": job.status, "provider": settings.ANALYSIS_PROVIDER})

    # 功能：为公司建立 CRM 档案并保存带来源的基础资料。
    # 输入：`request` 含 RegisterSerializer 与 If-Match；`pk` 为公司 UUID。
    # 输出：新的公司投影和 revision。
    # 逻辑：先锁 owner 再锁公司，修改版本并记录审计；agent 任务持久排队，rules 在事务提交后计算。
    # 约束：人数非空必须有来源，不将邮件人数线索自动视为权威人数。
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
            if CompanySettings.objects.filter(company=company, archived=True).exists():
                raise InvalidState("客户已归档，请先恢复后编辑。")
            company.name, company.crm_status = data["company_name"], "registered"
            company.customer = {**company.customer, "customer_id": str(company.pk), **{key: value for key, value in data.items() if key != "company_name"}}
            company.revision += 1
            company.external_version += 1
            company.save(update_fields=["name", "crm_status", "customer", "revision", "external_version"])
            company_settings, _ = CompanySettings.objects.get_or_create(company=company, defaults={"owner": request.user})
            audit(request.user, company_settings, "company_registered", {"fields": sorted(data)})
            jobs.enqueue(company, "external_updated")
        process_if_rules(request.user, company.pk)
        company.refresh_from_db()
        return versioned(selectors.company_row(company), company.revision)


# 功能：管理当前登录员工自己的 Gmail 连接与同步状态。
# 逻辑：OAuth 回调验证实际账号，所有读取和写入都按 request.user 隔离。
# 约束：浏览器永远不接收 Google access token 或 refresh token。
class MailboxViewSet(ViewSet):
    queryset = Mailbox.objects.none()
    # 功能：列出当前用户邮箱。
    # 输入：`request` 提供会话用户。
    # 输出：邮箱 ID、地址及 SyncState 数组。
    # 逻辑：只查询 owner 匹配的记录。
    # 约束：不返回任何授权令牌。
    @extend_schema(responses=MailboxResponseSerializer(many=True), tags=["mailboxes"])
    def list(self, request):
        mailboxes = Mailbox.objects.select_related("gmail_credential").filter(
            owner=request.user
        )
        return Response([gmail_oauth.mailbox_status(item) for item in mailboxes])

    # 功能：创建或复用当前用户的业务邮箱。
    # 输入：`request`.data 含 address。
    # 输出：邮箱标识与地址。
    # 逻辑：地址小写后按 owner 唯一创建。
    # 约束：不请求 Gmail，不宣称已同步。
    @extend_schema(request=MailboxSerializer, responses=OBJECT, tags=["mailboxes"])
    def create(self, request):
        data = validated(MailboxSerializer, request.data)
        mailbox, created = Mailbox.objects.get_or_create(owner=request.user, address=data["address"].lower())
        return Response(gmail_oauth.mailbox_status(mailbox), status=201 if created else 200)

    # 功能：为当前员工生成 Google OAuth 跳转地址。
    # 输入：`request` 为已登录浏览器会话并携带 CSRF token。
    # 输出：authorization_url。
    # 逻辑：state 保存在该员工浏览器会话中，回调后才能建立 Mailbox 绑定。
    # 约束：只申请 gmail.readonly 权限。
    @extend_schema(request=None, responses=OBJECT, tags=["mailboxes"])
    @action(detail=False, methods=["post"], url_path="gmail-authorize")
    def gmail_authorize(self, request):
        return Response({"authorization_url": gmail_oauth.begin_authorization(request)})

    # 功能：完成当前员工 Google OAuth 并请求第一次同步。
    # 输入：`request` 含 Google 返回的 code、state 和当前员工会话。
    # 输出：重定向回工作台并携带授权结果。
    # 逻辑：后端换取凭证、读取 Gmail profile、绑定真实邮箱并持久化同步批次，由独立 Worker 消费。
    # 约束：失败时不建立未经验证的邮箱连接。
    @extend_schema(responses={302: None}, tags=["mailboxes"])
    @action(detail=False, methods=["get"], url_path="gmail-callback")
    def gmail_callback(self, request):
        try:
            mailbox = gmail_oauth.finish_authorization(request)
            query = urlencode({"gmail": "authorized", "address": mailbox.address})
        except Exception as error:
            # 本地开发阶段保留完整堆栈，便于区分 state、令牌交换和
            # Gmail API 调用失败；日志中不主动输出授权码或凭证。
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

    # 功能：让当前员工请求刷新自己的 Gmail 邮件。
    # 输入：`request` 为当前员工请求，`pk` 为 URL 中的 mailbox_id。
    # 输出：不含凭证的最新连接及同步状态。
    # 逻辑：创建持久批次并返回 HTTP 202，独立 Worker 领取。
    # 约束：不可请求其他员工或未授权邮箱。
    @extend_schema(request=None, responses={202: OBJECT}, tags=["mailboxes"])
    @action(detail=True, methods=["post"], url_path="request-sync")
    def request_sync(self, request, pk=None):
        mailbox = gmail_oauth.request_mailbox_sync(request.user, pk)
        return Response(mailbox, status=202)

    # 功能：移除当前员工的 Gmail 本地授权。
    # 输入：`request` 为当前员工请求，`pk` 为 URL 中的 mailbox_id。
    # 输出：authorization_required 状态。
    # 逻辑：删除凭证但保留已同步邮件和业务分析。
    # 约束：不会删除历史客户或邮件。
    @extend_schema(responses=MailboxResponseSerializer, tags=["mailboxes"])
    @action(detail=True, methods=["delete"], url_path="gmail-authorization")
    def disconnect_gmail(self, request, pk=None):
        return Response(gmail_oauth.disconnect_mailbox(request.user, pk))


# 功能：提供运行能力和显式模拟邮件入口。
# 逻辑：模拟数据通过正式邮件入库及分析服务执行。
# 约束：仅 rules 模式允许写入演示数据，agent 模式拒绝而不回退。
class DemoViewSet(ViewSet):
    # 功能：返回前端需要的运行能力。
    # 输入：`request` 为登录用户请求。
    # 输出：provider、时区、是否可模拟以及版本。
    # 逻辑：读取显式配置，不探测后自动改变模式。
    # 约束：只统计当前登录员工自己的授权连接。
    @extend_schema(responses=OBJECT, tags=["demo"])
    @action(detail=False, methods=["get"])
    def runtime(self, request):
        return Response({"provider": settings.ANALYSIS_PROVIDER, "simulation_enabled": settings.ANALYSIS_PROVIDER == "rules", "gmail_connected": Mailbox.objects.filter(owner=request.user, gmail_credential__isnull=False).exists(),
                         "timezone": settings.TIME_ZONE, "analysis_version": rules.ANALYSIS_VERSION if settings.ANALYSIS_PROVIDER == "rules" else None})

    # 功能：提交一封人工模拟邮件。
    # 输入：`request`.data 含业务邮箱、发送人、主题和正文。
    # 输出：邮件入库结果和处理后的公司 ID。
    # 逻辑：规则提取后走正式提交、Job、快照和结果接口服务。
    # 约束：不发送邮件、不读取外部邮箱，邮件 source 为 synthetic_sample。
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

    # 功能：显式导入独立合成演示材料。
    # 输入：`request` 为用户点击导入样例的请求。
    # 输出：已创建邮件数与公司数。
    # 逻辑：固定样例 ID 保证重复导入不改变已有邮件或时间，首次生成当前时间。
    # 约束：不修改研究数据库或实验划分，不创建真实业务交易。
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


# 功能：承载 README 中 Agent 主动调用的后端协议。
# 逻辑：独立 AgentAuthentication 验证服务身份，所有实体按绑定 owner 隔离。
# 约束：浏览器会话不能调用这些路由；业务数据中不接收 Gmail access_token。
class AgentViewSet(ViewSet):
    authentication_classes = [AgentAuthentication]

    # 功能：领取当前凭证所属员工请求的 Gmail 同步任务。
    # 输入：`request`.data 含 limit，单次最多十个邮箱。
    # 输出：邮箱标识、地址、Google 授权信息和读取上限。
    # 逻辑：将 sync_requested 改为 sync_running 后返回给一次性 Agent。
    # 约束：完整 Google 凭证只通过 AgentAuthentication 路由返回。
    @extend_schema(request=MailboxSyncClaimSerializer, responses=MailboxSyncClaimResponseSerializer(many=True), tags=["agent"])
    @action(detail=False, methods=["post"], url_path="mailbox-syncs/claim")
    def claim_mailbox_syncs(self, request):
        data = validated(MailboxSyncClaimSerializer, request.data)
        return Response(gmail_oauth.claim_mailbox_syncs(request.user, data["limit"]))

    # 功能：保存员工邮箱同步的成功或失败结果。
    # 输入：`request`.data 含 mailbox_id、状态、同步摘要及可选刷新凭证。
    # 输出：浏览器可见且不含凭证的邮箱状态。
    # 逻辑：成功写入 last_synced_at，失败保留可显示错误。
    # 约束：不自动重试或持续轮询。
    @extend_schema(request=MailboxSyncReportSerializer, responses=MailboxResponseSerializer, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="mailbox-syncs/report")
    def report_mailbox_sync(self, request):
        data = validated(MailboxSyncReportSerializer, request.data)
        return Response(gmail_oauth.report_mailbox_sync(request.user, data))

    # 功能：接收整批标准邮件。
    # 输入：`request`.data 为 EmailSubmission 数组。
    # 输出：每封邮件的创建或去重状态。
    # 逻辑：调用原子批量入库服务。
    # 约束：不触发规则占位，任务等待 Agent 主动领取。
    @extend_schema(request=EmailSubmissionSerializer(many=True), responses=SubmissionResultSerializer(many=True), tags=["agent"])
    @action(detail=False, methods=["post"], url_path="emails")
    def submit_emails(self, request):
        return Response(ingestion.submit_emails(request.user, request.data))

    # 功能：补交失败邮件事实。
    # 输入：`request`.data 为 FactsResubmission。
    # 输出：新公司 revision。
    # 逻辑：交由失败到成功的一次转换服务。
    # 约束：完成记录不可再次补交。
    @extend_schema(request=FactsResubmissionSerializer, responses=OBJECT, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="facts")
    def resubmit_facts(self, request):
        return Response(ingestion.resubmit_facts(request.user, request.data))

    # 功能：返回失败抽取的去重键或单封邮件完整重做输入。
    # 输入：`request` 查询 mailbox_id，可选 dedupe_key。
    # 输出：失败键数组，或包含当前事实与完整正文的邮件。
    # 逻辑：先核验邮箱归属，再读取当前抽取状态。
    # 约束：不跨邮箱查找相同 dedupe_key。
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

    # 功能：读取公司归组对象。
    # 输入：`request`.query_params.company_id 为公司 UUID。
    # 输出：Grouping 与 ETag revision。
    # 逻辑：在公司行锁内构造一致投影。
    # 约束：后续 context 必须核对相同 ETag。
    @extend_schema(responses=GroupingResponseSerializer, tags=["agent"], parameters=[OpenApiParameter("company_id", str, required=True)])
    @action(detail=False, methods=["get"])
    @transaction.atomic
    def grouping(self, request):
        company = company_for(request.user, request.query_params.get("company_id"), lock=True)
        return versioned(selectors.context_pair(company)[0], company.revision)

    # 功能：读取邮件与 CRM 业务上下文。
    # 输入：`request`.query_params.company_id，可选 If-Match 保证与 Grouping 同版。
    # 输出：CompanyContext 与 ETag。
    # 逻辑：持有公司锁读取全部邮件和外部业务快照。
    # 约束：传入旧版本时返回冲突而不混合两次读取。
    @extend_schema(responses=CompanyContextResponseSerializer, tags=["agent"], parameters=[OpenApiParameter("company_id", str, required=True), *VERSION_HEADERS[:1]])
    @action(detail=False, methods=["get"])
    @transaction.atomic
    def context(self, request):
        company = company_for(request.user, request.query_params.get("company_id"), lock=True)
        check_version(expected(request), company.revision)
        return versioned(selectors.context_pair(company)[1], company.revision)

    # 功能：查询当前 revision 最新 AnalysisInput。
    # 输入：`request`.query_params.company_id。
    # 输出：原样快照及 ETag；尚无当前快照返回 404。
    # 逻辑：排除已被业务变化淘汰的快照。
    # 约束：不把旧快照冒充详情页当前输入。
    @extend_schema(responses=AnalysisInputSerializer, tags=["agent"], parameters=[OpenApiParameter("company_id", str, required=True)])
    @action(detail=False, methods=["get"], url_path="latest-analysis-input")
    def latest_analysis_input(self, request):
        company = company_for(request.user, request.query_params.get("company_id"))
        snapshot = company.inputs.filter(revision=company.revision).order_by("-id").first()
        if snapshot is None:
            raise NotFound("当前上下文尚未归并。")
        return versioned(snapshot.payload, company.revision)

    # 功能：查询分析缓存元数据。
    # 输入：`request` 含 company_id、input_version、可选 analysis_prompt_version。
    # 输出：CachedAnalysis。
    # 逻辑：仅当前 revision、输入和提示词匹配时 hit=true。
    # 约束：未命中不调用规则或模型。
    @extend_schema(responses=CachedAnalysisResponseSerializer, tags=["agent"], parameters=[OpenApiParameter(name, str, required=name != "analysis_prompt_version") for name in ["company_id", "input_version", "analysis_prompt_version"]])
    @action(detail=False, methods=["get"], url_path="cached-analysis")
    def cached_analysis(self, request):
        company = company_for(request.user, request.query_params.get("company_id"))
        if not request.query_params.get("input_version"):
            raise ValidationError("input_version 必填。")
        return Response(results.cached_analysis(company, request.query_params["input_version"], request.query_params.get("analysis_prompt_version")))

    # 功能：保存 L2 快照。
    # 输入：`request`.data 为 AnalysisInput，头含版本与领取凭证。
    # 输出：原样归档载荷。
    # 逻辑：统一服务验证来源、revision 和租约。
    # 约束：不接受旧任务覆盖当前输入。
    @extend_schema(request=AnalysisInputSerializer, responses=AnalysisInputSerializer, parameters=VERSION_HEADERS, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="analysis-inputs")
    def save_analysis_input(self, request):
        return Response(results.save_input(request.user, request.data, expected(request), request.headers.get("X-Job-ID"), request.headers.get("X-Lease-Token")))

    # 功能：保存 L3 分析。
    # 输入：`request`.data 为 Analysis，头含版本和领取凭证。
    # 输出：已保存分析。
    # 逻辑：强制 provider=agent，由服务核查原文来源。
    # 约束：客户端不能把来源标成其他 provider。
    @extend_schema(request=AnalysisSerializer, responses=AnalysisSerializer, parameters=VERSION_HEADERS, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="analyses")
    def save_analysis(self, request):
        return Response(results.save_analysis(request.user, request.data, expected(request), request.headers.get("X-Job-ID"), request.headers.get("X-Lease-Token")))

    # 功能：保存 L4 评分。
    # 输入：`request`.data 为 Score，头含版本和领取凭证。
    # 输出：已保存 Score。
    # 逻辑：绑定当前成功分析并核对贡献和。
    # 约束：空分与零分严格区分。
    @extend_schema(request=ScoreSerializer, responses=ScoreSerializer, parameters=VERSION_HEADERS, tags=["agent"])
    @action(detail=False, methods=["post"], url_path="scores")
    def save_score(self, request):
        return Response(results.save_score(request.user, request.data, expected(request), request.headers.get("X-Job-ID"), request.headers.get("X-Lease-Token")))

    # 功能：读取业务邮箱同步状态。
    # 输入：`request`.query_params.mailbox_id。
    # 输出：SyncState 与 ETag version。
    # 逻辑：授权后读取业务游标。
    # 约束：不读取 Gmail 令牌。
    @extend_schema(responses=SyncStateSerializer, tags=["agent"], parameters=[OpenApiParameter("mailbox_id", str, required=True)])
    @action(detail=False, methods=["get"], url_path="sync-state")
    def get_sync_state(self, request):
        mailbox = mailbox_for(request.user, request.query_params.get("mailbox_id"))
        return versioned(ingestion.sync_state(mailbox), mailbox.version)

    # 功能：写入同步游标。
    # 输入：`request`.data 为 SyncState，If-Match 指向旧版本。
    # 输出：递增后的状态。
    # 逻辑：调用邮箱行锁和乐观锁服务。
    # 约束：Agent 负责保证此前所有邮件提交成功。
    @extend_schema(request=SyncStateSerializer, responses=SyncStateSerializer, parameters=VERSION_HEADERS[:1], tags=["agent"])
    @action(detail=False, methods=["post"], url_path="sync-state-save")
    def save_sync_state(self, request):
        data = validated(SyncStateSerializer, request.data)
        state = ingestion.save_sync_state(request.user, data, expected(request))
        return versioned(state, state["version"])

    # 功能：领取待处理任务。
    # 输入：`request`.data 包含 limit 和 lease_seconds。
    # 输出：Job 数组，含领取凭证与 expected_version 扩展。
    # 逻辑：行锁领取；无工作返回空数组。
    # 约束：仅该服务凭证 owner 的任务。
    @extend_schema(request=ClaimSerializer, responses=JobResponseSerializer(many=True), tags=["agent"])
    @action(detail=False, methods=["post"], url_path="jobs/claim")
    def claim_jobs(self, request):
        data = validated(ClaimSerializer, request.data)
        return Response(jobs.claim(request.user, data["limit"], data["lease_seconds"]))

    # 功能：回报任务最终状态。
    # 输入：`request`.data 为 JobReport，X-Lease-Token 为领取凭证。
    # 输出：持久化状态。
    # 逻辑：验证租约及成功产出，失败保留错误状态。
    # 约束：不自动重试、不把回报当作产出已保存的证据。
    @extend_schema(request=JobReportSerializer, responses=OBJECT, parameters=VERSION_HEADERS[2:], tags=["agent"])
    @action(detail=False, methods=["post"], url_path="jobs/report")
    def report_job(self, request):
        return Response(jobs.report(request.user, validated(JobReportSerializer, request.data), request.headers.get("X-Lease-Token")))
