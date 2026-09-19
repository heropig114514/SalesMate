"""职责：验证正式 L4 的后端数据口径、权限、版本触发和保存契约。
实现：使用隔离 PostgreSQL 测试库、HTTP 客户端与合成业务记录；真实 Agent 规则计算仅使用固定结构化信号。
关联：覆盖 sales.priority、销售方接口、商机事务和 crm 评分保存；不连接邮件或模型服务。
目录：
- PriorityTests：正式评分后端回归测试。
- PriorityTests.setUp：准备两个 owner、公司和合成产品。
- PriorityTests.order：建立明确状态和金额的合成订单。
- PriorityTests.opportunity：建立明确产品和金额的合成商机。
- PriorityTests.prepare_score：通过真实 HTTP 保存当前 L2、L3 并准备正式 L4 结果。
- PriorityTests.test_context_aggregates_same_currency_and_isolates_owner：验证权威映射及统计隔离。
- PriorityTests.test_incomplete_and_mixed_currency_deals_remain_unknown：验证不完整数据不产生确定金额。
- PriorityTests.test_similarity_requires_complete_evidence：验证未知和明确不匹配分离。
- PriorityTests.test_profile_api_validates_versions_and_fans_out：验证画像权限、版本、校验及任务传播。
- PriorityTests.test_opportunity_mutations_refresh_current_company：验证商机创建、编辑、状态与归档触发。
- PriorityTests.test_order_and_product_changes_refresh_other_companies：验证共享统计和目录变更传播。
- PriorityTests.test_formal_score_persists_without_legacy_features：验证新分数及解释不依赖旧 L3 特征。
- PriorityTests.test_provisional_score_with_empty_details_persists：验证真实暂定分及空解释能保存和读取。
- PriorityTests.test_invalid_explanations_and_sources_are_rejected：验证错误贡献和越界原文证据拒绝。
- PriorityTests.test_null_score_and_stale_lease_contract：验证空分语义和旧版本写入拒绝。
- PriorityTests.test_formal_sorting_and_legacy_score_visibility：验证正式版本展示与同分排序。
变量索引：
- 无
"""

from copy import deepcopy
from decimal import Decimal
import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.workflows.lead_score import compute_priority_result
from apps.crm import jobs, rules, selectors
from apps.crm.models import AgentCredential, Analysis, AnalysisInput, Company, Mailbox, Score
from apps.sales.models import Opportunity, OrderLine, Product, SalesOrder, SellerProfile
from apps.sales.priority import priority_context, similar_won
from apps.sales.services import transition_record


