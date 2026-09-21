"""职责：验证合成事实兼容、升级工具及恢复账号隔离后的真实接口行为。
实现：隔离数据库生成两组真实夹具，使用 HTTP、Agent L2 和持久化接口串联验证。
关联：extraction_contract、CompanyViewSet、Tool API、个人空间访问策略。
目录：
- ExtractionCompatibilityTests：跨层回归测试。
- ExtractionCompatibilityTests.setUp：生成独立样例和访问者。
- ExtractionCompatibilityTests.call：执行工具 HTTP 请求。
- ExtractionCompatibilityTests.test_existing_seed_builds_and_saves_l2：历史样例可重新分析和归档。
- ExtractionCompatibilityTests.test_compatible_upgrade_is_noop：兼容来源不重复调用模型。
- ExtractionCompatibilityTests.test_real_legacy_upgrade_tool：真实旧版本保留显式升级。
- ExtractionCompatibilityTests.test_malformed_fixture_still_rejected：合成声明不绕过事实结构。
- ExtractionCompatibilityTests.test_mailbox_chip_uses_current_account：实验模式邮箱列表也只展示自己的连接。
- ExtractionCompatibilityTests.test_owner_isolation_covers_all_entry_points：个人隔离覆盖网页、工具、实验和 Agent。
变量索引：
- 无
"""

import hashlib
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.workflows.analysis_input import AnalysisInput, build_analysis_input, _validate_email
from apps.crm import selectors
from apps.crm.models import AgentCredential, Company, Mailbox
from apps.crm.durable_models import ExtractionRepair
from apps.sales.management.commands.seed_kg_lab import run_seed
from apps.sales.experiments import APPROVED_BATCHES
from apps.sales import models


