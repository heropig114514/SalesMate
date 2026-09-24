"""职责：验证全业务 schema 与外部信息自动建图的数据库不变量。
实现：真实 PostgreSQL 捕获、投影及 HTTP；模型边界使用可核验合成响应，独立本机冒烟另行验证。
关联：knowledge_graph 新输入链路及既有图谱；不会调用真实外部服务或修改业务数据库。
目录：
- SemanticGraphTests：隔离数据库集成测试。
- SemanticGraphTests.setUp：创建同用户业务与隔离身份。
- SemanticGraphTests.records：建立部分产品观察。
- SemanticGraphTests.test_catalog_and_all_triggers：核验目录与捕获覆盖。
- SemanticGraphTests.test_partial_records_link_and_idempotence：部分字段、自动关联及幂等。
- SemanticGraphTests.test_missing_reference_resolves_later：未知外键目标随后进入业务图。
- SemanticGraphTests.test_conflict_and_retraction_keep_independent_support：多来源及候选冲突撤回。
- SemanticGraphTests.test_text_links_with_evidence：模型候选、实体引用与证据血缘。
- SemanticGraphTests.test_bad_model_output_is_atomic：非法引用及引句不能污染图谱。
- SemanticGraphTests.test_context_change_rejects_commit：推理期间业务变化导致提交拒绝。
- SemanticGraphTests.test_context_change_rejects_commit.change：模拟模型期间提交新业务值。
- SemanticGraphTests.test_api_owner_scope_and_invalid_input：HTTP 隔离与输入契约。
- SemanticGraphTests.test_expanded_owner_paths_and_archive：新来源捕获归属和归档。
- SemanticGraphTests.test_reset_removes_episodes：账号清理覆盖新来源。
- SemanticGraphTests.test_local_provider_rejects_remote：本机模型端点不接受远程主机。
- SemanticGraphTests.test_mail_observation_tracks_source_validity：邮件观察随源分类和原文变化失效。
- EntityResolutionTests：独立实体解析单元测试。
- EntityResolutionTests.test_unique_name_and_type_preserves_raw_output：名称对齐不改写原始模型建议。
- EntityResolutionTests.test_ambiguous_name_stays_separate：同名歧义不任意合并。
- EntityResolutionTests.test_known_neighbour_disambiguates：同名候选以共同关系端点限定。
- GenerationContractTests：生成约束的独立协议测试。
- GenerationContractTests.test_relations_require_local_keys_and_null_value：拦截合法 JSON 中的非法关系形状。
变量索引：
- 无
"""
import copy
import uuid
import jsonschema
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import TransactionTestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.reset import reset_account
from apps.crm.models import Company, AnalysisInput, Analysis, Score, Mailbox, Email
from apps.sales.models import Product, Quote
from apps.knowledge_graph.business_schema import catalog, source_models
from apps.knowledge_graph.episodes import ingest, retract
from apps.knowledge_graph.models import Change, Entity, Episode, Fact
from apps.knowledge_graph.projection import identity
from apps.knowledge_graph.sync import require_capture, sync_owner
from apps.knowledge_graph.semantic_provider import generate
from apps.knowledge_graph.management.commands.graph_ingest import email_payload
from apps.knowledge_graph.entity_resolution import resolve_entities
from apps.knowledge_graph.semantic_contract import response_schema