# 功能：正式评分后端回归测试。
# 逻辑：每项测试使用独立数据库事务和同 owner/跨 owner 对照，运行模式固定为正式 agent。
# 约束：不调用模型、Gmail 或生产数据库；直接建模的样本不代表浏览器状态转换已验证。
@override_settings(ANALYSIS_PROVIDER="agent")
class PriorityTests(TestCase):
    # 功能：准备两个 owner、公司和合成产品。
    # 输入：无外部参数，由测试框架初始化隔离数据库。
    # 输出：初始化身份、客户、API 客户端与目录产品。
    # 逻辑：同 owner 两家公司用于依赖传播，另一 owner 用于越权对照。
    # 约束：固定令牌仅用于测试，所有邮件均为合成数据。
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="priority-owner")
        self.other = get_user_model().objects.create_user(username="priority-other")
        self.company = Company.objects.create(owner=self.owner, group_key="domain:buyer.example", name="Buyer",
                                              customer={"industry_from_crm": "Manufacturing", "employee_count": 100, "country": "Singapore"})
        self.peer = Company.objects.create(owner=self.owner, group_key="domain:history.example", name="History",
                                           customer={"industry_from_crm": "Manufacturing"})
        self.foreign = Company.objects.create(owner=self.other, group_key="domain:foreign.example")
        self.product = Product.objects.create(owner=self.owner, sku="WMS", name="WMS", currency="SGD", unit_price="100")
        SellerProfile.objects.create(owner=self.owner, profile={"target_industries": ["Manufacturing"],
                                     "target_company_size": {"min": 20, "max": 500}, "service_regions": ["Singapore"],
                                     "time_zone": "Asia/Singapore"})
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        AgentCredential.objects.create(owner=self.owner, name="priority-test", digest=hashlib.sha256(b"priority-test-token").hexdigest())
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent priority-test-token")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@internal.example")

    # 功能：建立明确状态和金额的合成订单。
    # 输入：`amount` 为行净额，`currency` 为币种，`company` 可指定所属客户，`status` 默认为 confirmed。
    # 输出：SalesOrder 实例。
    # 逻辑：创建一条数量为一的明细，使每单计一次的统计可直接核验。
    # 约束：为测试准备直接落库，不代替订单 HTTP 状态流转测试。
    def order(self, amount="30000", currency="SGD", company=None, status="confirmed"):
        company = company or self.peer
        order = SalesOrder.objects.create(owner=company.owner, company=company, number=f"order-{SalesOrder.objects.count()}",
                                         currency=currency, status=status, confirmed_at=timezone.now() if status != "draft" else None)
        OrderLine.objects.create(owner=company.owner, order=order, product=self.product if company.owner_id == self.owner.pk else None,
                                 description="WMS", quantity=1, unit_price=amount)
        return order

    # 功能：建立明确产品和金额的合成商机。
    # 输入：`amount` 为可空金额，`currency` 为币种。
    # 输出：Opportunity 实例。
    # 逻辑：为当前客户建立 proposal 商机并显式关联规范产品名称。
    # 约束：仅准备数据，不模拟状态转换副作用。
    def opportunity(self, amount="250000", currency="SGD"):
        return Opportunity.objects.create(owner=self.owner, company=self.company, title="Warehouse", status="proposal",
                                          amount=amount, currency=currency, product_names=["WMS"])

    # 功能：通过真实 HTTP 保存当前 L2、L3 并准备正式 L4 结果。
    # 输入：无外部参数，使用测试客户、邮箱及合成权威记录。
    # 输出：正式 score 载荷、有效租约头和对应 Analysis。
    # 逻辑：入库合成询价邮件、领取任务、保存 L2/L3；清空旧 L3 特征，调用真实规则函数生成新结果。
    # 约束：结构化信号固定提供，不连接模型；验证的是后端契约而非信号提取准确度。
    def prepare_score(self):
        self.opportunity()
        self.order()
        email = rules.extract_email(self.mailbox, "buyer@buyer.example", "设备询价", "需求：采购设备\n数量：12 台", "priority-one")
        response = self.agent.post("/api/v1/agent/emails/", [email], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        job = jobs.claim(self.owner, 1, 120, company_id=self.company.pk)[0]
        grouping, context = selectors.context_pair(self.company)
        snapshot = rules.build_input(grouping, context)
        headers = {"HTTP_IF_MATCH": str(self.company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        response = self.agent.post("/api/v1/agent/analysis-inputs/", snapshot, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["list_view"]["signal"] = "unknown"
        for feature in analysis["list_view"]["score_features"].values():
            feature["value"] = None
        response = self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        context["priority_context"]["signals"] = [{"type": "FORMAL_QUOTATION_REQUEST", "value": None,
                                                   "confidence": 0.9, "evidence": "设备询价", "source_id": email["dedupe_key"]}]
        output = compute_priority_result(analysis, snapshot, clock=timezone.now, priority_context=context["priority_context"])
        score = {**output["score"], "score_details": output["details"]}
        self.assertIsNotNone(score["score"])
        return score, headers, Analysis.objects.get(snapshot__company=self.company)

    # 功能：验证权威映射及统计隔离。
    # 输入：同币种多商机、两张有效订单及草稿/外部 owner 对照。
    # 输出：汇总金额、逐单均值和匹配结论符合已确认口径。
    # 逻辑：通过真实 Agent context HTTP 读取并核对资料及 ETag。
    # 约束：不把草稿或其他 owner 的订单纳入样本。
    def test_context_aggregates_same_currency_and_isolates_owner(self):
        self.opportunity("100000")
        self.opportunity("150000")
        self.order("20000")
        self.order("40000")
        self.order("999999", status="draft")
        self.order("999999", company=self.foreign)
        response = self.agent.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}, HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        context = response.data["priority_context"]
        self.assertEqual(context["customer"]["company_size"], 100)
        self.assertEqual(context["deal"]["deal_value"], "250000.00")
        self.assertEqual(Decimal(context["seller"]["average_deal_value"]), Decimal("30000"))
        self.assertTrue(context["seller"]["similar_won_deals"])
        self.assertNotIn("communications", context)
        self.assertEqual(self.agent.get("/api/v1/agent/context/", {"company_id": str(self.foreign.pk)}).status_code, 404)

    # 功能：验证不完整数据不产生确定金额。
    # 输入：混币种、未知金额、无产品及失活商机。
    # 输出：不输出确定金额或缺失的匹配字段。
    # 逻辑：逐项改变输入，直接核对权威上下文而不补造默认值。
    # 约束：数据库直接修改仅用于边界构造。
    def test_incomplete_and_mixed_currency_deals_remain_unknown(self):
        first = self.opportunity("100")
        second = self.opportunity("100", "USD")
        self.assertNotIn("deal_value", priority_context(self.company)["deal"])
        second.currency, second.amount = "SGD", None
        second.save()
        self.assertNotIn("deal_value", priority_context(self.company)["deal"])
        second.status = "lost"
        second.save()
        self.assertEqual(priority_context(self.company)["deal"]["deal_value"], "100.00")
        first.product_names = []
        first.save()
        self.assertNotIn("product", priority_context(self.company)["deal"])
        self.assertNotIn("average_deal_value", priority_context(self.company)["seller"])

    # 功能：验证未知和明确不匹配分离。
    # 输入：行业产品完整、缺失和已证实匹配的历史样本。
    # 输出：分别返回 False、None 和 True。
    # 逻辑：没有可靠正证据时，只有完整样本才能给否定结论。
    # 约束：不使用相似度阈值或模型推断。
    def test_similarity_requires_complete_evidence(self):
        customer, deal = {"industry": "Manufacturing"}, {"product": ["WMS"]}
        self.assertIsNone(similar_won(customer, deal, []))
        self.assertFalse(similar_won(customer, deal, [{"industry": "Retail", "products": ["WMS"]}]))
        self.assertIsNone(similar_won(customer, deal, [{"industry": None, "products": ["WMS"]}]))
        self.assertTrue(similar_won(customer, deal, [{"industry": "manufacturing", "products": ["wms"]}]))

    # 功能：验证画像权限、版本、校验及任务传播。
    # 输入：当前 owner 的画像更新与越权字段、非法时区及旧版本请求。
    # 输出：同 owner 公司更新版本并入队，其他 owner 不受影响，错误请求无副作用。
    # 逻辑：走 Session API 并检查数据库版本、画像和任务。
    # 约束：force_authenticate 仅隔离认证步骤，不替代 CSRF 专项测试。
    def test_profile_api_validates_versions_and_fans_out(self):
        path = "/api/v1/sales/seller-profile/"
        response = self.browser.patch(path, {"service_regions": ["Singapore", "Malaysia"]}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        for company in (self.company, self.peer):
            company.refresh_from_db()
            self.assertEqual((company.revision, company.external_version), (1, 1))
            self.assertEqual(company.jobs.get().trigger, "external_updated")
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.revision, 0)
        self.assertEqual(self.browser.patch(path, {}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        for data in ({"owner": self.other.pk}, {"time_zone": "Invalid/Zone"}, {"target_company_size": {"min": 10, "max": 1}}):
            self.assertEqual(self.browser.patch(path, data, format="json", HTTP_IF_MATCH="1").status_code, 400)
        self.assertEqual(SellerProfile.objects.get(owner=self.owner).revision, 1)
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.get(path).data, {"revision": 0, "profile": {}})

    # 功能：验证商机创建、编辑、状态与归档触发。
    # 输入：商机 HTTP 创建、版本化修改、状态命令和归档命令。
    # 输出：当前公司版本逐次递增，其他公司版本不变。
    # 逻辑：使用实际 sales 资源入口，核对商机产品进入评分背景。
    # 约束：不依赖前端表单，业务权限和事务仍由后端执行。
    def test_opportunity_mutations_refresh_current_company(self):
        response = self.browser.post("/api/v1/sales/records/opportunities/", {"company": str(self.company.pk), "title": "New",
                                     "currency": "SGD", "amount": "1000", "product_names": ["WMS"]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        opportunity = Opportunity.objects.get()
        path = f"/api/v1/sales/records/opportunities/{opportunity.pk}/"
        response = self.browser.patch(path, {"amount": "2000"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        opportunity.refresh_from_db()
        transition_record(opportunity, self.owner, opportunity.revision, "qualified")
        opportunity.refresh_from_db()
        from apps.sales.services import archive_record
        archive_record(opportunity, self.owner, opportunity.revision, True)
        self.company.refresh_from_db()
        self.peer.refresh_from_db()
        self.assertEqual(self.company.external_version, 4)
        self.assertEqual(self.peer.external_version, 0)
        self.assertEqual(priority_context(self.company)["deal"], {})

    # 功能：验证共享统计和目录变更传播。
    # 输入：草稿订单确认和目录产品改名。
    # 输出：其他同 owner 公司版本及待办更新，跨 owner 不受影响。
    # 逻辑：通过真实业务服务确认订单，再通过 HTTP 修改产品；未领取任务合并最新版本。
    # 约束：不启动 Worker 或模型。
    def test_order_and_product_changes_refresh_other_companies(self):
        order = self.order(status="draft")
        transition_record(order, self.owner, 0, "confirmed")
        self.company.refresh_from_db()
        self.assertEqual(self.company.external_version, 1)
        response = self.browser.patch(f"/api/v1/sales/records/products/{self.product.pk}/", {"name": "WMS Next"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.external_version, 2)
        self.assertEqual(self.company.jobs.filter(status="pending").count(), 1)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.external_version, 0)

    # 功能：验证新分数及解释不依赖旧 L3 特征。
    # 输入：旧特征为空但正式上下文完整的真实规则结果。
    # 输出：评分与解释原子保存、重复请求幂等、详情能读取解释及邮件。
    # 逻辑：通过 scores API 提交两次，核对数据库行数及公司详情。
    # 约束：尚未验证真实 Agent 自动提交 score_details，测试显式附加该字段。
    def test_formal_score_persists_without_legacy_features(self):
        score, headers, analysis = self.prepare_score()
        for _ in range(2):
            response = self.agent.post("/api/v1/agent/scores/", score, format="json", **headers)
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(analysis.scores.count(), 1)
        detail = self.browser.get(f"/api/v1/companies/{self.company.pk}/").data
        self.assertEqual(detail["score_detail"]["score_details"], score["score_details"])
        self.assertEqual(detail["score_version"], "score-v2")
        self.assertIn(score["score_details"]["evidence"][0]["source_id"], detail["grouping"]["member_dedupe_keys"])

    # 功能：验证 Agent 在缺少业务资料时生成的暂定分可以原样入库。
    # 输入：prepare_score 的成功分析及固定采购信号，显式移除全部业务上下文。
    # 输出：非空分数与完整空解释保存成功；半空解释仍返回 400。
    # 逻辑：调用真实 Agent 规则函数，通过持有租约的 HTTP 接口保存并读取详情。
    # 约束：只构造隔离样本，不调用模型，不改变权重或生产评分。
    def test_provisional_score_with_empty_details_persists(self):
        _, headers, analysis = self.prepare_score()
        email = self.company.emails.first()
        output = compute_priority_result(analysis.payload, analysis.snapshot.payload, clock=timezone.now,
            priority_context={"signals": [{"type": "L2 Interested", "value": None, "confidence": 1.0,
                                           "evidence": "设备询价", "source_id": email.pk}]})
        score = {**output["score"], "score_details": output["details"]}
        self.assertIsNotNone(score["score"])
        self.assertIsNone(score["score_details"]["score_breakdown"])
        response = self.agent.post("/api/v1/agent/scores/", score, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        stored = Score.objects.get(analysis=analysis).payload
        # DRF 规范化日期时区表示；评分数值、原因和解释内容必须保持原样。
        self.assertEqual({key: value for key, value in stored.items() if key != "scored_at"},
                         {key: value for key, value in score.items() if key != "scored_at"})
        detail = self.browser.get(f"/api/v1/companies/{self.company.pk}/")
        self.assertEqual(detail.data["score_detail"]["score_details"], output["details"])
        broken = deepcopy(score)
        broken["score_details"]["recommended_next_action"] = "伪造完整建议"
        self.assertEqual(self.agent.post("/api/v1/agent/scores/", broken, format="json", **headers).status_code, 400)

    # 功能：验证错误贡献和越界原文证据拒绝。
    # 输入：非整数贡献、重复特征、解释对账错误、跨公司引用和伪造原文。
    # 输出：每种无效载荷均返回 400 且不写入 Score。
    # 逻辑：从合法规则结果复制并逐项破坏契约。
    # 约束：不修改评分规则来适配测试。
    def test_invalid_explanations_and_sources_are_rejected(self):
        score, headers, _ = self.prepare_score()
        candidates = []
        bad = deepcopy(score)
        bad["score_reasons"][0]["contribution"] = float(bad["score_reasons"][0]["contribution"])
        candidates.append(bad)
        bad = deepcopy(score)
        bad["score_reasons"][0]["feature"] = "buying_intent"
        candidates.append(bad)
        bad = deepcopy(score)
        bad["score_details"]["score_breakdown"]["contributions"]["urgency"] += 1
        candidates.append(bad)
        for source, evidence in (("foreign:mail", "设备询价"), (score["score_details"]["evidence"][0]["source_id"], "不存在的原文")):
            bad = deepcopy(score)
            reason = next(item for item in bad["score_details"]["top_reasons"] if item["source_id"] is not None)
            reason["source_id"], reason["evidence"] = source, evidence
            bad["score_details"]["evidence"] = [{"source_id": source, "text": evidence}]
            candidates.append(bad)
        for candidate in candidates:
            response = self.agent.post("/api/v1/agent/scores/", candidate, format="json", **headers)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Score.objects.count(), 0)

    # 功能：验证空分语义和旧版本写入拒绝。
    # 输入：合法空分、带确定解释的空分和已过期公司版本。
    # 输出：合法空分保存，其余请求拒绝且已保存结果不改变。
    # 逻辑：先保存空分，再更新画像使原租约版本过期。
    # 约束：不把空分当零分，不隐式重试。
    def test_null_score_and_stale_lease_contract(self):
        score, headers, _ = self.prepare_score()
        score.update(score=None, score_reasons=[{"feature": "insufficient_data", "contribution": 0, "note": "资料不足"}],
                     score_details={"score_breakdown": None, "top_reasons": [], "evidence": [], "recommended_next_action": None})
        response = self.agent.post("/api/v1/agent/scores/", score, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        invalid = deepcopy(score)
        invalid["score_details"]["recommended_next_action"] = "准备报价"
        self.assertEqual(self.agent.post("/api/v1/agent/scores/", invalid, format="json", **headers).status_code, 400)
        self.browser.patch("/api/v1/sales/seller-profile/", {"service_regions": ["Malaysia"]}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(self.agent.post("/api/v1/agent/scores/", score, format="json", **headers).status_code, 409)

    # 功能：验证正式版本展示与同分排序。
    # 输入：旧算法分数及具有不同紧急度、标识和空分的列表投影。
    # 输出：正式模式隐藏旧分数；新排序与已确认规则一致。
    # 逻辑：版本选择使用真实数据库；列表投影局部模拟以独立验证排序键。
    # 约束：模拟排序不代表真实分页性能已验证。
    def test_formal_sorting_and_legacy_score_visibility(self):
        snapshot = AnalysisInput.objects.create(company=self.company, revision=0, input_version="test", payload={"member_dedupe_keys": []})
        analysis = Analysis.objects.create(snapshot=snapshot, prompt_version="test", provider="rules", payload={"status": "completed"})
        Score.objects.create(analysis=analysis, score_version="rules-score-v1", value=90, payload={})
        self.assertIsNone(selectors.latest_result(self.company)[1])
        with self.settings(ANALYSIS_PROVIDER="rules"):
            self.assertIsNotNone(selectors.latest_result(self.company)[1])
        from unittest.mock import MagicMock
        query = MagicMock()
        query.exclude.return_value.filter.return_value.distinct.return_value = [self.company, self.peer, self.foreign]
        rows = [{"company_id": key, "score": value, "score_reasons": [{"feature": "urgency", "contribution": urgency}], "crm_status": "registered"}
                for key, value, urgency in (("b", 60, 10), ("a", 60, 20), ("c", None, 0))]
        with patch("apps.crm.selectors.company_row", side_effect=rows):
            self.assertEqual([item["company_id"] for item in selectors.list_companies(query, {})["results"]], ["a", "b", "c"])
