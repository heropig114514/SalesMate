"""职责：验证真实 PostgreSQL 图谱捕获、版本血缘、撤销和接口隔离。
实现：隔离测试数据库执行真实触发器与事务；邮件为合成原文，不调用 LLM。
关联：knowledge_graph 应用；TransactionTestCase 允许验证真实提交顺序和 REPEATABLE READ。
目录：
- GraphTests：图谱集成测试。
- GraphTests.setUp：创建隔离业务记录。
- GraphTests.order：创建合成订单及明细。
- GraphTests.email：创建带可核验证据的业务邮件及抽取。
- GraphTests.active：查询当前指定关系。
- GraphTests.test_capture_bulk_sql_and_rollback：验证捕获覆盖与事务回滚。
- GraphTests.test_purchase_has_independent_supports：验证取消一单不撤销其他订单支持。
- GraphTests.test_versions_idempotence_and_archive_restore：验证版本、幂等和归档恢复。
- GraphTests.test_latest_failed_extraction_and_review_withdraw：验证不回退旧抽取。
- GraphTests.test_conflicting_candidates_are_not_overwritten：验证不同预算候选待复核。
- GraphTests.test_failure_rolls_back_and_requires_explicit_recovery：验证失败原子性及显式恢复。
- GraphTests.test_delete_and_parent_cascade：验证删除与邮箱级联。
- GraphTests.test_owner_transfer_and_api_isolation：验证身份转移、旧用户撤销及 API 隔离。
- GraphTests.test_stale_api_and_lineage_contract：验证新鲜度和证据接口。
- GraphTests.test_new_commit_during_projection_stays_pending：验证并发新事件不会被错误确认。
- GraphTests.test_new_commit_during_projection_stays_pending.change_during_build：从独立连接制造中途提交。
- GraphTests.update_price_in_thread：在独立线程提交价格变更。
- GraphTests.test_truncate_capture_and_missing_trigger：验证清表捕获和触发器缺失检测。
- GraphTests.test_same_owner_lock_prevents_double_writer：验证同用户并发锁。
- GraphTests.test_account_reset_removes_graph_and_keeps_other_owner：验证账号清空同步删除图谱历史。
- GraphTests.test_opportunity_attributes_keep_field_evidence：验证商机属性的字段证据不混用状态字段。
变量索引：
- 无
"""

from concurrent.futures import ThreadPoolExecutor
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm.models import Company, Email, Extraction, Mailbox
from apps.accounts.reset import reset_account
from apps.accounts.reset_locks import account_lock
from apps.sales.models import CompanySettings, Opportunity, OrderLine, Product, SalesOrder
from apps.knowledge_graph.models import Change, Derivation, Entity, Fact, ProjectionState, SourceVersion
from apps.knowledge_graph.projection import Projector
from apps.knowledge_graph.sync import pending_owners, request_sync, require_capture, sync_owner