# 功能：验证真实 PostgreSQL 上自动建图和隔离。
# 逻辑：每项测试独立账号，真实数据库事务；仅替换模型生成边界。
# 约束：模拟模型测试不证明 Qwen 的语义质量或真实推理速度。
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class SemanticGraphTests(TransactionTestCase):
    # 功能：准备业务实体和 API 身份。
    # 输入：无外部参数，读取隔离测试数据库。
    # 输出：owner、other、company、product、now、client 状态。
    # 逻辑：创建来源后真实同步，所有触发器必须已安装。
    # 约束：不是 PostgreSQL 则失败，不能用 SQLite 模拟通过。
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.owner = get_user_model().objects.create_user(username="semantic-owner")
        self.other = get_user_model().objects.create_user(username="semantic-other")
        self.company = Company.objects.create(owner=self.owner, group_key="acme.example", name="Acme")
        self.product = Product.objects.create(owner=self.owner, sku="EDGE", name="Edge", currency="SGD", unit_price="100")
        self.now = timezone.now()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        sync_owner(self.owner.pk)

    # 功能：生成只包含部分字段的产品观察。
    # 输入：`price` 为可选价格字符串。
    # 输出：结构化输入列表。
    # 逻辑：只有 sku 和选填价格，不提供 name/currency 等业务必填字段。
    # 约束：用于验证观察不会强制创建业务产品。
    def records(self, price="120"):
        return [{"key": "product", "schema": "sales.product", "fields": {"sku": "EDGE", "unit_price": price}}]

    # 功能：核验全部声明来源已安装捕获。
    # 输入：无外部参数；读取模型目录和真实 pg_trigger。
    # 输出：来源数、敏感字段排除和 schema 字段断言。
    # 逻辑：对完整目录调用运行前提检查。
    # 约束：结构覆盖不是每个领域的语义抽取质量证明。
    def test_catalog_and_all_triggers(self):
        require_capture()
        self.assertEqual(len(source_models()), 48)
        self.assertNotIn("encrypted_credentials", catalog()["sales.connection"])
        self.assertNotIn("accounts.user", catalog())
        self.assertEqual(catalog()["sales.quote"]["company"]["target"], "crm.company")

    # 功能：验证字段缺失、身份对齐和重复请求。
    # 输入：无外部参数；只给 sku/价格的输入。
    # 输出：复用产品实体，业务价格不变，同键不增加记录。
    # 逻辑：结构化分支不调用模型；字段作为独立观察保存。
    # 约束：不把观察价格解释为目录已更新。
    def test_partial_records_link_and_idempotence(self):
        with patch("apps.knowledge_graph.episodes.generate") as model:
            first = ingest(self.owner.pk, "partial", self.now, records=self.records())
            second = ingest(self.owner.pk, "partial", self.now, records=self.records())
        model.assert_not_called()
        self.assertEqual(first.pk, second.pk)
        product_id = identity("entity", self.owner.pk, "sales.product", str(self.product.pk))
        self.assertTrue(Fact.objects.filter(subject_id=product_id, predicate="field:unit_price", value="120", status="active").exists())
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.unit_price), "100.00")
        self.assertEqual(Product.objects.count(), 1)
        with self.assertRaises(Exception):
            ingest(self.owner.pk, "partial", self.now, records=self.records("121"))

    # 功能：验证不完整关联可随权威记录到达而补齐。
    # 输入：无外部参数；报价引用尚不存在的客户 UUID。
    # 输出：先有外部占位，随后外键指向新增业务客户。
    # 逻辑：来源 ID 稳定匹配，同步器重新投影，不再次调用模型。
    # 约束：外部报价只存在图中，未创建真实 Quote。
    def test_missing_reference_resolves_later(self):
        customer_id = uuid.uuid4()
        ingest(self.owner.pk, "quote", self.now, records=[{"key": "q", "schema": "sales.quote", "fields": {"number": "Q-NEW", "company": str(customer_id)}}])
        self.assertTrue(Entity.objects.filter(owner=self.owner, kind="external.crm.company", active=True).exists())
        Company.objects.create(id=customer_id, owner=self.owner, group_key="later.example", name="Later")
        sync_owner(self.owner.pk)
        target = identity("entity", self.owner.pk, "crm.company", str(customer_id))
        self.assertTrue(Fact.objects.filter(predicate="field:company", object_id=target, status="active").exists())
        self.assertEqual(Quote.objects.count(), 0)

    # 功能：验证多来源相同值与不同候选值的独立支持。
    # 输入：无外部参数；三份价格观察。
    # 输出：不同值标记待复核；撤回后恢复剩余唯一值及来源。
    # 逻辑：相同断言复用 Fact，但不同 Episode 有独立 Derivation。
    # 约束：不通过新时间自动覆盖旧值。
    def test_conflict_and_retraction_keep_independent_support(self):
        first = ingest(self.owner.pk, "one", self.now, records=self.records())
        ingest(self.owner.pk, "two", self.now, records=self.records())
        third = ingest(self.owner.pk, "three", self.now, records=self.records("130"))
        fact = Fact.objects.get(owner=self.owner, predicate="field:unit_price", value="120")
        self.assertEqual(fact.status, "needs_review")
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 2)
        retract(self.owner.pk, first.pk)
        retract(self.owner.pk, third.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "active")
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 1)

    # 功能：验证模型文本候选在原文和已有实体上落地。
    # 输入：无外部参数；合成模型返回带原文引句的预算。
    # 输出：既有公司获得陈述，来源血缘包含真实引句。
    # 逻辑：生成边界被 mock，其余校验、数据库和同步真实执行。
    # 约束：不据此宣称实际模型理解了预算。
    def test_text_links_with_evidence(self):
        company_id = str(identity("entity", self.owner.pk, "crm.company", str(self.company.pk)))
        result = {"entities": [{"key": "c", "kind": "crm.company", "name": "Acme", "existing_id": company_id, "quote": "Acme"}],
                  "facts": [{"subject": "c", "predicate": "reported_budget", "object": None, "value": "800 SGD", "quote": "budget is 800 SGD"}]}
        with patch("apps.knowledge_graph.episodes.generate", return_value=(result, {"mode": "mock"})):
            ingest(self.owner.pk, "text", self.now, text="Acme budget is 800 SGD")
        fact = Fact.objects.get(subject_id=company_id, predicate="reported_budget", status="active")
        self.assertEqual(fact.supports.get(derivation__active=True).derivation.evidence[-1]["quote"], "budget is 800 SGD")

    # 功能：验证模型越权或伪造引句不能写入。
    # 输入：无外部参数；非法实体引用和不在原文的引句。
    # 输出：每次失败后 Episode 数为零。
    # 逻辑：通过真实输入服务执行完整契约校验。
    # 约束：只模拟坏响应，不访问远程模型。
    def test_bad_model_output_is_atomic(self):
        bad = {"entities": [{"key": "c", "kind": "crm.company", "name": "Acme", "existing_id": str(uuid.uuid4()), "quote": "Acme"}], "facts": []}
        for index in range(2):
            result = copy.deepcopy(bad)
            if index:
                result["entities"][0].update(existing_id=None, quote="not present")
            with patch("apps.knowledge_graph.episodes.generate", return_value=(result, {})), self.assertRaises(Exception):
                ingest(self.owner.pk, str(index), self.now, text="Acme")
        self.assertEqual(Episode.objects.count(), 0)

    # 功能：验证模型调用期间的上下文变化不会被忽略。
    # 输入：无外部参数；模型桩更新产品。
    # 输出：关联提交被拒绝，没有新来源。
    # 逻辑：业务触发器新增 pending 状态，提交前要求当前快照。
    # 约束：这里是同线程模拟外部提交边界，非真实多进程压力测试。
    def test_context_change_rejects_commit(self):
        # 功能：在模型边界改变业务来源。
        # 输入：`prompt` 为传给模型的消息列表。
        # 输出：合法空抽取并产生已提交业务变更。
        # 逻辑：验证输入服务会发现期间新增事件。
        # 约束：仅测试库内改变合成产品。
        def change(prompt):
            Product.objects.filter(pk=self.product.pk).update(name="Changed")
            return {"entities": [], "facts": []}, {}
        with patch("apps.knowledge_graph.episodes.generate", side_effect=change), self.assertRaises(Exception):
            ingest(self.owner.pk, "stale", self.now, text="Acme")
        self.assertEqual(Episode.objects.count(), 0)

    # 功能：验证 HTTP 的隔离和严格字段约束。
    # 输入：无外部参数；两个身份及无效 schema 字段。
    # 输出：本人创建成功、越权404、未知字段400。
    # 逻辑：使用真实 DRF 视图，强制认证仅隔离登录流程。
    # 约束：未声称真实浏览器 CSRF 会话已验证。
    def test_api_owner_scope_and_invalid_input(self):
        payload = {"source_key": "api", "observed_at": self.now.isoformat(), "records": self.records()}
        response = self.client.post("/api/v1/graph/episodes/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/graph/episodes/{response.data['id']}/").status_code, 404)
        self.client.force_authenticate(self.owner)
        payload["source_key"] = "invalid-fields"
        payload["records"][0]["fields"]["owner"] = self.other.pk
        self.assertEqual(self.client.post("/api/v1/graph/episodes/", payload, format="json").status_code, 400)

    # 功能：验证新增来源的多层归属捕获及归档撤销。
    # 输入：无外部参数；分析链和报价。
    # 输出：Score 变更归属正确，报价归档后不再是活动实体。
    # 逻辑：真实 PostgreSQL 函数解析 snapshot/analysis 外键。
    # 约束：合成分析载荷不代表实际模型结果。
    def test_expanded_owner_paths_and_archive(self):
        snapshot = AnalysisInput.objects.create(company=self.company, input_version="v", revision=1, payload={})
        analysis = Analysis.objects.create(snapshot=snapshot, prompt_version="v", payload={}, provider="rules")
        Score.objects.create(analysis=analysis, payload={}, score_version="v", value=1)
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, kind="crm.score", status="pending").exists())
        quote = Quote.objects.create(owner=self.owner, company=self.company, number="Q1", currency="SGD")
        sync_owner(self.owner.pk)
        self.assertTrue(Entity.objects.filter(kind="sales.quote", source_id=str(quote.pk), active=True).exists())
        quote.archived = True
        quote.save()
        sync_owner(self.owner.pk)
        self.assertFalse(Entity.objects.filter(kind="sales.quote", source_id=str(quote.pk), active=True).exists())

    # 功能：验证新来源属于账号清空范围。
    # 输入：无外部参数；一份合成观察和账号会话。
    # 输出：清空后没有 Episode 和捕获事件残留。
    # 逻辑：调用现有真实清空服务。
    # 约束：仅合成测试账号，不测试并发文件清理。
    def test_reset_removes_episodes(self):
        ingest(self.owner.pk, "reset", self.now, records=self.records())
        session = SessionStore()
        session.create()
        reset_account(self.owner, uuid.uuid4(), session)
        self.assertFalse(Episode.objects.filter(owner=self.owner).exists())
        self.assertFalse(Change.objects.filter(owner_id=self.owner.pk).exists())

    # 功能：验证模型适配器不会把输入发送到远程服务。
    # 输入：无外部参数；模拟非回环管理员配置。
    # 输出：请求前明确拒绝。
    # 逻辑：HTTP Session 不应被创建。
    # 约束：这是端点检查，不证明真实本机模型服务可用。
    def test_local_provider_rejects_remote(self):
        with patch.dict("os.environ", {"GRAPH_LLM_URL": "https://example.com/v1", "GRAPH_LLM_MODEL": "model"}), patch("requests.Session") as session:
            with self.assertRaises(RuntimeError):
                generate([])
            session.assert_not_called()

    # 功能：验证自动邮件输入保留源生命周期约束。
    # 输入：无外部参数；合成邮件及预算模型桩。
    # 输出：分类撤回、恢复和正文改变分别撤销、恢复和撤销观察支持。
    # 逻辑：邮件源指纹与业务资格由投影器复核，源变化不触发模型自动重试。
    # 约束：模型输出被模拟，验证真实数据库触发器与证据依赖，非真实邮件抽取质量。
    def test_mail_observation_tracks_source_validity(self):
        mailbox = Mailbox.objects.create(owner=self.owner, address="local@example.com")
        email = Email.objects.create(dedupe_key="semantic-mail", mailbox=mailbox, company=self.company,
            direction="inbound", sent_at=self.now, received_at=self.now, payload={"subject": "Acme", "body_text": "Budget 800 SGD"})
        company_id = str(identity("entity", self.owner.pk, "crm.company", str(self.company.pk)))
        result = {"entities": [{"key": "c", "kind": "crm.company", "name": "Acme", "existing_id": company_id, "quote": "Acme"}],
                  "facts": [{"subject": "c", "predicate": "reported_budget", "object": None, "value": "800 SGD", "quote": "Budget 800 SGD"}]}
        with patch("apps.knowledge_graph.episodes.generate", return_value=(result, {})):
            ingest(self.owner.pk, **email_payload(email))
        fact = Fact.objects.get(owner=self.owner, predicate="reported_budget")
        self.assertEqual(fact.status, "active")
        email.business_classification = "non_business"
        email.save()
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "unsupported")
        email.business_classification = "business"
        email.save()
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "active")
        email.payload["body_text"] = "Budget changed"
        email.save()
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "unsupported")


