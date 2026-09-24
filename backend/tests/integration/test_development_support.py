"""职责：验证数据库占位、全球聚合、商机结果和开发权限。
实现：隔离 PostgreSQL 中验证公共活动共享和私人商机隔离，以及正式/实验模式差异；不连接模型和外部服务。
关联：sales.world、algorithm_views、seed_development_support 及现有 Tool 鉴权。
目录：
- DevelopmentSupportTests：端到端支持层测试。
- DevelopmentSupportTests.setUp：创建隔离账号和虚拟批次。
- DevelopmentSupportTests.test_seed_is_idempotent_and_does_not_rewrite：确认显式占位稳定。
- DevelopmentSupportTests.test_world_deduplicates_and_keeps_currencies：验证重复商机和币种。
- DevelopmentSupportTests.test_partial_results_context_and_latest_score：验证最小提交和上下文。
- DevelopmentSupportTests.test_formal_public_events_keep_private_business：验证公共活动共享而商机仍隔离。
- DevelopmentSupportTests.test_lab_tools_omit_credentials_versions_and_source：验证放宽规则。
- DevelopmentSupportTests.test_essential_boundaries_remain：验证必要边界。
变量索引：
- 无
"""

from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.sales.models import Opportunity, OpportunityPriority, WorldEvent
from apps.sales.management.commands.seed_development_support import seed_support