# 功能：在隔离 PostgreSQL 库验证图谱不变量。
# 逻辑：每项测试独立数据，真实触发器和 SQL；API 身份用强制认证隔离会话流程。
# 约束：不代表真实 LLM、真实业务质量或生产会话已验证。
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class GraphTests(TransactionTestCase):
    # 功能：创建独立的测试身份与基础目录。
    # 输入：无外部参数；由测试框架调用。
    # 输出：初始化 owner、other、company、product 和 client。
    # 逻辑：源 ORM 写入必须触发真实事件；非 PostgreSQL 明确失败。
    # 约束：仅测试库，不写入本地业务库。
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "Run graph tests against PostgreSQL; no SQLite substitution.")
        require_capture()
        self.owner = get_user_model().objects.create_user(username="graph-owner")
        self.other = get_user_model().objects.create_user(username="graph-other")
        self.company = Company.objects.create(owner=self.owner, group_key="graph.example", name="Graph customer")
        self.product = Product.objects.create(owner=self.owner, sku="P1", name="Device", currency="SGD", unit_price="100.00")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # 功能：创建合成订单和明细。
    # 输入：`number` 为唯一单号；`status` 默认 confirmed。
    # 输出：SalesOrder 实例。
    # 逻辑：一条数量为 2 的产品明细，由真实触发器捕获。
    # 约束：测试直接构造数据，不声称验证订单状态转换业务服务。
    def order(self, number, status="confirmed"):
        order = SalesOrder.objects.create(owner=self.owner, company=self.company, number=number, currency="SGD", status=status)
        OrderLine.objects.create(owner=self.owner, order=order, product=self.product, description="Device", quantity="2", unit_price="100.00")
        return order

    # 功能：建立可定位证据的合成入站邮件。
    # 输入：`key` 为消息键；`budget` 为原文预算字符串。
    # 输出：Email、Extraction 实例对。
    # 逻辑：原文与抽取共享同一证据字符串，不经过外部模型。
    # 约束：用于验证投影与血缘，不评估抽取质量。
    def email(self, key, budget="100 SGD"):
        mailbox, _ = Mailbox.objects.get_or_create(owner=self.owner, address="graph@internal.example")
        body = f"Budget: {budget}"
        email = Email.objects.create(dedupe_key=key, mailbox=mailbox, company=self.company, payload={"subject": "Request", "body_text": body}, sent_at=timezone.now(), received_at=timezone.now(), direction="inbound")
        extraction = Extraction.objects.create(email=email, prompt_version="synthetic-test-v1", status="completed", facts={"budget": [{"value": budget, "evidences": [body]}]})
        return email, extraction

    # 功能：取得当前仍获支持的本人关系。
    # 输入：`predicate` 为关系类型。
    # 输出：Fact 查询集。
    # 逻辑：排除 unsupported，保留待复核事实。
    # 约束：测试辅助，不作为生产权限接口。
    def active(self, predicate):
        return Fact.objects.filter(owner=self.owner, predicate=predicate).exclude(status="unsupported")

    # 功能：核验商机属性证据与实际输入字段一致。
    # 输入：无外部参数；含金额和未匹配产品名称的商机。
    # 输出：金额、币种及名称的字段证据准确；不生成未经验证的产品关系。
    # 逻辑：检查派生证据中明确登记的字段集合。
    # 约束：产品名称保留原属性，不因与目录相似而自动合并。
    def test_opportunity_attributes_keep_field_evidence(self):
        Opportunity.objects.create(owner=self.owner, company=self.company, title="Expansion", currency="SGD", amount="120", product_names=["Unknown device"])
        sync_owner(self.owner.pk)
        for predicate, expected in (("opportunity_amount", "amount,currency,archived"), ("requested_product_names", "product_names,archived")):
            fact = self.active(predicate).get()
            evidence = fact.supports.get(derivation__active=True).derivation.evidence
            self.assertIn(expected, [item["field"] for item in evidence])
        self.assertFalse(self.active("needs_product").exists())

    # 功能：验证批量 ORM、直接 SQL 和回滚捕获。
    # 输入：无外部参数；使用基础产品。
    # 输出：事件数量、归属与回滚不变量断言。
    # 逻辑：分别执行 bulk_create、SQL UPDATE 和主动回滚事务。
    # 约束：不以 Django signals 模拟数据库触发器。
    def test_capture_bulk_sql_and_rollback(self):
        initial = Change.objects.count()
        Product.objects.bulk_create([Product(owner=self.owner, sku="P2", name="Other", currency="SGD", unit_price="2")])
        self.assertEqual(Change.objects.count(), initial + 1)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sales_product SET name = %s WHERE id = %s", ["Updated", self.product.pk])
        self.assertEqual(Change.objects.count(), initial + 2)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Product.objects.filter(pk=self.product.pk).update(name="Rolled back")
                raise RuntimeError("rollback fixture")
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated")
        self.assertEqual(Change.objects.count(), initial + 2)

    # 功能：验证多来源支持按订单独立撤销。
    # 输入：无外部参数；创建两张确认单和一张草稿。
    # 输出：只有一个购买关系，初始有两条有效支持路径。
    # 逻辑：逐单取消，最后一条支持消失后购买事实才失效。
    # 约束：草稿和邮件不能产生购买事实。
    def test_purchase_has_independent_supports(self):
        first, second = self.order("O1"), self.order("O2")
        self.order("draft", "draft")
        sync_owner(self.owner.pk)
        fact = self.active("purchased").get()
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 2)
        SalesOrder.objects.filter(pk=first.pk).update(status="cancelled")
        sync_owner(self.owner.pk)
        self.assertEqual(self.active("purchased").get().pk, fact.pk)
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 1)
        SalesOrder.objects.filter(pk=second.pk).update(status="cancelled")
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "unsupported")

    # 功能：验证内容版本保留、重复构建与归档恢复。
    # 输入：无外部参数；产品价格和客户归档状态变更。
    # 输出：旧快照保留、重复构建不增版本，归档撤销后可恢复支持。
    # 逻辑：同一来源的新价格形成新事实，恢复使用匹配的新来源版本。
    # 约束：不覆盖旧 SourceVersion.snapshot。
    def test_versions_idempotence_and_archive_restore(self):
        self.order("O1")
        sync_owner(self.owner.pk)
        count = SourceVersion.objects.count()
        request_sync(self.owner.pk)
        sync_owner(self.owner.pk)
        self.assertEqual(SourceVersion.objects.count(), count)
        old = SourceVersion.objects.get(kind="sales.product", current=True)
        Product.objects.filter(pk=self.product.pk).update(unit_price="120")
        sync_owner(self.owner.pk)
        old.refresh_from_db()
        self.assertFalse(old.current)
        self.assertEqual(old.snapshot["unit_price"], "100.00")
        setting = CompanySettings.objects.create(owner=self.owner, company=self.company, archived=True)
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("purchased").exists())
        CompanySettings.objects.filter(pk=setting.pk).update(archived=False)
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("purchased").exists())

    # 功能：验证失败新抽取及非业务复核撤销旧事实。
    # 输入：无外部参数；一封有预算证据的邮件。
    # 输出：最新抽取失败时无旧事实回退，修复后恢复，复核撤销后再次失效。
    # 逻辑：只按抽取 ID 判断新旧，不按版本字符串排序。
    # 约束：没有模型调用或自动补抽取。
    def test_latest_failed_extraction_and_review_withdraw(self):
        email, extraction = self.email("email-1")
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("reported_budget").exists())
        failed = Extraction.objects.create(email=email, prompt_version="synthetic-test-v2", status="failed", facts=None)
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("reported_budget").exists())
        Extraction.objects.filter(pk=failed.pk).update(status="completed", facts=extraction.facts)
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("reported_budget").exists())
        Email.objects.filter(pk=email.pk).update(business_classification="non_business", review_revision=1)
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("reported_budget").exists())

    # 功能：保留同一客户的不同预算候选。
    # 输入：无外部参数；两封不同预算邮件。
    # 输出：两个候选均标记 needs_review。
    # 逻辑：不以较晚处理的邮件覆盖较早邮件，不宣称一定矛盾。
    # 约束：首版不自动把邮件归入特定商机。
    def test_conflicting_candidates_are_not_overwritten(self):
        self.email("email-1", "100 SGD")
        self.email("email-2", "200 SGD")
        sync_owner(self.owner.pk)
        self.assertEqual(set(self.active("reported_budget").values_list("value", flat=True)), {"100 SGD", "200 SGD"})
        self.assertEqual(set(self.active("reported_budget").values_list("status", flat=True)), {"needs_review"})

    # 功能：验证投影失败整体回滚且需显式恢复。
    # 输入：无外部参数；已发布图谱和无法定位的伪证据。
    # 输出：代次保持不变、失败事件持久存在，显式恢复后成功。
    # 逻辑：修正源数据不会偷偷重排旧 failed；--retry-failed 对应服务参数显式恢复。
    # 约束：失败样例只在测试库生成。
    def test_failure_rolls_back_and_requires_explicit_recovery(self):
        sync_owner(self.owner.pk)
        generation = ProjectionState.objects.get(owner=self.owner).generation
        email, extraction = self.email("invalid")
        original = extraction.facts
        Extraction.objects.filter(pk=extraction.pk).update(facts={"budget": [{"value": "100 SGD", "evidences": ["invented"]}]})
        with self.assertRaises(ValueError):
            sync_owner(self.owner.pk)
        self.assertEqual(ProjectionState.objects.get(owner=self.owner).generation, generation)
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, status="failed").exists())
        Extraction.objects.filter(pk=extraction.pk).update(facts=original)
        self.assertNotIn(self.owner.pk, pending_owners())
        request_sync(self.owner.pk, retry_failed=True)
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("reported_budget").exists())

    # 功能：验证实体删除与邮箱级联撤销。
    # 输入：无外部参数；一封已投影邮件和无订单引用的产品。
    # 输出：购买目录及邮件事实均不再有效，历史版本仍可审计。
    # 逻辑：真实 delete 触发父级事件并覆盖子级级联。
    # 约束：不存在发信或外部邮箱删除。
    def test_delete_and_parent_cascade(self):
        email, _ = self.email("email-1")
        sync_owner(self.owner.pk)
        Mailbox.objects.filter(pk=email.mailbox_id).delete()
        self.product.delete()
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("reported_budget").exists())
        self.assertFalse(Entity.objects.filter(kind="sales.product", active=True).exists())
        self.assertTrue(SourceVersion.objects.filter(kind="crm.email", current=False).exists())

    # 功能：验证归属转移撤销旧用户视图且不开放他人血缘。
    # 输入：无外部参数；将无交易引用产品直接转移到另一个账号。
    # 输出：双方产生事件，旧用户不再有当前产品，新用户有独立实体。
    # 逻辑：旧/新 owner 均重建；跨用户事实详情返回 404。
    # 约束：直接 SQL 属测试边界，正常业务仍沿用不可修改归属约束。
    def test_owner_transfer_and_api_isolation(self):
        sync_owner(self.owner.pk)
        Product.objects.filter(pk=self.product.pk).update(owner=self.other)
        self.assertIn(self.other.pk, pending_owners())
        sync_owner(self.owner.pk)
        sync_owner(self.other.pk)
        self.assertFalse(Entity.objects.filter(owner=self.owner, kind="sales.product", active=True).exists())
        foreign = Fact.objects.get(owner=self.other, predicate="catalog_price", status="active")
        response = self.client.get(f"/api/v1/graph/facts/{foreign.pk}/lineage/")
        self.assertEqual(response.status_code, 404)

    # 功能：验证图谱新鲜度、查询与历史血缘响应。
    # 输入：无外部参数；已创建但未处理的来源和确认订单。
    # 输出：未同步 503，同步后事实与血缘可读，源变更立即阻止旧图读取。
    # 逻辑：APIClient 使用真实路由、状态检查与模型查询。
    # 约束：强制认证不证明浏览器真实登录已验证。
    def test_stale_api_and_lineage_contract(self):
        self.order("O1")
        self.assertEqual(self.client.get("/api/v1/graph/facts/").status_code, 503)
        sync_owner(self.owner.pk)
        response = self.client.get("/api/v1/graph/facts/?predicate=purchased")
        self.assertEqual(response.status_code, 200)
        fact_id = response.json()["results"][0]["id"]
        lineage = self.client.get(f"/api/v1/graph/facts/{fact_id}/lineage/").json()
        self.assertIn("sales.orderline", {row["kind"] for row in lineage["results"][0]["inputs"]})
        Product.objects.filter(pk=self.product.pk).update(name="New label")
        self.assertFalse(self.client.get("/api/v1/graph/status/").json()["current"])
        self.assertEqual(self.client.get("/api/v1/graph/entities/").status_code, 503)
        self.assertEqual(APIClient().get("/api/v1/graph/status/").status_code, 403)

    # 功能：验证重建期间的新提交不会被旧快照错误确认。
    # 输入：无外部参数；线程使用独立数据库连接修改产品。
    # 输出：首轮后新事件仍 pending，下一轮才发布更新价格。
    # 逻辑：在 Projector 已读取源快照后写入，验证真实 PostgreSQL MVCC 行为。
    # 约束：不依赖 sleep 定时猜测竞态。
    def test_new_commit_during_projection_stays_pending(self):
        original = Projector.run

        # 功能：在独立连接提交产品价格后继续旧快照构建。
        # 输入：`projector` 为已读取输入的投影实例。
        # 输出：原 run 的统计结果。
        # 逻辑：线程池等待数据库提交完成，下一请求应看见新 pending。
        # 约束：线程数据库连接显式关闭，不向业务库写入。
        def change_during_build(projector):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.update_price_in_thread).result(timeout=15)
            return original(projector)

        with patch.object(Projector, "run", change_during_build):
            sync_owner(self.owner.pk)
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, status="pending").exists())
        sync_owner(self.owner.pk)
        self.assertEqual(self.active("catalog_price").get().value["amount"], "150.00")

    # 功能：在线程独立连接中提交一次价格更新。
    # 输入：无外部参数；读取当前测试产品主键。
    # 输出：无；提交完成后关闭该线程连接。
    # 逻辑：与构建事务并行的真实 autocommit 写入。
    # 约束：异常传播到 Future，不静默忽略。
    def update_price_in_thread(self):
        try:
            Product.objects.filter(pk=self.product.pk).update(unit_price="150")
        finally:
            connections.close_all()

    # 功能：验证 TRUNCATE 产生重建事件且禁用捕获可检测。
    # 输入：无外部参数；隔离库中的全部源表。
    # 输出：清表事件存在，禁用触发器时 require_capture 拒绝。
    # 逻辑：先检查禁用并在 finally 恢复，再显式 TRUNCATE CASCADE。
    # 约束：仅测试库，禁止在真实业务库运行本测试 SQL。
    def test_truncate_capture_and_missing_trigger(self):
        sync_owner(self.owner.pk)
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE sales_product DISABLE TRIGGER salesmate_kg_capture")
            try:
                with self.assertRaises(RuntimeError):
                    require_capture()
            finally:
                cursor.execute("ALTER TABLE sales_product ENABLE TRIGGER salesmate_kg_capture")
            cursor.execute("TRUNCATE sales_product CASCADE")
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, operation="TRUNCATE").exists())
        sync_owner(self.owner.pk)
        self.assertFalse(Entity.objects.filter(owner=self.owner, kind="sales.product", active=True).exists())

    # 功能：验证同用户图谱构建不能双写。
    # 输入：无外部参数；用第二数据库连接持有该用户事务锁。
    # 输出：锁占用时 sync_owner 返回 None，事件保持 pending。
    # 逻辑：副连接获取相同 advisory key，finally 释放并关闭。
    # 约束：只锁测试用户，不测试吞吐量。
    def test_same_owner_lock_prevents_double_writer(self):
        other = connection.copy(alias="graph-lock-test")
        try:
            other.set_autocommit(False)
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"salesmate-kg:{self.owner.pk}"])
            self.assertIsNone(sync_owner(self.owner.pk))
            self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, status="pending").exists())
        finally:
            other.rollback()
            other.close()

    # 功能：验证清空账号不会留下图谱证据或误删其他账号。
    # 输入：无外部参数；双方图谱和本人原始邮件。
    # 输出：本人实体、快照、事件与支持清除，另一用户图谱保留。
    # 逻辑：运行真实 reset_account 与独占锁，覆盖原始 SQL 删除及 M2M 输入边。
    # 约束：只操作测试账号和测试会话，不调用实际文件或外部服务。
    def test_account_reset_removes_graph_and_keeps_other_owner(self):
        self.email("email-reset")
        sync_owner(self.owner.pk)
        Product.objects.create(owner=self.other, sku="OTHER", name="Other", currency="SGD", unit_price="1")
        sync_owner(self.other.pk)
        session = SessionStore()
        session.create()
        with account_lock(self.owner.pk, exclusive=True):
            reset_account(self.owner, uuid.uuid4(), session)
        self.assertFalse(Entity.objects.filter(owner=self.owner).exists())
        self.assertFalse(SourceVersion.objects.filter(owner=self.owner).exists())
        self.assertFalse(Change.objects.filter(owner_id=self.owner.pk).exists())
        self.assertFalse(Derivation.inputs.through.objects.filter(derivation__owner=self.owner).exists())
        self.assertTrue(Entity.objects.filter(owner=self.other).exists())