# 功能：验证实体解析的独立、确定性契约。
# 逻辑：只用内存中的候选和关系，不调用模型或数据库。
# 约束：验证解析程序，不证明现实世界中同名身份确实相同。
class EntityResolutionTests(SimpleTestCase):
    # 功能：验证唯一名称和类型匹配能复用既有实体。
    # 输入：无外部参数；模拟模型未选择 ID 的同名实体。
    # 输出：解析结果引用既有 ID，原始模型对象仍保持 null。
    # 逻辑：同名异类型候选不会被选中。
    # 约束：这是确定性对齐程序测试，不宣称模型本身完成了对齐。
    def test_unique_name_and_type_preserves_raw_output(self):
        raw = {"entities": [{"key": "m", "name": "Mira", "kind": "crm.contact", "existing_id": None}], "facts": []}
        context = {"entities": [{"id": "contact", "label": "Mira", "kind": "crm.contact"}, {"id": "company", "label": "Mira", "kind": "crm.company"}], "facts": []}
        resolved, decisions = resolve_entities(raw, context)
        self.assertEqual(resolved["entities"][0]["existing_id"], "contact")
        self.assertIsNone(raw["entities"][0]["existing_id"])
        self.assertEqual(decisions[0]["method"], "unique_exact_name_and_type")

    # 功能：验证多候选且无额外证据时保持歧义。
    # 输入：无外部参数；两个同类型同名联系人。
    # 输出：解析结果不指向任一候选。
    # 逻辑：没有共同已知邻居时不能依据候选顺序选人。
    # 约束：保留独立观察可能产生重复，需要后续人工或更强证据消歧。
    def test_ambiguous_name_stays_separate(self):
        raw = {"entities": [{"key": "m", "name": "Mira", "kind": "crm.contact", "existing_id": None}], "facts": []}
        context = {"entities": [{"id": "a", "label": "Mira", "kind": "crm.contact"}, {"id": "b", "label": "Mira", "kind": "crm.contact"}], "facts": []}
        result, _ = resolve_entities(raw, context)
        self.assertIsNone(result["entities"][0]["existing_id"])

    # 功能：验证共同已知邻居可限定同名候选。
    # 输入：无外部参数；两位 Mira 中仅一位已关联目标公司。
    # 输出：解析到具有同一已知关系端点的联系人。
    # 逻辑：关系类型可不同，只以共同端点作身份上下文，解析记录明确说明该依据。
    # 约束：此关联仍是推断，不等同于唯一外部身份证明。
    def test_known_neighbour_disambiguates(self):
        raw = {"entities": [{"key": "m", "name": "Mira", "kind": "crm.contact", "existing_id": None},
                            {"key": "c", "name": "Acme", "kind": "crm.company", "existing_id": "c"}],
               "facts": [{"subject": "m", "object": "c"}]}
        context = {"entities": [{"id": "a", "label": "Mira", "kind": "crm.contact"}, {"id": "b", "label": "Mira", "kind": "crm.contact"}],
                   "facts": [{"subject": "a", "object": "c"}]}
        result, decisions = resolve_entities(raw, context)
        self.assertEqual(result["entities"][0]["existing_id"], "a")
        self.assertEqual(decisions[0]["method"], "exact_name_and_known_neighbour")


# 功能：验证约束解码所依据的外部 JSON Schema 契约。
# 逻辑：以独立 JSON Schema 验证器检查合法及非法模型响应形状。
# 约束：只验证生成结构，不替代真实 llama.cpp 冒烟、实体存在性及语义校验。
class GenerationContractTests(SimpleTestCase):
    # 功能：防止关系输出业务 ID、名称或非空属性值。
    # 输入：无外部参数；固定关系响应及三种历史错误形状。
    # 输出：正确局部引用通过，三类错误均被生成协议拒绝。
    # 逻辑：验证器独立消费 schema，与应用层 validate_extraction 分离。
    # 约束：局部目标存在性和端点类型仍由应用校验，不能由此宣称关系正确。
    def test_relations_require_local_keys_and_null_value(self):
        schema = response_schema()
        result = {"entities": [], "facts": [{"subject": "e1", "predicate": "works_for", "object": "e2", "value": None, "quote": "Mira works for Acme"}]}
        jsonschema.validate(result, schema)
        for field, value in (("object", "Acme"), ("value", "Acme"), ("predicate", "invented_relation")):
            invalid = copy.deepcopy(result)
            invalid["facts"][0][field] = value
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid, schema)