# 功能：验证新增支持层而非算法计算。
# 逻辑：使用数据库占位和真实 API，模式显式覆盖避免本地配置影响结论。
# 约束：无真实客户数据、凭证或网络。
@override_settings(DEBUG=True, LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class DevelopmentSupportTests(TestCase):
    # 功能：建立两个隔离账号。
    # 输入：无外部参数。
    # 输出：测试用户、客户端和批次。
    # 逻辑：本人导入占位，另一账号用于越权验证。
    # 约束：使用 Django 测试数据库。
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="support-lab")
        self.other = get_user_model().objects.create_user(username="support-other")
        self.manifest = seed_support(self.user)
        self.client = APIClient()
        self.client.force_login(self.user)
        self.opportunity = Opportunity.objects.get(pk=self.manifest["opportunities"][0])

    # 功能：检查占位重复执行和已有编辑保留。
    # 输入：已有导入清单和修改后的活动标题。
    # 输出：记录不重复，日期和修改内容不被重写。
    # 逻辑：重复调用真实初始化函数。
    # 约束：不把占位误当实时新闻。
    def test_seed_is_idempotent_and_does_not_rewrite(self):
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        event.title = "人工已编辑"
        event.save()
        self.assertEqual(seed_support(self.user), self.manifest)
        event.refresh_from_db()
        self.assertEqual(event.title, "人工已编辑")
        self.assertEqual(WorldEvent.objects.count(), 8)

    # 功能：验证地图金额与私有活动权限。
    # 输入：同坐标重复关联和同客户另一币种商机。
    # 输出：金额去重、币种分列、国家计数正确。
    # 逻辑：调用真实 world API。
    # 约束：不验证地理服务或真实交易。
    def test_world_deduplicates_and_keeps_currencies(self):
        event = WorldEvent.objects.get(pk=self.manifest["events"][1])
        event.opportunity_ids.append(str(self.opportunity.pk))
        usd = Opportunity.objects.create(owner=self.user, company=self.opportunity.company, title="美元商机", currency="USD", amount="20", status="proposal")
        event.opportunity_ids.append(str(usd.pk))
        event.save()
        response = self.client.get("/api/v1/sales/world/?country=SG")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["map_amounts"], {"SGD": "145000.00", "USD": "20.00"})
        self.assertEqual(next(row for row in response.data["countries"] if row["code"] == "SG")["event_count"], 2)

    # 功能：验证商机结果无需完整解释即可提交。
    # 输入：显式商机、分数和自定义信号类型。
    # 输出：公司自动归属、最新结果进入列表、上下文包含原资料。
    # 逻辑：通过原 CRUD 创建，再通过板块及上下文查询。
    # 约束：未执行评分算法；公司级分数不被修改。
    def test_partial_results_context_and_latest_score(self):
        response = self.client.post("/api/v1/sales/records/opportunity-priorities/", {"opportunity": str(self.opportunity.pk), "priority_score": 99}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(str(response.data["company"]), str(self.opportunity.company_id))
        signal = self.client.post("/api/v1/sales/records/opportunity-signals/", {"opportunity": str(self.opportunity.pk), "signal_type": "CUSTOM_ALGORITHM_SIGNAL", "signal_value": {"new": "shape"}}, format="json")
        self.assertEqual(signal.status_code, 201, signal.data)
        board = self.client.get("/api/v1/sales/priority-board/")
        self.assertEqual(board.status_code, 200, board.data)
        self.assertEqual(board.data["results"][0]["priority"]["priority_score"], 99)
        context = self.client.get(f"/api/v1/sales/opportunity-context/{self.opportunity.pk}/")
        self.assertEqual(context.status_code, 200, context.data)
        self.assertEqual(len(context.data["signals"]), 2)
        self.assertTrue(context.data["seller_context"]["sales_setup"]["products"])

    # 功能：验证公共活动共享与正式业务隔离。
    # 输入：无外部参数；实例中的匿名及另一登录账号。
    # 输出：匿名拒绝，已登录员工看到公共活动但看不到私人关联和上下文。
    # 逻辑：使用真实 Session 请求同时检查活动数量、字段投影和商机权限。
    # 约束：不模拟权限、不扩大客户和商机读取。
    def test_formal_public_events_keep_private_business(self):
        other = APIClient()
        self.assertEqual(other.get("/api/v1/sales/world/").status_code, 403)
        other.force_login(self.other)
        world = other.get("/api/v1/sales/world/").data
        self.assertEqual(world["count"], 8)
        for row in world["results"]:
            self.assertEqual((row["opportunity_ids"], row["customers"], row["amounts"], row["map_amounts"]), ([], [], {}, {}))
        self.assertEqual(other.get(f"/api/v1/sales/opportunity-context/{self.opportunity.pk}/").status_code, 404)

    # 功能：验证实验模式无需凭据、版本和来源。
    # 输入：匿名 Tool HTTP 调用、公开所选身份和跨账号商机关联。
    # 输出：读写成功且归属不被伪造。
    # 逻辑：显式覆盖实验设置，保留业务服务和数据校验。
    # 约束：不执行外部发送或日历动作。
    @override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False)
    def test_lab_tools_omit_credentials_versions_and_source(self):
        client = APIClient()
        client.credentials(HTTP_X_LAB_USER=self.other.username)
        response = client.post("/api/v1/agent-tools/call/", {"name": "opportunity_priorities.create", "arguments": {"data": {"opportunity": str(self.opportunity.pk), "priority_score": 70}}}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        record = OpportunityPriority.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(record.owner_id, self.user.pk)
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        updated = client.patch(f"/api/v1/sales/records/world-events/{event.pk}/", {"description": "已由算法修改", "opportunity_ids": [str(self.opportunity.pk)], "data_source": "agent", "source_url": ""}, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        context = client.post("/api/v1/agent-tools/call/", {"name": "opportunity_context.get", "arguments": {"opportunity_id": str(self.opportunity.pk)}}, format="json")
        self.assertEqual(context.status_code, 200, context.data)

    # 功能：保留必要数值及来源安全边界。
    # 输入：超范围分数和不安全链接。
    # 输出：400 且无错误结果写入。
    # 逻辑：开放模式也执行可显示和可存储的数据边界。
    # 约束：不验证算法公式。
    @override_settings(LAB_OPEN_ACCESS=True, WORKSPACE_OWNER_ONLY=False)
    def test_essential_boundaries_remain(self):
        response = self.client.post("/api/v1/sales/records/opportunity-priorities/", {"opportunity": str(self.opportunity.pk), "priority_score": 101}, format="json")
        self.assertEqual(response.status_code, 400)
        event = WorldEvent.objects.get(pk=self.manifest["events"][0])
        response = self.client.patch(f"/api/v1/sales/records/world-events/{event.pk}/", {"source_url": "javascript:alert(1)"}, format="json")
        self.assertEqual(response.status_code, 400)