# 功能：验证兼容性和账号隔离的端到端契约。
# 逻辑：使用真实 ORM 与 HTTP，仅 L2 客户端的网络读取替换为同一数据库投影。
# 约束：没有外部模型、邮箱或生产数据库调用。
@override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False, ANALYSIS_PROVIDER="agent", LOCAL_DEBUG_AUTO_LOGIN=False)
class ExtractionCompatibilityTests(TestCase):
    # 功能：准备可复现的样例。
    # 输入：测试数据库和临时附件目录。
    # 输出：owner、reader、company、client 及 batch 实例状态。
    # 逻辑：使用生产生成器建立关系，访问者不属于样例 owner。
    # 约束：每项测试回滚，附件由临时目录自动清理。
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(BASE_DIR=Path(folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user(username="compat-owner")
        self.reader = get_user_model().objects.create_user(username="compat-reader")
        self.batch = APPROVED_BATCHES[0]
        manifest = run_seed(self.owner, self.batch, 2)
        self.company = Company.objects.get(pk=manifest["truth"][0]["company_id"])
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    # 功能：执行真实工具路由。
    # 输入：`name` 为固定工具名；`arguments` 为已知业务输入。
    # 输出：HTTP 响应。
    # 逻辑：通过服务认证与 registry/dispatch，不直接调用视图。
    # 约束：实验模式允许不传幂等键；不会访问外部服务。
    def call(self, name, arguments):
        return self.client.post("/api/v1/agent-tools/call/", {"name": name, "arguments": arguments}, format="json")

    # 功能：覆盖线上旧样例从读取到 Agent L2 保存的完整路径。
    # 输入：移除新增结构字段后的原始批次封装。
    # 输出：详情、分析入队和 L2 保存均成功，合成版本保持原值。
    # 逻辑：模拟已部署旧行，不重写 prompt_version；使用实际上下文构造 Agent 输入。
    # 约束：未运行 L3 模型，不把 L2 测试视为模型质量验证。
    def test_existing_seed_builds_and_saves_l2(self):
        for email in self.company.emails.all():
            email.payload.pop("extract_schema_version")
            email.save(update_fields=["payload"])
        response = self.client.get(f"/api/v1/companies/{self.company.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["extraction_upgrade"]["incompatible_emails"], 0)
        self.assertEqual(self.call("customers.analyze", {"company_id": str(self.company.pk)}).status_code, 200)
        grouping, context = selectors.context_pair(self.company)
        backend = SimpleNamespace(get_company_grouping=lambda identity: grouping, get_company_context=lambda identity: context)
        result = build_analysis_input(str(self.company.pk), backend=backend, clock=timezone.now)
        self.assertIsInstance(result, AnalysisInput, result)
        saved = self.client.post("/api/v1/agent/analysis-inputs/", result.to_dict(), format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertTrue(all(item["extract_prompt_version"] == f"{self.batch}:fixture-extract-v1" for item in context["emails"]))

    # 功能：验证正常样例不触发无谓的 300 封重抽取。
    # 输入：生成器明确记录结构版本的批次。
    # 输出：两个工具可发现，预览兼容，升级零创建，revision 不变。
    # 逻辑：真实执行只读预览及写入工具，核对修复表为空。
    # 约束：没有模型调用或旧事实改写。
    def test_compatible_upgrade_is_noop(self):
        args = {"company_id": str(self.company.pk)}
        preview = self.call("customers.extraction_status", args)
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["data"]["incompatible_emails"], 0)
        upgraded = self.call("customers.upgrade_extractions", args)
        self.assertEqual(upgraded.status_code, 200, upgraded.data)
        self.assertEqual(upgraded.data["data"]["created"], 0)
        self.assertFalse(ExtractionRepair.objects.exists())

    # 功能：验证真实旧结构通过工具明确排队。
    # 输入：一个 extract-v6 最新抽取及遗留 schema 声明。
    # 输出：分析返回 409，升级创建一次修复并可查询进度。
    # 逻辑：版本检查不能因 synthetic_batch 或 schema 字段而接受普通旧提示词。
    # 约束：Worker 不在测试中启动。
    def test_real_legacy_upgrade_tool(self):
        source = self.company.emails.first().extractions.latest("pk")
        source.prompt_version = "extract-v6"
        source.save(update_fields=["prompt_version"])
        response = self.call("customers.analyze", {"company_id": str(self.company.pk)})
        self.assertEqual(response.status_code, 409, response.data)
        upgraded = self.call("customers.upgrade_extractions", {"company_id": str(self.company.pk)})
        self.assertEqual(upgraded.status_code, 200, upgraded.data)
        self.assertEqual(upgraded.data["data"]["created"], 1)

    # 功能：确认样例兼容没有取消 Agent 事实校验。
    # 输入：当前格式封装但字段缺失的事实。
    # 输出：明确 ValueError，原字典未被改写。
    # 逻辑：分别覆盖事实缺字段、未知批次和不匹配的结构声明。
    # 约束：不制造或回填缺失事实。
    def test_malformed_fixture_still_rejected(self):
        email = selectors.email_data(self.company.emails.first())
        for change in ({"facts": {}}, {"synthetic_batch": "OTHER"}, {"extract_schema_version": "extract-v6"}):
            candidate = {**deepcopy(email), **change}
            with self.assertRaises(ValueError):
                _validate_email(candidate, 0)

    # 功能：验证顶部 Gmail 的数据源与当前账号一致。
    # 输入：两个账号各自的邮箱，实验开放模式仍开启。
    # 输出：读取者只得到自己的邮箱地址。
    # 逻辑：通过实际邮箱列表接口排除另一账号的全部样例邮箱。
    # 约束：没有创建或复制 OAuth 凭据。
    def test_mailbox_chip_uses_current_account(self):
        mailbox = Mailbox.objects.create(owner=self.reader, address="mine@example.test")
        response = self.client.get("/api/v1/mailboxes/")
        self.assertEqual([row["mailbox_id"] for row in response.data], [str(mailbox.pk)])

    # 功能：验证个人隔离优先于遗留实验开关及团队授权。
    # 输入：reader 的登录会话、其他账号数据和一项主动授予的团队访问权。
    # 输出：邮箱/客户/实验无跨账号内容，工具和 Agent 明细拒绝，匿名不能读业务。
    # 逻辑：保留原数据、团队和所有者，仅通过服务策略过滤访问。
    # 约束：测试权限边界，不撤销或删除真实账号的授权记录。
    @override_settings(WORKSPACE_OWNER_ONLY=True)
    def test_owner_isolation_covers_all_entry_points(self):
        team = models.Team.objects.create(owner=self.owner, name="Shared")
        models.Membership.objects.create(owner=self.owner, team=team, user=self.reader, role="editor")
        models.CompanyGrant.objects.create(owner=self.owner, team=team, company=self.company, role="editor")
        self.assertEqual(self.client.get("/api/v1/mailboxes/").data, [])
        self.assertEqual(self.client.get("/api/v1/sales/browse/directory/").data["count"], 0)
        self.assertEqual(self.client.get("/api/v1/experiments/").data, {"batches": []})
        for path in (f"/api/v1/companies/{self.company.pk}/", f"/api/v1/experiments/{self.batch}/crm.Email/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.call("customers.context", {"company_id": str(self.company.pk)}).status_code, 404)
        anonymous = APIClient()
        self.assertIn(anonymous.get("/api/v1/mailboxes/").status_code, (401, 403))
        session = APIClient()
        session.force_login(self.reader)
        self.assertEqual(session.get("/api/v1/session/").data["username"], self.reader.username)
        self.assertFalse(session.get("/api/v1/session/").data["lab_open_access"])
        self.assertEqual(session.get("/api/v1/session/", HTTP_X_LAB_USER=self.owner.username).data["username"], self.reader.username)
        AgentCredential.objects.create(owner=self.reader, name="isolation-test", digest=hashlib.sha256(b"isolation-reader-token").hexdigest())
        agent = APIClient()
        agent.credentials(HTTP_AUTHORIZATION="Agent isolation-reader-token")
        self.assertEqual(agent.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(f"/api/v1/companies/{self.company.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/companies/").data["count"], 2)
